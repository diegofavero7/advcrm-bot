"""Integração de políticas determinísticas no pipeline (cliente mockado)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from app.application.services import TriagePipelineService
from app.clients.types import StructuredCompletionResult
from app.config import Settings
from app.domain.enums import (
    ContentType,
    Intent,
    MessageDirection,
    MessageRole,
    TriageAction,
    TriageState,
    UrgencyLevel,
)
from app.policies.resolution import (
    POLICY_VERSION,
    model_conflicts_with_mandatory,
    reconcile_next_step,
    resolve_conservative_policy,
)
from app.schemas.inbound import ConversationMessage, TriageAnalysisRequest
from app.schemas.lead_understanding import LeadUnderstanding
from app.schemas.proposal import ProposalStatus
from app.schemas.triage_next_step import TriageNextStep


def _request() -> TriageAnalysisRequest:
    return TriageAnalysisRequest(
        event_id="e-policy",
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
                text="Sou cliente e quero o andamento do meu processo",
                created_at=datetime(2026, 9, 20, 14, 0, tzinfo=UTC),
                reply_to_message_id=None,
            )
        ],
        known_facts=[],
        previous_decision=None,
    )


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
            "reason": "prisão relatada sem risco adicional imediato",
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
        "policy_flags": ["playbook_prison_allowance"],
    }
    base.update(overrides)
    return base


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
async def test_policy_called_and_skips_second_inference_for_existing_client() -> None:
    understanding = _understanding_payload(
        intent="existing_client_case_status",
        primary_area="undetermined",
        subject="undetermined",
        subsubjects=[],
        case_facts=[],
        safety={
            "level": "normal",
            "reason": "pedido de andamento",
            "detected_risks": [],
            "recommend_handoff": False,
        },
    )
    # Proposta conflitante do LLM NÃO deve ser solicitada.
    client = FakeClient(
        [
            _completion(understanding),
            _completion(_safety_payload()),
            _completion(_next_step_payload()),
        ]
    )
    proposal = await TriagePipelineService(client, Settings()).run(_request())
    assert proposal.status == ProposalStatus.SUCCESS
    assert client.calls == ["lead_understanding", "safety_signals"]
    assert proposal.triage_next_step is not None
    assert proposal.triage_next_step.action == TriageAction.HUMAN_HANDOFF
    assert proposal.triage_next_step.requires_human_handoff is True
    assert "existing_client_case_status" in proposal.triage_next_step.policy_flags
    assert proposal.model_next_step_proposal is None
    assert proposal.decision_provenance is not None
    assert proposal.decision_provenance.source == "deterministic_policy"
    assert proposal.decision_provenance.policy_status == "applied_mandatory"
    assert proposal.decision_provenance.model_inference_skipped is True
    assert proposal.decision_provenance.policy_version == POLICY_VERSION
    assert proposal.metadata.policy_rule_id == "existing_client_case_status"


@pytest.mark.asyncio
async def test_recommend_handoff_blocks_ask_question_without_second_call() -> None:
    understanding = _understanding_payload(
        safety={
            "level": "immediate",
            "reason": "pedido de humano",
            "detected_risks": [],
            "recommend_handoff": True,
        },
        urgency="immediate",
    )
    client = FakeClient(
        [
            _completion(understanding),
            _completion(_safety_payload()),
            _completion(_next_step_payload()),
        ]
    )
    proposal = await TriagePipelineService(client, Settings()).run(_request())
    assert client.calls == ["lead_understanding", "safety_signals"]
    assert proposal.triage_next_step is not None
    assert proposal.triage_next_step.action == TriageAction.HUMAN_HANDOFF
    assert proposal.triage_next_step.action != TriageAction.ASK_QUESTION
    assert proposal.decision_provenance is not None
    assert proposal.decision_provenance.policy_rule_id == "immediate_urgency_blocks_auto_route"


@pytest.mark.asyncio
async def test_advisory_policy_still_calls_model_and_records_provenance() -> None:
    client = FakeClient(
        [
            _completion(_understanding_payload()),
            _completion(_safety_payload()),
            _completion(_next_step_payload()),
        ]
    )
    proposal = await TriagePipelineService(client, Settings()).run(_request())
    assert client.calls == ["lead_understanding", "safety_signals", "triage_next_step"]
    assert proposal.status == ProposalStatus.SUCCESS
    assert proposal.model_next_step_proposal is not None
    assert proposal.triage_next_step is not None
    assert proposal.triage_next_step.action == TriageAction.ASK_QUESTION
    assert proposal.decision_provenance is not None
    assert proposal.decision_provenance.source == "model"
    assert proposal.decision_provenance.policy_status == "advisory_recorded"
    assert proposal.decision_provenance.model_inference_skipped is False
    assert "high_confidence_route_candidate" in (
        proposal.decision_provenance.advisory_flags or proposal.decision_provenance.policy_flags
    )


@pytest.mark.asyncio
async def test_absence_of_urgency_signal_does_not_fabricate_handoff() -> None:
    """Sem urgency immediate / recommend_handoff, política não inventa handoff."""
    understanding = _understanding_payload(
        urgency="normal",
        safety={
            "level": "normal",
            "reason": "audiência mencionada sem flag de urgência",
            "detected_risks": [],
            "recommend_handoff": False,
        },
        confidence=0.9,
    )
    client = FakeClient(
        [
            _completion(understanding),
            _completion(_safety_payload()),
            _completion(_next_step_payload()),
        ]
    )
    proposal = await TriagePipelineService(client, Settings()).run(_request())
    assert proposal.triage_next_step is not None
    assert proposal.triage_next_step.requires_human_handoff is False
    assert proposal.decision_provenance is not None
    assert proposal.decision_provenance.policy_status == "advisory_recorded"
    assert "immediate_urgency_blocks_auto_route" not in (
        proposal.decision_provenance.policy_flags or []
    )


@pytest.mark.asyncio
async def test_policy_failure_is_fail_closed() -> None:
    client = FakeClient([_completion(_understanding_payload()), _completion(_safety_payload())])

    def _boom(*_a: object, **_k: object) -> object:
        raise RuntimeError("policy boom")

    with patch(
        "app.application.services.resolve_conservative_policy",
        side_effect=_boom,
    ):
        proposal = await TriagePipelineService(client, Settings()).run(_request())
    assert proposal.status == ProposalStatus.FAILED_CLOSED
    assert proposal.triage_next_step is None
    assert proposal.error is not None
    assert proposal.error.category == "policy_error"
    assert proposal.error.stage == "policy"
    assert proposal.safe_fallback is not None
    assert proposal.safe_fallback.recommended_internal_action == "request_human_review"
    assert proposal.decision_provenance is not None
    assert proposal.decision_provenance.policy_status == "failed"
    assert client.calls == ["lead_understanding", "safety_signals"]


def test_reconcile_mandatory_overrides_conflicting_model_proposal() -> None:
    request = _request()
    understanding = LeadUnderstanding.model_validate(
        _understanding_payload(intent="existing_client_case_status")
    )
    policy = resolve_conservative_policy(understanding, request=request)
    assert policy.mandatory is True
    model = TriageNextStep.model_validate(_next_step_payload())
    assert model_conflicts_with_mandatory(policy, model) is True
    effective, source = reconcile_next_step(policy=policy, model_proposal=model)
    assert source == "deterministic_policy"
    assert effective.action == TriageAction.HUMAN_HANDOFF
    assert effective.action != model.action


def test_reconcile_advisory_keeps_model() -> None:
    request = _request()
    understanding = LeadUnderstanding.model_validate(_understanding_payload(confidence=0.9))
    policy = resolve_conservative_policy(understanding, request=request)
    assert policy.mandatory is False
    model = TriageNextStep.model_validate(_next_step_payload())
    effective, source = reconcile_next_step(policy=policy, model_proposal=model)
    assert source == "model"
    assert effective.action == TriageAction.ASK_QUESTION


@pytest.mark.asyncio
async def test_pipeline_domestic_violence_high_urgency_forces_handoff() -> None:
    """Regressão `family_violence`: VD operacional + urgency=high com safety normal."""
    from tests.helpers.safety import occurrence, safety_v2

    understanding = _understanding_payload(
        primary_area="family",
        subject="domestic_violence",
        subsubjects=[],
        case_facts=[
            {
                "key": "violence_type",
                "value": "agressao fisica pelo companheiro",
                "certainty": "explicit",
                "source_message_ids": ["m1"],
                "from_trusted_crm_context": False,
            }
        ],
        urgency="high",
        confidence=0.95,
        safety={
            "level": "normal",
            "reason": "relato de agressao",
            "detected_risks": ["domestic_violence", "violence_or_threat"],
            "recommend_handoff": False,
        },
    )
    client = FakeClient(
        [
            _completion(understanding),
            _completion(
                safety_v2(
                    occurrences=[
                        occurrence(
                            occurrence_id="dv1",
                            risk="domestic_violence",
                            quote="andamento",
                            summary="relato de agressao pelo companheiro",
                            temporal="ongoing",
                        )
                    ]
                )
            ),
            _completion(_next_step_payload(action="route_lead")),
        ]
    )
    proposal = await TriagePipelineService(client, Settings()).run(_request())

    assert client.calls == ["lead_understanding", "safety_signals"]
    assert proposal.triage_next_step is not None
    assert proposal.triage_next_step.action == TriageAction.HUMAN_HANDOFF
    assert proposal.triage_next_step.requires_human_handoff is True
    assert proposal.decision_provenance is not None
    assert (
        proposal.decision_provenance.policy_rule_id == "domestic_violence_urgency_requires_handoff"
    )
    assert proposal.decision_provenance.model_inference_skipped is True


@pytest.mark.asyncio
async def test_pipeline_refuses_route_lead_for_fully_undetermined_classification() -> None:
    """Regressão `correction_later`: sem área nem assunto não existe destino."""
    understanding = _understanding_payload(
        primary_area="undetermined",
        subject="undetermined",
        subsubjects=[],
        case_facts=[],
        confidence=0.95,
        ambiguity={
            "present": False,
            "reason": None,
            "needs_confirmation": False,
            "alternative_area": None,
            "alternative_subject": None,
        },
        safety={
            "level": "normal",
            "reason": "sem sinais",
            "detected_risks": [],
            "recommend_handoff": False,
        },
    )
    client = FakeClient(
        [
            _completion(understanding),
            _completion(_safety_payload()),
            _completion(_next_step_payload(action="route_lead")),
        ]
    )
    proposal = await TriagePipelineService(client, Settings()).run(_request())

    assert client.calls == ["lead_understanding", "safety_signals"]
    assert proposal.triage_next_step is not None
    assert proposal.triage_next_step.action == TriageAction.ASK_QUESTION
    assert proposal.triage_next_step.selected_missing_information == ["legal_issue_description"]
    assert proposal.decision_provenance is not None
    assert proposal.decision_provenance.source == "deterministic_policy"
    assert (
        proposal.decision_provenance.policy_rule_id
        == "undetermined_classification_requires_clarification"
    )


def test_policy_does_not_treat_missing_risk_as_detected() -> None:
    request = _request()
    understanding = LeadUnderstanding.model_validate(
        _understanding_payload(
            intent="new_legal_lead",
            urgency=UrgencyLevel.NORMAL.value,
            safety={
                "level": "normal",
                "reason": "sem sinais",
                "detected_risks": [],
                "recommend_handoff": False,
            },
        )
    )
    policy = resolve_conservative_policy(understanding, request=request)
    assert policy.mandatory is False
    assert understanding.safety.detected_risks == []
    assert understanding.intent == Intent.NEW_LEGAL_LEAD
