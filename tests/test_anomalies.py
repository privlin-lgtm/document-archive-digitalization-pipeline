from extraction.anomalies import (
    detect_all_anomalies,
    detect_entity_conflicts,
    detect_extraction_failures,
    detect_illegible,
    detect_implausible_amounts,
    detect_implausible_dates,
    detect_inconsistent_name_spellings,
    detect_low_ocr_confidence,
)
from extraction.entities import ExtractedEntity
from ocr.engine import OCRResult, OCRWord
from ocr.layout import Region


def make_entity(entity_type, raw_text, normalized_value, **kwargs):
    defaults = {"confidence": 0.8, "start_char": 0, "end_char": len(raw_text)}
    defaults.update(kwargs)
    return ExtractedEntity(entity_type=entity_type, raw_text=raw_text, normalized_value=normalized_value, **defaults)


class TestLowOcrConfidence:
    def test_flags_whole_region_below_threshold(self):
        result = OCRResult(text="x", words=[], engine="tesseract", document_confidence=30.0, line_confidences={})
        flags = detect_low_ocr_confidence(result, threshold=60.0)
        assert len(flags) == 1
        assert flags[0].flag_type == "low_ocr_confidence"
        assert "30" in flags[0].explanation and "60" in flags[0].explanation

    def test_severity_escalates_with_confidence_gap(self):
        near_threshold = OCRResult(text="x", words=[], engine="t", document_confidence=55.0, line_confidences={})
        far_below = OCRResult(text="x", words=[], engine="t", document_confidence=5.0, line_confidences={})
        assert detect_low_ocr_confidence(near_threshold, threshold=60.0)[0].severity == "low"
        assert detect_low_ocr_confidence(far_below, threshold=60.0)[0].severity == "high"

    def test_flags_individual_low_confidence_word_in_otherwise_fine_region(self):
        result = OCRResult(
            text="hello world",
            words=[
                OCRWord("hello", (0, 0, 10, 10), 40.0, 0),
                OCRWord("world", (20, 0, 10, 10), 90.0, 0),
            ],
            engine="tesseract",
            document_confidence=65.0,
            line_confidences={},
        )
        flags = detect_low_ocr_confidence(result, threshold=60.0)
        assert len(flags) == 1
        assert "hello" in flags[0].explanation

    def test_no_flag_when_everything_is_confident(self):
        result = OCRResult(
            text="hello",
            words=[OCRWord("hello", (0, 0, 10, 10), 95.0, 0)],
            engine="tesseract",
            document_confidence=90.0,
            line_confidences={},
        )
        assert detect_low_ocr_confidence(result, threshold=60.0) == []

    def test_region_bbox_is_attached(self):
        result = OCRResult(text="x", words=[], engine="t", document_confidence=10.0, line_confidences={})
        flags = detect_low_ocr_confidence(result, region_bbox=(1, 2, 3, 4), threshold=60.0)
        assert flags[0].region_bbox == (1, 2, 3, 4)

    def test_default_threshold_comes_from_settings(self):
        result = OCRResult(text="x", words=[], engine="t", document_confidence=55.0, line_confidences={})
        # 55 < default low_ocr_confidence_threshold (60.0)
        assert detect_low_ocr_confidence(result) != []


class TestIllegible:
    def test_flags_near_zero_confidence(self):
        result = OCRResult(text="asdf", words=[], engine="t", document_confidence=5.0, line_confidences={})
        flag = detect_illegible(result, confidence_threshold=15.0)
        assert flag is not None
        assert flag.severity == "high"
        assert "near-zero" in flag.explanation

    def test_flags_mostly_non_alphanumeric_output(self):
        result = OCRResult(text="#$%^&*()_+~`|<>?", words=[], engine="t", document_confidence=70.0, line_confidences={})
        flag = detect_illegible(result, alnum_ratio_threshold=0.4)
        assert flag is not None
        assert "noise" in flag.explanation

    def test_no_flag_for_legible_text(self):
        result = OCRResult(
            text="This is perfectly legible text.", words=[], engine="t", document_confidence=85.0, line_confidences={}
        )
        assert detect_illegible(result) is None

    def test_empty_text_is_not_treated_as_noise(self):
        result = OCRResult(text="", words=[], engine="t", document_confidence=85.0, line_confidences={})
        assert detect_illegible(result) is None


class TestImplausibleDates:
    def test_flags_year_after_max(self):
        entities = [make_entity("date", "the 1st of January, 2999", "2999-01-01")]
        flags = detect_implausible_dates(entities, min_year=1000, max_year=2100)
        assert len(flags) == 1
        assert "2999" in flags[0].explanation

    def test_flags_year_before_min(self):
        entities = [make_entity("date", "the 1st of January, 500", "0500-01-01")]
        flags = detect_implausible_dates(entities, min_year=1000, max_year=2100)
        assert len(flags) == 1

    def test_plausible_date_not_flagged(self):
        entities = [make_entity("date", "the 3rd day of March, 1897", "1897-03-03")]
        assert detect_implausible_dates(entities, min_year=1000, max_year=2100) == []

    def test_non_date_entities_ignored(self):
        entities = [make_entity("person", "John Smith", "John Smith")]
        assert detect_implausible_dates(entities) == []

    def test_unparseable_normalized_value_ignored_not_crashed(self):
        entities = [make_entity("date", "sometime", None)]
        assert detect_implausible_dates(entities) == []


