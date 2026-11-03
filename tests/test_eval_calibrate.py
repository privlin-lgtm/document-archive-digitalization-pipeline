"""Tests for the real-scan calibration harness: layout metrics, the drop-in
loader, the stub generator, the Markdown/JSON report and the CLI.

OCR is faked (as everywhere else in this suite) so these run without a
tesseract binary; one end-to-end test uses the real binary when present.
"""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
import pytest

import eval.report as eval_report
from eval import calibrate
from eval.calibration_report import render_json, render_markdown, worst_pages
from eval.ground_truth import GroundTruthError, load_drop_in
from eval.layout_metrics import LayoutScore, iou, score_layout
from eval.metrics import score_entities
from eval.report import EvaluationReport, ExampleResult
from ocr.engine import OCRResult

FIXTURES = Path(__file__).resolve().parent.parent / "eval" / "fixtures"


# --------------------------------------------------------------------------
# layout metrics


class TestIoU:
    def test_identical_boxes(self):
        assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0

    def test_disjoint_boxes(self):
        assert iou((0, 0, 10, 10), (20, 20, 5, 5)) == 0.0

    def test_half_overlap(self):
        # overlap 5x10=50, union 100+100-50=150
        assert iou((0, 0, 10, 10), (5, 0, 10, 10)) == pytest.approx(50 / 150)


class TestScoreLayout:
    def test_perfect_match(self):
        regions = [("paragraph", (0, 0, 100, 20)), ("signature", (0, 200, 80, 30))]
        score = score_layout(regions, regions)
        assert score.detection_precision == 1.0
        assert score.detection_recall == 1.0
        assert score.classification_accuracy == 1.0
        assert score.error_count == 0

    def test_found_but_mistyped(self):
        score = score_layout([("paragraph", (0, 0, 100, 20))], [("signature", (0, 0, 100, 20))])
        assert score.detection_recall == 1.0
        assert score.classification_accuracy == 0.0
        assert score.confusion() == {("signature", "paragraph"): 1}
        per_type = score.per_type()
        assert per_type["signature"].false_negatives == 1
        assert per_type["paragraph"].false_positives == 1

    def test_missed_and_spurious(self):
        score = score_layout([("stamp", (500, 500, 50, 50))], [("paragraph", (0, 0, 100, 20))])
        assert score.missed == [("paragraph", (0, 0, 100, 20))]
        assert score.spurious == [("stamp", (500, 500, 50, 50))]
        assert score.detection_precision == 0.0
        assert score.detection_recall == 0.0
        assert score.error_count == 2

    def test_overlap_below_threshold_is_not_a_match(self):
        score = score_layout([("paragraph", (5, 0, 10, 10))], [("paragraph", (0, 0, 10, 10))])
        assert score.matches == []

    def test_one_big_box_matches_at_most_one_label(self):
        # Segmenter merged two labeled paragraphs into one blob.
        predicted = [("paragraph", (0, 0, 100, 42))]
        reference = [("paragraph", (0, 0, 100, 22)), ("paragraph", (0, 20, 100, 22))]
        score = score_layout(predicted, reference)
        assert len(score.matches) == 1
        assert len(score.missed) == 1

    def test_greedy_prefers_best_overlap(self):
        predicted = [("paragraph", (0, 0, 100, 20)), ("paragraph", (2, 0, 100, 20))]
        reference = [("paragraph", (2, 0, 100, 20))]
        score = score_layout(predicted, reference)
        assert score.matches[0].predicted_index == 1
        assert score.matches[0].iou == 1.0

    def test_empty_page_is_perfect(self):
        score = score_layout([], [])
        assert score.detection_precision == 1.0 and score.detection_recall == 1.0

    def test_combine_pools_pages(self):
        a = score_layout([("paragraph", (0, 0, 10, 10))], [("paragraph", (0, 0, 10, 10))])
        b = score_layout([], [("table", (0, 0, 10, 10))])
        combined = LayoutScore.combine([a, b])
        assert combined.reference_count == 2
        assert combined.detection_recall == 0.5


