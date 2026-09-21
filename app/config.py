"""Configuração da aplicação via pydantic-settings."""

from functools import lru_cache

from pydantic import Field
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
