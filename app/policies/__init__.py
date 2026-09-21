"""Políticas determinísticas puras — limites vêm de Settings."""

from __future__ import annotations

from enum import StrEnum

from app.config import Settings, get_settings
from app.domain.enums import (
    ContentType,
    HandoffReason,
    Intent,
    LegalArea,
    Priority,
    TriageAction,
    UrgencyLevel,
)
from app.schemas.inbound import TriageAnalysisRequest
from app.schemas.lead_understanding import LeadUnderstanding
from app.schemas.triage_next_step import TriageNextStep
from app.taxonomy import validate_area_subject


class ConfidenceBand(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class UnsupportedContentDecision(StrEnum):
    CONTINUE = "continue"
    REQUEST_HUMAN_REVIEW = "request_human_review"


def check_area_subject_coherence(area: LegalArea, subject: str) -> bool:
    return validate_area_subject(area, subject)


def check_secondary_area_distinct(primary: LegalArea, secondary: LegalArea | None) -> bool:
    if secondary is None:
        return True
    return secondary != primary


def confidence_band(confidence: float, settings: Settings | None = None) -> ConfidenceBand:
    cfg = settings or get_settings()
    if confidence >= cfg.confidence_high_threshold:
        return ConfidenceBand.HIGH
    if confidence >= cfg.confidence_medium_threshold:
        return ConfidenceBand.MEDIUM
    return ConfidenceBand.LOW


def requires_review_for_low_confidence(confidence: float, settings: Settings | None = None) -> bool:
    return confidence_band(confidence, settings) == ConfidenceBand.LOW


def prioritize_explicit_human_request(intent_or_text_flags: bool) -> bool:
    """Pedido explícito de humano tem prioridade de handoff."""
    return intent_or_text_flags


def must_block_question_when_handoff_required(
    requires_human_handoff: bool, action: TriageAction
) -> bool:
    return requires_human_handoff and action == TriageAction.ASK_QUESTION


def detect_silent_reclassification(
    previous_area: LegalArea | None,
    previous_subject: str | None,
    new_area: LegalArea,
    new_subject: str,
    explicitly_acknowledged: bool,
) -> bool:
    """Retorna True se houve reclassificação silenciosa (proibida)."""
    if previous_area is None or previous_subject is None:
        return False
    changed = previous_area != new_area or previous_subject != new_subject
    return changed and not explicitly_acknowledged


def questions_limit_reached(questions_asked: int, settings: Settings | None = None) -> bool:
    cfg = settings or get_settings()
    return questions_asked >= cfg.max_automated_questions


def evaluate_unsupported_content(
    request: TriageAnalysisRequest,
    *,
    content_indispensable: bool,
) -> UnsupportedContentDecision:
    """
    Conteúdo não suportado (áudio/imagem/documento/unsupported):
    - se o contexto textual já autoriza seguir → CONTINUE
    - se a mídia for indispensável para decidir → REQUEST_HUMAN_REVIEW
    Não promove human_handoff automático em todo caso.
    """
    has_unsupported = any(
        m.content_type
        in {
            ContentType.AUDIO,
            ContentType.IMAGE,
            ContentType.DOCUMENT,
            ContentType.UNSUPPORTED,
        }
        for m in request.messages
    )
    if not has_unsupported:
        return UnsupportedContentDecision.CONTINUE

    has_usable_text = any(
        m.content_type == ContentType.TEXT and m.text and m.text.strip()
        for m in request.messages
        if m.role.value == "lead"
    )
    if has_usable_text and not content_indispensable:
        return UnsupportedContentDecision.CONTINUE
    if content_indispensable:
        return UnsupportedContentDecision.REQUEST_HUMAN_REVIEW
    if not has_usable_text:
        return UnsupportedContentDecision.REQUEST_HUMAN_REVIEW
    return UnsupportedContentDecision.CONTINUE


def fail_closed_on_invalid(
    *,
    contract_valid: bool,
    settings: Settings | None = None,
) -> bool:
    """True quando o sistema deve rejeitar (fail-closed)."""
    cfg = settings or get_settings()
    if contract_valid:
        return False
    return cfg.fail_closed


def decide_conservative_action(
    understanding: LeadUnderstanding,
    *,
    explicit_human_request: bool = False,
    questions_asked: int = 0,
    settings: Settings | None = None,
) -> TriageNextStep:
    """Produz decisão conservadora determinística a partir da compreensão."""
    cfg = settings or get_settings()
    flags: list[str] = []

    if understanding.intent == Intent.EXISTING_CLIENT_CASE_STATUS:
        return TriageNextStep(
            action=TriageAction.HUMAN_HANDOFF,
            priority=Priority.CRITICAL,
            missing_information=[],
            selected_missing_information=[],
            proposed_question=None,
            requires_human_handoff=True,
            handoff_reason=HandoffReason.CASE_STATUS_REQUEST,
            policy_flags=["existing_client_case_status"],
        )

    if explicit_human_request or understanding.intent == Intent.EXISTING_CLIENT_OTHER_REQUEST:
        return TriageNextStep(
            action=TriageAction.HUMAN_HANDOFF,
            priority=Priority.HIGH,
            missing_information=[],
            selected_missing_information=[],
            proposed_question=None,
            requires_human_handoff=True,
            handoff_reason=(
                HandoffReason.USER_REQUESTED_HUMAN
                if explicit_human_request
                else HandoffReason.EXISTING_CLIENT
            ),
            policy_flags=["explicit_human_or_existing_client"],
        )

    if understanding.urgency == UrgencyLevel.IMMEDIATE or understanding.safety.recommend_handoff:
        return TriageNextStep(
            action=TriageAction.HUMAN_HANDOFF,
            priority=Priority.CRITICAL,
            missing_information=[],
            selected_missing_information=[],
            proposed_question=None,
            requires_human_handoff=True,
            handoff_reason=HandoffReason.IMMEDIATE_RISK,
            policy_flags=["immediate_urgency_blocks_auto_route"],
        )

    if understanding.intent in {Intent.SPAM, Intent.NON_LEGAL_CONTACT}:
        is_spam = understanding.intent == Intent.SPAM
        return TriageNextStep(
            action=TriageAction.IGNORE if is_spam else TriageAction.REQUEST_HUMAN_REVIEW,
            priority=Priority.LOW,
            missing_information=[],
            selected_missing_information=[],
            proposed_question=None,
            requires_human_handoff=not is_spam,
            handoff_reason=None if is_spam else HandoffReason.UNSUPPORTED_REQUEST,
            policy_flags=["non_legal_or_spam"],
        )

    band = confidence_band(understanding.confidence, cfg)
    if band == ConfidenceBand.LOW:
        flags.append("low_confidence")
        return TriageNextStep(
            action=TriageAction.REQUEST_HUMAN_REVIEW,
            priority=Priority.HIGH,
            missing_information=["classification_confirmation"],
            selected_missing_information=["classification_confirmation"],
            proposed_question=None,
            requires_human_handoff=True,
            handoff_reason=HandoffReason.LOW_CONFIDENCE,
            policy_flags=flags,
        )

    if questions_limit_reached(questions_asked, cfg):
        return TriageNextStep(
            action=TriageAction.HUMAN_HANDOFF,
            priority=Priority.NORMAL,
            missing_information=[],
            selected_missing_information=[],
            proposed_question=None,
            requires_human_handoff=True,
            handoff_reason=HandoffReason.MAXIMUM_QUESTIONS_REACHED,
            policy_flags=["max_questions"],
        )

    if band == ConfidenceBand.MEDIUM or understanding.ambiguity.present:
        flags.append("needs_clarification")
        question = (
            "Para eu encaminhar corretamente: o assunto principal é "
            f"{understanding.subject.replace('_', ' ')}?"
        )
        return TriageNextStep(
            action=TriageAction.ASK_QUESTION,
            priority=Priority.NORMAL,
            missing_information=["subject_confirmation"],
            selected_missing_information=["subject_confirmation"],
            proposed_question=question,
            requires_human_handoff=False,
            handoff_reason=None,
            policy_flags=flags,
        )

    return TriageNextStep(
        action=TriageAction.ROUTE_LEAD,
        priority=Priority.NORMAL,
        missing_information=[],
        selected_missing_information=[],
        proposed_question=None,
        requires_human_handoff=False,
        handoff_reason=None,
        policy_flags=["high_confidence_route_candidate"],
    )