# --------------------------------------------------------------------------
# drop-in loader


def _write_image(path: Path, text: str = "Hello") -> None:
    image = np.full((200, 600, 3), 255, dtype=np.uint8)
    cv2.putText(image, text, (20, 100), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 3)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), image)


def _write_gt(root: Path, name: str, entry: dict) -> Path:
    path = root / "ground_truth" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entry), encoding="utf-8")
    return path


def _done(example_id: str, image: str, **extra) -> dict:
    entry = {"id": example_id, "image": image, "status": "done", "text": "Hello", "entities": []}
    entry.update(extra)
    return entry


class TestLoadDropIn:
    def test_splits_done_todo_and_unlabeled(self, tmp_path):
        for name in ("a.png", "b.png", "c.png"):
            _write_image(tmp_path / "images" / name)
        _write_gt(tmp_path, "a", _done("a", "a.png"))
        _write_gt(tmp_path, "b", {**_done("b", "b.png"), "status": "todo", "text": ""})

        drop_in = load_drop_in(tmp_path)

        assert [e.id for e in drop_in.examples] == ["a"]
        assert [p.name for p in drop_in.pending] == ["b.json"]
        assert [p.name for p in drop_in.unlabeled_images] == ["c.png"]

    def test_regions_null_means_unlabeled_and_empty_list_means_none(self, tmp_path):
        _write_image(tmp_path / "images" / "a.png")
        _write_image(tmp_path / "images" / "b.png")
        _write_gt(tmp_path, "a", _done("a", "a.png", regions=None))
        _write_gt(tmp_path, "b", _done("b", "b.png", regions=[]))

        by_id = {e.id: e for e in load_drop_in(tmp_path).examples}

        assert by_id["a"].regions is None
        assert by_id["b"].regions == []

    def test_parses_regions_and_page_size(self, tmp_path):
        _write_image(tmp_path / "images" / "a.png")
        _write_gt(
            tmp_path,
            "a",
            _done(
                "a",
                "a.png",
                regions=[{"type": "stamp", "bbox": [1, 2, 3, 4]}],
                page_size=[600, 200],
            ),
        )
        example = load_drop_in(tmp_path).examples[0]
        assert example.regions[0].region_type == "stamp"
        assert example.regions[0].bbox == (1, 2, 3, 4)
        assert example.page_size == (600, 200)

    def test_collects_every_problem_at_once(self, tmp_path):
        _write_image(tmp_path / "images" / "a.png")
        _write_gt(
            tmp_path,
            "bad",
            {
                "id": "bad",
                "image": "missing.png",
                "status": "done",
                "text": "",
                "entities": [{"type": "persn", "value": "x"}],
                "regions": [
                    {"type": "para", "bbox": [0, 0, 1, 1]},
                    {"type": "stamp", "bbox": [0, 0, 0]},
                ],
            },
        )
        (tmp_path / "ground_truth" / "broken.json").write_text("{oops", encoding="utf-8")
        _write_gt(tmp_path, "status", {**_done("s", "a.png"), "status": "finished"})

        with pytest.raises(GroundTruthError) as excinfo:
            load_drop_in(tmp_path)

        text = "\n".join(excinfo.value.problems)
        assert "image not found" in text
        assert '"text" is empty' in text
        assert "'persn'" in text
        assert "'para'" in text
        assert "bbox must be" in text
        assert "broken.json: not valid JSON" in text
        assert "'finished'" in text

    def test_duplicate_ids_are_rejected(self, tmp_path):
        _write_image(tmp_path / "images" / "a.png")
        _write_gt(tmp_path, "one", _done("same", "a.png"))
        _write_gt(tmp_path, "two", _done("same", "a.png"))
        with pytest.raises(GroundTruthError, match="also used by"):
            load_drop_in(tmp_path)

    def test_missing_folder_is_empty_not_an_error(self, tmp_path):
        drop_in = load_drop_in(tmp_path / "nope")
        assert drop_in.examples == [] and drop_in.pending == [] and drop_in.unlabeled_images == []