class TestImplausibleAmounts:
    def test_flags_amount_over_max(self):
        entities = [make_entity("amount", "$50,000,000.00", "USD 50000000.00")]
        flags = detect_implausible_amounts(entities, max_plausible_amount=1_000_000.0)
        assert len(flags) == 1

    def test_plausible_amount_not_flagged(self):
        entities = [make_entity("amount", "$45.00", "USD 45.00")]
        assert detect_implausible_amounts(entities, max_plausible_amount=1_000_000.0) == []

    def test_non_amount_entities_ignored(self):
        entities = [make_entity("location", "Bombay", "Mumbai")]
        assert detect_implausible_amounts(entities) == []


class TestInconsistentNameSpellings:
    def test_flags_similar_but_not_identical_names(self):
        entities = [
            make_entity("person", "John Smith", "John Smith"),
            make_entity("person", "John Smyth", "John Smyth"),
        ]
        flags = detect_inconsistent_name_spellings(entities, fuzzy_threshold=0.75)
        assert len(flags) == 1
        assert "John Smith" in flags[0].explanation and "John Smyth" in flags[0].explanation

    def test_identical_names_not_flagged(self):
        entities = [
            make_entity("person", "John Smith", "John Smith"),
            make_entity("person", "John Smith", "John Smith"),
        ]
        assert detect_inconsistent_name_spellings(entities) == []

    def test_clearly_different_names_not_flagged(self):
        entities = [
            make_entity("person", "John Smith", "John Smith"),
            make_entity("person", "Robert Williams", "Robert Williams"),
        ]
        assert detect_inconsistent_name_spellings(entities, fuzzy_threshold=0.75) == []

    def test_single_person_produces_no_flags(self):
        entities = [make_entity("person", "John Smith", "John Smith")]
        assert detect_inconsistent_name_spellings(entities) == []

    def test_non_person_entities_ignored(self):
        entities = [
            make_entity("location", "Bombay", "Mumbai"),
            make_entity("location", "Bombay", "Mumbai"),
        ]
        assert detect_inconsistent_name_spellings(entities) == []


class TestEntityConflictsAggregate:
    def test_combines_all_three_checks(self):
        entities = [
            make_entity("date", "the 1st of January, 2999", "2999-01-01"),
            make_entity("amount", "$50,000,000.00", "USD 50000000.00"),
            make_entity("person", "John Smith", "John Smith"),
            make_entity("person", "John Smyth", "John Smyth"),
        ]
        flags = detect_entity_conflicts(entities, min_year=1000, max_year=2100, max_plausible_amount=1_000_000.0)
        assert len(flags) == 3
        assert {f.flag_type for f in flags} == {"entity_conflict"}


class TestExtractionFailures:
    def test_flags_table_region_with_no_amount(self):
        regions = [Region(bbox=(0, 0, 100, 50), region_type="table", reading_order=0, confidence=0.8)]
        flags = detect_extraction_failures(regions, {0: []})
        assert len(flags) == 1
        assert "table" in flags[0].explanation and "amount" in flags[0].explanation

    def test_table_region_with_amount_not_flagged(self):
        regions = [Region(bbox=(0, 0, 100, 50), region_type="table", reading_order=0, confidence=0.8)]
        entities_by_region = {0: [make_entity("amount", "$5.00", "USD 5.00")]}
        assert detect_extraction_failures(regions, entities_by_region) == []

    def test_flags_signature_region_with_no_person(self):
        regions = [Region(bbox=(0, 0, 100, 20), region_type="signature", reading_order=0, confidence=0.6)]
        flags = detect_extraction_failures(regions, {0: []})
        assert len(flags) == 1

    def test_paragraph_region_has_no_expectation(self):
        regions = [Region(bbox=(0, 0, 100, 50), region_type="paragraph", reading_order=0, confidence=0.9)]
        assert detect_extraction_failures(regions, {0: []}) == []

    def test_missing_entry_in_map_treated_as_no_entities(self):
        regions = [Region(bbox=(0, 0, 100, 50), region_type="table", reading_order=0, confidence=0.8)]
        assert len(detect_extraction_failures(regions, {})) == 1


class TestDetectAllAnomalies:
    def test_runs_all_checks_across_a_document(self):
        regions = [
            Region(bbox=(0, 0, 100, 50), region_type="table", reading_order=0, confidence=0.8),
            Region(bbox=(0, 60, 100, 20), region_type="paragraph", reading_order=1, confidence=0.9),
        ]
        ocr_results_by_region = {
            0: OCRResult(text="x", words=[], engine="t", document_confidence=20.0, line_confidences={}),
            1: OCRResult(text="fine legible text here", words=[], engine="t", document_confidence=90.0, line_confidences={}),
        }
        entities = [make_entity("date", "the 1st of January, 2999", "2999-01-01")]
        entities_by_region = {0: [], 1: entities}

        flags = detect_all_anomalies(regions, ocr_results_by_region, entities, entities_by_region)
        flag_types = {f.flag_type for f in flags}
        # region 0: low confidence (20%, below default 60 but above illegible 15) + no amount extracted
        assert "low_ocr_confidence" in flag_types
        assert "extraction_failure" in flag_types
        assert "entity_conflict" in flag_types
