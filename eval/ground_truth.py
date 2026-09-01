"""Ground-truth format + loader. See eval/README.md for the full schema and
how to add a new example.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GroundTruthEntity:
    entity_type: str
    value: str


@dataclass
class GroundTruthExample:
    id: str
    image_path: Path
    text: str
    entities: list[GroundTruthEntity]


def load_ground_truth(fixtures_dir: Path) -> list[GroundTruthExample]:
    """Loads fixtures_dir/ground_truth.json — a JSON array of:

        {
          "id": "unique-slug",
          "image": "filename.png",       // relative to fixtures_dir/images/
          "text": "expected full OCR text, one logical line",
          "entities": [
            {"type": "person", "value": "John A. Smith"},
            ...
          ]
        }
    """
    manifest_path = fixtures_dir / "ground_truth.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))

    examples = []
    for entry in raw:
        examples.append(
            GroundTruthExample(
                id=entry["id"],
                image_path=fixtures_dir / "images" / entry["image"],
                text=entry["text"],
                entities=[GroundTruthEntity(e["type"], e["value"]) for e in entry["entities"]],
            )
        )
    return examples
