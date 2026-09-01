"""Structured entity extraction from noisy OCR text: person names, dates,
locations, and cash amounts.

Base NER (person/location) comes from a spaCy pipeline — by default
`en_core_web_sm` (fast, no heavy deps); swap in `en_core_web_trf` (the
`ner-transformer` extra + NER_MODEL setting) for higher accuracy at a much
heavier install cost. Two custom spaCy pipeline components layer rule-based
parsing on top for formats spaCy's generic DATE/MONEY labels don't
normalize: historical dates ("the 3rd day of March, 1897", OCR-mangled
digits like "l897" for "1897") and currency (£/s/d ledger notation,
"X dollars and Y cents").

Every extracted entity records: type, normalized value (where derivable),
raw source text, a confidence blending the extraction method's own
confidence with the OCR confidence of the source text (if supplied), and
the char span plus an optional region bounding box (from ocr.layout) it
came from.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from typing import Literal

import spacy
from rapidfuzz import process as rf_process
from spacy.language import Language
from spacy.tokens import Doc, Span
from spacy.util import filter_spans

from config import get_settings

logger = logging.getLogger(__name__)

BBox = tuple[int, int, int, int]
EntityType = Literal["person", "date", "location", "amount"]

if not Span.has_extension("parsed"):
    Span.set_extension("parsed", default=None)


@dataclass
class ExtractedEntity:
    entity_type: EntityType
    normalized_value: str | None
    raw_text: str
    confidence: float
    start_char: int
    end_char: int
    region_bbox: BBox | None = None


# --------------------------------------------------------------------------
# OCR-error tolerance: a narrow digit-confusion table, applied only to
# substrings already matched by a digit-like character class (never applied
# to arbitrary text), so "l897" in a year position normalizes to "1897"
# without risking corruption of real words elsewhere.
# --------------------------------------------------------------------------

_OCR_DIGIT_TRANSLATION = str.maketrans(
    {"l": "1", "I": "1", "i": "1", "|": "1", "O": "0", "o": "0", "S": "5", "Z": "2", "B": "8", "g": "9", "q": "9"}
)


def _normalize_ocr_digit_run(raw: str) -> tuple[str, bool]:
    translated = raw.translate(_OCR_DIGIT_TRANSLATION)
    return translated, translated != raw


# --------------------------------------------------------------------------
# Historical date component
# --------------------------------------------------------------------------

MONTHS: dict[str, int] = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
MONTH_ABBREVS: dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_ALL_MONTH_NAMES = list(MONTHS) + list(MONTH_ABBREVS)
_MONTH_FUZZY_SCORE_CUTOFF = 75

_ORDINAL_SUFFIX = r"(?:st|nd|rd|th)?"
# Digit-like class tolerant of common OCR letter/digit confusions (spec
# example: "l8" for "18"). Restricted to this class, so the fuzzy
# normalization below only ever touches genuinely digit-shaped substrings.
_YEAR_CLASS = r"[0-9lIOSZBgqi|]{3,4}"
# Same tolerance, sized for a 1-2 digit day or numeric month rather than a
# 3-4 digit year (e.g. "1st" OCR'd as "ist" -> day="i" here, then the
# ordinal suffix "st" still matches right after; none of the OCR-confusable
# letters overlap with the ordinal suffixes' own letters, so this doesn't
# make day/ordinal parsing ambiguous).
_DAY_MONTH_DIGIT_CLASS = r"[0-9lIOSZBgqi|]{1,2}"

_DAY_MONTH_YEAR_RE = re.compile(
    rf"""\b(?:the\s+)?
    (?P<day>{_DAY_MONTH_DIGIT_CLASS}){_ORDINAL_SUFFIX}
    \s+(?:(?:day\s+)?of\s+)?
    (?P<month>[A-Za-z]{{3,9}})\.?,?\s+
    (?P<year>{_YEAR_CLASS})\b""",
    re.VERBOSE | re.IGNORECASE,
)
_MONTH_DAY_YEAR_RE = re.compile(
    rf"""\b(?P<month>[A-Za-z]{{3,9}})\.?\s+
    (?P<day>{_DAY_MONTH_DIGIT_CLASS}){_ORDINAL_SUFFIX},?\s+
    (?P<year>{_YEAR_CLASS})\b""",
    re.VERBOSE | re.IGNORECASE,
)
_NUMERIC_DATE_RE = re.compile(
    rf"\b(?P<month>{_DAY_MONTH_DIGIT_CLASS})[/-](?P<day>{_DAY_MONTH_DIGIT_CLASS})[/-](?P<year>{_YEAR_CLASS})\b"
)


def _resolve_month(raw: str) -> tuple[int | None, bool]:
    """Return (month_number, matched_exactly). Falls back to a fuzzy match
    against the canonical month list so a single OCR-misread letter
    ("Morch") doesn't lose the whole date.
    """
    key = raw.lower()
    if key in MONTHS:
        return MONTHS[key], True
    if key in MONTH_ABBREVS:
        return MONTH_ABBREVS[key], True
    match = rf_process.extractOne(key, _ALL_MONTH_NAMES, score_cutoff=_MONTH_FUZZY_SCORE_CUTOFF)
    if match is None:
        return None, False
    matched_name = match[0]
    return MONTHS.get(matched_name, MONTH_ABBREVS.get(matched_name)), False


def _parse_word_date_match(m: re.Match) -> dict | None:
    month_num, month_exact = _resolve_month(m.group("month").rstrip("."))
    if month_num is None:
        return None
    day_digits, day_was_ocr_corrected = _normalize_ocr_digit_run(m.group("day"))
    if not day_digits.isdigit():
        return None
    day = int(day_digits)
    year_digits, year_was_ocr_corrected = _normalize_ocr_digit_run(m.group("year"))
    if not year_digits.isdigit():
        return None
    year = int(year_digits)
    if not (1000 <= year <= 2100):
        return None
    try:
        iso = date(year, month_num, day).isoformat()
    except ValueError:
        return None
    confidence = 0.92
    if not month_exact:
        confidence -= 0.15
    if year_was_ocr_corrected:
        confidence -= 0.15
    if day_was_ocr_corrected:
        confidence -= 0.15
    return {"iso": iso, "confidence": max(confidence, 0.4)}


def _parse_numeric_date_match(m: re.Match) -> dict | None:
    month_digits, month_was_ocr_corrected = _normalize_ocr_digit_run(m.group("month"))
    day_digits, day_was_ocr_corrected = _normalize_ocr_digit_run(m.group("day"))
    if not month_digits.isdigit() or not day_digits.isdigit():
        return None
    month, day = int(month_digits), int(day_digits)
    year_digits, year_was_ocr_corrected = _normalize_ocr_digit_run(m.group("year"))
    if not year_digits.isdigit():
        return None
    year = int(year_digits)
    if not (1 <= month <= 12) or not (1000 <= year <= 2100):
        return None
    try:
        iso = date(year, month, day).isoformat()
    except ValueError:
        return None
    any_ocr_corrected = year_was_ocr_corrected or month_was_ocr_corrected or day_was_ocr_corrected
    return {"iso": iso, "confidence": 0.7 if any_ocr_corrected else 0.85}


@Language.component("historical_date_ruler")
def historical_date_ruler(doc: Doc) -> Doc:
    candidates: list[Span] = []
    for pattern, parser in (
        (_DAY_MONTH_YEAR_RE, _parse_word_date_match),
        (_MONTH_DAY_YEAR_RE, _parse_word_date_match),
        (_NUMERIC_DATE_RE, _parse_numeric_date_match),
    ):
        for m in pattern.finditer(doc.text):
            parsed = parser(m)
            if parsed is None:
                continue
            span = doc.char_span(m.start(), m.end(), label="HIST_DATE", alignment_mode="expand")
            if span is None:
                continue
            span._.parsed = parsed
            candidates.append(span)
    doc.ents = filter_spans(filter_spans(candidates) + list(doc.ents))
    return doc


# --------------------------------------------------------------------------
# Currency component: modern decimal amounts, spelled dollars/cents, and
# historical pre-decimal £/s/d (pounds/shillings/pence) ledger notation.
# --------------------------------------------------------------------------

_DOLLAR_RE = re.compile(r"\$\s?(?P<amount>[0-9][0-9,]*(?:\.[0-9]{1,2})?)")
_DOLLARS_CENTS_RE = re.compile(
    r"\b(?P<dollars>\d+)\s+dollars?(?:\s+and\s+(?P<cents>\d+)\s+cents?)?\b", re.IGNORECASE
)
# £ is frequently OCR-mangled to "L"; require it directly precede a digit to
# avoid matching ordinary words starting with "L". Deliberately case-sensitive
# (uppercase "L" only) — lowercase "l" is already claimed by the OCR
# digit-confusion table as a stand-in for "1" (e.g. "l897" -> "1897"), and
# treating it as a currency symbol too would make "l8" ambiguous between "18"
# and "£8". Shillings/pence unit letters (s/d) are optional so dot-separated
# ledger notation without them ("£3.12.6") still matches.
_GBP_RE = re.compile(
    r"(?:£|(?<![A-Za-z0-9])L(?=\d))\s?"
    r"(?P<pounds>[0-9lIOSZBgqi|]{1,4})"
    r"(?:[\s.,]+(?P<shillings>\d{1,2})\s?(?:[sS]\.?)?)?"
    r"(?:[\s.,]+(?P<pence>\d{1,2})\s?(?:[dD]\.?)?)?"
)
# Bare "12s 6d" with no leading £/L — common on ledger continuation lines.
_SD_ONLY_RE = re.compile(r"\b(?P<shillings>\d{1,2})\s?s\.?(?:\s+(?P<pence>\d{1,2})\s?d\.?)?\b", re.IGNORECASE)


def _parse_dollar_match(m: re.Match) -> dict | None:
    try:
        value = float(m.group("amount").replace(",", ""))
    except ValueError:
        return None
    return {"normalized": f"USD {value:.2f}", "confidence": 0.95}


def _parse_dollars_cents_match(m: re.Match) -> dict | None:
    dollars = int(m.group("dollars"))
    cents = int(m.group("cents")) if m.group("cents") else 0
    if cents > 99:
        return None
    return {"normalized": f"USD {dollars + cents / 100.0:.2f}", "confidence": 0.85}


def _parse_gbp_match(m: re.Match) -> dict | None:
    pounds_digits, pounds_was_ocr_corrected = _normalize_ocr_digit_run(m.group("pounds"))
    if not pounds_digits.isdigit():
        return None
    pounds = int(pounds_digits)
    shillings = int(m.group("shillings")) if m.group("shillings") else 0
    pence = int(m.group("pence")) if m.group("pence") else 0
    if shillings > 19 or pence > 11:
        return None
    value = pounds + shillings / 20.0 + pence / 240.0
    return {"normalized": f"GBP {value:.2f}", "confidence": 0.7 if pounds_was_ocr_corrected else 0.9}


def _parse_sd_only_match(m: re.Match) -> dict | None:
    shillings = int(m.group("shillings"))
    pence = int(m.group("pence")) if m.group("pence") else 0
    if shillings > 19 or pence > 11:
        return None
    value = shillings / 20.0 + pence / 240.0
    return {"normalized": f"GBP {value:.2f}", "confidence": 0.6}


@Language.component("currency_ruler")
def currency_ruler(doc: Doc) -> Doc:
    candidates: list[Span] = []
    for pattern, parser in (
        (_GBP_RE, _parse_gbp_match),
        (_DOLLAR_RE, _parse_dollar_match),
        (_DOLLARS_CENTS_RE, _parse_dollars_cents_match),
        (_SD_ONLY_RE, _parse_sd_only_match),
    ):
        for m in pattern.finditer(doc.text):
            parsed = parser(m)
            if parsed is None:
                continue
            span = doc.char_span(m.start(), m.end(), label="CURRENCY", alignment_mode="expand")
            if span is None:
                continue
            span._.parsed = parsed
            candidates.append(span)
    doc.ents = filter_spans(filter_spans(candidates) + list(doc.ents))
    return doc


# --------------------------------------------------------------------------
# Location gazetteer: historical place names that won't match modern
# geocoding, disambiguated with rapidfuzz so a single OCR-misread character
# doesn't drop the match entirely.
# --------------------------------------------------------------------------

HISTORICAL_PLACE_GAZETTEER: dict[str, str] = {
    "bombay": "Mumbai",
    "calcutta": "Kolkata",
    "madras": "Chennai",
    "ceylon": "Sri Lanka",
    "constantinople": "Istanbul",
    "peking": "Beijing",
    "canton": "Guangzhou",
    "siam": "Thailand",
    "persia": "Iran",
    "rhodesia": "Zimbabwe",
    "new amsterdam": "New York City",
    "saigon": "Ho Chi Minh City",
    "leningrad": "Saint Petersburg",
}
_GAZETTEER_FUZZY_SCORE_CUTOFF = 82


def _gazetteer_lookup(raw_text: str) -> tuple[str, float] | None:
    """Exact or fuzzy gazetteer match. Returns None if nothing matches —
    callers decide whether "no gazetteer hit" still counts as a location.
    """
    key = raw_text.lower().strip()
    if key in HISTORICAL_PLACE_GAZETTEER:
        return HISTORICAL_PLACE_GAZETTEER[key], 0.95

    match = rf_process.extractOne(
        key, HISTORICAL_PLACE_GAZETTEER.keys(), score_cutoff=_GAZETTEER_FUZZY_SCORE_CUTOFF
    )
    if match is not None:
        matched_key, score, _ = match
        return HISTORICAL_PLACE_GAZETTEER[matched_key], round(0.6 + (score - _GAZETTEER_FUZZY_SCORE_CUTOFF) / 100, 4)

    return None


def _scan_ungazetted_proper_nouns(
    doc: Doc, covered: list[tuple[int, int]]
) -> list[tuple[str, int, int, tuple[str, float]]]:
    """Some obscure historical place names get no NER label at all from the
    base model (not even a mislabel) — recovered here via a plain scan over
    consecutive proper-noun tokens not already claimed by an entity, checked
    only against the gazetteer. Never emits anything without a gazetteer hit,
    so this can't turn an arbitrary proper noun into a false "location".
    """
    hits: list[tuple[str, int, int, tuple[str, float]]] = []
    tokens = list(doc)
    i, n = 0, len(tokens)
    while i < n:
        if tokens[i].pos_ != "PROPN":
            i += 1
            continue
        j = i + 1
        while j < n and tokens[j].pos_ == "PROPN":
            j += 1
        span = doc[i:j]
        if not any(span.start_char < end and start < span.end_char for start, end in covered):
            hit = _gazetteer_lookup(span.text)
            if hit is not None:
                hits.append((span.text, span.start_char, span.end_char, hit))
        i = j
    return hits


def _disambiguate_place(raw_text: str) -> tuple[str, float]:
    hit = _gazetteer_lookup(raw_text)
    if hit is not None:
        return hit
    return re.sub(r"\s+", " ", raw_text.strip()).title(), 0.5


# --------------------------------------------------------------------------
# Pipeline construction & main entry point
# --------------------------------------------------------------------------


@lru_cache(maxsize=4)
def get_nlp(model_name: str | None = None) -> Language:
    """Load (and cache) the spaCy pipeline with the custom date/currency
    components layered on. Cached per model name so repeated calls in a
    long-running process (API/worker) don't reload the model every time.
    """
    name = model_name or get_settings().ner_model
    nlp = spacy.load(name)
    if "historical_date_ruler" not in nlp.pipe_names:
        nlp.add_pipe("historical_date_ruler", after="ner")
    if "currency_ruler" not in nlp.pipe_names:
        nlp.add_pipe("currency_ruler", after="historical_date_ruler")
    return nlp


def _combine_confidence(method_confidence: float, ocr_confidence: float | None) -> float:
    """Blend the extraction method's own confidence with OCR confidence for
    the source text span (OCR confidence is 0-100, as produced by
    ocr.engine.OCRResult; the combined score is 0-1).
    """
    clamped_method = min(max(method_confidence, 0.0), 1.0)
    if ocr_confidence is None:
        return round(clamped_method, 4)
    ocr_factor = min(max(ocr_confidence / 100.0, 0.0), 1.0)
    return round(clamped_method * ocr_factor, 4)


_ENTITY_TYPE_BY_LABEL: dict[str, EntityType] = {
    "PERSON": "person",
    "GPE": "location",
    "LOC": "location",
    "FAC": "location",
    "HIST_DATE": "date",
    "CURRENCY": "amount",
}
# The base NER model sometimes mislabels historical/colonial place names it
# wasn't trained on well (e.g. "Calcutta" tagged PRODUCT or ORG depending on
# sentence context). These labels are recovered as a location *only* on an
# exact/fuzzy gazetteer hit — never unconditionally, since that would
# misclassify genuine organizations/products as places.
_LOCATION_FALLBACK_LABELS = {"ORG", "PRODUCT", "WORK_OF_ART", "NORP"}


def extract_entities(
    text: str,
    *,
    ocr_confidence: float | None = None,
    region_bbox: BBox | None = None,
    nlp: Language | None = None,
) -> list[ExtractedEntity]:
    """Extract person/date/location/amount entities from OCR text.

    `ocr_confidence` (0-100, matching ocr.engine.OCRResult's scale) is the
    OCR confidence of the source text span, if known — it's blended into
    each entity's confidence. `region_bbox` (from ocr.layout.Region) is
    attached to every entity extracted from this call, so the annotation UI
    can trace an entity back to the box on the page it came from.
    """
    if not text or not text.strip():
        return []

    pipeline = nlp or get_nlp()
    doc = pipeline(text)

    entities: list[ExtractedEntity] = []
    for ent in doc.ents:
        entity_type = _ENTITY_TYPE_BY_LABEL.get(ent.label_)
        gazetteer_hit: tuple[str, float] | None = None

        if entity_type is None:
            if ent.label_ not in _LOCATION_FALLBACK_LABELS:
                continue  # not a type this module extracts (CARDINAL, ...)
            gazetteer_hit = _gazetteer_lookup(ent.text)
            if gazetteer_hit is None:
                continue
            entity_type = "location"

        if entity_type == "person":
            normalized_value = re.sub(r"\s+", " ", ent.text.strip())
            method_confidence = 0.7
        elif entity_type == "location":
            normalized_value, method_confidence = gazetteer_hit or _disambiguate_place(ent.text)
        elif entity_type in ("date", "amount"):
            parsed = ent._.parsed
            if parsed is None:
                logger.warning("custom entity %r (%s) had no parsed data; skipping", ent.text, ent.label_)
                continue
            normalized_value = parsed.get("iso") or parsed.get("normalized")
            method_confidence = parsed["confidence"]
        else:  # pragma: no cover - exhaustive given _ENTITY_TYPE_BY_LABEL
            continue

        entities.append(
            ExtractedEntity(
                entity_type=entity_type,
                normalized_value=normalized_value,
                raw_text=ent.text,
                confidence=_combine_confidence(method_confidence, ocr_confidence),
                start_char=ent.start_char,
                end_char=ent.end_char,
                region_bbox=region_bbox,
            )
        )

    covered = [(ent.start_char, ent.end_char) for ent in doc.ents]
    for raw_text, start, end, (normalized_value, method_confidence) in _scan_ungazetted_proper_nouns(
        doc, covered
    ):
        entities.append(
            ExtractedEntity(
                entity_type="location",
                normalized_value=normalized_value,
                raw_text=raw_text,
                confidence=_combine_confidence(method_confidence, ocr_confidence),
                start_char=start,
                end_char=end,
                region_bbox=region_bbox,
            )
        )

    return entities
