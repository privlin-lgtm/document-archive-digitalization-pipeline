"""Calibrate on real scans: a drop-in folder plus one command.

    python -m eval.calibrate init            # create eval/real/{images,ground_truth,label-pages,reports}
    python -m eval.calibrate stubs           # blank ground-truth file for every new page
    python -m eval.calibrate run             # score everything marked "done", write a dated report
    python -m eval.calibrate seed-synthetic  # copy the synthetic fixtures in, to check the plumbing

Every subcommand takes `--root DIR` (default: $EVAL_REAL_DIR, else
eval/real next to this file). The folder layout, ground-truth format and
labeling workflow are documented in eval/real/README.md. Everything in
eval/real/ except that README is gitignored and dockerignored: real client
scans and anything derived from them (label pages, overlays, reports) must
never be committed or baked into an image.

Scans can be single images, multi-page PDFs or multi-frame TIFFs; pages
are split with ocr.pages (see eval/page_reader.py) and each page gets its
own ground-truth file, report row and overlay.

Like eval.report, this is a standalone measurement tool: it calls
ocr.preprocess, ocr.layout, ocr.engine and extraction.entities directly
and needs no Postgres or Redis.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from eval.ground_truth import (
    STATUS_DONE,
    STATUS_TODO,
    GroundTruthError,
    GroundTruthExample,
    is_multi_page,
    list_images,
    load_drop_in,
    load_ground_truth,
    scan_page_count,
)
from eval.page_reader import PageReader

EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_ROOT = EVAL_DIR / "real"
SUBDIRS = ("images", "ground_truth", "label-pages", "reports")

# extraction.entities reads config.Settings, which insists on DATABASE_URL and
# REVIEW_API_TOKEN even though the eval never touches the database or the API.
# Placeholders let the harness run with no .env at all; real values (from the
# environment) always win. Neither is used for anything here.
_SETTINGS_PLACEHOLDERS = {
    "DATABASE_URL": "postgresql+psycopg://eval-unused@localhost:5432/eval_unused",
    "REVIEW_API_TOKEN": "eval-harness-placeholder-not-used-for-anything",
}


def _ensure_settings_env() -> None:
    for key, value in _SETTINGS_PLACEHOLDERS.items():
        os.environ.setdefault(key, value)


def _max_pages() -> int:
    """MAX_PAGES_PER_DOCUMENT, the same page limit uploads have."""
    from config import get_settings

    _ensure_settings_env()
    return get_settings().max_pages_per_document


def default_root() -> Path:
    env = os.environ.get("EVAL_REAL_DIR")
    return Path(env) if env else DEFAULT_ROOT


def slugify(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()
    return slug or "scan"


def init_layout(root: Path) -> list[Path]:
    """Creates the drop-in subfolders. Returns the ones that were new."""
    created = []
    for sub in SUBDIRS:
        path = root / sub
        if not path.is_dir():
            path.mkdir(parents=True, exist_ok=True)
            created.append(path)
    return created


def _preprocessed(image: np.ndarray) -> np.ndarray:
    from ocr.preprocess import preprocess

    return preprocess(image).image


def stub_entry(image_name: str, example_id: str, page: int | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": example_id, "image": image_name}
    if page is not None:
        entry["page"] = page
    return entry | {
        "status": STATUS_TODO,
        "text": "",
        "entities": [],
        "regions": None,
        "notes": "",
    }


def create_stubs(
    root: Path,
    *,
    prefill_regions: bool = False,
    max_pages: int | None = None,
    skipped: list[str] | None = None,
) -> list[Path]:
    """Writes ground_truth/<id>.json for every image (or page) that has none yet.

    A single image gets one stub, ground_truth/<slug>.json, as always. A PDF
    or multi-frame TIFF gets one stub per page, ground_truth/<slug>.p<N>.json
    with id "<slug>.p<N>" and "page": N. A scan with more pages than
    `max_pages` (default MAX_PAGES_PER_DOCUMENT), or one that can't be read,
    gets no stubs; the reason is appended to `skipped` when given.

    Never overwrites an existing ground-truth file. Also writes
    label-pages/<id>.png, the preprocessed page that region boxes are
    measured against, and records its size in the stub. With
    `prefill_regions`, the stub's "regions" list starts as whatever
    detect_regions finds today, so labeling is correcting boxes and types
    rather than drawing every one from scratch.
    """
    init_layout(root)
    gt_dir = root / "ground_truth"
    # (image name, page) already covered; page 1 for single-image entries
    referenced: set[tuple[str, int]] = set()
    taken_ids: set[str] = set()
    for path in gt_dir.glob("*.json"):
        taken_ids.add(path.stem)
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            if isinstance(entry.get("image"), str):
                page = entry.get("page")
                referenced.add((entry["image"], page if isinstance(page, int) else 1))
            if isinstance(entry.get("id"), str):
                taken_ids.add(entry["id"])

    def unique_id(base: str) -> str:
        example_id, n = base, 2
        while example_id in taken_ids:
            example_id = f"{base}-{n}"
            n += 1
        taken_ids.add(example_id)
        return example_id

    def write_stub(image_name: str, example_id: str, image: np.ndarray, page: int | None) -> None:
        entry = stub_entry(image_name, example_id, page)
        label_page = _preprocessed(image)
        cv2.imwrite(str(root / "label-pages" / f"{example_id}.png"), label_page)
        entry["page_size"] = [int(label_page.shape[1]), int(label_page.shape[0])]
        if prefill_regions:
            from ocr.layout import detect_regions

            entry["regions"] = [
                {"type": r.region_type, "bbox": list(r.bbox)}
                for r in detect_regions(label_page)
            ]
        out = gt_dir / f"{example_id}.json"
        out.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written.append(out)

    written: list[Path] = []
    with PageReader() as reader:
        for image_path in list_images(root / "images"):
            try:
                multi = is_multi_page(image_path)
                count = scan_page_count(image_path) if multi else 1
            except (OSError, RuntimeError, ValueError) as exc:  # corrupt PDF/TIFF: skip it
                _skip(skipped, f"{image_path.name}: could not read it ({exc.__class__.__name__}: {exc})")
                continue

            if not multi:
                if (image_path.name, 1) not in referenced:
                    image = reader.read(image_path, None)
                    write_stub(image_path.name, unique_id(slugify(image_path.stem)), image, None)
                continue

            limit = max_pages if max_pages is not None else _max_pages()
            if count > limit:
                _skip(
                    skipped,
                    f"{image_path.name}: {count} pages, more than the {limit}-page limit "
                    "(MAX_PAGES_PER_DOCUMENT); split it into smaller files",
                )
                continue
            if count == 0:
                _skip(skipped, f"{image_path.name}: has no pages")
                continue
            base = slugify(image_path.stem)
            for page in range(1, count + 1):
                if (image_path.name, page) in referenced:
                    continue
                image = reader.read(image_path, page)
                write_stub(image_path.name, unique_id(f"{base}.p{page}"), image, page)
    return written


def _skip(skipped: list[str] | None, reason: str) -> None:
    if skipped is not None:
        skipped.append(reason)


def seed_synthetic(root: Path, fixtures_dir: Path = EVAL_DIR / "fixtures") -> list[Path]:
    """Copies the checked-in synthetic fixtures into the drop-in layout, as if
    they were real scans that had been fully labeled. Region labels are
    whatever detect_regions finds today (the fixtures have none of their
    own), so layout scores here prove the wiring, not the segmenter.
    """
    from ocr.layout import detect_regions

    init_layout(root)
    written = []
    for example in load_ground_truth(fixtures_dir):
        target_image = root / "images" / example.image_path.name
        shutil.copyfile(example.image_path, target_image)
        page = _preprocessed(PageReader().read(target_image, None))
        cv2.imwrite(str(root / "label-pages" / f"{example.id}.png"), page)
        entry = stub_entry(example.image_path.name, example.id)
        entry.update(
            status=STATUS_DONE,
            text=example.text,
            entities=[{"type": e.entity_type, "value": e.value} for e in example.entities],
            regions=[{"type": r.region_type, "bbox": list(r.bbox)} for r in detect_regions(page)],
            page_size=[int(page.shape[1]), int(page.shape[0])],
            notes="synthetic fixture copied by `python -m eval.calibrate seed-synthetic`",
        )
        out = root / "ground_truth" / f"{example.id}.json"
        out.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written.append(out)
    return written


def _draw_overlay(
    example: GroundTruthExample,
    predicted: list[tuple[str, tuple[int, int, int, int]]],
    out: Path,
    reader: PageReader,
) -> None:
    page = _preprocessed(reader.read(example.image_path, example.page))
    canvas = cv2.cvtColor(page, cv2.COLOR_GRAY2BGR) if page.ndim == 2 else page.copy()
    for region in example.regions or []:
        x, y, w, h = region.bbox
        cv2.rectangle(canvas, (x, y), (x + w, y + h), (0, 160, 0), 3)
        cv2.putText(
            canvas,
            region.region_type,
            (x, y + h + 18),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 160, 0),
            2,
        )
    for region_type, (x, y, w, h) in predicted:
        cv2.rectangle(canvas, (x, y), (x + w, y + h), (0, 0, 220), 2)
        cv2.putText(
            canvas, region_type, (x, max(12, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 220), 2
        )
    cv2.imwrite(str(out), canvas)


def run(
    root: Path,
    *,
    overlays: bool = True,
    now: datetime | None = None,
    max_pages: int | None = None,
) -> Path:
    """Scores every "done" page and writes reports/<timestamp>/report.{md,json}.

    Pages of PDFs and multi-frame TIFFs are scored one by one, exactly like
    single images; a stub whose "page" doesn't exist in its scan, or a scan
    over `max_pages` (default MAX_PAGES_PER_DOCUMENT), is a GroundTruthError.

    Returns the report directory. Raises GroundTruthError for malformed
    ground truth and LookupError when nothing is ready to score.
    """
    from eval.calibration_report import render_json, render_markdown
    from eval.report import evaluate_examples

    _ensure_settings_env()
    limit = max_pages if max_pages is not None else _max_pages()
    drop_in = load_drop_in(root, max_pages=limit)
    if not drop_in.examples:
        raise LookupError(
            f"nothing to score in {root}: "
            f'{len(drop_in.pending)} stub(s) still have status "todo", '
            f"{len(drop_in.unlabeled_images)} image(s) have no ground-truth file. "
            'Fill in a stub and set "status": "done", then run again.'
        )

    # Sorted by (file, page) so each multi-page scan is rendered in one pass.
    examples = sorted(drop_in.examples, key=_page_order)
    with PageReader() as reader:
        report = evaluate_examples(
            examples, read_image=lambda e: reader.read(e.image_path, e.page)
        )

    warnings = []
    by_id = {e.id: e for e in drop_in.examples}
    for result in report.results:
        example = by_id[result.example_id]
        expected, actual = example.page_size, result.page_size
        if example.regions is not None and expected and actual and actual != expected:
            warnings.append(
                f"{result.example_id}: regions were labeled on a {expected[0]}x{expected[1]} page but "
                f"preprocessing now produces {actual[0]}x{actual[1]} (preprocessing changed?), so "
                "layout scores for this page may be off. Re-check its boxes against a fresh label page."
            )

    generated_at = now or datetime.now().astimezone()
    report_dir = root / "reports" / generated_at.strftime("%Y-%m-%d_%H%M%S")
    report_dir.mkdir(parents=True, exist_ok=False)
    overlays_dir = None
    if overlays:
        overlays_dir = report_dir / "overlays"
        overlays_dir.mkdir()
        with PageReader() as reader:
            for result in report.results:
                _draw_overlay(
                    by_id[result.example_id],
                    result.predicted_regions,
                    overlays_dir / f"{result.example_id}.png",
                    reader,
                )

    context = {
        "root": root,
        "generated_at": generated_at,
        "pending": drop_in.pending,
        "unlabeled_images": drop_in.unlabeled_images,
        "warnings": warnings,
    }
    markdown = render_markdown(report, overlays_dir=overlays_dir, **context)  # type: ignore[arg-type]
    (report_dir / "report.md").write_text(markdown + "\n", encoding="utf-8")
    data = render_json(report, **context)  # type: ignore[arg-type]
    (report_dir / "report.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return report_dir


def _page_order(example: GroundTruthExample) -> tuple[str, int, str]:
    return (str(example.image_path), example.page or 0, example.id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.calibrate", description=__doc__.split("\n\n")[0]
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_root(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--root", type=Path, default=None, help="drop-in folder (default: eval/real)"
        )

    add_root(sub.add_parser("init", help="create the drop-in folder layout"))
    p_stubs = sub.add_parser("stubs", help="write a blank ground-truth file for each new scan")
    add_root(p_stubs)
    p_stubs.add_argument(
        "--prefill-regions",
        action="store_true",
        help="start each stub's regions from what the segmenter finds today",
    )
    p_run = sub.add_parser("run", help="score labeled scans and write a dated report")
    add_root(p_run)
    p_run.add_argument(
        "--no-overlays", action="store_true", help="skip the per-page overlay images"
    )
    add_root(
        sub.add_parser("seed-synthetic", help="copy the synthetic fixtures in as labeled scans")
    )

    args = parser.parse_args(argv)
    root: Path = (args.root or default_root()).resolve()

    if args.command == "init":
        created = init_layout(root)
        print(f"Drop-in folder ready: {root}")
        for path in created:
            print(f"  created {path.relative_to(root)}/")
        print("Next: put scans in images/, then run `python -m eval.calibrate stubs`.")
        return 0

    if args.command == "stubs":
        skipped: list[str] = []
        written = create_stubs(root, prefill_regions=args.prefill_regions, skipped=skipped)
        if not written and not skipped:
            print(f"Every image and page in {root / 'images'} already has a ground-truth file.")
        for path in written:
            print(f"  wrote {path.relative_to(root)}")
        if written:
            print(
                'Next: fill in each file in ground_truth/, set "status": "done", '
                "then run `python -m eval.calibrate run`."
            )
        if skipped:
            print("Skipped (no stubs written for these):", file=sys.stderr)
            for reason in skipped:
                print(f"  - {reason}", file=sys.stderr)
            return 1
        return 0

    if args.command == "seed-synthetic":
        written = seed_synthetic(root)
        print(f"Copied {len(written)} synthetic fixture(s) into {root}.")
        return 0

    try:
        report_dir = run(root, overlays=not args.no_overlays)
    except GroundTruthError as exc:
        print("Ground truth needs fixing before the run can start:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    markdown = (report_dir / "report.md").read_text(encoding="utf-8")
    summary = markdown.split("\n## ", 2)
    print("\n## ".join(summary[:2]).rstrip())
    print()
    print(f"Full report: {report_dir / 'report.md'}")
    print(f"JSON:        {report_dir / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
