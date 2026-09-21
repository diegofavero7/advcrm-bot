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
    for key in dumped:
        assert "password" not in key.lower()
        assert "secret" not in key.lower()
        assert "token" not in key.lower()