# --------------------------------------------------------------------------
# stub generator


class TestCreateStubs:
    def test_writes_a_todo_stub_and_label_page_per_new_image(self, tmp_path):
        _write_image(tmp_path / "images" / "Parish Ledger 1872 p14.png")

        written = calibrate.create_stubs(tmp_path)

        assert [p.name for p in written] == ["parish-ledger-1872-p14.json"]
        entry = json.loads(written[0].read_text(encoding="utf-8"))
        assert entry["status"] == "todo"
        assert entry["image"] == "Parish Ledger 1872 p14.png"
        assert entry["text"] == "" and entry["entities"] == [] and entry["regions"] is None
        label_page = tmp_path / "label-pages" / "parish-ledger-1872-p14.png"
        assert label_page.is_file()
        page = cv2.imread(str(label_page), cv2.IMREAD_GRAYSCALE)
        assert entry["page_size"] == [page.shape[1], page.shape[0]]
        # and the new stub shows up as pending, not as a problem
        assert len(load_drop_in(tmp_path).pending) == 1

    def test_never_overwrites_existing_ground_truth(self, tmp_path):
        _write_image(tmp_path / "images" / "a.png")
        path = _write_gt(tmp_path, "a", _done("a", "a.png", notes="hand-written"))

        assert calibrate.create_stubs(tmp_path) == []
        assert json.loads(path.read_text(encoding="utf-8"))["notes"] == "hand-written"

    def test_id_collisions_get_a_suffix(self, tmp_path):
        _write_image(tmp_path / "images" / "a b.png")
        _write_image(tmp_path / "images" / "a-b.jpg")

        written = calibrate.create_stubs(tmp_path)

        assert sorted(p.name for p in written) == ["a-b-2.json", "a-b.json"]

    def test_prefill_regions_uses_the_segmenter(self, tmp_path):
        _write_image(tmp_path / "images" / "a.png")
        entry = json.loads(
            calibrate.create_stubs(tmp_path, prefill_regions=True)[0].read_text("utf-8")
        )
        assert entry["regions"], "expected at least one detected region on a page with text"
        assert all(r["type"] == "paragraph" and len(r["bbox"]) == 4 for r in entry["regions"])

    def test_ignores_non_image_files(self, tmp_path):
        (tmp_path / "images").mkdir(parents=True)
        (tmp_path / "images" / "notes.txt").write_text("x", encoding="utf-8")
        assert calibrate.create_stubs(tmp_path) == []


# --------------------------------------------------------------------------
# run + report (OCR faked)


class _FakeRouter:
    """Returns a fixed transcript for every region, like a very stubborn OCR."""

    def __init__(self, text: str):
        self.text = text

    def run(self, image):
        return OCRResult(
            text=self.text, words=[], engine="fake", document_confidence=0.9, line_confidences={}
        )


@pytest.fixture
def fake_ocr(monkeypatch):
    monkeypatch.setattr(eval_report, "_router", _FakeRouter("Paid to Robert Jones"))


