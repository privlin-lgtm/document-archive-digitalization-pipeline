import json
from pathlib import Path

from eval.ground_truth import load_ground_truth
from eval.metrics import character_error_rate, normalize_text, score_entities, word_error_rate
from eval.report import EvaluationReport, ExampleResult, format_report


class TestNormalizeText:
    def test_collapses_whitespace_runs_and_newlines(self):
        assert normalize_text("hello\n\n  world   there") == "hello world there"

    def test_strips_leading_and_trailing_whitespace(self):
        assert normalize_text("  hello  ") == "hello"


class TestCharacterErrorRate:
    def test_identical_text_is_zero(self):
        assert character_error_rate("hello world", "hello world") == 0.0

    def test_whitespace_only_differences_do_not_count(self):
        assert character_error_rate("hello\nworld", "hello world") == 0.0

    def test_counts_character_edits_relative_to_reference_length(self):
        # "hello wrld" vs "hello world": one deleted character, ref len 11
        assert character_error_rate("hello wrld", "hello world") == 1 / 11

    def test_empty_reference_and_empty_prediction_is_zero(self):
        assert character_error_rate("", "") == 0.0

    def test_empty_reference_with_nonempty_prediction_is_one(self):
        assert character_error_rate("garbage", "") == 1.0


class TestWordErrorRate:
    def test_identical_text_is_zero(self):
        assert word_error_rate("the quick fox", "the quick fox") == 0.0

    def test_one_wrong_word_out_of_three(self):
        assert word_error_rate("the slow fox", "the quick fox") == 1 / 3

    def test_empty_reference_and_empty_prediction_is_zero(self):
        assert word_error_rate("", "") == 0.0


class TestScoreEntities:
    def test_perfect_match_has_no_false_positives_or_negatives(self):
        pairs = [("person", "John Smith"), ("date", "1897-03-03")]
        scores = score_entities(pairs, pairs)

        assert scores["person"].true_positives == 1
        assert scores["person"].false_positives == 0
        assert scores["person"].false_negatives == 0
        assert scores["person"].precision == 1.0
        assert scores["person"].recall == 1.0
        assert scores["person"].f1 == 1.0

    def test_missed_entity_is_a_false_negative(self):
        scores = score_entities([], [("person", "John Smith")])

        assert scores["person"].true_positives == 0
        assert scores["person"].false_negatives == 1
        assert scores["person"].recall == 0.0
        assert scores["person"].precision == 1.0  # no predictions -> vacuously perfect precision

    def test_spurious_entity_is_a_false_positive(self):
        scores = score_entities([("person", "Not Real")], [])

        assert scores["person"].false_positives == 1
        assert scores["person"].precision == 0.0
        assert scores["person"].recall == 1.0  # no reference entities -> vacuously perfect recall

    def test_duplicate_predictions_match_duplicate_references_without_double_counting(self):
        predicted = [("person", "John Smith"), ("person", "John Smith")]
        reference = [("person", "John Smith")]

        scores = score_entities(predicted, reference)

        assert scores["person"].true_positives == 1
        assert scores["person"].false_positives == 1  # the second "John Smith" has nothing left to match
        assert scores["person"].false_negatives == 0

    def test_entity_types_are_scored_independently(self):
        predicted = [("person", "Wrong Name"), ("date", "1897-03-03")]
        reference = [("person", "John Smith"), ("date", "1897-03-03")]

        scores = score_entities(predicted, reference)

        assert scores["date"].f1 == 1.0
        assert scores["person"].f1 == 0.0


class TestLoadGroundTruth:
    def test_loads_examples_with_image_paths_relative_to_fixtures_dir(self, tmp_path):
        (tmp_path / "images").mkdir()
        manifest = [
            {
                "id": "example-1",
                "image": "scan.png",
                "text": "hello world",
                "entities": [{"type": "person", "value": "John Smith"}],
            }
        ]
        (tmp_path / "ground_truth.json").write_text(json.dumps(manifest), encoding="utf-8")

        examples = load_ground_truth(tmp_path)

        assert len(examples) == 1
        example = examples[0]
        assert example.id == "example-1"
        assert example.image_path == tmp_path / "images" / "scan.png"
        assert example.text == "hello world"
        assert example.entities[0].entity_type == "person"
        assert example.entities[0].value == "John Smith"

    def test_checked_in_fixtures_load_and_reference_real_image_files(self):
        fixtures_dir = Path(__file__).resolve().parent.parent / "eval" / "fixtures"

        examples = load_ground_truth(fixtures_dir)

        assert len(examples) >= 1
        for example in examples:
            assert example.image_path.is_file(), f"missing fixture image: {example.image_path}"
            assert example.text.strip() != ""
            assert len(example.entities) > 0


class TestFormatReport:
    def test_report_includes_summary_and_worst_examples(self):
        report = EvaluationReport(
            results=[
                ExampleResult(
                    example_id="good",
                    predicted_text="hello world",
                    reference_text="hello world",
                    cer=0.0,
                    wer=0.0,
                    predicted_entities=[("person", "John Smith")],
                    reference_entities=[("person", "John Smith")],
                ),
                ExampleResult(
                    example_id="bad",
                    predicted_text="gibberish",
                    reference_text="hello world",
                    cer=0.8,
                    wer=1.0,
                    predicted_entities=[],
                    reference_entities=[("person", "John Smith")],
                ),
            ],
            entity_scores=score_entities(
                [("person", "John Smith")], [("person", "John Smith"), ("person", "John Smith")]
            ),
        )

        text = format_report(report)

        assert "Examples evaluated: 2" in text
        assert "person" in text
        assert "bad" in text
        assert "gibberish" in text  # worst example's predicted text is shown for inspection

    def test_empty_report_does_not_crash(self):
        report = EvaluationReport(results=[], entity_scores={})
        text = format_report(report)
        assert "Examples evaluated: 0" in text
