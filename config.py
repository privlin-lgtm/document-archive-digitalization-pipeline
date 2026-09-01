from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/document_archive"

    # OCR
    ocr_engine: Literal["tesseract", "trocr", "textract"] = "tesseract"

    # Storage backend for raw/processed images
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_path: str = "./data/documents"
    storage_s3_bucket: str | None = None
    storage_s3_region: str | None = None

    # Celery / broker
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # App
    app_env: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
