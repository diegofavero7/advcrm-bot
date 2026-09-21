"""Cobertura adicional dos caminhos críticos da Fase 1.1."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from app.application.context_builder import build_next_step_context
from app.application.services import (
    LeadUnderstandingService,
    TriageNextStepService,
)
from app.clients.ai_runtime import AiRuntimeClient
from app.clients.errors import (
    AiRuntimeConnectionError,
    AiRuntimeDisabledError,
    AiRuntimeInvalidJsonError,
    AiRuntimeProtocolError,
    AiRuntimeSchemaValidationError,
    AiRuntimeSemanticValidationError,
    AiRuntimeServerError,
    AiRuntimeTimeoutError,
    ContextLimitExceededError,
)
from app.clients.schemas import SCHEMA_NAME_LEAD
from app.clients.types import StructuredCompletionResult
from app.config import Settings, clear_settings_cache
from app.domain.enums import ContentType, MessageDirection, MessageRole, TriageState
from app.prompts import clear_prompt_cache, load_lead_understanding_prompt
from app.schemas.inbound import ConversationMessage, PreviousDecisionSummary, TriageAnalysisRequest
from app.schemas.lead_understanding import LeadUnderstanding
from app.schemas.proposal import (
    ProposalError,
    ProposalMetadata,
    ProposalStatus,
    SafeFallback,
    TriageProposal,
)


def _settings(**kwargs: object) -> Settings:
    clear_settings_cache()
    base: dict[str, object] = {
        "ai_runtime_enabled": True,
        "ai_runtime_model": "test-model",
        "ai_runtime_base_url": "http://testserver",
        "ai_runtime_max_retries": 1,
        "ai_runtime_retry_after_cap_seconds": 0.01,
        "ai_runtime_timeout_seconds": 5.0,
        "ai_runtime_connect_timeout_seconds": 1.0,
    }
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def _request(**kwargs: object) -> TriageAnalysisRequest:
    data: dict[str, Any] = {
        "event_id": "e1",
        "tenant_id": "t",
        "lead_id": "l",
        "conversation_id": "c",
        "triage_state": TriageState.PENDING_CLASSIFICATION,
        "source": "whatsapp",
        "messages": [
            ConversationMessage(
                message_id="m1",
                role=MessageRole.LEAD,
                direction=MessageDirection.INBOUND,
                content_type=ContentType.TEXT,
                text="Meu cônjuge foi preso e quero auxílio-reclusão",
                created_at=datetime(2026, 9, 20, 14, 0, tzinfo=UTC),
                reply_to_message_id=None,
            )
        ],
        "known_facts": [],
        "previous_decision": None,
    }
    data.update(kwargs)
    return TriageAnalysisRequest.model_validate(data)


def _understanding_payload(**overrides: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": "lead_understanding.v1",
        "intent": "new_legal_lead",
        "language": "pt-BR",
        "primary_area": "social_security",
        "secondary_area": None,
        "subject": "prison_allowance",
        "subsubjects": ["spouse_relationship"],
        "confidence": 0.9,
        "reasoning_summary": "Relata prisão do cônjuge e solicita auxílio-reclusão",
        "participants": [{"role": "contact_person", "relationship": "spouse"}],
        "case_facts": [
            {
                "key": "relationship_to_detainee",
                "value": "cônjuge",
                "certainty": "explicit",
                "source_message_ids": ["m1"],
                "from_trusted_crm_context": False,
            }
        ],
        "procedural_situation": {
            "stage": None,
            "prior_request": None,
            "prior_denial": None,
            "existing_case": False,
            "prior_attempts": None,
        },
        "mentioned_documents": [],
        "documents_availability": "unknown",
        "ambiguity": {
            "present": False,
            "reason": None,
            "needs_confirmation": False,
            "alternative_area": None,
            "alternative_subject": None,
        },
        "urgency": "normal",
        "safety": {
            "level": "normal",
            "reason": "prisão relatada",
            "detected_risks": ["arrest_or_detention"],
            "recommend_handoff": False,
        },
    }
    base.update(overrides)
    return base


def _next_step_payload(**overrides: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": "triage_next_step.v1",
        "action": "ask_question",
        "priority": "normal",
        "missing_information": ["approximate_prison_date"],
        "selected_missing_information": ["approximate_prison_date"],
        "proposed_question": "Em que período aproximado ocorreu a prisão?",
        "requires_human_handoff": False,
        "handoff_reason": None,
        "policy_flags": [],
    }
    base.update(overrides)
    return base


def _completion(payload: dict[str, Any]) -> StructuredCompletionResult:
    return StructuredCompletionResult(
        parsed_content=payload,
        model="fake",
        finish_reason="stop",
        latency_ms=5.0,
        retry_count=0,
        request_id="r1",
        usage=None,
    )


class FakeClient:
    def __init__(self, responses: list[StructuredCompletionResult | Exception]) -> None:
        self.responses = list(responses)

    async def complete_structured(
        self,
        *,
        schema_name: str,
        json_schema: dict[str, object],
        messages: list[dict[str, str]],
        contract_label: str,
    ) -> StructuredCompletionResult:
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.mark.asyncio
async def test_timeout_retry_then_fail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout")

    async with AiRuntimeClient(
        _settings(ai_runtime_max_retries=1),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(AiRuntimeTimeoutError):
            await client.complete_structured(
                schema_name=SCHEMA_NAME_LEAD,
                json_schema={"type": "object"},
                messages=[{"role": "user", "content": "x"}],
                contract_label="lead_understanding",
            )


@pytest.mark.asyncio
async def test_health_paths() -> None:
    settings = _settings(ai_runtime_health_path="/health")

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("t")

    async with AiRuntimeClient(settings, transport=httpx.MockTransport(timeout_handler)) as client:
        with pytest.raises(AiRuntimeTimeoutError):
            await client.health_check()

    def conn_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("c")

    async with AiRuntimeClient(settings, transport=httpx.MockTransport(conn_handler)) as client:
        with pytest.raises(AiRuntimeConnectionError):
            await client.health_check()

    def bad_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={})

    async with AiRuntimeClient(settings, transport=httpx.MockTransport(bad_handler)) as client:
        with pytest.raises(AiRuntimeServerError):
            await client.health_check()

    async with AiRuntimeClient(_settings(ai_runtime_health_path=None)) as client:
        with pytest.raises(AiRuntimeProtocolError):
            await client.health_check()


@pytest.mark.asyncio
async def test_payload_max_tokens_and_missing_model() -> None:
    client = AiRuntimeClient(_settings(ai_runtime_max_tokens=128))
    payload = client.build_chat_payload(
        schema_name=SCHEMA_NAME_LEAD,
        json_schema={"type": "object"},
        messages=[{"role": "user", "content": "x"}],
    )
    assert payload["max_tokens"] == 128
    # enabled=false permite Settings sem model; build_chat_payload ainda exige model
    offline = Settings(ai_runtime_enabled=False, ai_runtime_model=None)
    with pytest.raises(AiRuntimeDisabledError):
        AiRuntimeClient(offline).build_chat_payload(
            schema_name=SCHEMA_NAME_LEAD,
            json_schema={"type": "object"},
            messages=[{"role": "user", "content": "x"}],
        )
    preview = AiRuntimeClient(offline).build_chat_payload(
        schema_name=SCHEMA_NAME_LEAD,
        json_schema={"type": "object"},
        messages=[{"role": "user", "content": "x"}],
        allow_missing_model=True,
    )
    assert preview["model"] == "offline-preview"


@pytest.mark.asyncio
async def test_protocol_body_variants() -> None:
    bodies: list[tuple[dict[str, Any], type[Exception]]] = [
        (
            {"choices": [{"finish_reason": "stop", "message": {"content": "[]"}}]},
            AiRuntimeInvalidJsonError,
        ),
        (
            {"choices": [{"finish_reason": None, "message": {"content": "{}"}}]},
            AiRuntimeProtocolError,
        ),
        (
            {"choices": [{"finish_reason": "stop", "message": None}]},
            AiRuntimeProtocolError,
        ),
        (
            {"choices": [{"finish_reason": "stop", "message": {"content": 123}}]},
            AiRuntimeProtocolError,
        ),
    ]
    for body, exc in bodies:

        def handler(request: httpx.Request, b: dict[str, Any] = body) -> httpx.Response:
            return httpx.Response(200, json=b)

        async with AiRuntimeClient(
            _settings(ai_runtime_max_retries=0),
            transport=httpx.MockTransport(handler),
        ) as client:
            with pytest.raises(exc):
                await client.complete_structured(
                    schema_name=SCHEMA_NAME_LEAD,
                    json_schema={"type": "object"},
                    messages=[{"role": "user", "content": "x"}],
                    contract_label="lead_understanding",
                )


@pytest.mark.asyncio
async def test_invalid_retry_after_then_success() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "not-a-number"}, json={})
        return httpx.Response(
            200,
            json={
                "model": "m",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps({"ok": True})},
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )

    async with AiRuntimeClient(
        _settings(ai_runtime_max_retries=1),
        transport=httpx.MockTransport(handler),
    ) as client:
        result = await client.complete_structured(
            schema_name=SCHEMA_NAME_LEAD,
            json_schema={"type": "object"},
            messages=[{"role": "user", "content": "x"}],
            contract_label="lead_understanding",
        )
    assert result.retry_count == 1
    assert result.usage is not None


@pytest.mark.asyncio
async def test_understanding_schema_and_semantic_failures() -> None:
    client = FakeClient([_completion({"not": "valid"})])
    with pytest.raises(AiRuntimeSchemaValidationError):
        await LeadUnderstandingService(client, Settings()).run(_request())

    client = FakeClient([_completion(_understanding_payload(subject="debt_collection"))])
    with pytest.raises(AiRuntimeSchemaValidationError):
        await LeadUnderstandingService(client, Settings()).run(_request())

    client = FakeClient([_completion(_understanding_payload(secondary_area="social_security"))])
    with pytest.raises(AiRuntimeSchemaValidationError):
        await LeadUnderstandingService(client, Settings()).run(_request())

    prev = PreviousDecisionSummary(
        intent="new_legal_lead",  # type: ignore[arg-type]
        primary_area="civil",  # type: ignore[arg-type]
        subject="debt_collection",
        action="route_lead",  # type: ignore[arg-type]
        requires_human_handoff=False,
        handoff_reason=None,
    )
    client = FakeClient([_completion(_understanding_payload())])
    with pytest.raises(AiRuntimeSemanticValidationError, match="reclassificação"):
        await LeadUnderstandingService(client, Settings()).run(_request(previous_decision=prev))


@pytest.mark.asyncio
async def test_next_step_repeated_and_invalid() -> None:
    understanding = LeadUnderstanding.model_validate(_understanding_payload())
    req = _request()

    client = FakeClient([_completion({"action": "ask_question"})])
    with pytest.raises(AiRuntimeSchemaValidationError):
        await TriageNextStepService(client, Settings()).run(
            request=req, understanding=understanding
        )

    client = FakeClient([_completion(_next_step_payload(proposed_question="Qual a data?"))])
    with pytest.raises(AiRuntimeSemanticValidationError, match="repetida"):
        await TriageNextStepService(client, Settings()).run(
            request=req,
            understanding=understanding,
            last_bot_question="Qual a data?",
        )


def test_next_step_context_limit_and_prompt_cache() -> None:
    understanding = LeadUnderstanding.model_validate(_understanding_payload())
    with pytest.raises(ContextLimitExceededError):
        build_next_step_context(
            request=_request(),
            understanding=understanding,
            playbook_id="x",
            playbook_version="1",
            questions_asked=0,
            last_bot_question=None,
            missing_information=[],
            settings=Settings(ai_context_max_chars=1000),
        )
    clear_prompt_cache()
    assert load_lead_understanding_prompt().version.startswith("lead_understanding")


@pytest.mark.asyncio
async def test_check_runtime_readiness_mocked(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.clients.ai_runtime as crm
    from app.application import ReadinessError, check_runtime_readiness

    settings = Settings(
        ai_runtime_required=True,
        ai_runtime_health_path="/health",
        ai_runtime_base_url="http://testserver",
        ai_runtime_model="m",
    )

    class Ok:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        async def health_check(self) -> None:
            return None

        async def aclose(self) -> None:
            return None

    class Fail:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        async def health_check(self) -> None:
            raise AiRuntimeServerError("down")

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(crm, "AiRuntimeClient", Ok)
    await check_runtime_readiness(settings)

    monkeypatch.setattr(crm, "AiRuntimeClient", Fail)
    with pytest.raises(ReadinessError, match="Runtime indisponível"):
        await check_runtime_readiness(settings)

    await check_runtime_readiness(Settings(ai_runtime_required=False))


@pytest.mark.asyncio
async def test_cli_live_pipeline_mocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.cli import triage as cli

    example = Path("examples/valid/01_prison_allowance_spouse.json")
    out = tmp_path / "out.json"
    meta = ProposalMetadata(
        model="m",
        prompt_version_understanding="lead_understanding.v1",
        prompt_version_next_step="triage_next_step.v1",
        prompt_hash_understanding="a" * 64,
        prompt_hash_next_step="b" * 64,
        schema_version_understanding="lead_understanding.v1",
        schema_version_next_step="triage_next_step.v1",
        taxonomy_version="legal_subjects.v1",
        playbook_id="social_security_prison_allowance",
        playbook_version="1.0.0",
        understanding_latency_ms=1.0,
        next_step_latency_ms=1.0,
        understanding_retry_count=0,
        next_step_retry_count=0,
        request_id_understanding="r1",
        request_id_next_step="r2",
    )

    class FakePipeline:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        async def run(self, request: TriageAnalysisRequest, **k: object) -> TriageProposal:
            return TriageProposal(
                event_id=request.event_id,
                status=ProposalStatus.FAILED_CLOSED,
                lead_understanding=LeadUnderstanding.model_validate(
                    json.loads(example.read_text(encoding="utf-8"))["lead_understanding"]
                ),
                triage_next_step=None,
                safe_fallback=SafeFallback(reason_category="server", message="down"),
                error=ProposalError(category="server", message="down", stage="next_step"),
                metadata=meta,
            )

    class FakeAsyncClient:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

    monkeypatch.setattr(cli, "AiRuntimeClient", FakeAsyncClient)
    monkeypatch.setattr(cli, "TriagePipelineService", FakePipeline)
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: Settings(ai_runtime_enabled=True, ai_runtime_model="m"),
    )
    monkeypatch.setattr(cli, "clear_settings_cache", lambda: None)

    code = await cli._run_live(
        type(
            "A",
            (),
            {
                "input": str(example),
                "output": str(out),
                "understanding_only": False,
                "next_step_only": False,
            },
        )()
    )
    assert code == cli.EXIT_FAIL_CLOSED
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["status"] == "failed_closed"
    assert data["lead_understanding"] is not None
    assert data["triage_next_step"] is None
    assert data["safe_fallback"]["source"] == "deterministic_policy"


def test_eval_load_cases_missing_expected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from evaluations import runner as ev

    cases = tmp_path / "cases"
    expected = tmp_path / "expected"
    cases.mkdir()
    expected.mkdir()
    (cases / "orphan.json").write_text('{"case_id":"orphan","request":{}}', encoding="utf-8")
    monkeypatch.setattr(ev, "CASES_DIR", cases)
    monkeypatch.setattr(ev, "EXPECTED_DIR", expected)
    with pytest.raises(FileNotFoundError):
        ev.load_cases()


@pytest.mark.asyncio
async def test_eval_run_live_requires_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.config as cfg
    from evaluations import runner as ev

    monkeypatch.setattr(
        cfg, "get_settings", lambda: Settings(ai_runtime_enabled=False, ai_runtime_model=None)
    )
    with pytest.raises(RuntimeError, match="AI_RUNTIME_ENABLED"):
        await ev.run_live([])


@pytest.mark.asyncio
async def test_eval_run_live_success_with_both_latencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.application.services as svc
    import app.clients.ai_runtime as rt
    import app.config as cfg
    from evaluations import runner as ev

    settings = Settings(ai_runtime_enabled=True, ai_runtime_model="m")
    understanding = LeadUnderstanding.model_validate(_understanding_payload())
    from app.schemas.triage_next_step import TriageNextStep

    next_step = TriageNextStep.model_validate(_next_step_payload())
    meta = ProposalMetadata(
        model="m",
        prompt_version_understanding="lead_understanding.v1",
        prompt_version_next_step="triage_next_step.v1",
        prompt_hash_understanding="a" * 64,
        prompt_hash_next_step="b" * 64,
        schema_version_understanding="lead_understanding.v1",
        schema_version_next_step="triage_next_step.v1",
        taxonomy_version="legal_subjects.v1",
        playbook_id="p",
        playbook_version="1",
        understanding_latency_ms=10.0,
        next_step_latency_ms=5.0,
        understanding_retry_count=0,
        next_step_retry_count=1,
        request_id_understanding="r1",
        request_id_next_step="r2",
    )

    class FakePipeline:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        async def run(self, request: TriageAnalysisRequest, **k: object) -> TriageProposal:
            return TriageProposal(
                event_id=request.event_id,
                status=ProposalStatus.SUCCESS,
                lead_understanding=understanding,
                triage_next_step=next_step,
                safe_fallback=None,
                error=None,
                metadata=meta,
            )

    class FakeAsyncClient:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

    monkeypatch.setattr(cfg, "get_settings", lambda: settings)
    monkeypatch.setattr(rt, "AiRuntimeClient", FakeAsyncClient)
    monkeypatch.setattr(svc, "TriagePipelineService", FakePipeline)

    case = json.loads(
        Path("evaluations/cases/ss_prison_allowance.json").read_text(encoding="utf-8")
    )
    expected = json.loads(
        Path("evaluations/expected/ss_prison_allowance.json").read_text(encoding="utf-8")
    )
    results = await ev.run_live([(case, expected)])
    assert results[0]["latency_ms"] == 15.0
    assert results[0]["retry_count"] == 1
    assert results[0]["fail_closed"] is False


def test_eval_main_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from evaluations import runner as ev

    monkeypatch.setattr(ev, "ROOT", tmp_path)
    # use real cases
    code = ev.main([])
    assert code == 0
    dirs = list((tmp_path / "artifacts" / "evaluations").glob("*_offline"))
    assert dirs


def test_eval_main_live_mocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from evaluations import runner as ev

    async def fake_live(pairs: object) -> list[dict[str, object]]:
        return [
            {
                "case_id": "x",
                "json_valid": True,
                "schema_valid": True,
                "intent_match": True,
                "area_match": True,
                "subject_match": True,
                "expected_handoff": False,
                "handoff_match": True,
                "required_risks": [],
                "risks_match": True,
                "invented_facts": False,
                "repeated_question": False,
                "fail_closed": False,
                "latency_ms": 1.0,
                "retry_count": 0,
                "predicted_area": "other",
            }
        ]

    monkeypatch.setattr(ev, "ROOT", tmp_path)
    monkeypatch.setattr(ev, "run_live", fake_live)
    monkeypatch.setattr(ev, "load_cases", lambda: [])
    code = ev.main(["--live"])
    assert code in {0, 4}


@pytest.mark.asyncio
async def test_eval_run_live_mocked(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.application.services as svc
    import app.clients.ai_runtime as rt
    import app.config as cfg
    from evaluations import runner as ev

    settings = Settings(ai_runtime_enabled=True, ai_runtime_model="m")
    understanding = LeadUnderstanding.model_validate(_understanding_payload())
    meta = ProposalMetadata(
        model="m",
        prompt_version_understanding="lead_understanding.v1",
        prompt_version_next_step=None,
        prompt_hash_understanding="a" * 64,
        prompt_hash_next_step=None,
        schema_version_understanding="lead_understanding.v1",
        schema_version_next_step="triage_next_step.v1",
        taxonomy_version="legal_subjects.v1",
        playbook_id=None,
        playbook_version=None,
        understanding_latency_ms=10.0,
        next_step_latency_ms=None,
        understanding_retry_count=1,
        next_step_retry_count=None,
        request_id_understanding="r1",
        request_id_next_step=None,
    )

    class FakePipeline:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        async def run(self, request: TriageAnalysisRequest, **k: object) -> TriageProposal:
            return TriageProposal(
                event_id=request.event_id,
                status=ProposalStatus.FAILED_CLOSED,
                lead_understanding=understanding,
                triage_next_step=None,
                safe_fallback=SafeFallback(reason_category="server", message="x"),
                error=ProposalError(category="server", message="x", stage="next_step"),
                metadata=meta,
            )

    class FakeAsyncClient:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

    monkeypatch.setattr(cfg, "get_settings", lambda: settings)
    monkeypatch.setattr(rt, "AiRuntimeClient", FakeAsyncClient)
    monkeypatch.setattr(svc, "TriagePipelineService", FakePipeline)

    case = json.loads(
        Path("evaluations/cases/ss_prison_allowance.json").read_text(encoding="utf-8")
    )
    expected = json.loads(
        Path("evaluations/expected/ss_prison_allowance.json").read_text(encoding="utf-8")
    )
    results = await ev.run_live([(case, expected)])
    assert len(results) == 1
    assert results[0]["fail_closed"] is True
    assert results[0]["predicted_area"] == "social_security"
