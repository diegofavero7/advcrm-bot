"""Testes do pipeline e fail-closed."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from app.application.services import TriagePipelineService
from app.clients.errors import AiRuntimeServerError
from app.clients.types import StructuredCompletionResult
from app.config import Settings
from app.domain.enums import (
    ContentType,
    MessageDirection,
    MessageRole,
    TriageState,
)
from app.schemas.inbound import ConversationMessage, TriageAnalysisRequest
from app.schemas.proposal import ProposalStatus


def _request() -> TriageAnalysisRequest:
    return TriageAnalysisRequest(
        event_id="e1",
        tenant_id="t",
        lead_id="l",
        conversation_id="c",
        triage_state=TriageState.PENDING_CLASSIFICATION,
        source="whatsapp",  # type: ignore[arg-type]
        messages=[
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
        known_facts=[],
        previous_decision=None,
    )


def _understanding_payload() -> dict[str, Any]:
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
            "reason": "prisão relatada sem risco adicional imediato",
            "detected_risks": ["arrest_or_detention"],
            "recommend_handoff": False,
        },
    }


def _next_step_payload() -> dict[str, Any]:
    return {
        "schema_version": "triage_next_step.v1",
        "action": "ask_question",
        "priority": "normal",
        "missing_information": ["approximate_prison_date"],
        "selected_missing_information": ["approximate_prison_date"],
        "proposed_question": "Em que período aproximado ocorreu a prisão?",
        "requires_human_handoff": False,
        "handoff_reason": None,
        "policy_flags": ["playbook_prison_allowance"],
    }


def _safety_payload(**overrides):
    from tests.helpers.safety import safety_v2

    return safety_v2(**overrides)


class FakeClient:
    def __init__(self, responses: list[StructuredCompletionResult | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    async def complete_structured(
        self,
        *,
        schema_name: str,
        json_schema: dict[str, object],
        messages: list[dict[str, str]],
        contract_label: str,
        organization_id: str | None = None,
    ) -> StructuredCompletionResult:
        self.calls.append(contract_label)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _completion(payload: dict[str, Any]) -> StructuredCompletionResult:
    return StructuredCompletionResult(
        parsed_content=payload,
        model="fake",
        finish_reason="stop",
        latency_ms=12.0,
        retry_count=0,
        request_id="r1",
        usage=None,
    )


@pytest.mark.asyncio
async def test_pipeline_success() -> None:
    client = FakeClient(
        [
            _completion(_understanding_payload()),
            _completion(_safety_payload()),
            _completion(_next_step_payload()),
        ]
    )
    proposal = await TriagePipelineService(client, Settings()).run(_request())
    assert proposal.status == ProposalStatus.SUCCESS
    assert proposal.lead_understanding is not None
    assert proposal.triage_next_step is not None
    assert proposal.safe_fallback is None
    assert client.calls == ["lead_understanding", "safety_signals", "triage_next_step"]


@pytest.mark.asyncio
async def test_pipeline_preserves_failure_telemetry() -> None:
    err = AiRuntimeServerError(
        "down",
        latency_ms=1500.5,
        retry_count=2,
        http_status=503,
        request_id="req-fail-1",
    )
    client = FakeClient([err])
    proposal = await TriagePipelineService(client, Settings()).run(_request())
    assert proposal.status == ProposalStatus.FAILED_CLOSED
    assert proposal.metadata.understanding_latency_ms == 1500.5
    assert proposal.metadata.understanding_retry_count == 2
    assert proposal.metadata.request_id_understanding == "req-fail-1"
    assert proposal.error is not None
    assert proposal.error.category == "server"
    assert proposal.error.retry_count == 2
    assert proposal.error.latency_ms == 1500.5
    assert proposal.error.http_status == 503
    assert proposal.error.request_id == "req-fail-1"


@pytest.mark.asyncio
async def test_pipeline_preserves_understanding_on_second_failure() -> None:
    client = FakeClient(
        [
            _completion(_understanding_payload()),
            _completion(_safety_payload()),
            AiRuntimeServerError("down"),
        ]
    )
    proposal = await TriagePipelineService(client, Settings()).run(_request())
    assert proposal.status == ProposalStatus.FAILED_CLOSED
    assert proposal.lead_understanding is not None
    assert proposal.triage_next_step is None
    assert proposal.safe_fallback is not None
    assert proposal.error is not None
    assert proposal.error.stage == "next_step"


@pytest.mark.asyncio
async def test_fail_closed_does_not_set_requires_human_handoff() -> None:
    client = FakeClient([AiRuntimeServerError("down")])
    proposal = await TriagePipelineService(client, Settings()).run(_request())
    assert proposal.triage_next_step is None
    dumped = proposal.model_dump()
    assert "requires_human_handoff" not in json.dumps(dumped.get("safe_fallback"))