class TestRun:
    def test_seeded_synthetic_set_produces_dated_reports(self, tmp_path, fake_ocr):
        calibrate.seed_synthetic(tmp_path)
        _write_image(tmp_path / "images" / "new-scan.png")  # unlabeled image

        report_dir = calibrate.run(tmp_path, now=datetime(2026, 11, 2, 9, 30, 0, tzinfo=UTC))

        assert report_dir == tmp_path / "reports" / "2026-11-02_093000"
        markdown = (report_dir / "report.md").read_text(encoding="utf-8")
        assert "# Real-scan evaluation, 2026-11-02 09:30" in markdown
        assert "| pages scored | 4 |" in markdown
        assert "## Worst " in markdown
        assert "## Layout regions" in markdown
        assert "new-scan.png" in markdown  # listed under "Not scored yet"
        data = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
        assert data["summary"]["pages_scored"] == 4
        assert data["summary"]["images_without_ground_truth"] == 1
        assert data["layout"]["detection_recall"] == 1.0  # labels were seeded from the segmenter
        assert {p["id"] for p in data["pages"]} == {
            e.id for e in eval_report.load_ground_truth(FIXTURES)
        }
        assert sorted(p.name for p in (report_dir / "overlays").iterdir()) == sorted(
            f"{p['id']}.png" for p in data["pages"]
        )

    def test_warns_when_label_page_size_no_longer_matches(self, tmp_path, fake_ocr):
        _write_image(tmp_path / "images" / "a.png")
        _write_gt(tmp_path, "a", _done("a", "a.png", regions=[], page_size=[1, 1]))

        report_dir = calibrate.run(tmp_path, overlays=False)

        assert "regions were labeled on a 1x1 page" in (report_dir / "report.md").read_text("utf-8")
        assert not (report_dir / "overlays").exists()

    def test_nothing_done_raises_lookup_error(self, tmp_path):
        _write_image(tmp_path / "images" / "a.png")
        calibrate.create_stubs(tmp_path)
        with pytest.raises(LookupError, match="1 stub"):
            calibrate.run(tmp_path)


class TestCli:
    def test_init_stubs_run_exit_codes(self, tmp_path, fake_ocr, capsys):
        root = ["--root", str(tmp_path)]
        assert calibrate.main(["init", *root]) == 0
        assert (tmp_path / "images").is_dir() and (tmp_path / "reports").is_dir()

        _write_image(tmp_path / "images" / "a.png")
        assert calibrate.main(["stubs", *root]) == 0
        assert calibrate.main(["run", *root]) == 1  # stub still "todo"

        stub = tmp_path / "ground_truth" / "a.json"
        entry = json.loads(stub.read_text("utf-8"))
        entry.update(status="done", entities=[{"type": "persn", "value": "x"}])
        stub.write_text(json.dumps(entry), encoding="utf-8")
        assert calibrate.main(["run", *root]) == 2  # empty text + bad type
        assert "'persn'" in capsys.readouterr().err

        entry.update(text="Hello", entities=[])
        stub.write_text(json.dumps(entry), encoding="utf-8")
        assert calibrate.main(["run", *root]) == 0
        out = capsys.readouterr().out
        assert "## Summary" in out
        assert "Full report:" in out

    def test_root_defaults_to_env_var(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVAL_REAL_DIR", str(tmp_path / "x"))
        assert calibrate.main(["init"]) == 0
        assert (tmp_path / "x" / "ground_truth").is_dir()


# --------------------------------------------------------------------------
# report rendering


def _result(
    example_id: str, cer: float, *, layout=None, predicted=(), reference=()
) -> ExampleResult:
    return ExampleResult(
        example_id=example_id,
        predicted_text="pred | text",
        reference_text="ref text",
        cer=cer,
        wer=cer,
        predicted_entities=list(predicted),
        reference_entities=list(reference),
        layout=layout,
        image_name=f"{example_id}.png",
    )


class TestRenderReport:
    def _report(self) -> EvaluationReport:
        mistyped = score_layout([("paragraph", (0, 0, 10, 10))], [("signature", (0, 0, 10, 10))])
        results = [
            _result("clean", 0.0, layout=score_layout([], [])),
            _result(
                "messy",
                0.4,
                layout=mistyped,
                predicted=[("person", "Wrong Name")],
                reference=[("person", "John Smith")],
            ),
            _result("tie-more-entity-errors", 0.1, reference=[("date", "1900-01-01")]),
            _result("tie-fewer", 0.1),
        ]
        predicted = [p for r in results for p in r.predicted_entities]
        reference = [p for r in results for p in r.reference_entities]
        return EvaluationReport(results=results, entity_scores=score_entities(predicted, reference))

    def test_worst_pages_rank_by_cer_then_entity_errors(self):
        assert [r.example_id for r in worst_pages(self._report())] == [
            "messy",
            "tie-more-entity-errors",
            "tie-fewer",
            "clean",
        ]

    def test_markdown_explains_what_went_wrong(self):
        md = render_markdown(
            self._report(),
            root=Path("/x"),
            generated_at=datetime(2026, 11, 2, 12, 0, tzinfo=UTC),
            pending=[Path("todo.json")],
        )
        assert "missed entities: person `John Smith`" in md
        assert "spurious entities: person `Wrong Name`" in md
        assert "region labeled **signature** was predicted **paragraph**" in md
        assert "| **signature** | 1 | 0 |" in md  # confusion row
        assert "`todo.json`: status is still" in md
        assert "| stubs still to fill in (status `todo`) | 1 |" in md

    def test_markdown_without_layout_labels_says_so(self):
        report = EvaluationReport(results=[_result("a", 0.0)], entity_scores={})
        md = render_markdown(report, root=Path("/x"), generated_at=datetime(2026, 1, 1, tzinfo=UTC))
        assert "no pages have region labels yet" in md
        assert "layout was not scored" in md

    def test_json_round_trips(self):
        data = render_json(
            self._report(), root=Path("/x"), generated_at=datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
        )
        again = json.loads(json.dumps(data))
        assert again["summary"]["worst_pages"][0] == "messy"
        assert again["layout"]["confusion"] == [
            {"labeled": "signature", "predicted": "paragraph", "count": 1}
        ]
        messy = next(p for p in again["pages"] if p["id"] == "messy")
        assert messy["missed_entities"] == [{"type": "person", "value": "John Smith"}]


# --------------------------------------------------------------------------
# real OCR, when available


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="needs a real tesseract binary")
def test_end_to_end_with_real_tesseract(tmp_path, monkeypatch):
    monkeypatch.setattr(eval_report, "_router", None)
    calibrate.seed_synthetic(tmp_path)
    report_dir = calibrate.run(tmp_path, overlays=False)
    data = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    assert data["summary"]["pages_scored"] == 4
    assert data["summary"]["mean_cer"] < 0.05
    assert data["summary"]["entities_all_types"]["f1"] > 0.9


