"""Testes de readiness com runtime opcional/obrigatório."""

from __future__ import annotations

import httpx
import pytest
from app.application import check_readiness
from app.config import Settings, clear_settings_cache
from fastapi.testclient import TestClient


def test_ready_without_runtime(client: TestClient) -> None:
    clear_settings_cache()
    response = client.get("/ready")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_runtime_required_needs_health_path() -> None:
    clear_settings_cache()
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(ai_runtime_required=True, ai_runtime_health_path=None)


@pytest.mark.asyncio
async def test_runtime_health_ok() -> None:
    clear_settings_cache()
    settings = Settings(
        ai_runtime_required=True,
        ai_runtime_health_path="/health",
        ai_runtime_base_url="http://testserver",
        ai_runtime_model="m",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200, json={"ok": True})

    from app.clients.ai_runtime import AiRuntimeClient

    client = AiRuntimeClient(settings, transport=httpx.MockTransport(handler))
    await client.health_check()
    await client.aclose()


def test_check_readiness_loads_schemas_and_prompts() -> None:
    clear_settings_cache()
    check_readiness(Settings())
