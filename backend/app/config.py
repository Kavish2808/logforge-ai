"""Application configuration, loaded from environment variables / .env."""
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "LogForge AI"
    app_env: str = "development"
    api_v1_prefix: str = "/api/v1"
    debug: bool = True

    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    database_url: str = "postgresql+psycopg://logforge:logforge@localhost:5432/logforge"

    # LLM — used ONLY to suggest adapters during onboarding, never at runtime.
    # "anthropic" uses Claude when ANTHROPIC_API_KEY is set, otherwise the
    # deterministic offline analyzer; "offline" never calls a network API.
    llm_provider: str = "anthropic"
    anthropic_api_key: str = ""
    onboarding_llm_model: str = "claude-opus-5"
    onboarding_llm_timeout_seconds: float = Field(default=120.0, gt=0, le=600)

    # Onboarding sandbox thresholds. Passing them only makes a proposal
    # *eligible* for human approval; it never activates anything.
    onboarding_min_match_rate: float = Field(default=0.90, ge=0.0, le=1.0)
    onboarding_reject_below_match_rate: float = Field(default=0.50, ge=0.0, le=1.0)
    onboarding_min_mapping_coverage: float = Field(default=0.30, ge=0.0, le=1.0)
    onboarding_recommended_samples: int = Field(default=10, ge=1, le=50)

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
    # A critical field whose value no longer fits its typed target (port 0..65535,
    # IP address, parseable timestamp) forces drift review even when the structure
    # is unchanged. false = structural-only Phase 5 behavior.
    drift_value_shape_enabled: bool = True

    # ---- Phase 7: trust / integration / governance layer (all additive) ----
    # Adaptive extension spill: extensions larger than this inline budget are
    # split; the overflow is stored losslessly in event_extension_overflow.
    extension_inline_max_bytes: int = Field(default=8192, ge=256, le=1_048_576)
    extension_inline_max_fields: int = Field(default=64, ge=1, le=10_000)
    # Repeated overflow of the same key structure becomes onboarding evidence.
    overflow_evidence_min_occurrences: int = Field(default=3, ge=1, le=100_000)

    # Cold raw vault (write-through, content-addressed). "filesystem" only in Phase 7.
    raw_vault_enabled: bool = True
    raw_vault_backend: str = "filesystem"
    raw_vault_path: str = "./data/raw_vault"
    # Anchor provider for sealed Merkle roots. Only "local_worm" exists in Phase 7.
    evidence_anchor_backend: str = "local_worm"

    # Merkle evidence chain + local WORM-style anchor store.
    evidence_anchor_path: str = "./data/evidence_anchors"
    merkle_batch_max_events: int = Field(default=10_000, ge=1, le=1_000_000)
    # Only events received at least this long ago are sealed by the scheduler.
    merkle_seal_grace_seconds: int = Field(default=60, ge=0, le=86_400)

    # RBAC: "permissive" = anonymous calls allowed (audited as anonymous), any
    # presented token fully enforced; "enforce" = governance actions require a token.
    rbac_mode: str = Field(default="permissive", pattern="^(permissive|enforce)$")
    auth_token_ttl_minutes: int = Field(default=720, ge=5, le=10_080)
    # Failed-login throttling (counted from the tamper-evident audit log).
    login_max_failures: int = Field(default=5, ge=1, le=100)
    login_lockout_minutes: int = Field(default=15, ge=1, le=1440)
    password_hash_iterations: int = Field(default=390_000, ge=1_000, le=5_000_000)

    # Review SLA (hours) by severity; runtime-overridable by SOC_ADMIN.
    sla_hours_critical: float = Field(default=4, gt=0, le=8760)
    sla_hours_high: float = Field(default=24, gt=0, le=8760)
    sla_hours_medium: float = Field(default=72, gt=0, le=8760)
    sla_hours_low: float = Field(default=168, gt=0, le=8760)

    # Export bounds.
    export_max_events: int = Field(default=50_000, ge=1, le=1_000_000)
    export_json_max_events: int = Field(default=5_000, ge=1, le=100_000)
    export_batch_size: int = Field(default=500, ge=10, le=5_000)

    # Background scheduler (seal, SLA sweep, alert sweep, vault backfill).
    scheduler_enabled: bool = True
    scheduler_interval_seconds: int = Field(default=60, ge=5, le=86_400)

    # Alert delivery adapters: all optional, disabled unless configured.
    alert_webhook_url: str = ""
    alert_slack_webhook_url: str = ""
    alert_teams_webhook_url: str = ""
    alert_smtp_host: str = ""
    alert_smtp_port: int = Field(default=25, ge=1, le=65535)
    alert_email_from: str = ""
    alert_email_to: str = ""
    alert_delivery_timeout_seconds: float = Field(default=5.0, gt=0, le=60)

    # ---- Phase 8: scale, advanced adaptation and resilience (additive) ----
    # false = register no Phase 8 guard / scheduler step / persist hook (Phase 7 behavior).
    phase8_enabled: bool = True
    # Step 6 shadow gate on learning activation: "off" | "if_present" (a shadow run
    # for the current proposal governs activation; none = Phase 6 behavior) |
    # "required" (activation needs a PASSED run, or SOC_ADMIN review for REVIEW_REQUIRED).
    phase8_shadow_gate: Literal["off", "if_present", "required"] = "if_present"

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in ("production", "prod")

    @model_validator(mode="after")
    def _production_requires_enforced_rbac(self) -> "Settings":
        # Phase 7 production gate: permissive RBAC (anonymous, audited governance
        # actions) is for development and Demo Mode only. A production process
        # refuses to start rather than silently accept unauthenticated approvals.
        if self.is_production and self.rbac_mode != "enforce":
            raise ValueError("APP_ENV=production requires RBAC_MODE=enforce (permissive mode is for development/demo only)")
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def drift_critical_fields_list(self) -> list[str]:
        return [name.strip() for name in self.drift_critical_fields.split(",") if name.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
