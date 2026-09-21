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

    # --- AdvCRM AI runtime (Fase 1.1) ---
    ai_runtime_enabled: bool = False
    ai_runtime_required: bool = False
    ai_runtime_base_url: str = "http://localhost:8001"
    ai_runtime_chat_completions_path: str = "/v1/chat/completions"
    ai_runtime_model: str | None = None
    ai_runtime_api_key: str | None = None
    ai_runtime_timeout_seconds: float = Field(default=60.0, gt=0.0, le=600.0)
    ai_runtime_connect_timeout_seconds: float = Field(default=5.0, gt=0.0, le=60.0)
    ai_runtime_max_retries: int = Field(default=2, ge=0, le=5)
    ai_runtime_retry_after_cap_seconds: float = Field(default=5.0, gt=0.0, le=60.0)
    ai_runtime_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    ai_runtime_max_tokens: int | None = Field(default=None, ge=1)
    ai_runtime_verify_tls: bool = True
    ai_runtime_prompt_version: str = "triage_prompts.v1"
    ai_runtime_health_path: str | None = None
    ai_context_max_chars: int = Field(default=50_000, ge=1_000, le=500_000)

    @field_validator(
        "ai_runtime_model",
        "ai_runtime_api_key",
        "ai_runtime_health_path",
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

    @field_validator("ai_runtime_chat_completions_path", "ai_runtime_health_path")
    @classmethod
    def path_must_start_with_slash(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if not value.startswith("/"):
            raise ValueError("path do runtime deve começar com /")
        return value

    @model_validator(mode="after")
    def validate_runtime_flags(self) -> Settings:
        if self.ai_runtime_enabled and not (
            self.ai_runtime_model and self.ai_runtime_model.strip()
        ):
            raise ValueError("AI_RUNTIME_MODEL é obrigatório quando AI_RUNTIME_ENABLED=true")
        if self.ai_runtime_required and not self.ai_runtime_health_path:
            raise ValueError("AI_RUNTIME_HEALTH_PATH é obrigatório quando AI_RUNTIME_REQUIRED=true")
        if not (0.0 <= self.confidence_medium_threshold <= self.confidence_high_threshold <= 1.0):
            raise ValueError("Limiares de confiança inconsistentes")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
