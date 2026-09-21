"""Testes do cliente AI runtime com MockTransport."""

from __future__ import annotations

import json

import httpx
import pytest
from app.clients.ai_runtime import AiRuntimeClient
from app.clients.errors import (
    AiRuntimeAuthenticationError,
    AiRuntimeInvalidJsonError,
    AiRuntimeProtocolError,
    AiRuntimeRateLimitError,
    AiRuntimeRefusalError,
    AiRuntimeTruncatedError,
)
from app.clients.schemas import SCHEMA_NAME_LEAD, get_lead_understanding_schema
from app.config import Settings, clear_settings_cache


def _settings(**kwargs: object) -> Settings:
    clear_settings_cache()
    base = {
        "ai_runtime_enabled": True,
        "ai_runtime_model": "test-model",
        "ai_runtime_base_url": "http://testserver",
        "ai_runtime_max_retries": 2,
        "ai_runtime_retry_after_cap_seconds": 0.01,
        "ai_runtime_timeout_seconds": 5.0,
        "ai_runtime_connect_timeout_seconds": 1.0,
    }
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def _ok_body(content: dict, *, finish_reason: str = "stop") -> dict:
    return {
        "id": "req-1",
        "model": "test-model",
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": json.dumps(content)},
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


@pytest.mark.asyncio
async def test_payload_nested_response_format() -> None:
    settings = _settings()
    client = AiRuntimeClient(settings)
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    payload = client.build_chat_payload(
        schema_name=SCHEMA_NAME_LEAD,
        json_schema=schema,
        messages=[{"role": "user", "content": "x"}],
    )
    rf = payload["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["name"] == SCHEMA_NAME_LEAD
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"] == schema
    assert "Authorization" not in client._headers()


@pytest.mark.asyncio
async def test_bearer_header_without_logging_key() -> None:
    settings = _settings(ai_runtime_api_key="secret-key")
    client = AiRuntimeClient(settings)
    assert client._headers()["Authorization"] == "Bearer secret-key"


@pytest.mark.asyncio
async def test_success_and_retry_count_additional_only() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(200, json=_ok_body({"schema_version": "lead_understanding.v1"}))

    transport = httpx.MockTransport(handler)
    settings = _settings(ai_runtime_max_retries=2)
    async with AiRuntimeClient(settings, transport=transport) as client:
        result = await client.complete_structured(
            schema_name=SCHEMA_NAME_LEAD,
            json_schema=get_lead_understanding_schema(),
            messages=[{"role": "user", "content": "{}"}],
            contract_label="lead_understanding",
        )
    assert result.retry_count == 1
    assert result.finish_reason == "stop"
    assert result.parsed_content["schema_version"] == "lead_understanding.v1"


@pytest.mark.asyncio
async def test_no_retry_on_400() -> None:
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        return httpx.Response(400, json={"error": "x"})

    async with AiRuntimeClient(
        _settings(ai_runtime_max_retries=3), transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(AiRuntimeProtocolError):
            await client.complete_structured(
                schema_name=SCHEMA_NAME_LEAD,
                json_schema={"type": "object"},
                messages=[{"role": "user", "content": "x"}],
                contract_label="lead_understanding",
            )
    assert counter["n"] == 1


@pytest.mark.asyncio
async def test_no_retry_on_401() -> None:
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        return httpx.Response(401, json={"error": "x"})

    async with AiRuntimeClient(
        _settings(ai_runtime_max_retries=3), transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(AiRuntimeAuthenticationError):
            await client.complete_structured(
                schema_name=SCHEMA_NAME_LEAD,
                json_schema={"type": "object"},
                messages=[{"role": "user", "content": "x"}],
                contract_label="lead_understanding",
            )
    assert counter["n"] == 1


@pytest.mark.asyncio
async def test_retry_429_with_capped_retry_after() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"Retry-After": "100"}, json={})
        return httpx.Response(200, json=_ok_body({"ok": True}))

    transport = httpx.MockTransport(handler)
    async with AiRuntimeClient(
        _settings(ai_runtime_max_retries=2, ai_runtime_retry_after_cap_seconds=0.01),
        transport=transport,
    ) as client:
        result = await client.complete_structured(
            schema_name=SCHEMA_NAME_LEAD,
            json_schema={"type": "object"},
            messages=[{"role": "user", "content": "x"}],
            contract_label="lead_understanding",
        )
    assert result.retry_count == 2


@pytest.mark.asyncio
async def test_finish_reason_length_and_refusal_and_unknown() -> None:
    async def run(finish: str, exc: type[Exception]) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_ok_body({"a": 1}, finish_reason=finish))

        async with AiRuntimeClient(_settings(), transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(exc):
                await client.complete_structured(
                    schema_name=SCHEMA_NAME_LEAD,
                    json_schema={"type": "object"},
                    messages=[{"role": "user", "content": "x"}],
                    contract_label="lead_understanding",
                )

    await run("length", AiRuntimeTruncatedError)
    await run("refusal", AiRuntimeRefusalError)
    await run("weird", AiRuntimeProtocolError)


@pytest.mark.asyncio
async def test_empty_choices_and_invalid_json_content() -> None:
    def empty_choices(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [], "model": "m"})

    async with AiRuntimeClient(_settings(), transport=httpx.MockTransport(empty_choices)) as client:
        with pytest.raises(AiRuntimeProtocolError):
            await client.complete_structured(
                schema_name=SCHEMA_NAME_LEAD,
                json_schema={"type": "object"},
                messages=[{"role": "user", "content": "x"}],
                contract_label="lead_understanding",
            )

    def bad_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "m",
                "choices": [{"finish_reason": "stop", "message": {"content": "{not-json"}}],
            },
        )

    async with AiRuntimeClient(_settings(), transport=httpx.MockTransport(bad_json)) as client:
        with pytest.raises(AiRuntimeInvalidJsonError):
            await client.complete_structured(
                schema_name=SCHEMA_NAME_LEAD,
                json_schema={"type": "object"},
                messages=[{"role": "user", "content": "x"}],
                contract_label="lead_understanding",
            )


