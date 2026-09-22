"""Pedido explícito de humano via case_facts canônicos (sem matching textual)."""

from __future__ import annotations

from app.domain.enums import (
    FactCertainty,
    Intent,
    LegalArea,
    UrgencyLevel,
)
from app.domain.fact_vocabulary import EXPLICIT_HUMAN_REQUEST
from app.policies.resolution import (
    POLICY_VERSION,
    detect_explicit_human_request,
    resolve_conservative_policy,
)
from app.schemas.inbound import ConversationMessage, KnownFact, TriageAnalysisRequest
from app.schemas.lead_understanding import (
    Ambiguity,
    CaseFact,
    LeadUnderstanding,
    ProceduralSituation,
    SafetyAssessment,
)


def _msg(mid: str = "m1", text: str = "oi") -> ConversationMessage:
    return ConversationMessage(
        message_id=mid,
        role="lead",
        direction="inbound",
        content_type="text",
        text=text,
        created_at="2026-09-20T14:00:00-03:00",
        reply_to_message_id=None,
    )


def _request(*, known: list[KnownFact] | None = None) -> TriageAnalysisRequest:
    return TriageAnalysisRequest(
        event_id="e1",
        tenant_id="t1",
        lead_id="l1",
        conversation_id="c1",
        triage_state="pending_classification",
        source="whatsapp",
        messages=[_msg()],
        known_facts=known or [],
        previous_decision=None,
    )


def _understanding(*, facts: list[CaseFact] | None = None) -> LeadUnderstanding:
    return LeadUnderstanding(
        schema_version="lead_understanding.v1",
        intent=Intent.NEW_LEGAL_LEAD,
        primary_area=LegalArea.UNDETERMINED,
        secondary_area=None,
        subject="undetermined",
        subsubjects=[],
        urgency=UrgencyLevel.NORMAL,
        confidence=0.9,
        language="pt-BR",
        case_facts=facts or [],
        participants=[],
        mentioned_documents=[],
        documents_availability="unknown",
        procedural_situation=ProceduralSituation(
            stage=None,
            prior_request=None,
            prior_denial=None,
            prior_attempts=None,
            existing_case=None,
        ),
        ambiguity=Ambiguity(
            present=True,
            reason="sem_materia",
            needs_confirmation=True,
            alternative_area=None,
            alternative_subject=None,
        ),
        safety=SafetyAssessment(
            level=UrgencyLevel.NORMAL,
            detected_risks=[],
            recommend_handoff=False,
            reason="none",
        ),
        reasoning_summary="pedido humano sem materia",
    )


def test_policy_version_is_current() -> None:
    assert POLICY_VERSION == "conservative_action.v7"


def test_crm_known_fact_still_triggers() -> None:
    req = _request(
        known=[
            KnownFact(
                key=EXPLICIT_HUMAN_REQUEST,
                value="true",
                certainty=FactCertainty.EXPLICIT,
                source_message_ids=[],
                from_trusted_crm_context=True,
            )
        ]
    )
    assert detect_explicit_human_request(req, _understanding()) is True


def test_case_fact_explicit_human_triggers_handoff() -> None:
    facts = [
        CaseFact(
            key=EXPLICIT_HUMAN_REQUEST,
            value="true",
            certainty=FactCertainty.EXPLICIT,
            source_message_ids=["m1"],
        )
    ]
    u = _understanding(facts=facts)
    req = _request()
    assert detect_explicit_human_request(req, u) is True
    resolution = resolve_conservative_policy(u, request=req)
    assert resolution.policy_rule_id == "explicit_human_or_existing_client"
    assert resolution.decision.requires_human_handoff is True
    assert resolution.skips_model_inference is True


def test_undetermined_does_not_neutralize_human_request() -> None:
    facts = [
        CaseFact(
            key=EXPLICIT_HUMAN_REQUEST,
            value="sim",
            certainty=FactCertainty.EXPLICIT,
            source_message_ids=["m1"],
        )
    ]
    u = _understanding(facts=facts)
    resolution = resolve_conservative_policy(u, request=_request())
    assert resolution.policy_rule_id == "explicit_human_or_existing_client"


def test_generic_lawyer_request_is_not_human_signal() -> None:
    facts = [
        CaseFact(
            key="legal_help_request",
            value="preciso de um advogado",
            certainty=FactCertainty.EXPLICIT,
            source_message_ids=["m1"],
        )
    ]
    assert detect_explicit_human_request(_request(), _understanding(facts=facts)) is False


def test_inferred_certainty_rejected() -> None:
    facts = [
        CaseFact(
            key=EXPLICIT_HUMAN_REQUEST,
            value="true",
            certainty=FactCertainty.INFERRED,
            source_message_ids=["m1"],
        )
    ]
    assert detect_explicit_human_request(_request(), _understanding(facts=facts)) is False
