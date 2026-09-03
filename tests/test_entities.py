import pytest

from extraction.entities import ExtractedEntity, extract_entities


def _only(text: str, entity_type: str) -> list[ExtractedEntity]:
    return [e for e in extract_entities(text) if e.entity_type == entity_type]


# --------------------------------------------------------------------------
# Dates: ordinal/word forms, abbreviated months, numeric forms, and
# OCR-mangled digits/month spellings.
# --------------------------------------------------------------------------

DATE_CASES = [
    ("the 3rd day of March, 1897", "1897-03-03"),
    ("3 March 1897", "1897-03-03"),
    ("March 3, 1897", "1897-03-03"),
    ("Mar. 3, 1897", "1897-03-03"),
    ("the 21st of December, 1899", "1899-12-21"),
    ("12/25/1901", "1901-12-25"),
    ("01-15-1850", "1850-01-15"),
    ("the 1st of January, 1900", "1900-01-01"),
    ("the 3rd day of Morch, l897", "1897-03-03"),  # fuzzy month + OCR-mangled year
    ("Sept 9, 1888", "1888-09-09"),
    ("the ist of January, 1850", "1850-01-01"),  # OCR-mangled day ("1st" -> "ist")
    ("the 3lst of December, 1901", "1901-12-31"),  # OCR-mangled two-digit day ("31st" -> "3lst")
    ("O3/15/1923", "1923-03-15"),  # OCR-mangled numeric month ("03" -> "O3")
]


class TestDateExtraction:
    @pytest.mark.parametrize("text,expected_iso", DATE_CASES)
    def test_extracts_and_normalizes_to_iso(self, text, expected_iso):
        dates = _only(text, "date")
        assert len(dates) == 1
        assert dates[0].normalized_value == expected_iso

    def test_ocr_mangled_year_lowers_confidence(self):
        clean = _only("3 March 1897", "date")[0]
        mangled = _only("3 March l897", "date")[0]
        assert mangled.confidence < clean.confidence

    def test_ocr_mangled_day_lowers_confidence(self):
        clean = _only("the 1st of January, 1850", "date")[0]
        mangled = _only("the ist of January, 1850", "date")[0]
        assert mangled.confidence < clean.confidence

    def test_ocr_mangled_numeric_month_lowers_confidence(self):
        clean = _only("03/15/1923", "date")[0]
        mangled = _only("O3/15/1923", "date")[0]
        assert mangled.confidence < clean.confidence

    def test_fuzzy_month_correction_lowers_confidence(self):
        clean = _only("3 March 1897", "date")[0]
        fuzzy = _only("3 Morch 1897", "date")[0]
        assert fuzzy.confidence < clean.confidence

    def test_invalid_day_for_month_is_not_extracted(self):
        assert _only("the 31st day of February, 1897", "date") == []

    def test_span_covers_the_matched_text(self):
        text = "Recorded on the 3rd day of March, 1897 in the ledger."
        ent = _only(text, "date")[0]
        assert text[ent.start_char : ent.end_char] == ent.raw_text


# --------------------------------------------------------------------------
# Amounts: modern decimal, spelled dollars/cents, and pre-decimal £/s/d.
# --------------------------------------------------------------------------

AMOUNT_CASES = [
    ("$45.00", "USD 45.00"),
    ("$1,234.56", "USD 1234.56"),
    ("$5", "USD 5.00"),
    ("5 dollars and 20 cents", "USD 5.20"),
    ("10 dollars", "USD 10.00"),
    ("£3 12s 6d", "GBP 3.62"),
    ("£3.12.6", "GBP 3.62"),
    ("L5 10s", "GBP 5.50"),
    ("12s 6d", "GBP 0.62"),
    ("£100", "GBP 100.00"),
]


class TestAmountExtraction:
    @pytest.mark.parametrize("text,expected_normalized", AMOUNT_CASES)
    def test_extracts_and_normalizes(self, text, expected_normalized):
        amounts = _only(text, "amount")
        assert len(amounts) == 1
        assert amounts[0].normalized_value == expected_normalized

    def test_dot_separated_and_letter_separated_gbp_agree(self):
        letters = _only("£3 12s 6d", "amount")[0]
        dots = _only("£3.12.6", "amount")[0]
        assert letters.normalized_value == dots.normalized_value

    def test_bare_shillings_pence_has_lower_confidence_than_full_pounds(self):
        full = _only("£3 12s 6d", "amount")[0]
        bare = _only("12s 6d", "amount")[0]
        assert bare.confidence < full.confidence

    def test_lowercase_l_is_not_misread_as_currency_symbol(self):
        """Lowercase 'l' is reserved for the OCR digit-confusion table
        (stand-in for '1'); it must not also trigger the £->L heuristic,
        or an OCR-mangled year fragment like "l8" would misparse as £8.
        """
        assert _only("Born l8 April 1850.", "amount") == []

    def test_implausible_shillings_value_is_rejected(self):
        assert _only("£3 25s 6d", "amount") == []


