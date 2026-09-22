"""Testes de privacidade e fail-closed."""

from __future__ import annotations

from app.config import Settings, clear_settings_cache
from app.policies import fail_closed_on_invalid


def test_fail_closed_rejects_invalid_contracts() -> None:
    assert fail_closed_on_invalid(contract_valid=False, settings=Settings(fail_closed=True))
    assert not fail_closed_on_invalid(contract_valid=False, settings=Settings(fail_closed=False))


def test_settings_do_not_embed_secrets(monkeypatch: object) -> None:
    """Defaults do código não embutem segredos; isolamento de .env local."""
    clear_settings_cache()
    monkeypatch.delenv("AI_RUNTIME_API_KEY", raising=False)  # type: ignore[attr-defined]
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    dumped = settings.model_dump()
    assert dumped.get("ai_runtime_api_key") in (None, "")
    for key, value in dumped.items():
        if key in {"ai_runtime_api_key"}:
            continue
        if isinstance(value, str):
            assert "sk-" not in value.lower()
            assert "password" not in value.lower()
