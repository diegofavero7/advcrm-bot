"""Diagnóstico sanitizado de falha de schema na CLI/serviço."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from app.application.services import LeadUnderstandingService
from app.cli import triage as cli
from app.clients.errors import AiRuntimeSchemaValidationError
from app.clients.types import StructuredCompletionResult
from app.clients.validation_diagnostics import sanitize_validation_error
from app.config import Settings
from app.domain.enums import ContentType, MessageDirection, MessageRole, TriageState
from app.schemas.inbound import ConversationMessage, TriageAnalysisRequest
from app.schemas.lead_understanding import LeadUnderstanding
from pydantic import ValidationError


def _request() -> TriageAnalysisRequest:
    return TriageAnalysisRequest.model_validate(
        {
            "event_id": "e-diag",
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
                    text="DADO SENSÍVEL DO LEAD NÃO DEVE VAZAR",
                    created_at=datetime(2026, 9, 20, 14, 0, tzinfo=UTC),
                    reply_to_message_id=None,
                )
            ],
            "known_facts": [],
            "previous_decision": None,
        }
    )


def _valid_understanding_payload() -> dict[str, Any]:
    return {
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
                "value": "SEGredo-no-fato-nao-deve-aparecer",
                "certainty": "explicit",
                "source_message_ids": [],
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


class FakeClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    async def complete_structured(self, **kwargs: object) -> StructuredCompletionResult:
        return StructuredCompletionResult(
            parsed_content=self.payload,
            model="fake-model",
            finish_reason="stop",
            latency_ms=1.0,
            retry_count=0,
            request_id="r-diag",
            usage=None,
        )


def test_sanitize_validation_error_only_loc_and_type() -> None:
    payload = _valid_understanding_payload()
    # Dispara validador Pydantic CaseFact (não expresso no JSON Schema estrito).
    with pytest.raises(ValidationError) as caught:
        LeadUnderstanding.model_validate(payload)
    details = sanitize_validation_error(caught.value)
    assert details
    for item in details:
        assert set(item.keys()) == {"loc", "type"}
        assert "input" not in item
        assert "ctx" not in item
        assert "msg" not in item
    blob = json.dumps(details)
    assert "SEGRedo-no-fato-nao-deve-aparecer" not in blob
    assert "DADO SENSÍVEL" not in blob


@pytest.mark.asyncio
async def test_schema_validation_preserves_sanitized_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = _valid_understanding_payload()
    client = FakeClient(payload)
    with (
        caplog.at_level(logging.INFO),
        pytest.raises(AiRuntimeSchemaValidationError) as caught,
    ):
        await LeadUnderstandingService(client, Settings()).run(_request())
    err = caught.value
    assert err.category == "schema_validation"
    assert err.details
    assert all(set(d.keys()) == {"loc", "type"} for d in err.details)
    # CaseFact.require_source_or_trusted → loc aponta para case_facts
    locs = [tuple(d["loc"]) for d in err.details]
    assert any(loc and loc[0] == "case_facts" for loc in locs)
    assert "SEGRedo-no-fato-nao-deve-aparecer" not in err.message
    assert "SEGRedo-no-fato-nao-deve-aparecer" not in caplog.text
    assert "DADO SENSÍVEL DO LEAD NÃO DEVE VAZAR" not in caplog.text
    assert str(caught.value.__cause__)  # cause existe, mas CLI/logs não devem dumpá-lo


@pytest.mark.asyncio
async def test_cli_live_understanding_writes_sanitized_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    example = Path("examples/valid/01_prison_allowance_spouse.json")
    out = tmp_path / "fail.json"
    secret = "SEGRedo-no-fato-nao-deve-aparecer"
    bad = _valid_understanding_payload()
    bad["case_facts"][0]["value"] = secret

    class BoomService:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        async def run(self, request: TriageAnalysisRequest) -> object:
            raise AiRuntimeSchemaValidationError(
                "lead_understanding fora do schema",
                details=[{"loc": ["case_facts", 0], "type": "value_error"}],
            )

    class FakeAsyncClient:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

    monkeypatch.setattr(cli, "AiRuntimeClient", FakeAsyncClient)
    monkeypatch.setattr(cli, "LeadUnderstandingService", BoomService)
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: Settings(
            ai_runtime_enabled=True,
            ai_runtime_organization_id="11111111-1111-4111-8111-111111111111",
        ),
    )
    monkeypatch.setattr(cli, "clear_settings_cache", lambda: None)

    code = await cli._run_live(
        type(
            "A",
            (),
            {
                "input": str(example),
                "output": str(out),
                "understanding_only": True,
                "next_step_only": False,
            },
        )()
    )
    assert code == cli.EXIT_FAIL_CLOSED
    assert out.is_file()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["status"] == "failed_closed"
    assert data["error"]["category"] == "schema_validation"
    assert data["error"]["details"] == [{"loc": ["case_facts", 0], "type": "value_error"}]
    assert data["safe_fallback"]["source"] == "deterministic_policy"
    dumped = out.read_text(encoding="utf-8")
    assert secret not in dumped
    assert "DADO SENSÍVEL" not in dumped
    assert "ctx" not in dumped
