"""Layout-segmentation metrics: how well `ocr.layout.detect_regions` finds
and classifies regions, scored against hand-labeled region boxes.

Two questions, answered separately because they fail for different reasons
and are tuned by different thresholds in `ocr/layout.py`:

1. **Detection** — did the segmenter find a box where a labeled region is?
   A predicted box and a labeled box match when their IoU (intersection over
   union) is at least `IOU_MATCH_THRESHOLD`. Matching is greedy by highest
   IoU first and one-to-one, so a single big predicted blob can't "match"
   three labeled paragraphs it swallowed (only one of them; the other two
   are misses, which is exactly the merge error worth seeing).
2. **Classification** — of the boxes that matched, was the type right?
   Reported as a confusion matrix (labeled type -> predicted type), plus
   per-type precision/recall/F1 where a labeled region only counts as a
   true positive when it was both found *and* typed correctly.

Boxes are `(x, y, width, height)` in the coordinates of the *preprocessed*
page image (the one `detect_regions` actually sees — deskew and auto-crop
can shift coordinates relative to the raw scan). See eval/real/README.md.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from eval.metrics import EntityPRF

BBox = tuple[int, int, int, int]

IOU_MATCH_THRESHOLD = 0.5


def iou(a: BBox, b: BBox) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    intersection = ix * iy
    union = aw * ah + bw * bh - intersection
    return intersection / union if union > 0 else 0.0


@dataclass
class RegionMatch:
    reference_index: int
    predicted_index: int
    iou: float
    reference_type: str
    predicted_type: str


@dataclass
class LayoutScore:
    """Layout result for one page, or (via `combine`) for a whole set."""

    matches: list[RegionMatch] = field(default_factory=list)
    # (type, bbox) of labeled regions nothing was matched to
    missed: list[tuple[str, BBox]] = field(default_factory=list)
    # (type, bbox) of predicted regions that matched no labeled region
    spurious: list[tuple[str, BBox]] = field(default_factory=list)

    @property
    def reference_count(self) -> int:
        return len(self.matches) + len(self.missed)

    @property
    def predicted_count(self) -> int:
        return len(self.matches) + len(self.spurious)

    @property
    def detection_precision(self) -> float:
        return len(self.matches) / self.predicted_count if self.predicted_count else 1.0

    @property
    def detection_recall(self) -> float:
        return len(self.matches) / self.reference_count if self.reference_count else 1.0

    @property
    def mislabeled(self) -> list[RegionMatch]:
        return [m for m in self.matches if m.reference_type != m.predicted_type]

    @property
    def classification_accuracy(self) -> float:
        """Share of *matched* regions whose type is right."""
        if not self.matches:
            return 1.0
        return (len(self.matches) - len(self.mislabeled)) / len(self.matches)

    @property
    def error_count(self) -> int:
        return len(self.missed) + len(self.spurious) + len(self.mislabeled)

    def confusion(self) -> dict[tuple[str, str], int]:
        """(reference_type, predicted_type) -> count, over matched regions only."""
        counts: dict[tuple[str, str], int] = {}
        for m in self.matches:
            key = (m.reference_type, m.predicted_type)
            counts[key] = counts.get(key, 0) + 1
        return counts

    def per_type(self) -> dict[str, EntityPRF]:
        """Per-type P/R/F1 where TP = found *and* typed correctly.

        A labeled region that was missed, or found but given another type,
        is a false negative for its labeled type; a spurious or mistyped
        prediction is a false positive for its predicted type.
        """
        tp: dict[str, int] = {}
        fp: dict[str, int] = {}
        fn: dict[str, int] = {}
        for m in self.matches:
            if m.reference_type == m.predicted_type:
                tp[m.reference_type] = tp.get(m.reference_type, 0) + 1
            else:
                fn[m.reference_type] = fn.get(m.reference_type, 0) + 1
                fp[m.predicted_type] = fp.get(m.predicted_type, 0) + 1
        for region_type, _ in self.missed:
            fn[region_type] = fn.get(region_type, 0) + 1
        for region_type, _ in self.spurious:
            fp[region_type] = fp.get(region_type, 0) + 1
        types = sorted(set(tp) | set(fp) | set(fn))
        return {t: EntityPRF(t, tp.get(t, 0), fp.get(t, 0), fn.get(t, 0)) for t in types}

    @staticmethod
    def combine(scores: Sequence[LayoutScore]) -> LayoutScore:
        combined = LayoutScore()
        for s in scores:
            combined.matches.extend(s.matches)
            combined.missed.extend(s.missed)
            combined.spurious.extend(s.spurious)
        return combined


def score_layout(
    predicted: Sequence[tuple[str, BBox]],
    reference: Sequence[tuple[str, BBox]],
    *,
    iou_threshold: float = IOU_MATCH_THRESHOLD,
) -> LayoutScore:
    """predicted/reference: (region_type, bbox) pairs for one page."""
    candidates = []
    for ri, (_, rbox) in enumerate(reference):
        for pi, (_, pbox) in enumerate(predicted):
            overlap = iou(rbox, pbox)
            if overlap >= iou_threshold:
                candidates.append((overlap, ri, pi))
    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))

    used_ref: set[int] = set()
    used_pred: set[int] = set()
    score = LayoutScore()
    for overlap, ri, pi in candidates:
        if ri in used_ref or pi in used_pred:
            continue
        used_ref.add(ri)
        used_pred.add(pi)
        score.matches.append(
            RegionMatch(
                reference_index=ri,
                predicted_index=pi,
                iou=overlap,
                reference_type=reference[ri][0],
                predicted_type=predicted[pi][0],
            )
        )
    score.matches.sort(key=lambda m: m.reference_index)
    score.missed = [reference[i] for i in range(len(reference)) if i not in used_ref]
    score.spurious = [predicted[i] for i in range(len(predicted)) if i not in used_pred]
    return score
