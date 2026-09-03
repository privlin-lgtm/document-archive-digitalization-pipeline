"""Shared pytest fixtures.

`sqlite_engine` builds a SQLite-backed schema mirroring storage/models.py
closely enough for the ORM (documents/pages/regions/ocr_results/entities/
entity_corrections/review_flags) to work against it — used by tests that
don't need genuinely Postgres-only features (tsvector full-text search,
BRIN, pg_trgm; those live in test_queries.py and test_review_api.py's
search tests, gated on a real Postgres being reachable).

Raw SQL rather than `Base.metadata.create_all()`, because two columns use
Postgres-only types that don't exist in SQLite at all:
`pages.full_text_search` (TSVECTOR + a `to_tsvector(...)` generated
expression) and `ocr_results.notes` (ARRAY). Both are left as plain
nullable TEXT here — fine for tests that don't exercise search or notes.
"""

import pytest
import redis
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from config import get_settings


@pytest.fixture(autouse=True)
def _reset_rate_limit_redis_state():
    """review_api.rate_limit is process-wide state keyed by (client_ip,
    path, window); FastAPI's TestClient reports the same fake client host
    for every test, so unrelated tests hitting the same route within the
    same window share a bucket. That's mostly harmless against the general
    60/minute default, but /auth/login's much stricter 5/minute limit makes
    it a real flakiness risk wherever a live Redis is actually reachable
    (e.g. CI's redis service) -- reset the bucket before every test rather
    than only inside test_rate_limit.py's own gated integration class.
    A no-op (not a skip) when Redis isn't reachable, matching how the rate
    limiter itself fails open in that case.
    """
    try:
        client = redis.from_url(get_settings().rate_limit_redis_url, socket_connect_timeout=0.5)
        client.ping()
        client.flushdb()
    except Exception:  # noqa: BLE001, S110 - any connectivity failure means "skip", not "fail"
        pass
    yield

_SQLITE_SCHEMA = """
CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    upload_time DATETIME NOT NULL,
    status TEXT NOT NULL,
    raw_image_path TEXT NOT NULL,
    processed_image_path TEXT,
    error_message TEXT
);

CREATE TABLE pages (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    full_text TEXT,
    full_text_search TEXT,
    processed_image_path TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(document_id, page_number)
);

CREATE TABLE regions (
    id TEXT PRIMARY KEY,
    page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    bbox_x INTEGER NOT NULL,
    bbox_y INTEGER NOT NULL,
    bbox_w INTEGER NOT NULL,
    bbox_h INTEGER NOT NULL,
    region_type TEXT NOT NULL,
    reading_order INTEGER NOT NULL,
    confidence REAL NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE ocr_results (
    id TEXT PRIMARY KEY,
    region_id TEXT NOT NULL UNIQUE REFERENCES regions(id) ON DELETE CASCADE,
    engine TEXT NOT NULL,
    text TEXT NOT NULL,
    confidence REAL NOT NULL,
    status TEXT NOT NULL,
    notes TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    region_id TEXT NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    normalized_value TEXT,
    confidence REAL NOT NULL,
    start_char INTEGER NOT NULL,
    end_char INTEGER NOT NULL,
    date_value DATE,
    amount_value NUMERIC,
    amount_currency TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE entity_corrections (
    id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    original_value TEXT,
    corrected_value TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE review_flags (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_id TEXT REFERENCES pages(id) ON DELETE CASCADE,
    region_id TEXT REFERENCES regions(id) ON DELETE CASCADE,
    entity_id TEXT REFERENCES entities(id) ON DELETE CASCADE,
    flag_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    explanation TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    status_changed_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture()
def sqlite_engine(tmp_path):
    """A real on-disk SQLite file, not `:memory:` + StaticPool.

    A shared in-memory DB with StaticPool hands every session the *same*
    underlying DBAPI connection object; the stdlib sqlite3 module doesn't
    support genuinely concurrent execute() calls against one connection from
    multiple threads even with check_same_thread=False, and would
    intermittently raise "bad parameter or other API misuse" under real
    concurrency (reproduced via tests/test_batch.py's ThreadPoolExecutor-
    based ingest_directory tests). A temp file gives each session its own
    connection, so SQLite's own file-level locking serializes concurrent
    writers correctly instead of the two connections corrupting each
    other's DBAPI-level state.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    with engine.begin() as conn:
        for statement in _SQLITE_SCHEMA.strip().split(";\n\n"):
            if statement.strip():
                conn.exec_driver_sql(statement)
    return engine


@pytest.fixture()
def sqlite_session_factory(sqlite_engine):
    return sessionmaker(bind=sqlite_engine)