# --------------------------------------------------------------------------
# multi-page scans: PDFs and multi-frame TIFFs, one stub and one row per page


def _text_page(text: str) -> np.ndarray:
    image = np.full((400, 900), 255, dtype=np.uint8)
    cv2.putText(image, text, (40, 200), cv2.FONT_HERSHEY_SIMPLEX, 2.0, 0, 5)
    return image


def _write_pdf(path: Path, pages: int = 3) -> None:
    from PIL import Image

    frames = [Image.fromarray(_text_page(f"Page {i + 1}")).convert("RGB") for i in range(pages)]
    path.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(path, "PDF", resolution=300, save_all=True, append_images=frames[1:])


def _write_tiff(path: Path, frames: int = 3) -> None:
    from PIL import Image

    images = [Image.fromarray(_text_page(f"Frame {i + 1}")) for i in range(frames)]
    path.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(path, "TIFF", save_all=True, append_images=images[1:], compression="tiff_deflate")


def _mark_all_done(root: Path) -> None:
    for stub in (root / "ground_truth").glob("*.json"):
        entry = json.loads(stub.read_text("utf-8"))
        entry.update(status="done", text="Paid to Robert Jones", regions=[])
        stub.write_text(json.dumps(entry), encoding="utf-8")


@pytest.fixture
def settings_env(monkeypatch):
    # iter_page_images reads PDF_RENDER_DPI / MAX_IMAGE_PIXELS from settings
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x@localhost/x")
    monkeypatch.setenv("REVIEW_API_TOKEN", "test-token-not-a-real-secret-0000000000")


