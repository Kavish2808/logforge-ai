"""Application configuration, loaded from environment variables / .env."""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "LogForge AI"
    app_env: str = "development"
    api_v1_prefix: str = "/api/v1"
    debug: bool = True

    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    database_url: str = "postgresql+psycopg://logforge:logforge@localhost:5432/logforge"

    llm_provider: str = "anthropic"
    anthropic_api_key: str = ""

    # Phase 5 drift detection. DRIFT_ENABLED=false restores Phase 0-4
    # ingestion behavior exactly (no drift evaluation, no baselines).
    drift_enabled: bool = True
    # Events of a known vendor source whose structural similarity to the
    # source's baseline falls below this value are marked UNDER_REVIEW.
    drift_similarity_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    # Comma-separated OCSF targets treated as critical for every vendor
    # source; each is resolved to the vendor's raw field name through its
    # adapter YAML. Removing one, or changing its type, always forces drift
    # review. Vendors may add raw field names via `critical_fields` in YAML.
    drift_critical_fields: str = (
        "event_action,severity,network.src_ip,network.dst_ip,network.src_port,network.dst_port"
    )

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def drift_critical_fields_list(self) -> list[str]:
        return [name.strip() for name in self.drift_critical_fields.split(",") if name.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