@pytest.mark.asyncio
async def test_rate_limit_exhausted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "1"}, json={})

    async with AiRuntimeClient(
        _settings(ai_runtime_max_retries=0, ai_runtime_retry_after_cap_seconds=0.01),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(AiRuntimeRateLimitError):
            await client.complete_structured(
                schema_name=SCHEMA_NAME_LEAD,
                json_schema={"type": "object"},
                messages=[{"role": "user", "content": "x"}],
                contract_label="lead_understanding",
            )


@pytest.mark.asyncio
async def test_disabled_and_empty_content_and_message_refusal() -> None:
    clear_settings_cache()
    disabled = Settings(ai_runtime_enabled=False, ai_runtime_model="m")
    async with AiRuntimeClient(disabled) as client:
        from app.clients.errors import AiRuntimeDisabledError

        with pytest.raises(AiRuntimeDisabledError):
            await client.complete_structured(
                schema_name=SCHEMA_NAME_LEAD,
                json_schema={"type": "object"},
                messages=[{"role": "user", "content": "x"}],
                contract_label="lead_understanding",
            )

    def empty_content(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "m",
                "choices": [{"finish_reason": "stop", "message": {"content": "  "}}],
            },
        )

    async with AiRuntimeClient(_settings(), transport=httpx.MockTransport(empty_content)) as client:
        with pytest.raises(AiRuntimeProtocolError):
            await client.complete_structured(
                schema_name=SCHEMA_NAME_LEAD,
                json_schema={"type": "object"},
                messages=[{"role": "user", "content": "x"}],
                contract_label="lead_understanding",
            )

    def refusal_field(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "m",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "{}", "refusal": "nope"},
                    }
                ],
            },
        )

    async with AiRuntimeClient(_settings(), transport=httpx.MockTransport(refusal_field)) as client:
        with pytest.raises(AiRuntimeRefusalError):
            await client.complete_structured(
                schema_name=SCHEMA_NAME_LEAD,
                json_schema={"type": "object"},
                messages=[{"role": "user", "content": "x"}],
                contract_label="lead_understanding",
            )


@pytest.mark.asyncio
async def test_connection_error_retries_then_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    async with AiRuntimeClient(
        _settings(ai_runtime_max_retries=1),
        transport=httpx.MockTransport(handler),
    ) as client:
        from app.clients.errors import AiRuntimeConnectionError

        with pytest.raises(AiRuntimeConnectionError):
            await client.complete_structured(
                schema_name=SCHEMA_NAME_LEAD,
                json_schema={"type": "object"},
                messages=[{"role": "user", "content": "x"}],
                contract_label="lead_understanding",
            )