@pytest.mark.parametrize(
    ("name", "writer", "pages_stem"),
    [("Vestry Minutes 1871.pdf", _write_pdf, "vestry-minutes-1871"), ("ledger.tif", _write_tiff, "ledger")],
)
class TestMultiPageScans:
    def test_one_stub_and_label_page_per_page(self, tmp_path, settings_env, name, writer, pages_stem):
        writer(tmp_path / "images" / name)

        written = calibrate.create_stubs(tmp_path)

        assert [p.name for p in written] == [f"{pages_stem}.p{n}.json" for n in (1, 2, 3)]
        for n, path in enumerate(written, start=1):
            entry = json.loads(path.read_text("utf-8"))
            assert entry["id"] == f"{pages_stem}.p{n}"
            assert entry["image"] == name
            assert entry["page"] == n
            assert entry["status"] == "todo"
            label = cv2.imread(str(tmp_path / "label-pages" / f"{entry['id']}.png"), cv2.IMREAD_GRAYSCALE)
            assert entry["page_size"] == [label.shape[1], label.shape[0]]
        # the three label pages are different pages, not page 1 three times
        labels = [cv2.imread(str(tmp_path / "label-pages" / f"{pages_stem}.p{n}.png")) for n in (1, 2, 3)]
        assert not np.array_equal(labels[0], labels[1]) and not np.array_equal(labels[1], labels[2])
        # running stubs again writes nothing new
        assert calibrate.create_stubs(tmp_path) == []

    def test_run_scores_every_page_with_a_row_and_overlay_each(
        self, tmp_path, settings_env, fake_ocr, name, writer, pages_stem
    ):
        writer(tmp_path / "images" / name)
        calibrate.create_stubs(tmp_path)
        _mark_all_done(tmp_path)

        report_dir = calibrate.run(tmp_path, now=datetime(2026, 11, 3, 10, 0, tzinfo=UTC))

        data = json.loads((report_dir / "report.json").read_text("utf-8"))
        assert data["summary"]["pages_scored"] == 3
        ids = [f"{pages_stem}.p{n}" for n in (1, 2, 3)]
        assert [p["id"] for p in data["pages"]] == ids
        assert [p["page"] for p in data["pages"]] == [1, 2, 3]
        assert {p["image"] for p in data["pages"]} == {name}
        assert all(p["cer"] == 0.0 for p in data["pages"])
        markdown = (report_dir / "report.md").read_text("utf-8")
        for n in (1, 2, 3):
            assert f"| {pages_stem}.p{n} | {name} p{n} |" in markdown
        assert sorted(p.name for p in (report_dir / "overlays").iterdir()) == [f"{i}.png" for i in ids]

    def test_stub_pointing_at_a_missing_page_exits_2(
        self, tmp_path, settings_env, fake_ocr, capsys, name, writer, pages_stem
    ):
        writer(tmp_path / "images" / name)
        _write_gt(tmp_path, "p9", _done("p9", name, page=9))

        assert calibrate.main(["run", "--root", str(tmp_path)]) == 2
        err = capsys.readouterr().err
        assert f'"page" is 9 but {name} has only 3 page(s)' in err

    def test_over_limit_page_count_is_skipped_and_rejected(
        self, tmp_path, settings_env, fake_ocr, name, writer, pages_stem
    ):
        writer(tmp_path / "images" / name)
        skipped: list[str] = []

        assert calibrate.create_stubs(tmp_path, max_pages=2, skipped=skipped) == []
        assert skipped == [
            (
                f"{name}: 3 pages, more than the 2-page limit (MAX_PAGES_PER_DOCUMENT); "
                "split it into smaller files"
            )
        ]
        _write_gt(tmp_path, "p1", _done("p1", name, page=1))
        with pytest.raises(GroundTruthError, match="more than the 2-page limit"):
            calibrate.run(tmp_path, max_pages=2)


