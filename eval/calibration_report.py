"""Markdown and JSON renderings of an EvaluationReport, for the real-scan
calibration run (`python -m eval.calibrate run`). The Markdown is meant to be
read top to bottom by a person: headline numbers, then the worst pages
with what went wrong on each, then the full tables. The JSON carries the
same data for diffing two runs or loading into a notebook.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from eval.layout_metrics import IOU_MATCH_THRESHOLD, LayoutScore
from eval.metrics import EntityPRF
from eval.report import EvaluationReport, ExampleResult

WORST_N = 5


def _micro(scores: dict[str, EntityPRF]) -> EntityPRF:
    return EntityPRF(
        "all",
        sum(s.true_positives for s in scores.values()),
        sum(s.false_positives for s in scores.values()),
        sum(s.false_negatives for s in scores.values()),
    )


def _layout_errors(r: ExampleResult) -> int:
    return r.layout.error_count if r.layout is not None else 0


def worst_pages(report: EvaluationReport, n: int = WORST_N) -> list[ExampleResult]:
    """Ranked by CER, then entity errors, then layout errors (all descending)."""
    return sorted(
        report.results,
        key=lambda r: (-r.cer, -r.entity_errors, -_layout_errors(r), r.example_id),
    )[:n]


def _source(r: ExampleResult) -> str:
    """The scan a result came from, with its page for multi-page scans."""
    return f"{r.image_name} p{r.page}" if r.page is not None else r.image_name


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _quote(text: str, limit: int = 400) -> str:
    flat = " ".join(text.split())
    if len(flat) > limit:
        flat = flat[:limit] + " …"
    return "`" + flat.replace("`", "'") + "`" if flat else "_(empty)_"


def _entity_diff(r: ExampleResult) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """(missed reference entities, spurious predicted entities), multiset-aware."""
    remaining = list(r.reference_entities)
    spurious = []
    for pair in r.predicted_entities:
        if pair in remaining:
            remaining.remove(pair)
        else:
            spurious.append(pair)
    return remaining, spurious


def _prf_table(scores: dict[str, EntityPRF], label: str) -> list[str]:
    lines = [
        f"| {label} | precision | recall | F1 | TP | FP | FN |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, s in sorted(scores.items()):
        lines.append(
            f"| {name} | {s.precision:.3f} | {s.recall:.3f} | {s.f1:.3f} | "
            f"{s.true_positives} | {s.false_positives} | {s.false_negatives} |"
        )
    return lines


def _confusion_table(layout: LayoutScore) -> list[str]:
    confusion = layout.confusion()
    ref_types = sorted({r for r, _ in confusion} | {t for t, _ in layout.missed})
    pred_types = sorted({p for _, p in confusion})
    if not ref_types:
        return ["_No labeled regions._"]
    header = "| labeled ↓ / predicted → | " + " | ".join(pred_types) + " | missed |"
    lines = [header, "|---|" + "---:|" * (len(pred_types) + 1)]
    for rt in ref_types:
        missed = sum(1 for t, _ in layout.missed if t == rt)
        cells = [str(confusion.get((rt, pt), 0)) for pt in pred_types]
        lines.append(f"| **{rt}** | " + " | ".join(cells) + f" | {missed} |")
    spurious: dict[str, int] = {}
    for t, _ in layout.spurious:
        spurious[t] = spurious.get(t, 0) + 1
    if spurious:
        lines.append(
            "| _spurious (no label)_ | "
            + " | ".join(str(spurious.get(pt, 0)) for pt in pred_types)
            + " | |"
        )
        extra = sorted(set(spurious) - set(pred_types))
        if extra:
            lines.append("")
            lines.append(
                "Spurious predictions of types never matched: "
                + ", ".join(f"{t} ×{spurious[t]}" for t in extra)
            )
    return lines


def render_markdown(
    report: EvaluationReport,
    *,
    root: Path,
    generated_at: datetime,
    pending: list[Path] | None = None,
    unlabeled_images: list[Path] | None = None,
    warnings: list[str] | None = None,
    overlays_dir: Path | None = None,
) -> str:
    pending = pending or []
    unlabeled_images = unlabeled_images or []
    warnings = warnings or []
    lines: list[str] = []
    lines.append(f"# Real-scan evaluation, {generated_at:%Y-%m-%d %H:%M}")
    lines.append("")
    lines.append(f"Drop-in folder: `{root}`")
    lines.append("")

    micro = _micro(report.entity_scores)
    layout = report.layout
    labeled_pages = sum(1 for r in report.results if r.layout is not None)

    lines.append("## Summary")
    lines.append("")
    lines.append("| measure | value |")
    lines.append("|---|---:|")
    lines.append(f"| pages scored | {len(report.results)} |")
    lines.append(f"| stubs still to fill in (status `todo`) | {len(pending)} |")
    lines.append(f"| images with no ground-truth file | {len(unlabeled_images)} |")
    lines.append(f"| mean CER (lower is better) | {report.mean_cer:.4f} |")
    lines.append(f"| mean WER (lower is better) | {report.mean_wer:.4f} |")
    lines.append(
        f"| entities, all types: precision / recall / F1 | "
        f"{micro.precision:.3f} / {micro.recall:.3f} / {micro.f1:.3f} |"
    )
    if layout is not None:
        lines.append(f"| pages with region labels | {labeled_pages} |")
        lines.append(
            f"| layout detection: precision / recall (IoU ≥ {IOU_MATCH_THRESHOLD}) | "
            f"{layout.detection_precision:.3f} / {layout.detection_recall:.3f} |"
        )
        lines.append(
            f"| layout classification accuracy (matched regions) | {layout.classification_accuracy:.3f} |"
        )
    else:
        lines.append("| layout | no pages have region labels yet |")
    lines.append("")

    if warnings:
        lines.append("## Warnings")
        lines.append("")
        lines.extend(f"- {w}" for w in warnings)
        lines.append("")

    worst = worst_pages(report)
    if worst:
        lines.append(f"## Worst {len(worst)} page(s)")
        lines.append("")
        lines.append("Ranked by CER, then entity errors, then layout errors.")
        lines.append("")
        for r in worst:
            lines.append(f"### {r.example_id} (`{_source(r)}`)")
            lines.append("")
            quality = f"{r.quality_score:.3f}" if r.quality_score is not None else "n/a"
            lines.append(
                f"CER {r.cer:.3f}, WER {r.wer:.3f}, entity errors {r.entity_errors}, "
                f"layout errors {_layout_errors(r) if r.layout is not None else 'n/a'}, scan quality {quality}"
            )
            lines.append("")
            lines.append(f"- reference: {_quote(r.reference_text)}")
            lines.append(f"- predicted: {_quote(r.predicted_text)}")
            missed, spurious = _entity_diff(r)
            if missed:
                lines.append("- missed entities: " + ", ".join(f"{t} `{v}`" for t, v in missed))
            if spurious:
                lines.append("- spurious entities: " + ", ".join(f"{t} `{v}`" for t, v in spurious))
            if r.layout is not None:
                for m in r.layout.mislabeled:
                    lines.append(
                        f"- region labeled **{m.reference_type}** was predicted **{m.predicted_type}** "
                        f"(IoU {m.iou:.2f})"
                    )
                for t, box in r.layout.missed:
                    lines.append(f"- labeled {t} region at {list(box)} was not found")
                for t, box in r.layout.spurious:
                    lines.append(f"- predicted {t} region at {list(box)} matches no label")
            lines.append("")

    lines.append("## Entities by type")
    lines.append("")
    lines.append("Micro-averaged across all scored pages; exact match on normalized value.")
    lines.append("")
    if report.entity_scores:
        lines.extend(_prf_table(report.entity_scores, "type"))
    else:
        lines.append("_No entities in ground truth or predictions._")
    lines.append("")

    lines.append("## Layout regions")
    lines.append("")
    if layout is None:
        lines.append(
            'No scored page has a `"regions"` list, so layout was not scored. '
            "See eval/real/README.md for how to label regions."
        )
    else:
        lines.append(
            f"A predicted box matches a labeled box at IoU ≥ {IOU_MATCH_THRESHOLD} (one-to-one, "
            "best overlap first). Per-type scores count a region as correct only when it was "
            "found *and* given the right type."
        )
        lines.append("")
        lines.extend(_prf_table(layout.per_type(), "region type"))
        lines.append("")
        lines.append("Confusion matrix (matched regions, plus misses and spurious boxes):")
        lines.append("")
        lines.extend(_confusion_table(layout))
    lines.append("")

    lines.append("## All pages")
    lines.append("")
    lines.append(
        "| page | image | quality | CER | WER | entity errors | regions predicted / labeled | layout errors |"
    )
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for r in sorted(report.results, key=lambda r: r.example_id):
        quality = f"{r.quality_score:.3f}" if r.quality_score is not None else ""
        labeled = str(r.layout.reference_count) if r.layout is not None else "–"
        layout_err = str(r.layout.error_count) if r.layout is not None else "–"
        lines.append(
            f"| {_cell(r.example_id)} | {_cell(_source(r))} | {quality} | {r.cer:.3f} | {r.wer:.3f} | "
            f"{r.entity_errors} | {len(r.predicted_regions)} / {labeled} | {layout_err} |"
        )
    lines.append("")

    if pending or unlabeled_images:
        lines.append("## Not scored yet")
        lines.append("")
        for p in pending:
            lines.append(f'- `{p.name}`: status is still `"todo"`')
        for p in unlabeled_images:
            lines.append(
                f"- `{p.name}`: no ground-truth file; run `python -m eval.calibrate stubs`"
            )
        lines.append("")

    if overlays_dir is not None:
        lines.append("## Overlays")
        lines.append("")
        lines.append(
            f"`{overlays_dir}` has one image per page: predicted regions in red with their type, "
            "labeled regions in green."
        )
        lines.append("")

    return "\n".join(lines)


def _prf_json(scores: dict[str, EntityPRF]) -> dict[str, Any]:
    return {
        name: {
            "precision": s.precision,
            "recall": s.recall,
            "f1": s.f1,
            "tp": s.true_positives,
            "fp": s.false_positives,
            "fn": s.false_negatives,
        }
        for name, s in sorted(scores.items())
    }


def _layout_json(layout: LayoutScore) -> dict[str, Any]:
    return {
        "iou_threshold": IOU_MATCH_THRESHOLD,
        "detection_precision": layout.detection_precision,
        "detection_recall": layout.detection_recall,
        "classification_accuracy": layout.classification_accuracy,
        "per_type": _prf_json(layout.per_type()),
        "confusion": [
            {"labeled": rt, "predicted": pt, "count": n}
            for (rt, pt), n in sorted(layout.confusion().items())
        ],
        "missed": [{"type": t, "bbox": list(b)} for t, b in layout.missed],
        "spurious": [{"type": t, "bbox": list(b)} for t, b in layout.spurious],
    }


def render_json(
    report: EvaluationReport,
    *,
    root: Path,
    generated_at: datetime,
    pending: list[Path] | None = None,
    unlabeled_images: list[Path] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    micro = _micro(report.entity_scores)
    layout = report.layout
    pages = []
    for r in sorted(report.results, key=lambda r: r.example_id):
        missed, spurious = _entity_diff(r)
        pages.append(
            {
                "id": r.example_id,
                "image": r.image_name,
                "page": r.page,
                "quality_score": r.quality_score,
                "page_size": list(r.page_size) if r.page_size else None,
                "cer": r.cer,
                "wer": r.wer,
                "reference_text": r.reference_text,
                "predicted_text": r.predicted_text,
                "entity_errors": r.entity_errors,
                "missed_entities": [{"type": t, "value": v} for t, v in missed],
                "spurious_entities": [{"type": t, "value": v} for t, v in spurious],
                "predicted_regions": [{"type": t, "bbox": list(b)} for t, b in r.predicted_regions],
                "layout": _layout_json(r.layout) if r.layout is not None else None,
            }
        )
    return {
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "root": str(root),
        "summary": {
            "pages_scored": len(report.results),
            "pending_stubs": len(pending or []),
            "images_without_ground_truth": len(unlabeled_images or []),
            "mean_cer": report.mean_cer,
            "mean_wer": report.mean_wer,
            "entities_all_types": _prf_json({"all": micro})["all"],
            "worst_pages": [r.example_id for r in worst_pages(report)],
        },
        "warnings": warnings or [],
        "entities": _prf_json(report.entity_scores),
        "layout": _layout_json(layout) if layout is not None else None,
        "pages": pages,
        "pending": [p.name for p in pending or []],
        "unlabeled_images": [p.name for p in unlabeled_images or []],
    }
