"""OCR (CER/WER) and entity-extraction (precision/recall/F1) metrics.

CER/WER use rapidfuzz's Levenshtein distance (already a project dependency
for fuzzy entity matching) — it operates on any sequence of hashable
objects, not just strings, so the same function serves both the
character-level (CER) and word-level (WER) case by passing characters vs.
word tokens.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz.distance import Levenshtein

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Collapses whitespace runs (including newlines) to a single space.

    Ground-truth text is authored as one logical line per example; OCR
    output is naturally split across regions/lines. Normalizing both before
    comparison measures recognition accuracy, not paragraph-joining
    conventions.
    """
    return _WHITESPACE_RE.sub(" ", text.strip())


def character_error_rate(predicted: str, reference: str) -> float:
    """Levenshtein edit distance at the character level, divided by
    reference length. An empty reference is a degenerate case (nothing to
    measure recognition against) — scored 0.0 if the prediction is also
    empty, else 1.0 (predicting text that should not exist is a full miss).
    """
    ref = normalize_text(reference)
    pred = normalize_text(predicted)
    if not ref:
        return 0.0 if not pred else 1.0
    return Levenshtein.distance(pred, ref) / len(ref)


def word_error_rate(predicted: str, reference: str) -> float:
    """Same as character_error_rate, but over word tokens."""
    ref_words = normalize_text(reference).split()
    pred_words = normalize_text(predicted).split()
    if not ref_words:
        return 0.0 if not pred_words else 1.0
    return Levenshtein.distance(pred_words, ref_words) / len(ref_words)


@dataclass
class EntityPRF:
    entity_type: str
    true_positives: int
    false_positives: int
    false_negatives: int

    @property
    def precision(self) -> float:
        denom = self.true_positives + self.false_positives
        return self.true_positives / denom if denom else 1.0

    @property
    def recall(self) -> float:
        denom = self.true_positives + self.false_negatives
        return self.true_positives / denom if denom else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


def score_entities(
    predicted: list[tuple[str, str]], reference: list[tuple[str, str]]
) -> dict[str, EntityPRF]:
    """predicted/reference: (entity_type, normalized_value) pairs.

    Matching is exact (type, value) equality, multiset-aware: two identical
    predicted entities can each match one of two identical reference
    entities, but a single reference entity is never counted as matched
    twice.
    """
    types = {t for t, _ in predicted} | {t for t, _ in reference}
    scores: dict[str, EntityPRF] = {}
    for entity_type in sorted(types):
        pred_values = [v for t, v in predicted if t == entity_type]
        ref_remaining = [v for t, v in reference if t == entity_type]

        true_positives = 0
        for value in pred_values:
            if value in ref_remaining:
                ref_remaining.remove(value)
                true_positives += 1

        false_positives = len(pred_values) - true_positives
        false_negatives = len(ref_remaining)
        scores[entity_type] = EntityPRF(entity_type, true_positives, false_positives, false_negatives)
    return scores
