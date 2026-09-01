"""Review-flagging: runs after OCR and entity extraction to automatically
flag content that needs human review.

Operates purely on the in-memory dataclasses from prior stages (ocr.engine
.OCRResult, ocr.layout.Region, extraction.entities.ExtractedEntity) — like
those modules, it doesn't touch the database itself. Mapping a flag's
`region_bbox`/`entity_raw_text` reference to a persisted `region_id`/
`entity_id` UUID happens once the pipeline is actually wired end-to-end
(stage 9), at which point these flags get written into `review_flags`
(schema: storage.models.ReviewFlag).

Flag types (mirrors storage.models.FlagType):
- low_ocr_confidence: a region's overall OCR confidence, or an individual
  word within an otherwise-fine region, is below threshold.
- illegible: OCR confidence is near-zero, or the output is mostly
  non-alphanumeric noise (garbage characters rather than real text).
- entity_conflict: a date outside a plausible range, a cash amount with an
  implausible magnitude, or two person names within the same document that
  are similar-but-not-identical (likely OCR variance on the same person,
  not two different people).
- extraction_failure: layout suggests an entity should be present (e.g. a
  table/ledger region) but none was extracted from it.

All thresholds are configurable via config.Settings — see config.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from itertools import combinations
from typing import Literal

from rapidfuzz import fuzz

from config import get_settings
from extraction.entities import ExtractedEntity
from ocr.engine import OCRResult
from ocr.layout import BBox, Region

FlagType = Literal["low_ocr_confidence", "illegible", "entity_conflict", "extraction_failure"]
Severity = Literal["low", "medium", "high"]

_ALNUM_RE = re.compile(r"[A-Za-z0-9]")
_NON_WHITESPACE_RE = re.compile(r"\S")
_AMOUNT_VALUE_RE = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass
class AnomalyFlag:
    flag_type: FlagType
    severity: Severity
    explanation: str
    region_bbox: BBox | None = None
    entity_raw_text: str | None = None


# --------------------------------------------------------------------------
# low_ocr_confidence
# --------------------------------------------------------------------------


def _confidence_severity(confidence: float, threshold: float) -> Severity:
    """Graduated severity: the further below threshold, the more severe."""
    gap = threshold - confidence
    if gap >= threshold * 0.6:
        return "high"
    if gap >= threshold * 0.3:
        return "medium"
    return "low"


def detect_low_ocr_confidence(
    ocr_result: OCRResult, *, region_bbox: BBox | None = None, threshold: float | None = None
) -> list[AnomalyFlag]:
    """Flag a region whose overall confidence is below threshold (one flag,
    to avoid flooding review with a flag per word), or — if the region as a
    whole is fine — individual words that fall below it anyway.
    """
    threshold = threshold if threshold is not None else get_settings().low_ocr_confidence_threshold

    if ocr_result.document_confidence < threshold:
        return [
            AnomalyFlag(
                flag_type="low_ocr_confidence",
                severity=_confidence_severity(ocr_result.document_confidence, threshold),
                explanation=(
                    f"OCR confidence {ocr_result.document_confidence:.0f}%, "
                    f"below threshold of {threshold:.0f}%"
                ),
                region_bbox=region_bbox,
            )
        ]

    flags = []
    for word in ocr_result.words:
        if word.confidence < threshold:
            flags.append(
                AnomalyFlag(
                    flag_type="low_ocr_confidence",
                    severity=_confidence_severity(word.confidence, threshold),
                    explanation=(
                        f"word {word.text!r} has OCR confidence {word.confidence:.0f}%, "
                        f"below threshold of {threshold:.0f}%"
                    ),
                    region_bbox=region_bbox,
                )
            )
    return flags


# --------------------------------------------------------------------------
# illegible
# --------------------------------------------------------------------------


def _alnum_ratio(text: str) -> float:
    non_whitespace = _NON_WHITESPACE_RE.findall(text)
    if not non_whitespace:
        return 1.0  # empty/whitespace-only text isn't "noise" — nothing to judge
    alnum = _ALNUM_RE.findall(text)
    return len(alnum) / len(non_whitespace)


def detect_illegible(
    ocr_result: OCRResult,
    *,
    region_bbox: BBox | None = None,
    confidence_threshold: float | None = None,
    alnum_ratio_threshold: float | None = None,
) -> AnomalyFlag | None:
    settings = get_settings()
    confidence_threshold = (
        confidence_threshold if confidence_threshold is not None else settings.illegible_confidence_threshold
    )
    alnum_ratio_threshold = (
        alnum_ratio_threshold if alnum_ratio_threshold is not None else settings.illegible_alnum_ratio_threshold
    )

    if ocr_result.document_confidence < confidence_threshold:
        return AnomalyFlag(
            flag_type="illegible",
            severity="high",
            explanation=(
                f"OCR confidence {ocr_result.document_confidence:.0f}% is near-zero "
                f"(below {confidence_threshold:.0f}%) — region is likely illegible"
            ),
            region_bbox=region_bbox,
        )

    ratio = _alnum_ratio(ocr_result.text)
    if ratio < alnum_ratio_threshold:
        return AnomalyFlag(
            flag_type="illegible",
            severity="high",
            explanation=(
                f"OCR output is {ratio:.0%} alphanumeric (below {alnum_ratio_threshold:.0%}) "
                f"— mostly non-alphanumeric noise, likely illegible source"
            ),
            region_bbox=region_bbox,
        )
    return None


# --------------------------------------------------------------------------
# entity_conflict
# --------------------------------------------------------------------------


def _parse_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _parse_amount_magnitude(value: str) -> float | None:
    match = _AMOUNT_VALUE_RE.search(value)
    if match is None:
        return None
    return abs(float(match.group()))


def detect_implausible_dates(
    entities: list[ExtractedEntity], *, min_year: int | None = None, max_year: int | None = None
) -> list[AnomalyFlag]:
    settings = get_settings()
    min_year = min_year if min_year is not None else settings.entity_conflict_min_year
    max_year = max_year if max_year is not None else settings.entity_conflict_max_year

    flags = []
    for entity in entities:
        if entity.entity_type != "date" or not entity.normalized_value:
            continue
        parsed = _parse_iso_date(entity.normalized_value)
        if parsed is None:
            continue
        if not (min_year <= parsed.year <= max_year):
            flags.append(
                AnomalyFlag(
                    flag_type="entity_conflict",
                    severity="medium",
                    explanation=(
                        f"date {entity.normalized_value!r} (from {entity.raw_text!r}) has year "
                        f"{parsed.year}, outside the plausible range {min_year}-{max_year}"
                    ),
                    region_bbox=entity.region_bbox,
                    entity_raw_text=entity.raw_text,
                )
            )
    return flags


def detect_implausible_amounts(
    entities: list[ExtractedEntity], *, max_plausible_amount: float | None = None
) -> list[AnomalyFlag]:
    max_plausible_amount = (
        max_plausible_amount
        if max_plausible_amount is not None
        else get_settings().entity_conflict_max_plausible_amount
    )

    flags = []
    for entity in entities:
        if entity.entity_type != "amount" or not entity.normalized_value:
            continue
        magnitude = _parse_amount_magnitude(entity.normalized_value)
        if magnitude is None:
            continue
        if magnitude > max_plausible_amount:
            flags.append(
                AnomalyFlag(
                    flag_type="entity_conflict",
                    severity="medium",
                    explanation=(
                        f"amount {entity.normalized_value!r} (from {entity.raw_text!r}) exceeds the "
                        f"plausible maximum of {max_plausible_amount:,.2f}"
                    ),
                    region_bbox=entity.region_bbox,
                    entity_raw_text=entity.raw_text,
                )
            )
    return flags


def detect_inconsistent_name_spellings(
    entities: list[ExtractedEntity], *, fuzzy_threshold: float | None = None
) -> list[AnomalyFlag]:
    """Two person entities within the same document whose names are similar
    but not identical are more likely the same person spelled two ways by
    OCR than two different people — flag the pair for a human to confirm.
    """
    fuzzy_threshold = (
        fuzzy_threshold if fuzzy_threshold is not None else get_settings().entity_conflict_name_fuzzy_threshold
    )

    persons = [e for e in entities if e.entity_type == "person" and e.raw_text.strip()]
    flags = []
    seen_pairs: set[tuple[str, str]] = set()
    for a, b in combinations(persons, 2):
        name_a, name_b = a.raw_text.strip(), b.raw_text.strip()
        if name_a == name_b:
            continue
        pair_key = tuple(sorted((name_a, name_b)))
        if pair_key in seen_pairs:
            continue
        similarity = fuzz.ratio(name_a, name_b) / 100.0
        if similarity >= fuzzy_threshold:
            seen_pairs.add(pair_key)
            flags.append(
                AnomalyFlag(
                    flag_type="entity_conflict",
                    severity="medium",
                    explanation=(
                        f"inconsistent name spelling: {name_a!r} vs {name_b!r} "
                        f"({similarity:.0%} similar) — possibly the same person"
                    ),
                    region_bbox=a.region_bbox,
                    entity_raw_text=f"{name_a} / {name_b}",
                )
            )
    return flags


def detect_entity_conflicts(
    entities: list[ExtractedEntity],
    *,
    min_year: int | None = None,
    max_year: int | None = None,
    max_plausible_amount: float | None = None,
    fuzzy_threshold: float | None = None,
) -> list[AnomalyFlag]:
    return [
        *detect_implausible_dates(entities, min_year=min_year, max_year=max_year),
        *detect_implausible_amounts(entities, max_plausible_amount=max_plausible_amount),
        *detect_inconsistent_name_spellings(entities, fuzzy_threshold=fuzzy_threshold),
    ]


# --------------------------------------------------------------------------
# extraction_failure
# --------------------------------------------------------------------------

# Region types where a specific entity type is structurally expected —
# if layout found one of these but extraction found none of the matching
# entity type in it, something likely went wrong upstream.
_EXPECTED_ENTITY_BY_REGION_TYPE: dict[str, str] = {
    "table": "amount",  # a ledger row/table with no amount detected
    "signature": "person",  # a signature block with no name detected
}


def detect_extraction_failures(
    regions: list[Region], entities_by_region: dict[int, list[ExtractedEntity]]
) -> list[AnomalyFlag]:
    """`entities_by_region` maps each region's index in `regions` to the
    entities extracted from it (regions don't carry a persisted id yet at
    this stage — see the module docstring).
    """
    flags = []
    for index, region in enumerate(regions):
        expected_type = _EXPECTED_ENTITY_BY_REGION_TYPE.get(region.region_type)
        if expected_type is None:
            continue
        region_entities = entities_by_region.get(index, [])
        if not any(e.entity_type == expected_type for e in region_entities):
            flags.append(
                AnomalyFlag(
                    flag_type="extraction_failure",
                    severity="medium",
                    explanation=(
                        f"a {region.region_type!r} region was detected but no {expected_type!r} "
                        f"entity was extracted from it"
                    ),
                    region_bbox=region.bbox,
                )
            )
    return flags


# --------------------------------------------------------------------------
# Convenience: run everything for one document
# --------------------------------------------------------------------------


def detect_all_anomalies(
    regions: list[Region],
    ocr_results_by_region: dict[int, OCRResult],
    entities: list[ExtractedEntity],
    entities_by_region: dict[int, list[ExtractedEntity]],
) -> list[AnomalyFlag]:
    """Run every check for one document's worth of regions/OCR/entities.

    `ocr_results_by_region` and `entities_by_region` are keyed by each
    region's index in `regions` (see detect_extraction_failures).
    """
    flags: list[AnomalyFlag] = []

    for index, region in enumerate(regions):
        ocr_result = ocr_results_by_region.get(index)
        if ocr_result is None:
            continue
        illegible_flag = detect_illegible(ocr_result, region_bbox=region.bbox)
        if illegible_flag is not None:
            flags.append(illegible_flag)
        else:
            flags.extend(detect_low_ocr_confidence(ocr_result, region_bbox=region.bbox))

    flags.extend(detect_entity_conflicts(entities))
    flags.extend(detect_extraction_failures(regions, entities_by_region))

    return flags
