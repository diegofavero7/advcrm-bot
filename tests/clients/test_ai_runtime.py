"""Testes do cliente AdvCRM AI (generate/structured) com MockTransport."""

from __future__ import annotations

import json
import logging

import httpx
import pytest
from app.clients.ai_runtime import AiRuntimeClient
from app.clients.errors import (
    AiRuntimeAuthenticationError,
    AiRuntimeDisabledError,
    AiRuntimeInvalidJsonError,
    AiRuntimeProtocolError,
    AiRuntimeRateLimitError,
    AiRuntimeRefusalError,
    AiRuntimeTruncatedError,
)
from app.clients.schemas import SCHEMA_NAME_LEAD, get_lead_understanding_schema
from app.config import Settings, clear_settings_cache

ORG_A = "11111111-1111-4111-8111-111111111111"
ORG_B = "22222222-2222-4222-8222-222222222222"


def _settings(**kwargs: object) -> Settings:
    clear_settings_cache()
    base: dict[str, object] = {
        "ai_runtime_enabled": True,
        "ai_runtime_base_url": "http://testserver",
        "ai_runtime_api_key": "test-s2s-token",
        "ai_runtime_organization_id": ORG_A,
        "ai_runtime_max_retries": 2,
        "ai_runtime_retry_after_cap_seconds": 0.01,
        "ai_runtime_timeout_seconds": 5.0,
        "ai_runtime_connect_timeout_seconds": 1.0,
        "ai_runtime_max_tokens": 2048,
        "ai_runtime_temperature": 0.0,
        "ai_runtime_structured_generation_path": "/internal/v1/generate/structured",
        "ai_runtime_ready_path": "/ready",
    }
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def _ok_body(data: dict, *, finish_reason: str = "stop", model: str = "advcrm-qwen3-8b") -> dict:
    return {
        "data": data,
        "model": model,
        "finish_reason": finish_reason,
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _chat_completions_body(content: dict) -> dict:
    """Formato legado incorreto — deve ser rejeitado pelo adaptador."""
    return {
        "id": "req-1",
        "model": "test-model",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps(content)},
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


@pytest.mark.asyncio
async def test_chat_completions_response_is_incompatible() -> None:
    """Reproduz a incompatibilidade: envelope Chat Completions não é o contrato AdvCRM AI."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_chat_completions_body({"schema_version": "lead_understanding.v1"})
        )

    async with AiRuntimeClient(_settings(), transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AiRuntimeProtocolError, match="Chat Completions"):
            await client.complete_structured(
                schema_name=SCHEMA_NAME_LEAD,
                json_schema={"type": "object", "properties": {}},
                messages=[{"role": "user", "content": "x"}],
                contract_label="lead_understanding",
            )


@pytest.mark.asyncio
async def test_structured_endpoint_body_and_headers() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["authorization"] = request.headers.get("authorization")
        captured["org"] = request.headers.get("x-advcrm-organization-id")
        captured["user"] = request.headers.get("x-advcrm-user-id")
        body = json.loads(request.content.decode("utf-8"))
        captured["body"] = body
        return httpx.Response(200, json=_ok_body({"label": "ok"}))

    settings = _settings(ai_runtime_user_id="33333333-3333-4333-8333-333333333333")
    schema = {"type": "object", "properties": {"label": {"type": "string"}}}
    async with AiRuntimeClient(settings, transport=httpx.MockTransport(handler)) as client:
        result = await client.complete_structured(
            schema_name=SCHEMA_NAME_LEAD,
            json_schema=schema,
            messages=[
                {"role": "system", "content": "prompt"},
                {"role": "user", "content": "contexto"},
            ],
            contract_label="lead_understanding",
        )

    assert captured["path"] == "/internal/v1/generate/structured"
    assert captured["authorization"] == "Bearer test-s2s-token"
    assert captured["org"] == ORG_A
    assert captured["user"] == "33333333-3333-4333-8333-333333333333"
    body = captured["body"]
    assert isinstance(body, dict)
    assert set(body.keys()) == {"messages", "max_tokens", "temperature", "schema"}
    assert "model" not in body
    assert "response_format" not in body
    assert "strict" not in body
    assert "schema_name" not in body
    assert body["max_tokens"] == 2048
    assert body["temperature"] == 0.0
    assert body["schema"] == schema
    assert result.parsed_content == {"label": "ok"}
    assert result.model == "advcrm-qwen3-8b"
    assert result.finish_reason == "stop"
    assert result.usage is not None
    assert result.usage.total_tokens == 15


@pytest.mark.asyncio
async def test_organization_isolation_headers() -> None:
    orgs: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        orgs.append(request.headers["x-advcrm-organization-id"])
        return httpx.Response(200, json=_ok_body({"ok": True}))

    async with AiRuntimeClient(
        _settings(ai_runtime_organization_id=ORG_A),
        transport=httpx.MockTransport(handler),
    ) as client:
        await client.complete_structured(
            schema_name=SCHEMA_NAME_LEAD,
            json_schema={"type": "object"},
            messages=[{"role": "user", "content": "a"}],
            contract_label="lead_understanding",
            organization_id=ORG_A,
        )
        await client.complete_structured(
            schema_name=SCHEMA_NAME_LEAD,
            json_schema={"type": "object"},
            messages=[{"role": "user", "content": "b"}],
            contract_label="lead_understanding",
            organization_id=ORG_B,
        )
    assert orgs == [ORG_A, ORG_B]


@pytest.mark.asyncio
async def test_payload_limits_fail_closed_without_http() -> None:
    client = AiRuntimeClient(_settings())
    with pytest.raises(AiRuntimeProtocolError, match="messages"):
        client.build_structured_payload(json_schema={"type": "object"}, messages=[])
    with pytest.raises(AiRuntimeProtocolError, match="role"):
        client.build_structured_payload(
            json_schema={"type": "object"},
            messages=[{"role": "tool", "content": "x"}],
        )
    with pytest.raises(AiRuntimeProtocolError, match="content"):
        client.build_structured_payload(
            json_schema={"type": "object"},
            messages=[{"role": "user", "content": "x" * 32_001}],
        )
    with pytest.raises(AiRuntimeProtocolError, match="agregado"):
        client.build_structured_payload(
            json_schema={"type": "object"},
            messages=[
                {"role": "user", "content": "x" * 12_001},
                {"role": "user", "content": "y" * 12_001},
            ],
        )
    with pytest.raises(AiRuntimeProtocolError, match="AI_RUNTIME_MAX_TOKENS"):
        AiRuntimeClient(_settings(ai_runtime_max_tokens=None)).build_structured_payload(
            json_schema={"type": "object"},
            messages=[{"role": "user", "content": "x"}],
        )


@pytest.mark.asyncio
async def test_data_variants_and_missing_metadata() -> None:
    cases = [
        (
            {
                "model": "m",
                "finish_reason": "stop",
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            "data ausente",
        ),
        (
            {
                "data": None,
                "model": "m",
                "finish_reason": "stop",
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            "null",
        ),
        (
            {
                "data": "string",
                "model": "m",
                "finish_reason": "stop",
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            "string",
        ),
        (
            {
                "data": [],
                "model": "m",
                "finish_reason": "stop",
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            "array",
        ),
        (
            {
                "data": {"a": 1},
                "finish_reason": "stop",
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            "model",
        ),
        (
            {
                "data": {"a": 1},
                "model": "m",
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            "finish_reason",
        ),
        ({"data": {"a": 1}, "model": "m", "finish_reason": "stop"}, "usage"),
        (
            {
                "data": {"a": 1},
                "model": "m",
                "finish_reason": "stop",
                "usage": {"prompt_tokens": "1", "completion_tokens": 1, "total_tokens": 2},
            },
            "inteiro",
        ),
    ]

    for body, _needle in cases:

        def handler(request: httpx.Request, payload: dict = body) -> httpx.Response:
            return httpx.Response(200, json=payload)

        async with AiRuntimeClient(_settings(), transport=httpx.MockTransport(handler)) as client:
            with pytest.raises((AiRuntimeProtocolError, AiRuntimeInvalidJsonError)):
                await client.complete_structured(
                    schema_name=SCHEMA_NAME_LEAD,
                    json_schema={"type": "object"},
                    messages=[{"role": "user", "content": "x"}],
                    contract_label="lead_understanding",
                )


@pytest.mark.asyncio
async def test_invalid_http_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"not-json", headers={"content-type": "application/json"}
        )

    async with AiRuntimeClient(_settings(), transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AiRuntimeInvalidJsonError):
            await client.complete_structured(
                schema_name=SCHEMA_NAME_LEAD,
                json_schema={"type": "object"},
                messages=[{"role": "user", "content": "x"}],
                contract_label="lead_understanding",
            )


@pytest.mark.asyncio
async def test_finish_reason_length_refusal_unknown() -> None:
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
async def test_http_422_no_retry_and_no_body_leak(caplog: pytest.LogCaptureFixture) -> None:
    counter = {"n": 0}
    secret_input = "SEGredo-do-lead-nao-deve-aparecer-em-log"

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        return httpx.Response(
            422,
            json={"detail": [{"input": {"messages": [{"content": secret_input}]}}]},
        )

    with caplog.at_level(logging.INFO):
        async with AiRuntimeClient(
            _settings(ai_runtime_max_retries=3), transport=httpx.MockTransport(handler)
        ) as client:
            with pytest.raises(AiRuntimeProtocolError, match="422"):
                await client.complete_structured(
                    schema_name=SCHEMA_NAME_LEAD,
                    json_schema={"type": "object"},
                    messages=[{"role": "user", "content": "x"}],
                    contract_label="lead_understanding",
                )
    assert counter["n"] == 1
    assert secret_input not in caplog.text


@pytest.mark.asyncio
async def test_success_and_retry_count_additional_only() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={"detail": "Inferência indisponível."})
        return httpx.Response(200, json=_ok_body({"schema_version": "lead_understanding.v1"}))

    async with AiRuntimeClient(
        _settings(ai_runtime_max_retries=2), transport=httpx.MockTransport(handler)
    ) as client:
        result = await client.complete_structured(
            schema_name=SCHEMA_NAME_LEAD,
            json_schema=get_lead_understanding_schema(),
            messages=[{"role": "user", "content": "{}"}],
            contract_label="lead_understanding",
        )
    assert result.retry_count == 1
    assert result.parsed_content["schema_version"] == "lead_understanding.v1"


@pytest.mark.asyncio
async def test_no_retry_on_400_and_401() -> None:
    for status, exc in ((400, AiRuntimeProtocolError), (401, AiRuntimeAuthenticationError)):
        counter = {"n": 0}

        def make_handler(code: int, ctr: dict[str, int]) -> object:
            def handler(request: httpx.Request) -> httpx.Response:
                ctr["n"] += 1
                return httpx.Response(code, json={"detail": "x"})

            return handler

        async with AiRuntimeClient(
            _settings(ai_runtime_max_retries=3),
            transport=httpx.MockTransport(make_handler(status, counter)),  # type: ignore[arg-type]
        ) as client:
            with pytest.raises(exc):
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

    async with AiRuntimeClient(
        _settings(ai_runtime_max_retries=2, ai_runtime_retry_after_cap_seconds=0.01),
        transport=httpx.MockTransport(handler),
    ) as client:
        result = await client.complete_structured(
            schema_name=SCHEMA_NAME_LEAD,
            json_schema={"type": "object"},
            messages=[{"role": "user", "content": "x"}],
            contract_label="lead_understanding",
        )
    assert result.retry_count == 2


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
async def test_disabled_and_missing_organization() -> None:
    disabled = Settings(ai_runtime_enabled=False)
    async with AiRuntimeClient(disabled) as client:
        with pytest.raises(AiRuntimeDisabledError):
            await client.complete_structured(
                schema_name=SCHEMA_NAME_LEAD,
                json_schema={"type": "object"},
                messages=[{"role": "user", "content": "x"}],
                contract_label="lead_understanding",
            )

    async with AiRuntimeClient(
        _settings(ai_runtime_organization_id=None),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=_ok_body({}))),
    ) as client:
        with pytest.raises(AiRuntimeAuthenticationError, match="organization_id"):
            await client.complete_structured(
                schema_name=SCHEMA_NAME_LEAD,
                json_schema={"type": "object"},
                messages=[{"role": "user", "content": "x"}],
                contract_label="lead_understanding",
            )


@pytest.mark.asyncio
async def test_ready_check_uses_ready_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ready"
        assert request.method == "GET"
        return httpx.Response(200, json={"status": "ready"})

    async with AiRuntimeClient(
        _settings(ai_runtime_ready_path="/ready"),
        transport=httpx.MockTransport(handler),
    ) as client:
        await client.ready_check()


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


@pytest.mark.asyncio
async def test_model_not_required_when_enabled() -> None:
    """Adaptador structured: AI_RUNTIME_MODEL não é obrigatório nem enviado."""
    settings = _settings(ai_runtime_model=None)
    payload = AiRuntimeClient(settings).build_structured_payload(
        json_schema={"type": "object"},
        messages=[{"role": "user", "content": "x"}],
    )
    assert "model" not in payload


@pytest.mark.asyncio
async def test_structured_validation_failed_no_retry_sanitized_diag() -> None:
    from app.clients.errors import AiRuntimeStructuredValidationError

    counter = {"n": 0}
    leak = "confidence=1.5 secret-value"

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        return httpx.Response(
            502,
            headers={"x-request-id": "hdr-rid"},
            json={
                "detail": "Resposta de inferência inválida.",
                "code": "structured_validation_failed",
                "retryable": False,
                "request_id": "body-rid",
                "validator": "maximum",
                "instance_path": "confidence",
                "schema_path": "properties.confidence.maximum",
                "extra_leak": leak,
            },
        )

    async with AiRuntimeClient(
        _settings(ai_runtime_max_retries=3),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(AiRuntimeStructuredValidationError) as caught:
            await client.complete_structured(
                schema_name=SCHEMA_NAME_LEAD,
                json_schema={"type": "object"},
                messages=[{"role": "user", "content": "x"}],
                contract_label="lead_understanding",
            )

    assert counter["n"] == 1
    err = caught.value
    assert err.category == "structured_validation"
    assert err.retry_count == 0
    assert err.http_status == 502
    assert err.request_id == "body-rid"
    assert err.details
    diag = err.details[0]
    assert diag["code"] == "structured_validation_failed"
    assert diag["retryable"] is False
    assert diag["validator"] == "maximum"
    assert diag["instance_path"] == "confidence"
    assert diag["schema_path"] == "properties.confidence.maximum"
    assert "extra_leak" not in diag
    assert leak not in json.dumps(err.details)


@pytest.mark.asyncio
async def test_structured_validation_code_alone_no_retry() -> None:
    """code estável basta; retryable ausente não impede classificação tipada."""
    from app.clients.errors import AiRuntimeStructuredValidationError

    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        return httpx.Response(
            502,
            json={
                "detail": "Resposta de inferência inválida.",
                "code": "structured_validation_failed",
            },
        )

    async with AiRuntimeClient(
        _settings(ai_runtime_max_retries=3),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(AiRuntimeStructuredValidationError) as caught:
            await client.complete_structured(
                schema_name=SCHEMA_NAME_LEAD,
                json_schema={"type": "object"},
                messages=[{"role": "user", "content": "x"}],
                contract_label="lead_understanding",
            )
    assert counter["n"] == 1
    assert caught.value.category == "structured_validation"


@pytest.mark.asyncio
async def test_retryable_false_bool_no_retry_keeps_server_category() -> None:
    """retryable=false (bool) sem code: sem retry, categoria server — não schema."""
    from app.clients.errors import AiRuntimeServerError, AiRuntimeStructuredValidationError

    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        return httpx.Response(
            502,
            json={
                "detail": "Resposta de inferência inválida.",
                "retryable": False,
            },
        )

    async with AiRuntimeClient(
        _settings(ai_runtime_max_retries=3),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(AiRuntimeServerError) as caught:
            await client.complete_structured(
                schema_name=SCHEMA_NAME_LEAD,
                json_schema={"type": "object"},
                messages=[{"role": "user", "content": "x"}],
                contract_label="lead_understanding",
            )
    assert counter["n"] == 1
    assert caught.value.category == "server"
    assert not isinstance(caught.value, AiRuntimeStructuredValidationError)


@pytest.mark.asyncio
async def test_retryable_false_bool_on_503_no_retry_server() -> None:
    from app.clients.errors import AiRuntimeServerError

    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        return httpx.Response(503, json={"detail": "Inferência indisponível.", "retryable": False})

    async with AiRuntimeClient(
        _settings(ai_runtime_max_retries=3),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(AiRuntimeServerError) as caught:
            await client.complete_structured(
                schema_name=SCHEMA_NAME_LEAD,
                json_schema={"type": "object"},
                messages=[{"role": "user", "content": "x"}],
                contract_label="lead_understanding",
            )
    assert counter["n"] == 1
    assert caught.value.category == "server"


@pytest.mark.asyncio
async def test_retryable_string_false_still_retries() -> None:
    """String 'false' / ausente / inválido não bloqueiam retry."""
    cases: list[dict[str, object]] = [
        {"detail": "Resposta de inferência inválida.", "retryable": "false"},
        {"detail": "Resposta de inferência inválida.", "retryable": 0},
        {"detail": "Resposta de inferência inválida."},
    ]
    for body in cases:
        counter: dict[str, int] = {"n": 0}

        def make_handler(payload: dict[str, object], ctr: dict[str, int]) -> object:
            def handler(request: httpx.Request) -> httpx.Response:
                ctr["n"] += 1
                if ctr["n"] == 1:
                    return httpx.Response(502, json=payload)
                return httpx.Response(200, json=_ok_body({"ok": True}))

            return handler

        async with AiRuntimeClient(
            _settings(ai_runtime_max_retries=2),
            transport=httpx.MockTransport(make_handler(body, counter)),  # type: ignore[arg-type]
        ) as client:
            result = await client.complete_structured(
                schema_name=SCHEMA_NAME_LEAD,
                json_schema={"type": "object"},
                messages=[{"role": "user", "content": "x"}],
                contract_label="lead_understanding",
            )
        assert counter["n"] == 2, body
        assert result.retry_count == 1


@pytest.mark.asyncio
async def test_generic_502_still_retries_despite_detail_substring() -> None:
    """502 sem code estável: retry; não usar substring de detail."""
    from app.clients.errors import AiRuntimeServerError

    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        if counter["n"] == 1:
            return httpx.Response(
                502,
                json={
                    "detail": (
                        "Resposta de inferência inválida. structured_validation_failed no backend"
                    )
                },
            )
        return httpx.Response(200, json=_ok_body({"ok": True}))

    async with AiRuntimeClient(
        _settings(ai_runtime_max_retries=2),
        transport=httpx.MockTransport(handler),
    ) as client:
        result = await client.complete_structured(
            schema_name=SCHEMA_NAME_LEAD,
            json_schema={"type": "object"},
            messages=[{"role": "user", "content": "x"}],
            contract_label="lead_understanding",
        )

    assert counter["n"] == 2
    assert result.retry_count == 1
    assert result.parsed_content == {"ok": True}

    counter2 = {"n": 0}

    def always_502(request: httpx.Request) -> httpx.Response:
        counter2["n"] += 1
        return httpx.Response(502, json={"detail": "Resposta de inferência inválida."})

    async with AiRuntimeClient(
        _settings(ai_runtime_max_retries=2),
        transport=httpx.MockTransport(always_502),
    ) as client:
        with pytest.raises(AiRuntimeServerError):
            await client.complete_structured(
                schema_name=SCHEMA_NAME_LEAD,
                json_schema={"type": "object"},
                messages=[{"role": "user", "content": "x"}],
                contract_label="lead_understanding",
            )
    assert counter2["n"] == 3
