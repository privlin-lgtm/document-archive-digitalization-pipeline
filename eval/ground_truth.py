"""Ground-truth format + loaders. See eval/README.md for the full schema and
how to add a new example, and eval/real/README.md for the real-scan
drop-in folder (one JSON file per scan instead of one array).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eval.layout_metrics import BBox

ENTITY_TYPES = ("person", "date", "location", "amount")  # extraction.entities.EntityType
REGION_TYPES = ("paragraph", "table", "signature", "margin_annotation", "stamp")  # ocr.layout
STATUS_TODO = "todo"
STATUS_DONE = "done"


class GroundTruthError(ValueError):
    """One or more ground-truth files are malformed. `problems` lists each
    issue as a human-readable line naming the file, so a person filling in
    JSON by hand can fix them all in one pass."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("ground truth has problems:\n" + "\n".join(f"  - {p}" for p in problems))


@dataclass
class GroundTruthEntity:
    entity_type: str
    value: str


@dataclass
class GroundTruthRegion:
    region_type: str
    bbox: BBox


@dataclass
class GroundTruthExample:
    id: str
    image_path: Path
    text: str
    entities: list[GroundTruthEntity]
    # None = regions not labeled for this page (layout scoring skipped);
    # [] = labeled, and the page genuinely has no regions.
    regions: list[GroundTruthRegion] | None = None
    notes: str = ""
    source_file: Path | None = None
    # (width, height) of the preprocessed page the regions were drawn on,
    # recorded by the stub generator; used to warn when it no longer matches.
    page_size: tuple[int, int] | None = None


@dataclass
class DropInSet:
    """Result of loading the real-scan drop-in folder."""

    examples: list[GroundTruthExample]  # status "done", ready to score
    pending: list[Path] = field(default_factory=list)  # stubs still "todo"
    unlabeled_images: list[Path] = field(default_factory=list)  # images with no stub yet


def load_ground_truth(fixtures_dir: Path) -> list[GroundTruthExample]:
    """Loads fixtures_dir/ground_truth.json — a JSON array of:

        {
          "id": "unique-slug",
          "image": "filename.png",       // relative to fixtures_dir/images/
          "text": "expected full OCR text, one logical line",
          "entities": [
            {"type": "person", "value": "John A. Smith"},
            ...
          ],
          "regions": [                   // optional
            {"type": "paragraph", "bbox": [x, y, width, height]}
          ]
        }
    """
    manifest_path = fixtures_dir / "ground_truth.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))

    examples = []
    problems: list[str] = []
    for entry in raw:
        example = _parse_entry(entry, fixtures_dir / "images", manifest_path, problems, check_image=False)
        if example is not None:
            examples.append(example)
    if problems:
        raise GroundTruthError(problems)
    return examples


IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp")


def list_images(images_dir: Path) -> list[Path]:
    if not images_dir.is_dir():
        return []
    return sorted(p for p in images_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)


def load_drop_in(root: Path) -> DropInSet:
    """Loads the real-scan layout: root/images/<scan>, root/ground_truth/<id>.json.

    Every problem across every file is collected and raised together as a
    GroundTruthError, so one run tells you everything to fix.
    """
    images_dir = root / "images"
    gt_dir = root / "ground_truth"
    problems: list[str] = []
    examples: list[GroundTruthExample] = []
    pending: list[Path] = []
    referenced: set[str] = set()
    seen_ids: dict[str, Path] = {}

    for path in sorted(gt_dir.glob("*.json")) if gt_dir.is_dir() else []:
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"{path.name}: not valid JSON ({exc.msg} at line {exc.lineno})")
            continue
        if not isinstance(entry, dict):
            problems.append(f"{path.name}: must be a JSON object, not {type(entry).__name__}")
            continue
        if isinstance(entry.get("image"), str):
            referenced.add(entry["image"])
        status = entry.get("status", STATUS_DONE)
        if status == STATUS_TODO:
            pending.append(path)
            continue
        if status != STATUS_DONE:
            problems.append(f'{path.name}: "status" must be "{STATUS_TODO}" or "{STATUS_DONE}", got {status!r}')
            continue
        example = _parse_entry(entry, images_dir, path, problems)
        if example is None:
            continue
        if example.id in seen_ids:
            problems.append(f'{path.name}: id "{example.id}" is also used by {seen_ids[example.id].name}')
            continue
        seen_ids[example.id] = path
        examples.append(example)

    if problems:
        raise GroundTruthError(problems)

    unlabeled = [p for p in list_images(images_dir) if p.name not in referenced]
    return DropInSet(examples=examples, pending=pending, unlabeled_images=unlabeled)


