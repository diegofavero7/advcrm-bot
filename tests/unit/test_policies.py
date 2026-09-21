"""Testes de políticas determinísticas."""

from __future__ import annotations

from datetime import UTC, datetime

from app.config import Settings
from app.domain.enums import (
    ContentType,
    Intent,
    LegalArea,
    MessageDirection,
    MessageRole,
    TriageAction,
    UrgencyLevel,
)
from app.policies import (
    ConfidenceBand,
    UnsupportedContentDecision,
    check_area_subject_coherence,
    check_secondary_area_distinct,
    confidence_band,
    decide_conservative_action,
    detect_silent_reclassification,
    evaluate_unsupported_content,
    fail_closed_on_invalid,
    must_block_question_when_handoff_required,
    prioritize_explicit_human_request,
    questions_limit_reached,
    requires_review_for_low_confidence,
)
from app.schemas.inbound import ConversationMessage, TriageAnalysisRequest
from app.schemas.lead_understanding import (
    Ambiguity,
    LeadUnderstanding,
    ProceduralSituation,
    SafetyAssessment,
)


def _understanding(**kwargs: object) -> LeadUnderstanding:
    base = {
        "schema_version": "lead_understanding.v1",
        "intent": Intent.NEW_LEGAL_LEAD,
        "language": "pt-BR",
        "primary_area": LegalArea.CIVIL,
        "secondary_area": None,
        "subject": "debt_collection",
        "subsubjects": [],
        "confidence": 0.9,
        "reasoning_summary": "Relata cobrança civil",
        "participants": [],
        "case_facts": [],
        "procedural_situation": ProceduralSituation(
            stage=None,
            prior_request=None,
            prior_denial=None,
            existing_case=None,
            prior_attempts=None,
        ),
        "mentioned_documents": [],
        "documents_availability": "unknown",
        "ambiguity": Ambiguity(
            present=False,
            reason=None,
            needs_confirmation=False,
            alternative_area=None,
            alternative_subject=None,
        ),
        "urgency": UrgencyLevel.NORMAL,
        "safety": SafetyAssessment(
            level=UrgencyLevel.NORMAL,
            reason="ok",
            detected_risks=[],
            recommend_handoff=False,
        ),
    }
    base.update(kwargs)
    return LeadUnderstanding.model_validate(base)


def test_area_subject_and_secondary() -> None:
    assert check_area_subject_coherence(LegalArea.LABOR, "unpaid_severance")
    assert not check_area_subject_coherence(LegalArea.LABOR, "prison_allowance")
    assert check_secondary_area_distinct(LegalArea.LABOR, LegalArea.CIVIL)
    assert not check_secondary_area_distinct(LegalArea.LABOR, LegalArea.LABOR)


def test_confidence_bands_from_settings() -> None:
    settings = Settings(confidence_high_threshold=0.8, confidence_medium_threshold=0.6)
    assert confidence_band(0.85, settings) == ConfidenceBand.HIGH
    assert confidence_band(0.7, settings) == ConfidenceBand.MEDIUM
    assert confidence_band(0.5, settings) == ConfidenceBand.LOW
    assert requires_review_for_low_confidence(0.5, settings)


def test_human_request_and_handoff_blocks_question() -> None:
    assert prioritize_explicit_human_request(True)
    assert must_block_question_when_handoff_required(True, TriageAction.ASK_QUESTION)
    assert not must_block_question_when_handoff_required(False, TriageAction.ASK_QUESTION)


def test_silent_reclassification_and_question_limit() -> None:
    assert detect_silent_reclassification(
        LegalArea.CIVIL, "debt_collection", LegalArea.CONSUMER, "product_defect", False
    )
    assert not detect_silent_reclassification(
        LegalArea.CIVIL, "debt_collection", LegalArea.CONSUMER, "product_defect", True
    )
    settings = Settings(max_automated_questions=3)
    assert questions_limit_reached(3, settings)
    assert not questions_limit_reached(2, settings)


def test_unsupported_content_conditional() -> None:
    request = TriageAnalysisRequest(
        event_id="e",
        tenant_id="t",
        lead_id="l",
        conversation_id="c",
        triage_state="pending_classification",  # type: ignore[arg-type]
        source="whatsapp",  # type: ignore[arg-type]
        messages=[
            ConversationMessage(
                message_id="m1",
                role=MessageRole.LEAD,
                direction=MessageDirection.INBOUND,
                content_type=ContentType.AUDIO,
                text=None,
                created_at=datetime(2026, 9, 20, 14, 0, tzinfo=UTC),
                reply_to_message_id=None,
            ),
            ConversationMessage(
                message_id="m2",
                role=MessageRole.LEAD,
                direction=MessageDirection.INBOUND,
                content_type=ContentType.TEXT,
                text="Comprei um carro com problema",
                created_at=datetime(2026, 9, 20, 14, 1, tzinfo=UTC),
                reply_to_message_id=None,
            ),
        ],
        known_facts=[],
        previous_decision=None,
    )
    assert (
        evaluate_unsupported_content(request, content_indispensable=False)
        == UnsupportedContentDecision.CONTINUE
    )
    assert (
        evaluate_unsupported_content(request, content_indispensable=True)
        == UnsupportedContentDecision.REQUEST_HUMAN_REVIEW
    )


def test_fail_closed() -> None:
    assert fail_closed_on_invalid(contract_valid=False, settings=Settings(fail_closed=True))
    assert not fail_closed_on_invalid(contract_valid=True, settings=Settings(fail_closed=True))


def test_decide_immediate_and_case_status() -> None:
    urgent = _understanding(
        urgency=UrgencyLevel.IMMEDIATE,
        safety=SafetyAssessment(
            level=UrgencyLevel.IMMEDIATE,
            reason="risco",
            detected_risks=[],
            recommend_handoff=True,
        ),
    )
    step = decide_conservative_action(urgent)
    assert step.action == TriageAction.HUMAN_HANDOFF
    assert step.requires_human_handoff

    status = _understanding(intent=Intent.EXISTING_CLIENT_CASE_STATUS)
    step2 = decide_conservative_action(status)
    assert step2.handoff_reason is not None
    assert step2.handoff_reason.value == "case_status_request"


def test_decide_spam_max_questions_and_human() -> None:
    spam = _understanding(intent=Intent.SPAM, primary_area=LegalArea.OTHER, subject="other")
    step = decide_conservative_action(spam)
    assert step.action == TriageAction.IGNORE

    medium = _understanding(confidence=0.7)
    step_q = decide_conservative_action(medium)
    assert step_q.action == TriageAction.ASK_QUESTION

    settings = Settings(max_automated_questions=1)
    step_max = decide_conservative_action(
        _understanding(confidence=0.95), questions_asked=1, settings=settings
    )
    assert step_max.handoff_reason is not None
    assert step_max.handoff_reason.value == "maximum_questions_reached"

    step_human = decide_conservative_action(_understanding(), explicit_human_request=True)
    assert step_human.action == TriageAction.HUMAN_HANDOFF


def test_decide_low_confidence_and_route() -> None:
    low = _understanding(confidence=0.4)
    step = decide_conservative_action(low)
    assert step.action == TriageAction.REQUEST_HUMAN_REVIEW

    high = _understanding(confidence=0.92)
    step2 = decide_conservative_action(high)
    assert step2.action == TriageAction.ROUTE_LEAD
    assert step2.proposed_question is None
