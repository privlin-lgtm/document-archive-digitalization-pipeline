from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
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

    # Storage backend for raw/processed images. `s3` is a selectable value
    # but not implemented yet (ingestion.upload.save_raw_image raises
    # NotImplementedError) -- the validator below fails at settings-load
    # time (app startup), not on the first upload request, so a
    # misconfiguration is caught immediately rather than after real traffic
    # has already tried to use it.
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_path: str = "./data/documents"
    storage_s3_bucket: str | None = None
    storage_s3_region: str | None = None
    max_upload_size_bytes: int = 50 * 1024 * 1024  # 50 MB
    # Caps the number of files accepted in a single POST /documents request
    # (independent of per-file size) -- without this, one request could
    # enqueue an unbounded number of pipeline jobs.
    max_files_per_upload: int = 20
    # Reject decoded images above this many pixels (decompression-bomb cap).
    max_image_pixels: int = 40_000_000
    # When True (production default), Redis rate-limit failures reject
    # mutating requests instead of failing open. None means "derive from
    # app_env" (see the validator below) -- .env.example ships this blank
    # on purpose, to document the setting without forcing a value.
    rate_limit_fail_closed_mutating: bool | None = None

    @field_validator("rate_limit_fail_closed_mutating", mode="before")
    @classmethod
    def _blank_env_value_means_unset(cls, v: object) -> object:
        """pydantic-settings treats an env var that's *present but empty*
        differently from one that's *absent*: an empty string still gets
        handed to bool validation and fails outright, rather than falling
        through to the field's default -- verified live, this crashes
        Settings() on startup with a stock .env.example (which ships
        RATE_LIMIT_FAIL_CLOSED_MUTATING= blank, exactly so this stays
        documented but unset by default). Treat blank the same as unset.
        """
        return None if v == "" else v
    # Comma-separated CIDRs or "any" of reverse proxies allowed to set
    # X-Forwarded-For. Empty means use request.client.host only.
    trusted_proxy_ips: list[str] = Field(default_factory=list)

    # Celery / broker
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # API rate limiting (review_api.main) -- a separate Redis DB index from
    # the Celery broker/result-backend above, so rate-limit keys never
    # collide with queue/result data even when all three point at the same
    # Redis instance (the common case, per docker-compose.yml). One uniform
    # limit for every route (see review_api/rate_limit.py's docstring for
    # why a per-route stricter limit on uploads was tried and dropped).
    rate_limit_redis_url: str = "redis://localhost:6379/2"
    rate_limit_default: str = "60/minute"

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

    # Origins allowed to call the API from a browser (the web/ annotation UI
    # dev server by default). A JSON array in the env var, e.g.
    # CORS_ALLOWED_ORIGINS=["http://localhost:5173","https://review.example.com"]
    cors_allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # App
    app_env: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"

    @model_validator(mode="after")
    def _reject_unimplemented_storage_backend(self) -> "Settings":
        if self.storage_backend == "s3":
            raise ValueError(
                "STORAGE_BACKEND=s3 is not implemented yet (ingestion.upload.save_raw_image "
                "only handles 'local') -- set STORAGE_BACKEND=local, or implement S3 support "
                "before deploying with this setting."
            )
        return self

    @model_validator(mode="after")
    def _reject_insecure_production_config(self) -> "Settings":
        """Fails app startup rather than letting an obviously-unsafe config
        (checked-in local-dev defaults, a trivially short token) reach
        production silently -- these are exactly the kind of thing a code
        comment saying "change this before deploying" doesn't actually
        enforce.
        """
        if self.app_env != "production":
            return self

        if "postgres:postgres@" in self.database_url:
            raise ValueError(
                "DATABASE_URL still uses the local-dev default postgres/postgres credentials "
                "in a production environment (APP_ENV=production) -- rotate them first."
            )
        if len(self.review_api_token) < 32:
            raise ValueError(
                "REVIEW_API_TOKEN is shorter than 32 characters in a production environment "
                "(APP_ENV=production) -- generate a proper one: "
                "python -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )
        if any(origin.strip() == "*" for origin in self.cors_allowed_origins):
            raise ValueError(
                "CORS_ALLOWED_ORIGINS contains '*' in a production environment "
                "(APP_ENV=production) -- list the actual allowed origins explicitly."
            )
        return self

    @model_validator(mode="after")
    def _default_rate_limit_fail_closed(self) -> "Settings":
        if self.rate_limit_fail_closed_mutating is None:
            object.__setattr__(self, "rate_limit_fail_closed_mutating", self.app_env == "production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