def _parse_entry(
    entry: dict[str, Any],
    images_dir: Path,
    source: Path,
    problems: list[str],
    *,
    check_image: bool = True,
) -> GroundTruthExample | None:
    where = source.name
    before = len(problems)

    for key in ("id", "image", "text"):
        if not isinstance(entry.get(key), str) or (key != "text" and not entry[key].strip()):
            problems.append(f'{where}: "{key}" must be a non-empty string')
    if before != len(problems):
        return None
    where = f"{source.name} [{entry['id']}]"

    image_path = images_dir / entry["image"]
    if check_image and not image_path.is_file():
        problems.append(f"{where}: image not found: {image_path}")
    if not entry["text"].strip():
        problems.append(f'{where}: "text" is empty; transcribe the page before setting status "done"')

    entities: list[GroundTruthEntity] = []
    raw_entities = entry.get("entities", [])
    if not isinstance(raw_entities, list):
        problems.append(f'{where}: "entities" must be a list')
        raw_entities = []
    for i, e in enumerate(raw_entities):
        if not isinstance(e, dict) or not isinstance(e.get("type"), str) or not isinstance(e.get("value"), str):
            problems.append(f'{where}: entities[{i}] needs string "type" and "value"')
            continue
        if e["type"] not in ENTITY_TYPES:
            problems.append(f'{where}: entities[{i}] type {e["type"]!r} is not one of {", ".join(ENTITY_TYPES)}')
            continue
        entities.append(GroundTruthEntity(e["type"], e["value"]))

    regions: list[GroundTruthRegion] | None = None
    raw_regions = entry.get("regions")
    if raw_regions is not None:
        regions = []
        if not isinstance(raw_regions, list):
            problems.append(f'{where}: "regions" must be a list or null')
            raw_regions = []
        for i, r in enumerate(raw_regions):
            bbox = r.get("bbox") if isinstance(r, dict) else None
            region_type = r.get("type") if isinstance(r, dict) else None
            if region_type not in REGION_TYPES:
                problems.append(f'{where}: regions[{i}] type {region_type!r} is not one of {", ".join(REGION_TYPES)}')
                continue
            if (
                not isinstance(bbox, list)
                or len(bbox) != 4
                or not all(isinstance(v, int) and not isinstance(v, bool) for v in bbox)
                or bbox[0] < 0
                or bbox[1] < 0
                or bbox[2] <= 0
                or bbox[3] <= 0
            ):
                problems.append(
                    f"{where}: regions[{i}] bbox must be [x, y, width, height] as whole numbers, "
                    f"x/y >= 0 and width/height > 0, got {bbox!r}"
                )
                continue
            regions.append(GroundTruthRegion(region_type, (bbox[0], bbox[1], bbox[2], bbox[3])))

    page_size = None
    raw_size = entry.get("page_size")
    if isinstance(raw_size, list) and len(raw_size) == 2 and all(isinstance(v, int) for v in raw_size):
        page_size = (raw_size[0], raw_size[1])

    if before != len(problems):
        return None
    return GroundTruthExample(
        id=entry["id"],
        image_path=image_path,
        text=entry["text"],
        entities=entities,
        regions=regions,
        notes=entry.get("notes", "") if isinstance(entry.get("notes"), str) else "",
        source_file=source,
        page_size=page_size,
    )
