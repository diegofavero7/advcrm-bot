"""Confiança CRM em case_facts exige suporte verificável em known_facts.

Regressões do invariante: o modelo não cria a autoridade de um fato. Declaração
``from_trusted_crm_context=true`` sem suporte no request → fail-closed semântico,
antes de política, promoção ou next-step.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from app.application.services import LeadUnderstandingService, TriagePipelineService
from app.clients.types import StructuredCompletionResult
from app.clients.validation_diagnostics import (
    SEMANTIC_COHERENCE_ERROR_TYPES,
    is_semantic_coherence_failure,
)
from app.config import Settings
from app.domain.enums import (
    ContentType,
    FactCertainty,
    Intent,
    MessageDirection,
    MessageRole,
    RiskFlag,
    TriageState,
)
from app.domain.fact_trust import (
    UNVERIFIED_TRUSTED_CRM_CONTEXT,
    trusted_crm_origin_violations,
)
from app.domain.fact_vocabulary import DETAINEE_IMPRISONED, EXPLICIT_HUMAN_REQUEST
from app.policies.resolution import detect_explicit_human_request
from app.safety.promotion import find_supporting_fact
from app.schemas.inbound import ConversationMessage, KnownFact, TriageAnalysisRequest
from app.schemas.lead_understanding import (
    Ambiguity,
    CaseFact,
    LeadUnderstanding,
    ProceduralSituation,
    SafetyAssessment,
)
from app.schemas.proposal import ProposalStatus
from evaluations.metrics import compute_metrics
from evaluations.runner import score_case
from evaluations.stages import stages_for_failure


def _message(message_id: str = "m1", *, text: str = "mensagem") -> ConversationMessage:
    return ConversationMessage(
        message_id=message_id,
        role=MessageRole.LEAD,
        direction=MessageDirection.INBOUND,
        content_type=ContentType.TEXT,
        text=text,
        created_at=datetime(2026, 9, 20, 14, 0, tzinfo=UTC),
        reply_to_message_id=None,
    )


def _request(
    *,
    known: list[KnownFact] | None = None,
    messages: list[ConversationMessage] | None = None,
) -> TriageAnalysisRequest:
    return TriageAnalysisRequest(
        event_id="e-crm-trust",
        tenant_id="t",
        lead_id="l",
        conversation_id="c",
        triage_state=TriageState.PENDING_CLASSIFICATION,
        source="whatsapp",  # type: ignore[arg-type]
        messages=messages or [_message()],
        known_facts=known or [],
        previous_decision=None,
    )


def _known(
    key: str,
    value: str,
    *,
    trusted: bool = True,
) -> KnownFact:
    return KnownFact(
        key=key,
        value=value,
        certainty=FactCertainty.EXPLICIT,
        source_message_ids=[] if trusted else ["m1"],
        from_trusted_crm_context=trusted,
    )


def _fact(
    key: str,
    value: str,
    *,
    trusted_crm: bool = False,
    source_message_ids: list[str] | None = None,
) -> CaseFact:
    ids = source_message_ids if source_message_ids is not None else ([] if trusted_crm else ["m1"])
    return CaseFact(
        key=key,
        value=value,
        certainty=FactCertainty.EXPLICIT,
        source_message_ids=ids,
        from_trusted_crm_context=trusted_crm,
    )


def _understanding(*, case_facts: list[CaseFact]) -> LeadUnderstanding:
    return LeadUnderstanding(
        schema_version="lead_understanding.v1",
        intent=Intent.NEW_LEGAL_LEAD,
        language="pt-BR",  # type: ignore[arg-type]
        primary_area="undetermined",  # type: ignore[arg-type]
        secondary_area=None,
        subject="undetermined",
        subsubjects=[],
        confidence=0.9,
        reasoning_summary="resumo curto",
        participants=[],
        case_facts=case_facts,
        procedural_situation=ProceduralSituation(
            stage=None,
            prior_request=None,
            prior_denial=None,
            existing_case=None,
            prior_attempts=None,
        ),
        mentioned_documents=[],
        documents_availability="unknown",  # type: ignore[arg-type]
        ambiguity=Ambiguity(
            present=False,
            reason=None,
            needs_confirmation=False,
            alternative_area=None,
            alternative_subject=None,
        ),
        urgency="normal",  # type: ignore[arg-type]
        safety=SafetyAssessment(
            level="normal",  # type: ignore[arg-type]
            reason="nenhum risco",
            detected_risks=[],
            recommend_handoff=False,
        ),
    )


def _understanding_payload(*, case_facts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "lead_understanding.v1",
        "intent": "new_legal_lead",
        "language": "pt-BR",
        "primary_area": "undetermined",
        "secondary_area": None,
        "subject": "undetermined",
        "subsubjects": [],
        "confidence": 0.9,
        "reasoning_summary": "resumo curto",
        "participants": [],
        "case_facts": case_facts,
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
            "reason": "nenhum risco",
            "detected_risks": [],
            "recommend_handoff": False,
        },
    }


def _completion(parsed: dict[str, Any]) -> StructuredCompletionResult:
    return StructuredCompletionResult(
        parsed_content=parsed,
        model="fake-model",
        latency_ms=1.0,
        retry_count=0,
        request_id="rid-crm",
        finish_reason="stop",
        usage=None,
    )


class FakeClient:
    def __init__(self, results: list[Any]) -> None:
        self._results = list(results)
        self.calls = 0

    async def complete_structured(self, **_kwargs: object) -> StructuredCompletionResult:
        self.calls += 1
        item = self._results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# --- 1-6. Regra de suporte ---


def test_empty_crm_rejects_trusted_claim() -> None:
    facts = [_fact(EXPLICIT_HUMAN_REQUEST, "true", trusted_crm=True)]
    details = trusted_crm_origin_violations(facts, [])
    assert len(details) == 1
    assert details[0]["type"] == UNVERIFIED_TRUSTED_CRM_CONTEXT
    assert details[0]["loc"] == ["case_facts", 0, "from_trusted_crm_context"]


def test_divergent_crm_value_rejects_trusted_claim() -> None:
    known = [_known(EXPLICIT_HUMAN_REQUEST, "false")]
    facts = [_fact(EXPLICIT_HUMAN_REQUEST, "true", trusted_crm=True)]
    details = trusted_crm_origin_violations(facts, known)
    assert len(details) == 1
    assert details[0]["type"] == UNVERIFIED_TRUSTED_CRM_CONTEXT


def test_matching_crm_support_accepts_trusted_claim() -> None:
    known = [_known(EXPLICIT_HUMAN_REQUEST, "true")]
    facts = [_fact(EXPLICIT_HUMAN_REQUEST, "true", trusted_crm=True)]
    assert trusted_crm_origin_violations(facts, known) == []


def test_lead_message_does_not_grant_crm_trust() -> None:
    """source_message_ids do lead não substituem known_facts."""
    facts = [
        _fact(
            EXPLICIT_HUMAN_REQUEST,
            "true",
            trusted_crm=True,
            source_message_ids=["m1"],
        )
    ]
    assert trusted_crm_origin_violations(facts, []) != []


def test_legitimate_lead_fact_without_crm_flag_is_accepted() -> None:
    facts = [_fact("relationship_to_detainee", "conjuge", trusted_crm=False)]
    assert trusted_crm_origin_violations(facts, []) == []


def test_canonical_boolean_equivalents_count_as_crm_support() -> None:
    known = [_known(EXPLICIT_HUMAN_REQUEST, "true")]
    facts = [_fact(EXPLICIT_HUMAN_REQUEST, "sim", trusted_crm=True)]
    assert trusted_crm_origin_violations(facts, known) == []


def test_error_type_is_semantic_coherence() -> None:
    assert UNVERIFIED_TRUSTED_CRM_CONTEXT in SEMANTIC_COHERENCE_ERROR_TYPES
    details = [
        {
            "loc": ["case_facts", 0, "from_trusted_crm_context"],
            "type": UNVERIFIED_TRUSTED_CRM_CONTEXT,
        }
    ]
    assert is_semantic_coherence_failure(details) is True


# --- 7. Conflitos preservados ---


def test_conflicting_facts_still_do_not_authorize_human_or_promotion() -> None:
    understanding = _understanding(
        case_facts=[
            _fact(EXPLICIT_HUMAN_REQUEST, "false", trusted_crm=False),
            _fact(EXPLICIT_HUMAN_REQUEST, "true", trusted_crm=False),
        ]
    )
    assert detect_explicit_human_request(_request(), understanding) is False

    conflicting = [
        _fact(DETAINEE_IMPRISONED, "true"),
        _fact(DETAINEE_IMPRISONED, "false"),
    ]
    assert (
        find_supporting_fact(
            subject="prison_allowance",
            risk=RiskFlag.ARREST_OR_DETENTION,
            case_facts=conflicting,
            allowed_message_ids=frozenset({"m1"}),
        )
        is None
    )


# --- 8. Pipeline: rejeição não chega a consumidores ---


@pytest.mark.asyncio
async def test_unverified_crm_trust_reject_still_runs_safety_without_contamination() -> None:
    """Proveniência CRM falsa: payload rejeitado não contamina safety; extrator roda 1x."""
    payload = _understanding_payload(
        case_facts=[
            {
                "key": EXPLICIT_HUMAN_REQUEST,
                "value": "true",
                "certainty": "explicit",
                "source_message_ids": ["m1"],
                "from_trusted_crm_context": True,
            }
        ]
    )
    client = FakeClient(
        [
            _completion(payload),
            _completion(
                {
                    "schema_version": "safety_signals.v2",
                    "occurrences": [],
                    "urgent_help_request": {
                        "state": "not_informed",
                        "source_message_ids": [],
                        "evidence_quote": None,
                        "evidence_summary": None,
                        "related_occurrence_ids": [],
                    },
                    "immediate_danger": {
                        "state": "not_informed",
                        "source_message_ids": [],
                        "evidence_quote": None,
                        "evidence_summary": None,
                        "related_occurrence_ids": [],
                    },
                }
            ),
        ]
    )
    proposal = await TriagePipelineService(client, Settings()).run(_request(known=[]))
    assert proposal.status == ProposalStatus.DEGRADED_SAFETY
    assert proposal.lead_understanding is None
    assert proposal.triage_next_step is None
    assert proposal.model_next_step_proposal is None
    assert proposal.safety_signals is not None
    assert isinstance(proposal.safety_signals.occurrences, list)
    assert proposal.effective_safety_signals is not None
    assert proposal.effective_safety_signals.model_detected == []
    assert proposal.effective_safety_signals.taxonomy_derived == []
    assert proposal.effective_safety_signals.effective == []
    assert proposal.error is not None
    assert proposal.error.category == "semantic_validation"
    assert any(d.get("type") == UNVERIFIED_TRUSTED_CRM_CONTEXT for d in proposal.error.details)
    assert proposal.safe_fallback is not None
    assert proposal.safe_fallback.recommended_internal_action == "request_human_review"
    assert proposal.decision_provenance is not None
    assert proposal.decision_provenance.policy_version == "degraded_safety.v2"
    assert proposal.decision_provenance.policy_status == "applied_mandatory"
    assert client.calls == 2  # understanding + safety; sem next-step


@pytest.mark.asyncio
async def test_supported_crm_fact_reaches_understanding_service() -> None:
    payload = _understanding_payload(
        case_facts=[
            {
                "key": EXPLICIT_HUMAN_REQUEST,
                "value": "true",
                "certainty": "explicit",
                "source_message_ids": [],
                "from_trusted_crm_context": True,
            }
        ]
    )
    client = FakeClient([_completion(payload)])
    request = _request(known=[_known(EXPLICIT_HUMAN_REQUEST, "true")])
    understanding, *_ = await LeadUnderstandingService(client, Settings()).run(request)
    assert understanding.case_facts[0].from_trusted_crm_context is True


@pytest.mark.asyncio
async def test_lead_origin_fact_still_accepted_by_understanding_service() -> None:
    payload = _understanding_payload(
        case_facts=[
            {
                "key": "relationship_to_detainee",
                "value": "conjuge",
                "certainty": "explicit",
                "source_message_ids": ["m1"],
                "from_trusted_crm_context": False,
            }
        ]
    )
    client = FakeClient([_completion(payload)])
    understanding, *_ = await LeadUnderstandingService(client, Settings()).run(_request())
    assert understanding.case_facts[0].from_trusted_crm_context is False


# --- 9. Avaliação: fail-closed não conta como sucesso semântico ---


def test_eval_records_fail_closed_not_as_semantic_success() -> None:
    row = score_case(
        expected={
            "case_id": "existing_client",
            "expectations_version": "eval_expected.v3",
            "acceptable_intents": ["existing_client_other_request"],
            "acceptable_primary_areas": ["undetermined"],
            "acceptable_subjects": [],
            "acceptable_actions": [],
            "acceptable_policy_rules": [],
            "require_model_inference_skipped": None,
            "handoff_required": True,
            "required_risks": [],
            "forbidden_risks": [],
            "risks_exhaustive": False,
            "required_fact_keys": [],
            "forbidden_fact_keys": [],
            "required_fact_values": {},
            "forbidden_fact_values": {},
            "critical_expectations": [],
            "annotation_rationale": None,
        },
        understanding=None,
        next_step=None,
        fail_closed=True,
        latency_ms=10.0,
        retry_count=0,
        stages=stages_for_failure("semantic_validation", failure_stage="semantic_validation"),
        pipeline_status="failed_closed",
        error={
            "category": "semantic_validation",
            "message": "case_facts declara confiança CRM sem suporte em known_facts",
            "details": [
                {
                    "loc": ["case_facts", 0, "from_trusted_crm_context"],
                    "type": UNVERIFIED_TRUSTED_CRM_CONTEXT,
                }
            ],
        },
        safe_fallback={
            "reason_code": "semantic_validation",
            "recommended_internal_action": "request_human_review",
        },
    )
    assert row["fail_closed"] is True
    assert row["pipeline_status"] == "failed_closed"
    assert row["intent_match"] is not True
    assert row["area_match"] is not True

    metrics = compute_metrics([row], mode="live")
    assert metrics["cases_valid_output"] == 0
    assert metrics["cases_failed_closed"] == 1
    assert metrics["cases_success"] == 0


def test_violation_does_not_silently_rewrite_or_drop_fact() -> None:
    """A checagem só reporta; não muta o CaseFact (sem flip/apagamento)."""
    fact = _fact(EXPLICIT_HUMAN_REQUEST, "true", trusted_crm=True, source_message_ids=["m1"])
    details = trusted_crm_origin_violations([fact], [])
    assert details
    assert fact.from_trusted_crm_context is True
    assert fact.value == "true"
    assert fact.source_message_ids == ["m1"]