class TestMultiPageEdgeCases:
    def test_stubs_cli_exits_1_and_explains_an_over_limit_scan(
        self, tmp_path, settings_env, monkeypatch, capsys
    ):
        monkeypatch.setattr(calibrate, "_max_pages", lambda: 2)
        _write_pdf(tmp_path / "images" / "big.pdf", pages=3)
        _write_image(tmp_path / "images" / "a.png")

        assert calibrate.main(["stubs", "--root", str(tmp_path)]) == 1
        captured = capsys.readouterr()
        assert "wrote ground_truth/a.json" in captured.out  # other scans still get stubs
        assert "big.pdf: 3 pages, more than the 2-page limit" in captured.err

    def test_corrupt_pdf_is_skipped_not_a_crash(self, tmp_path, settings_env):
        (tmp_path / "images").mkdir(parents=True)
        (tmp_path / "images" / "broken.pdf").write_bytes(b"%PDF-1.4\nnot really a pdf")
        skipped: list[str] = []
        assert calibrate.create_stubs(tmp_path, skipped=skipped) == []
        assert len(skipped) == 1 and skipped[0].startswith("broken.pdf: could not read it")

    def test_only_missing_pages_get_new_stubs(self, tmp_path, settings_env):
        _write_pdf(tmp_path / "images" / "doc.pdf")
        _write_gt(tmp_path, "mine", {**_done("my-label", "doc.pdf", page=2), "notes": "keep"})

        written = calibrate.create_stubs(tmp_path)

        assert [p.name for p in written] == ["doc.p1.json", "doc.p3.json"]
        assert json.loads((tmp_path / "ground_truth" / "mine.json").read_text("utf-8"))["notes"] == "keep"

    def test_page_must_be_a_positive_whole_number(self, tmp_path):
        _write_image(tmp_path / "images" / "a.png")
        _write_gt(tmp_path, "zero", _done("zero", "a.png", page=0))
        _write_gt(tmp_path, "text", _done("text", "a.png", page="2"))
        with pytest.raises(GroundTruthError) as excinfo:
            load_drop_in(tmp_path)
        assert len([p for p in excinfo.value.problems if '"page" must be a whole number' in p]) == 2

    def test_single_images_are_unchanged(self, tmp_path, fake_ocr):
        _write_image(tmp_path / "images" / "a.png")
        entry = json.loads(calibrate.create_stubs(tmp_path)[0].read_text("utf-8"))
        assert "page" not in entry and entry["id"] == "a"
        _mark_all_done(tmp_path)
        data = json.loads((calibrate.run(tmp_path) / "report.json").read_text("utf-8"))
        assert data["pages"][0]["page"] is None
        assert data["pages"][0]["image"] == "a.png"

    def test_mixed_folder_scores_images_and_pages_together(self, tmp_path, settings_env, fake_ocr):
        _write_image(tmp_path / "images" / "a.png")
        _write_pdf(tmp_path / "images" / "doc.pdf", pages=2)
        _write_tiff(tmp_path / "images" / "roll.tiff", frames=2)
        assert len(calibrate.create_stubs(tmp_path)) == 5
        _mark_all_done(tmp_path)
        data = json.loads((calibrate.run(tmp_path) / "report.json").read_text("utf-8"))
        assert [p["id"] for p in data["pages"]] == ["a", "doc.p1", "doc.p2", "roll.p1", "roll.p2"]


def test_page_reader_renders_a_scan_once_when_read_in_order(tmp_path, settings_env, monkeypatch):
    import ocr.pages
    from eval.page_reader import PageReader

    _write_tiff(tmp_path / "roll.tif", frames=3)
    calls = []
    real = ocr.pages.iter_page_images

    def counting(path, **kwargs):
        calls.append(path)
        return real(path, **kwargs)

    monkeypatch.setattr(ocr.pages, "iter_page_images", counting)
    with PageReader() as reader:
        pages = [reader.read(tmp_path / "roll.tif", n) for n in (1, 2, 2, 3)]
        assert len(calls) == 1
        assert pages[1] is pages[2]
        assert not np.array_equal(pages[0], pages[1])
        reader.read(tmp_path / "roll.tif", 1)  # going back reopens the scan
        assert len(calls) == 2
        with pytest.raises(IndexError, match="no page 4"):
            reader.read(tmp_path / "roll.tif", 4)
        assert len(calls) == 2
