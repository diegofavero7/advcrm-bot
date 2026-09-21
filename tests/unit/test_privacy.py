"""Testes de privacidade e fail-closed."""

from __future__ import annotations

from app.config import Settings
from app.policies import fail_closed_on_invalid


def test_fail_closed_rejects_invalid_contracts() -> None:
    assert fail_closed_on_invalid(contract_valid=False, settings=Settings(fail_closed=True))
    assert not fail_closed_on_invalid(contract_valid=False, settings=Settings(fail_closed=False))


def test_settings_do_not_embed_secrets() -> None:
    settings = Settings()
    dumped = settings.model_dump()
    # Nomes de campos técnicos (ex. max_tokens) são ok; valores secretos não.
    assert dumped.get("ai_runtime_api_key") in (None, "")
    for key, value in dumped.items():
        if key in {"ai_runtime_api_key"}:
            continue
        if isinstance(value, str):
            assert "sk-" not in value.lower()
            assert "password" not in value.lower()
