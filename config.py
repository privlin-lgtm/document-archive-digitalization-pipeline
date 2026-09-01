from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database — no default: a missing DATABASE_URL must fail startup loudly
    # rather than silently connecting with default credentials.
    database_url: str
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle_seconds: int = 1800

    # OCR
    ocr_engine: Literal["tesseract", "trocr", "textract"] = "tesseract"

    # Entity extraction: spaCy NER model. Default is fast with no heavy deps;
    # swap in en_core_web_trf via the `ner-transformer` extra for higher
    # accuracy (much larger install: torch + spacy-transformers + ~500MB model).
    ner_model: str = "en_core_web_sm"

    # Storage backend for raw/processed images
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_path: str = "./data/documents"
    storage_s3_bucket: str | None = None
    storage_s3_region: str | None = None
    max_upload_size_bytes: int = 50 * 1024 * 1024  # 50 MB

    # Celery / broker
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # Anomaly/review-flag thresholds (extraction/anomalies.py) — configurable
    # rather than hardcoded, since the "right" threshold depends on the
    # archive's actual scan/OCR quality and is expected to need tuning.
    low_ocr_confidence_threshold: float = 60.0  # 0-100, OCRResult/OCRWord scale
    illegible_confidence_threshold: float = 15.0  # 0-100
    illegible_alnum_ratio_threshold: float = 0.4  # fraction of non-whitespace chars that must be alnum
    entity_conflict_min_year: int = 1000
    entity_conflict_max_year: int = 2100
    entity_conflict_max_plausible_amount: float = 1_000_000.0
    entity_conflict_name_fuzzy_threshold: float = 0.75  # 0-1; below this, treated as different people

    # review_api auth: bearer token required on all routes except /health.
    # No default — must be set explicitly so the API can't come up unprotected.
    review_api_token: str

    # App
    app_env: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
