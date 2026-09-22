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
    RiskFlag,
    TriageAction,
    UrgencyLevel,
)
from app.schemas.inbound import TriageAnalysisRequest
from app.schemas.lead_understanding import LeadUnderstanding
from app.schemas.triage_next_step import TriageNextStep
from app.taxonomy import validate_area_subject

# Chave canônica de missing_information (string aberta no contrato v1).
LEGAL_ISSUE_DESCRIPTION_KEY = "legal_issue_description"
UNDETERMINED_CLARIFICATION_QUESTION = (
    "Conte brevemente qual problema jurídico você precisa resolver?"
)
FLAGRANT_ARREST_SUBJECT = "flagrant_arrest"

UNDETERMINED_CLARIFICATION_FLAG = "undetermined_classification_requires_clarification"
DOMESTIC_VIOLENCE_HANDOFF_FLAG = "domestic_violence_urgency_requires_handoff"

# Níveis de urgência altos/imediatos já existentes no contrato v1.
ELEVATED_URGENCY_LEVELS: frozenset[UrgencyLevel] = frozenset(
    {UrgencyLevel.HIGH, UrgencyLevel.IMMEDIATE}
)


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


def is_fully_undetermined_classification(understanding: LeadUnderstanding) -> bool:
    """Ausência completa de área e assunto.

    Independente de confidence e de ``ambiguity.needs_confirmation``: sem área nem
    assunto não há destino para rotear, mesmo que o modelo afirme não precisar
    confirmar. Ambiguidade parcial (área ou assunto definidos) NÃO entra aqui e
    permanece recomendação.
    """
    return (
        understanding.primary_area == LegalArea.UNDETERMINED
        and understanding.subject == "undetermined"
    )


def build_undetermined_clarification_step() -> TriageNextStep:
    """Esclarecimento determinístico único para classificação totalmente indeterminada.

    Reutilizado pela precedência obrigatória e pela reconciliação final, para que
    não existam dois mecanismos divergentes com proveniências diferentes.
    """
    return TriageNextStep(
        action=TriageAction.ASK_QUESTION,
        priority=Priority.NORMAL,
        missing_information=[LEGAL_ISSUE_DESCRIPTION_KEY],
        selected_missing_information=[LEGAL_ISSUE_DESCRIPTION_KEY],
        proposed_question=UNDETERMINED_CLARIFICATION_QUESTION,
        requires_human_handoff=False,
        handoff_reason=None,
        policy_flags=[UNDETERMINED_CLARIFICATION_FLAG],
    )


def requires_domestic_violence_handoff(
    understanding: LeadUnderstanding,
    effective_risks: frozenset[RiskFlag],
) -> bool:
    """Encaminhamento operacional de violência doméstica com urgência elevada.

    Predicado exato: ``domestic_violence`` presente nos riscos **operacionais**
    (extrator confirmado ou taxonomia + fato explícito estruturado) E
    ``urgency in {high, immediate}``.

    Não exige ``safety.level=high`` nem ``safety.recommend_handoff=true``: a
    combinação acima já basta. Não generaliza para qualquer risco, qualquer
    urgency alta, nem para menção histórica sem o sinal operacional estruturado.
    """
    if RiskFlag.DOMESTIC_VIOLENCE not in effective_risks:
        return False
    return understanding.urgency in ELEVATED_URGENCY_LEVELS


def decide_conservative_action(
    understanding: LeadUnderstanding,
    *,
    explicit_human_request: bool = False,
    questions_asked: int = 0,
    settings: Settings | None = None,
    effective_risks: frozenset[RiskFlag] | None = None,
) -> TriageNextStep:
    """Produz decisão conservadora determinística a partir da compreensão.

    Precedência obrigatória (primeira correspondente vence) — conservative_action.v7:
    1. existing_client_case_status
    2. explicit_human_or_existing_client
    3. flagrant_arrest_requires_handoff  (subject estruturado)
    4. imminent_deadline_requires_handoff  (sinais operacionais / extractor)
    5. immediate_urgency_blocks_auto_route
    6. domestic_violence_urgency_requires_handoff  (operacional + urgência elevada)
    7. non_legal_or_spam
    8. low_confidence
    9. max_questions
    10. undetermined_classification_requires_clarification
    Depois: recomendações needs_clarification / high_confidence_route_candidate.
    Sem matching textual no lead. effective_risks = sinais operacionais quando
    fornecidos; senão understanding.safety.detected_risks (compat testes).
    """
    cfg = settings or get_settings()
    flags: list[str] = []
    risks = (
        effective_risks
        if effective_risks is not None
        else frozenset(understanding.safety.detected_risks)
    )

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

    if understanding.subject == FLAGRANT_ARREST_SUBJECT:
        return TriageNextStep(
            action=TriageAction.HUMAN_HANDOFF,
            priority=Priority.CRITICAL,
            missing_information=[],
            selected_missing_information=[],
            proposed_question=None,
            requires_human_handoff=True,
            handoff_reason=HandoffReason.CRIMINAL_EMERGENCY,
            policy_flags=["flagrant_arrest_requires_handoff"],
        )

    if RiskFlag.IMMINENT_DEADLINE in risks:
        return TriageNextStep(
            action=TriageAction.HUMAN_HANDOFF,
            priority=Priority.CRITICAL,
            missing_information=[],
            selected_missing_information=[],
            proposed_question=None,
            requires_human_handoff=True,
            handoff_reason=HandoffReason.LEGAL_DEADLINE_RISK,
            policy_flags=["imminent_deadline_requires_handoff"],
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

    if requires_domestic_violence_handoff(understanding, risks):
        return TriageNextStep(
            action=TriageAction.HUMAN_HANDOFF,
            priority=Priority.HIGH,
            missing_information=[],
            selected_missing_information=[],
            proposed_question=None,
            requires_human_handoff=True,
            handoff_reason=HandoffReason.SENSITIVE_SITUATION,
            policy_flags=[DOMESTIC_VIOLENCE_HANDOFF_FLAG],
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

    if is_fully_undetermined_classification(understanding):
        return build_undetermined_clarification_step()

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
