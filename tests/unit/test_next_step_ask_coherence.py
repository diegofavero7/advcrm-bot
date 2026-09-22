"""Coerência transversal de triage_next_step + prompt v3."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from app.application.services import TriageNextStepService, TriagePipelineService
from app.clients.errors import AiRuntimeSemanticValidationError
from app.clients.schemas import get_triage_next_step_schema
from app.clients.types import StructuredCompletionResult
from app.clients.validation_diagnostics import (
    SEMANTIC_COHERENCE_ERROR_TYPES,
    is_semantic_coherence_failure,
    sanitize_validation_error,
)
from app.config import Settings
from app.domain.enums import (
    ContentType,
    MessageDirection,
    MessageRole,
    TriageAction,
    TriageState,
)
from app.policies.resolution import POLICY_VERSION, resolve_conservative_policy
from app.prompts import clear_prompt_cache, load_triage_next_step_prompt
from app.schemas import build_strict_json_schema
from app.schemas.inbound import ConversationMessage, TriageAnalysisRequest
from app.schemas.lead_understanding import LeadUnderstanding
from app.schemas.proposal import ProposalStatus
from app.schemas.triage_next_step import TriageNextStep
from pydantic import ValidationError


def _base_next_step(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "triage_next_step.v1",
        "action": "ask_question",
        "priority": "normal",
        "missing_information": ["employment_end_date"],
        "selected_missing_information": ["employment_end_date"],
        "proposed_question": "Em que data aproximada o contrato de trabalho terminou?",
        "requires_human_handoff": False,
        "handoff_reason": None,
        "policy_flags": [],
    }
    payload.update(overrides)
    return payload


def _live_family_support_bad_payload() -> dict[str, Any]:
    return {
        "action": "ask_question",
        "handoff_reason": None,
        "missing_information": [],
        "policy_flags": [],
        "priority": "normal",
        "proposed_question": None,
        "requires_human_handoff": False,
        "schema_version": "triage_next_step.v1",
        "selected_missing_information": [],
    }


def _understanding_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "lead_understanding.v1",
        "intent": "new_legal_lead",
        "language": "pt-BR",
        "primary_area": "family",
        "secondary_area": None,
        "subject": "child_support",
        "subsubjects": [],
        "confidence": 0.95,
        "reasoning_summary": "pedido de pensão",
        "participants": [],
        "case_facts": [
            {
                "key": "request_type",
                "value": "pensão alimentícia",
                "certainty": "explicit",
                "source_message_ids": ["m1"],
                "from_trusted_crm_context": False,
            }
        ],
        "procedural_situation": {
            "stage": None,
            "prior_request": None,
            "prior_denial": None,
            "existing_case": None,
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
            "reason": "sem risco imediato",
            "detected_risks": [],
            "recommend_handoff": False,
        },
    }
    payload.update(overrides)
    return payload


def _request() -> TriageAnalysisRequest:
    return TriageAnalysisRequest(
        event_id="e-family",
        tenant_id="t",
        lead_id="l",
        conversation_id="c",
        triage_state=TriageState.PENDING_CLASSIFICATION,
        source="whatsapp",
        messages=[
            ConversationMessage(
                message_id="m1",
                role=MessageRole.LEAD,
                direction=MessageDirection.INBOUND,
                content_type=ContentType.TEXT,
                text="Quero pedir pensão alimentícia para os filhos",
                created_at=datetime(2026, 9, 20, 14, 0, tzinfo=UTC),
                reply_to_message_id=None,
            )
        ],
        known_facts=[],
        previous_decision=None,
    )


def _safety_payload(**overrides):
    from tests.helpers.safety import safety_v2

    return safety_v2(**overrides)


class FakeClient:
    def __init__(self, completions: list[StructuredCompletionResult]) -> None:
        self._completions = list(completions)
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
        return self._completions.pop(0)


def _completion(data: dict[str, Any], *, retry_count: int = 0) -> StructuredCompletionResult:
    return StructuredCompletionResult(
        parsed_content=data,
        model="fake-model",
        finish_reason="stop",
        latency_ms=12.0,
        retry_count=retry_count,
        request_id="rid-ns",
        usage=None,
    )


def test_prompt_is_triage_next_step_v3() -> None:
    clear_prompt_cache()
    prompt = load_triage_next_step_prompt()
    assert prompt.version == "triage_next_step.v3"
    assert "Nunca use action=ask_question quando missing_information estiver vazio" in prompt.text
    assert '"proposed_question":null' in prompt.text.replace(" ", "")
    assert 'missing_information":[]' in prompt.text.replace(" ", "") or (
        '"missing_information":[]' in prompt.text
    )


def test_live_family_support_payload_rejected() -> None:
    with pytest.raises(ValidationError) as caught:
        TriageNextStep.model_validate(_live_family_support_bad_payload())
    details = sanitize_validation_error(caught.value)
    assert any(d["type"] == "ask_question_requires_missing_information" for d in details)
    assert is_semantic_coherence_failure(details)


@pytest.mark.parametrize(
    ("overrides", "error_type"),
    [
        ({"missing_information": [], "selected_missing_information": ["x"]}, None),
        (
            {
                "missing_information": ["a"],
                "selected_missing_information": [],
            },
            "ask_question_requires_exactly_one_selected",
        ),
        (
            {
                "missing_information": ["a"],
                "selected_missing_information": ["a", "b"],
            },
            "ask_question_requires_exactly_one_selected",
        ),
        (
            {
                "missing_information": ["a"],
                "selected_missing_information": ["b"],
            },
            "ask_question_selected_not_in_missing",
        ),
        (
            {
                "missing_information": ["a"],
                "selected_missing_information": ["a"],
                "proposed_question": None,
            },
            "ask_question_requires_proposed_question",
        ),
        (
            {
                "missing_information": ["a"],
                "selected_missing_information": ["a"],
                "proposed_question": "   ",
            },
            "ask_question_requires_proposed_question",
        ),
    ],
)
def test_ask_question_coherence_rejects(overrides: dict[str, Any], error_type: str | None) -> None:
    payload = _base_next_step(**overrides)
    if "missing_information" in overrides and overrides["missing_information"] == []:
        with pytest.raises(ValidationError) as caught:
            TriageNextStep.model_validate(payload)
        details = sanitize_validation_error(caught.value)
        assert any(d["type"] == "ask_question_requires_missing_information" for d in details)
        return
    assert error_type is not None
    with pytest.raises(ValidationError) as caught:
        TriageNextStep.model_validate(payload)
    details = sanitize_validation_error(caught.value)
    assert any(d["type"] == error_type for d in details)
    assert error_type in SEMANTIC_COHERENCE_ERROR_TYPES


def test_valid_ask_question_accepted() -> None:
    step = TriageNextStep.model_validate(_base_next_step())
    assert step.action == TriageAction.ASK_QUESTION
    assert step.proposed_question is not None


def test_valid_non_interrogative_without_gaps() -> None:
    step = TriageNextStep.model_validate(
        {
            "schema_version": "triage_next_step.v1",
            "action": "route_lead",
            "priority": "normal",
            "missing_information": [],
            "selected_missing_information": [],
            "proposed_question": None,
            "requires_human_handoff": False,
            "handoff_reason": None,
            "policy_flags": ["high_confidence_route_candidate"],
        }
    )
    assert step.action == TriageAction.ROUTE_LEAD
    assert step.proposed_question is None


def test_runtime_schema_has_no_if_then_conditionals() -> None:
    """llama.cpp GBNF não suporta if/then; schema enviado não deve incluí-los."""
    schema = get_triage_next_step_schema()
    blob = str(schema)
    assert '"if"' not in blob.replace(" ", "")
    assert "then" not in schema
    assert "if" not in schema
    generated = build_strict_json_schema(TriageNextStep)
    assert "if" not in generated
    assert "then" not in generated


@pytest.mark.asyncio
async def test_invalid_next_step_fail_closed_zero_retry_no_effective() -> None:
    client = FakeClient(
        [
            _completion(_understanding_payload()),
            _completion(_safety_payload()),
            _completion(_live_family_support_bad_payload(), retry_count=0),
        ]
    )
    proposal = await TriagePipelineService(client, Settings()).run(_request())
    assert proposal.status == ProposalStatus.FAILED_CLOSED
    assert proposal.triage_next_step is None
    assert proposal.error is not None
    assert proposal.error.category == "semantic_validation"
    assert proposal.error.stage == "next_step"
    assert proposal.error.retry_count == 0
    assert proposal.lead_understanding is not None
    assert proposal.lead_understanding.subject == "child_support"
    assert any(
        d.get("type") == "ask_question_requires_missing_information"
        for d in (proposal.error.details or [])
    )
    assert proposal.metadata.prompt_version_next_step == "triage_next_step.v3"
    assert proposal.metadata.prompt_hash_next_step


@pytest.mark.asyncio
async def test_family_support_synthetic_regression_no_live() -> None:
    """Regressão do caso family_support: understanding ok + next_step incoerente → fail-closed."""
    client = FakeClient(
        [
            _completion(_understanding_payload()),
            _completion(_safety_payload()),
            _completion(_live_family_support_bad_payload()),
        ]
    )
    proposal = await TriagePipelineService(client, Settings()).run(_request())
    assert client.calls == ["lead_understanding", "safety_signals", "triage_next_step"]
    assert proposal.status == ProposalStatus.FAILED_CLOSED
    assert proposal.decision_provenance is None or proposal.triage_next_step is None
    assert proposal.triage_next_step is None


@pytest.mark.asyncio
async def test_mandatory_policy_skips_second_inference() -> None:
    spam = _understanding_payload(
        intent="spam",
        primary_area="other",
        subject="other",
        subsubjects=[],
        case_facts=[],
        confidence=0.99,
        safety={
            "level": "normal",
            "reason": "spam",
            "detected_risks": [],
            "recommend_handoff": False,
        },
    )
    client = FakeClient([_completion(spam), _completion(_safety_payload())])
    proposal = await TriagePipelineService(client, Settings()).run(_request())
    assert client.calls == ["lead_understanding", "safety_signals"]
    assert proposal.status == ProposalStatus.SUCCESS
    assert proposal.triage_next_step is not None
    assert proposal.triage_next_step.action == TriageAction.IGNORE
    assert proposal.decision_provenance is not None
    assert proposal.decision_provenance.model_inference_skipped is True


@pytest.mark.asyncio
async def test_advisory_policy_does_not_override_valid_model() -> None:
    """high_confidence_route_candidate permanece recomendativa."""
    understanding = LeadUnderstanding.model_validate(
        _understanding_payload(confidence=0.95, urgency="normal")
    )
    resolution = resolve_conservative_policy(understanding, request=_request())
    assert resolution.mandatory is False
    assert "high_confidence_route_candidate" in resolution.advisory_flags
    assert resolution.policy_version == POLICY_VERSION

    model_ask = TriageNextStep.model_validate(_base_next_step())
    client = FakeClient(
        [
            _completion(_understanding_payload(confidence=0.95)),
            _completion(_safety_payload()),
            _completion(model_ask.model_dump(mode="json")),
        ]
    )
    proposal = await TriagePipelineService(client, Settings()).run(_request())
    assert proposal.status == ProposalStatus.SUCCESS
    assert proposal.triage_next_step is not None
    assert proposal.triage_next_step.action == TriageAction.ASK_QUESTION
    assert proposal.decision_provenance is not None
    assert proposal.decision_provenance.source == "model"
    assert proposal.decision_provenance.model_inference_skipped is False


@pytest.mark.asyncio
async def test_next_step_service_semantic_category_and_retry_zero() -> None:
    client = FakeClient([_completion(_live_family_support_bad_payload(), retry_count=0)])
    understanding = LeadUnderstanding.model_validate(_understanding_payload())
    with pytest.raises(AiRuntimeSemanticValidationError) as caught:
        await TriageNextStepService(client, Settings()).run(
            request=_request(),
            understanding=understanding,
            last_bot_question=None,
            missing_information=[],
        )
    assert caught.value.category == "semantic_validation"
    assert caught.value.retry_count == 0
    assert caught.value.prompt_version == "triage_next_step.v3"
