"""Runs OCR + entity extraction (ocr.preprocess, ocr.layout, ocr.engine,
extraction.entities) against a checked-in ground-truth set and reports
CER/WER plus per-entity-type precision/recall/F1. Deliberately decoupled
from the DB/Celery pipeline (pipeline.run, storage.*, worker.py) — this is a
standalone quality measurement, not a production code path, and shouldn't
need Postgres/Redis to run.

Usage:
    python -m eval.report                  # eval/fixtures
    python -m eval.report path/to/fixtures # a different fixture set
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import cv2

from eval.ground_truth import GroundTruthExample, load_ground_truth
from eval.layout_metrics import BBox, LayoutScore, score_layout
from eval.metrics import EntityPRF, character_error_rate, score_entities, word_error_rate
from extraction.entities import extract_entities
from ocr.engine import OCRRouter, TesseractBackend, TrOCRBackend
from ocr.layout import detect_regions
from ocr.preprocess import preprocess

DEFAULT_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

_router: OCRRouter | None = None


def _get_router() -> OCRRouter:
    global _router
    if _router is None:
        _router = OCRRouter(typed_backend=TesseractBackend(), handwriting_backend=TrOCRBackend())
    return _router


@dataclass
class ExampleResult:
    example_id: str
    predicted_text: str
    reference_text: str
    cer: float
    wer: float
    predicted_entities: list[tuple[str, str]]
    reference_entities: list[tuple[str, str]]
    # (region_type, bbox) as detect_regions returned them, in reading order
    predicted_regions: list[tuple[str, BBox]] = field(default_factory=list)
    # None when the ground truth has no region labels for this page
    layout: LayoutScore | None = None
    quality_score: float | None = None
    # (width, height) of the preprocessed page detect_regions ran on
    page_size: tuple[int, int] | None = None
    image_name: str = ""

    @property
    def entity_scores(self) -> dict[str, EntityPRF]:
        return score_entities(self.predicted_entities, self.reference_entities)

    @property
    def entity_errors(self) -> int:
        return sum(s.false_positives + s.false_negatives for s in self.entity_scores.values())


@dataclass
class EvaluationReport:
    results: list[ExampleResult]
    entity_scores: dict[str, EntityPRF]

    @property
    def layout(self) -> LayoutScore | None:
        """Combined layout score over the pages that have region labels."""
        scored = [r.layout for r in self.results if r.layout is not None]
        return LayoutScore.combine(scored) if scored else None

    @property
    def mean_cer(self) -> float:
        return sum(r.cer for r in self.results) / len(self.results) if self.results else 0.0

    @property
    def mean_wer(self) -> float:
        return sum(r.wer for r in self.results) / len(self.results) if self.results else 0.0


def _run_example(example: GroundTruthExample) -> ExampleResult:
    image = cv2.imread(str(example.image_path))
    if image is None:
        raise FileNotFoundError(f"could not read fixture image: {example.image_path}")

    preprocessed = preprocess(image)
    regions = detect_regions(preprocessed.image)
    page_h, page_w = preprocessed.image.shape[:2]
    router = _get_router()

    texts: list[str] = []
    predicted_entities: list[tuple[str, str]] = []
    for region in regions:
        x, y, w, h = region.bbox
        crop = preprocessed.image[y : y + h, x : x + w]
        result = router.run(crop)
        if result.text.strip():
            texts.append(result.text)
        for entity in extract_entities(
            result.text, ocr_confidence=result.document_confidence, region_bbox=region.bbox
        ):
            if entity.normalized_value:
                predicted_entities.append((entity.entity_type, entity.normalized_value))

    predicted_text = "\n".join(texts)
    reference_entities = [(e.entity_type, e.value) for e in example.entities]
    predicted_regions: list[tuple[str, BBox]] = [(r.region_type, r.bbox) for r in regions]
    layout = None
    if example.regions is not None:
        layout = score_layout(predicted_regions, [(r.region_type, r.bbox) for r in example.regions])

    return ExampleResult(
        example_id=example.id,
        predicted_text=predicted_text,
        reference_text=example.text,
        cer=character_error_rate(predicted_text, example.text),
        wer=word_error_rate(predicted_text, example.text),
        predicted_entities=predicted_entities,
        reference_entities=reference_entities,
        predicted_regions=predicted_regions,
        layout=layout,
        quality_score=preprocessed.quality_score,
        page_size=(page_w, page_h),
        image_name=example.image_path.name,
    )


def run_evaluation(fixtures_dir: Path = DEFAULT_FIXTURES_DIR) -> EvaluationReport:
    return evaluate_examples(load_ground_truth(fixtures_dir))


def evaluate_examples(examples: Sequence[GroundTruthExample]) -> EvaluationReport:
    results = [_run_example(example) for example in examples]

    all_predicted = [pair for r in results for pair in r.predicted_entities]
    all_reference = [pair for r in results for pair in r.reference_entities]
    entity_scores = score_entities(all_predicted, all_reference)

    return EvaluationReport(results=results, entity_scores=entity_scores)


def format_report(report: EvaluationReport, *, worst_n: int = 3) -> str:
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("Pipeline Evaluation Report")
    lines.append("=" * 72)
    lines.append("")
    lines.append(f"Examples evaluated: {len(report.results)}")
    lines.append(f"Mean CER: {report.mean_cer:.4f}")
    lines.append(f"Mean WER: {report.mean_wer:.4f}")
    lines.append("")

    lines.append("Entity extraction (micro-averaged across all examples):")
    header = f"{'type':<12}{'precision':>10}{'recall':>10}{'f1':>10}{'tp':>6}{'fp':>6}{'fn':>6}"
    lines.append(header)
    lines.append("-" * len(header))
    for entity_type, prf in sorted(report.entity_scores.items()):
        lines.append(
            f"{entity_type:<12}{prf.precision:>10.3f}{prf.recall:>10.3f}{prf.f1:>10.3f}"
            f"{prf.true_positives:>6}{prf.false_positives:>6}{prf.false_negatives:>6}"
        )
    lines.append("")

    lines.append("Per-example results:")
    header2 = f"{'id':<28}{'cer':>8}{'wer':>8}"
    lines.append(header2)
    lines.append("-" * len(header2))
    for r in sorted(report.results, key=lambda r: r.example_id):
        lines.append(f"{r.example_id:<28}{r.cer:>8.3f}{r.wer:>8.3f}")
    lines.append("")

    worst = sorted(report.results, key=lambda r: r.cer, reverse=True)[:worst_n]
    if worst:
        lines.append(f"Worst {len(worst)} example(s) by CER:")
        for r in worst:
            lines.append(f"  [{r.example_id}] cer={r.cer:.3f} wer={r.wer:.3f}")
            lines.append(f"    reference : {r.reference_text!r}")
            lines.append(f"    predicted : {r.predicted_text!r}")
        lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    fixtures_dir = Path(argv[0]) if argv else DEFAULT_FIXTURES_DIR

    report = run_evaluation(fixtures_dir)
    print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
