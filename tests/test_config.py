import pytest

from config import Settings

BASE_KWARGS = {
    "database_url": "postgresql+psycopg://user:realpassword@db.internal:5432/archive",
    "review_api_token": "a" * 32,
}


class TestStorageBackendValidation:
    def test_local_backend_is_accepted(self):
        settings = Settings(**BASE_KWARGS, storage_backend="local")
        assert settings.storage_backend == "local"

    def test_s3_backend_is_rejected_at_settings_load_time(self):
        """save_raw_image only implements 'local' -- s3 must fail fast at
        startup, not silently accept the config and only fail on the first
        upload request.
        """
        with pytest.raises(ValueError, match="not implemented"):
            Settings(**BASE_KWARGS, storage_backend="s3")


class TestProductionConfigGuard:
    def test_development_env_allows_default_db_credentials(self):
        settings = Settings(
            database_url="postgresql+psycopg://postgres:postgres@db:5432/document_archive",
            review_api_token="short",
            app_env="development",
        )
        assert settings.app_env == "development"

    def test_production_env_rejects_default_db_credentials(self):
        with pytest.raises(ValueError, match="postgres/postgres"):
            Settings(
                database_url="postgresql+psycopg://postgres:postgres@db:5432/document_archive",
                review_api_token="a" * 32,
                app_env="production",
            )

    def test_production_env_rejects_short_token(self):
        with pytest.raises(ValueError, match="REVIEW_API_TOKEN"):
            Settings(**BASE_KWARGS | {"review_api_token": "too-short"}, app_env="production")

    def test_production_env_rejects_wildcard_cors(self):
        with pytest.raises(ValueError, match="CORS_ALLOWED_ORIGINS"):
            Settings(**BASE_KWARGS, app_env="production", cors_allowed_origins=["*"])

    def test_production_env_accepts_a_hardened_config(self):
        settings = Settings(
            **BASE_KWARGS,
            app_env="production",
            cors_allowed_origins=["https://review.example.com"],
        )
        assert settings.app_env == "production"
        assert settings.rate_limit_fail_closed_mutating is True

    def test_development_rate_limit_fails_open_by_default(self):
        settings = Settings(**BASE_KWARGS, app_env="development")
        assert settings.rate_limit_fail_closed_mutating is False


class TestBlankRateLimitFailClosedEnvVar:
    """.env.example ships RATE_LIMIT_FAIL_CLOSED_MUTATING= (blank), so the
    setting stays documented without forcing a value. Passing a kwarg
    doesn't exercise pydantic-settings' actual env-var string coercion, so
    these go through the real path (an environment variable) that broke:
    Settings() crashed on startup with pydantic_core.ValidationError
    (bool_parsing) against a stock .env.example, reproduced live via
    `docker compose up` before this fix.
    """

    def test_blank_env_var_is_treated_as_unset_not_invalid(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_FAIL_CLOSED_MUTATING", "")
        monkeypatch.setenv("DATABASE_URL", BASE_KWARGS["database_url"])
        monkeypatch.setenv("REVIEW_API_TOKEN", BASE_KWARGS["review_api_token"])
        monkeypatch.setenv("APP_ENV", "development")

        settings = Settings(_env_file=None)

        assert settings.rate_limit_fail_closed_mutating is False  # derived from app_env, not left blank/invalid

    def test_a_real_boolean_env_var_still_works(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_FAIL_CLOSED_MUTATING", "true")
        monkeypatch.setenv("DATABASE_URL", BASE_KWARGS["database_url"])
        monkeypatch.setenv("REVIEW_API_TOKEN", BASE_KWARGS["review_api_token"])
        monkeypatch.setenv("APP_ENV", "development")

        settings = Settings(_env_file=None)

        assert settings.rate_limit_fail_closed_mutating is True
