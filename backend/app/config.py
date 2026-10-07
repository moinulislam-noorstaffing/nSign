"""Configuration. Everything from the environment, nothing hardcoded."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Offer Letter Studio"
    environment: str = "development"
    log_level: str = "info"

    # ── database ────────────────────────────────────────────────────────────
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_user: str = "studio"
    postgres_password: str = "studio"
    postgres_db: str = "studio"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # ── object storage (MinIO) ──────────────────────────────────────────────
    s3_endpoint_url: str = "http://minio:9000"
    s3_access_key: str = "studio"
    s3_secret_key: str = "studio"
    s3_bucket: str = "studio-documents"
    s3_region: str = "us-east-1"

    # ── ONLYOFFICE ──────────────────────────────────────────────────────────
    #: Where the BROWSER reaches the editor. Must be an address the user's
    #: machine can resolve — not the compose service name.
    onlyoffice_public_url: str = "http://localhost:8080"
    #: Where the API reaches it, and where IT reaches the API back. Document
    #: Server downloads the file itself, so this must be resolvable from inside
    #: the container network, never localhost.
    onlyoffice_internal_url: str = "http://onlyoffice"
    api_internal_url: str = "http://api:8000"
    #: Signs both directions. Document Server signs its callbacks too, and an
    #: integration that verifies only the outbound half leaves an
    #: unauthenticated file-write into the document store.
    onlyoffice_jwt_secret: str = ""

    # ── Gotenberg ───────────────────────────────────────────────────────────
    gotenberg_url: str = "http://gotenberg:3000"
    gotenberg_timeout: int = 120

    # ── PandaDoc ────────────────────────────────────────────────────────────
    pandadoc_api_key: str = ""
    pandadoc_base_url: str = "https://api.pandadoc.com/public/v1"

    # ── OpenAI ──────────────────────────────────────────────────────────────
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # ── limits ──────────────────────────────────────────────────────────────
    max_upload_bytes: int = 25 * 1024 * 1024
    max_logo_bytes: int = 4 * 1024 * 1024


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