# --------------------------------------------------------------------------
# Persons: base spaCy NER, various name shapes.
# --------------------------------------------------------------------------

PERSON_CASES = [
    ("John Smith signed the document.", "John Smith"),
    ("Mary Ann Jones received the payment.", "Mary Ann Jones"),
    ("Robert O. Williams travelled abroad.", "Robert O. Williams"),
    ("Thomas Edward Brown was present.", "Thomas Edward Brown"),
    ("Signed, Elizabeth Carter.", "Elizabeth Carter"),
    ("Witness: George Henry Miller.", "George Henry Miller"),
    ("Dr. William Harrison attended.", "William Harrison"),
    ("Sarah Jane Wilkins departed.", "Sarah Jane Wilkins"),
    ("Charles Dickens wrote a letter.", "Charles Dickens"),
    ("James Alexander Fraser was born.", "James Alexander Fraser"),
]


class TestPersonExtraction:
    @pytest.mark.parametrize("text,expected_name", PERSON_CASES)
    def test_extracts_full_name(self, text, expected_name):
        persons = _only(text, "person")
        assert len(persons) == 1
        assert persons[0].normalized_value == expected_name


# --------------------------------------------------------------------------
# Locations: base NER + gazetteer disambiguation (exact, fuzzy, and
# recovery from base-model mislabeling/missed detection).
# --------------------------------------------------------------------------

LOCATION_CASES = [
    ("She sailed from Bombay to England.", "Bombay", "Mumbai"),
    ("Goods shipped from Ceyion in March.", "Ceyion", "Sri Lanka"),  # fuzzy OCR typo
    ("Trade routes to Calcutta expanded.", "Calcutta", "Kolkata"),  # base model mislabels as PRODUCT
    ("He lived in Constantinople for years.", "Constantinople", "Istanbul"),
    ("Reports arrived from Peking.", "Peking", "Beijing"),
    ("The Canton trade was profitable.", "Canton", "Guangzhou"),
    ("Settlers moved to Rhodesia.", "Rhodesia", "Zimbabwe"),
    ("Merchants traded via Siam.", "Siam", "Thailand"),  # base model gives no entity at all
    ("She was born in Persia.", "Persia", "Iran"),
    ("They fled to Saigon in 1954.", "Saigon", "Ho Chi Minh City"),
]


class TestLocationExtraction:
    @pytest.mark.parametrize("text,raw,expected_canonical", LOCATION_CASES)
    def test_disambiguates_to_canonical_name(self, text, raw, expected_canonical):
        locations = _only(text, "location")
        matches = [loc for loc in locations if loc.raw_text == raw]
        assert len(matches) == 1
        assert matches[0].normalized_value == expected_canonical

    def test_place_not_in_gazetteer_falls_back_to_title_case(self):
        locations = _only("She sailed from Bombay to England.", "location")
        england = next(loc for loc in locations if loc.raw_text == "England")
        assert england.normalized_value == "England"
        assert england.confidence < 0.7

    def test_gazetteer_hit_has_higher_confidence_than_fallback(self):
        locations = _only("She sailed from Bombay to England.", "location")
        by_text = {loc.raw_text: loc for loc in locations}
        assert by_text["Bombay"].confidence > by_text["England"].confidence


# --------------------------------------------------------------------------
# Cross-cutting: confidence blending, region_bbox propagation, empty input.
# --------------------------------------------------------------------------


class TestCrossCutting:
    def test_ocr_confidence_lowers_combined_confidence(self):
        text = "John Smith signed on 3 March 1897."
        without_ocr = extract_entities(text)
        with_low_ocr = extract_entities(text, ocr_confidence=50.0)

        by_text_a = {e.raw_text: e.confidence for e in without_ocr}
        by_text_b = {e.raw_text: e.confidence for e in with_low_ocr}
        for raw_text, confidence_a in by_text_a.items():
            assert by_text_b[raw_text] < confidence_a
            assert by_text_b[raw_text] == pytest.approx(confidence_a * 0.5, abs=1e-6)

    def test_region_bbox_is_attached_to_every_entity(self):
        bbox = (5, 10, 200, 40)
        entities = extract_entities("John Smith paid $5 on 3 March 1897.", region_bbox=bbox)
        assert entities
        assert all(e.region_bbox == bbox for e in entities)

    def test_region_bbox_defaults_to_none(self):
        entities = extract_entities("John Smith paid $5.")
        assert all(e.region_bbox is None for e in entities)

    def test_empty_text_returns_no_entities(self):
        assert extract_entities("") == []
        assert extract_entities("   ") == []

    def test_text_with_no_recognizable_entities_returns_empty(self):
        assert extract_entities("the quick brown fox jumps") == []
