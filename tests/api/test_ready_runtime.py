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
async def test_runtime_required_needs_ready_path() -> None:
    clear_settings_cache()
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(ai_runtime_required=True, ai_runtime_ready_path=None)


@pytest.mark.asyncio
async def test_runtime_ready_ok() -> None:
    clear_settings_cache()
    settings = Settings(
        ai_runtime_required=True,
        ai_runtime_ready_path="/ready",
        ai_runtime_base_url="http://testserver",
        ai_runtime_api_key="test-s2s-token",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ready"
        assert request.method == "GET"
        # /ready no AdvCRM AI não exige S2S do cliente; Authorization pode ser enviado.
        return httpx.Response(200, json={"status": "ready"})

    from app.clients.ai_runtime import AiRuntimeClient

    client = AiRuntimeClient(settings, transport=httpx.MockTransport(handler))
    await client.ready_check()
    await client.aclose()


def test_check_readiness_loads_schemas_and_prompts() -> None:
    clear_settings_cache()
    check_readiness(Settings())
