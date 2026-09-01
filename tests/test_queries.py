"""Integration tests for storage/queries.py.

These need a live Postgres with the stage 5 schema and, ideally, the
hand-crafted seed rows from scripts/seed_synthetic_data.sql (the assertions
below target those specific rows for determinism). They're skipped
automatically when DATABASE_URL isn't reachable — the rest of the suite
stays infra-free by design, and CI/local dev without Docker running should
see a clean skip, not a failure.

To actually run these: start the stack (`docker compose up -d db`), apply
migrations, seed the data, then run pytest with DATABASE_URL pointed at a
reachable Postgres (e.g. temporarily publish db's port, or run inside the
compose network).
"""

import datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from config import get_settings
from storage import queries
from storage.models import EntityType


def _make_session_factory():
    try:
        engine = create_engine(get_settings().database_url, pool_pre_ping=True)
        with engine.connect() as conn:
            has_seed_data = conn.execute(
                text("SELECT 1 FROM documents WHERE filename = 'ledger_1897_smith_bombay.png'")
            ).first()
        if not has_seed_data:
            return None
    except Exception:  # noqa: BLE001 - any connectivity failure means "skip", not "fail"
        return None
    return sessionmaker(bind=engine)


_session_factory = _make_session_factory()

pytestmark = pytest.mark.skipif(
    _session_factory is None,
    reason="requires a live Postgres with the stage 5 schema + scripts/seed_synthetic_data.sql applied",
)


@pytest.fixture()
def session():
    db = _session_factory()
    try:
        yield db
    finally:
        db.close()


class TestFullTextSearch:
    def test_finds_and_ranks_the_seeded_document(self, session):
        results = queries.full_text_search(session, "John Smith Bombay")
        assert results
        top = results[0]
        assert top["filename"] == "ledger_1897_smith_bombay.png"
        assert top["rank"] > 0
        assert "<b>John</b>" in top["snippet"]
        assert "<b>Bombay</b>" in top["snippet"]

    def test_no_match_returns_empty(self, session):
        assert queries.full_text_search(session, "xyzzy_no_such_word_in_the_corpus") == []


class TestAmountsOverBetweenDates:
    def test_finds_seeded_amount_in_date_range(self, session):
        results = queries.amounts_over_between_dates(
            session, 50, datetime.date(1897, 1, 1), datetime.date(1897, 12, 31)
        )
        assert any(r["filename"] == "ledger_1897_smyth_calcutta.png" and r["amount_value"] == 120 for r in results)

    def test_excludes_amounts_below_threshold(self, session):
        results = queries.amounts_over_between_dates(
            session, 100, datetime.date(1897, 3, 1), datetime.date(1897, 3, 10)
        )
        # the $3.62 GBP seed amount is in this date's document but below the threshold
        assert all(r["amount_value"] > 100 for r in results)

    def test_currency_filter(self, session):
        results = queries.amounts_over_between_dates(
            session, 1, datetime.date(1897, 1, 1), datetime.date(1897, 12, 31), currency="GBP"
        )
        assert all(r["amount_currency"] == "GBP" for r in results)
        assert any(r["filename"] == "ledger_1897_smith_bombay.png" for r in results)


class TestPersonNearLocation:
    def test_finds_exact_match(self, session):
        results = queries.documents_mentioning_person_near_location(session, "John Smith", "Bombay")
        assert any(r["filename"] == "ledger_1897_smith_bombay.png" for r in results)

    def test_fuzzy_match_tolerates_ocr_errors_on_both_sides(self, session):
        """'Jon Smyth' (missing h) / 'Calcuta' (missing t) — a single-ish
        character error on each side — should still surface the real
        'John Smyth' / 'Calcutta' seed row.
        """
        results = queries.documents_mentioning_person_near_location(session, "Jon Smyth", "Calcuta")
        assert any(
            r["filename"] == "ledger_1897_smyth_calcutta.png"
            and r["person_text"] == "John Smyth"
            and r["location_text"] == "Calcutta"
            for r in results
        )

    def test_unrelated_names_return_nothing(self, session):
        results = queries.documents_mentioning_person_near_location(session, "Zzqxw Ptrlmnop", "Qxzzwv Blorf")
        assert results == []


class TestFuzzyEntitySearch:
    def test_finds_similar_person_name(self, session):
        results = queries.fuzzy_entity_search(session, "Jon Smyth", EntityType.person)
        assert any(r["raw_text"] == "John Smyth" for r in results)

    def test_respects_entity_type_filter(self, session):
        results = queries.fuzzy_entity_search(session, "Calcuta", EntityType.location)
        assert all(r for r in results)  # all rows are location entities by construction of the query
        assert any(r["raw_text"] == "Calcutta" for r in results)

    def test_ranks_closer_matches_first(self, session):
        results = queries.fuzzy_entity_search(session, "Jon Smyth", EntityType.person, limit=5)
        similarities = [r["similarity"] for r in results]
        assert similarities == sorted(similarities, reverse=True)
