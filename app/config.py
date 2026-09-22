"""Configuração da aplicação via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Limites e flags operacionais — valores não espalhados no código."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "advcrm-bot"
    app_env: str = "local"
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8080

    confidence_high_threshold: float = Field(default=0.80, ge=0.0, le=1.0)
    confidence_medium_threshold: float = Field(default=0.60, ge=0.0, le=1.0)
    max_automated_questions: int = Field(default=5, ge=1, le=20)
    taxonomy_version: str = "legal_subjects.v1"
    fail_closed: bool = True

    # --- AdvCRM AI runtime (Fase 1.1.1 — generate/structured) ---
    ai_runtime_enabled: bool = False
    ai_runtime_required: bool = False
    ai_runtime_base_url: str = "http://localhost:8001"
    ai_runtime_structured_generation_path: str = "/internal/v1/generate/structured"
    # Credencial S2S do runtime (ADVCRM_AI_INTERNAL_TOKEN no AdvCRM AI) — não a chave llama.cpp.
    ai_runtime_api_key: str | None = None
    # Organização confiável para CLI/runtime local. Não derivar de tenant_id sem mapeamento.
    ai_runtime_organization_id: str | None = None
    # Opcional — metadado de auditoria (X-AdvCRM-User-Id).
    ai_runtime_user_id: str | None = None
    # Modelo é escolhido pelo runtime; o bot apenas registra o valor retornado.
    ai_runtime_model: str | None = None
    ai_runtime_timeout_seconds: float = Field(default=60.0, gt=0.0, le=600.0)
    ai_runtime_connect_timeout_seconds: float = Field(default=5.0, gt=0.0, le=60.0)
    ai_runtime_max_retries: int = Field(default=2, ge=0, le=5)
    ai_runtime_retry_after_cap_seconds: float = Field(default=5.0, gt=0.0, le=60.0)
    ai_runtime_temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    # Proposta inicial para teste sintético (ainda sem evidência live de suficiência).
    # Limite do servidor AdvCRM AI: 1..2048 (padrão do servidor: 512).
    ai_runtime_max_tokens: int | None = Field(default=2048, ge=1, le=2048)
    ai_runtime_verify_tls: bool = True
    ai_runtime_prompt_version: str = "triage_prompts.v1"
    # Readiness do AdvCRM AI (/ready). Não usar /health como prova de disponibilidade do modelo.
    ai_runtime_ready_path: str | None = "/ready"
    ai_context_max_chars: int = Field(default=50_000, ge=1_000, le=500_000)
    # Contrato do extrator: schema JSON vai inline no POST (sem registro no AdvCRM AI).
    # v2 habilita cues/temporalidade; v1 permanece disponível sem inventar campos.
    ai_safety_signals_schema_version: str = "safety_signals.v2"

    @field_validator(
        "ai_runtime_model",
        "ai_runtime_api_key",
        "ai_runtime_organization_id",
        "ai_runtime_user_id",
        "ai_runtime_ready_path",
        mode="before",
    )
    @classmethod
    def empty_str_to_none(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator("ai_runtime_max_tokens", mode="before")
    @classmethod
    def empty_tokens_to_none(cls, value: object) -> object:
        if value == "" or value is None:
            return None
        return value

    @field_validator("ai_runtime_base_url")
    @classmethod
    def base_url_must_be_http(cls, value: str) -> str:
        stripped = value.rstrip("/")
        if not stripped.startswith(("http://", "https://")):
            raise ValueError("ai_runtime_base_url deve começar com http:// ou https://")
        return stripped

    @field_validator(
        "ai_runtime_structured_generation_path",
        "ai_runtime_ready_path",
    )
    @classmethod
    def path_must_start_with_slash(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if not value.startswith("/"):
            raise ValueError("path do runtime deve começar com /")
        return value

    @field_validator("ai_safety_signals_schema_version")
    @classmethod
    def safety_schema_version_allowed(cls, value: str) -> str:
        allowed = {"safety_signals.v1", "safety_signals.v2"}
        if value not in allowed:
            raise ValueError(
                "ai_safety_signals_schema_version deve ser safety_signals.v1 ou safety_signals.v2"
            )
        return value

    @model_validator(mode="after")
    def validate_runtime_flags(self) -> Settings:
        if self.ai_runtime_required and not self.ai_runtime_ready_path:
            raise ValueError("AI_RUNTIME_READY_PATH é obrigatório quando AI_RUNTIME_REQUIRED=true")
        if not (0.0 <= self.confidence_medium_threshold <= self.confidence_high_threshold <= 1.0):
            raise ValueError("Limiares de confiança inconsistentes")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
