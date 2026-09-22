"""Proteções novas de `conservative_action.v7`.

Cobre os dois defeitos confirmados no último live:

- violência doméstica **operacional** com urgência elevada roteava automaticamente;
- classificação totalmente indeterminada com confiança alta roteava automaticamente.

Estes testes provam regra e integração determinística. Não comprovam que o modelo
compreende as instruções do prompt — isso só o live mede.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.domain.enums import (
    ContentType,
    FactCertainty,
    HandoffReason,
    Intent,
    LegalArea,
    MessageDirection,
    MessageRole,
    RiskFlag,
    TriageAction,
    TriageState,
    UrgencyLevel,
)
from app.policies import (
    DOMESTIC_VIOLENCE_HANDOFF_FLAG,
    LEGAL_ISSUE_DESCRIPTION_KEY,
    UNDETERMINED_CLARIFICATION_FLAG,
    decide_conservative_action,
    is_fully_undetermined_classification,
    requires_domestic_violence_handoff,
)
from app.policies.resolution import (
    MANDATORY_POLICY_FLAGS,
    POLICY_VERSION,
    reconcile_next_step,
    resolve_conservative_policy,
)
from app.schemas.inbound import ConversationMessage, TriageAnalysisRequest
from app.schemas.lead_understanding import (
    Ambiguity,
    CaseFact,
    LeadUnderstanding,
    ProceduralSituation,
    SafetyAssessment,
)
from app.schemas.triage_next_step import TriageNextStep

NO_RISK = frozenset[RiskFlag]()
DV_RISKS = frozenset({RiskFlag.DOMESTIC_VIOLENCE, RiskFlag.VIOLENCE_OR_THREAT})


def _message(
    message_id: str = "m1",
    *,
    text: str = "mensagem",
    role: MessageRole = MessageRole.LEAD,
    reply_to: str | None = None,
) -> ConversationMessage:
    return ConversationMessage(
        message_id=message_id,
        role=role,
        direction=(
            MessageDirection.INBOUND if role == MessageRole.LEAD else MessageDirection.OUTBOUND
        ),
        content_type=ContentType.TEXT,
        text=text,
        created_at=datetime(2026, 9, 20, 14, 0, tzinfo=UTC),
        reply_to_message_id=reply_to,
    )


def _request(messages: list[ConversationMessage] | None = None) -> TriageAnalysisRequest:
    return TriageAnalysisRequest(
        event_id="e1",
        tenant_id="t1",
        lead_id="l1",
        conversation_id="c1",
        triage_state=TriageState.PENDING_CLASSIFICATION,
        source="whatsapp",
        messages=messages or [_message()],
        known_facts=[],
        previous_decision=None,
    )


def _understanding(**overrides: object) -> LeadUnderstanding:
    base: dict[str, object] = {
        "schema_version": "lead_understanding.v1",
        "intent": Intent.NEW_LEGAL_LEAD,
        "language": "pt-BR",
        "primary_area": LegalArea.FAMILY,
        "secondary_area": None,
        "subject": "domestic_violence",
        "subsubjects": [],
        "confidence": 0.95,
        "reasoning_summary": "resumo curto",
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
        "urgency": UrgencyLevel.HIGH,
        "safety": SafetyAssessment(
            level=UrgencyLevel.NORMAL,
            reason="relato de violencia",
            detected_risks=[RiskFlag.DOMESTIC_VIOLENCE, RiskFlag.VIOLENCE_OR_THREAT],
            recommend_handoff=False,
        ),
    }
    base.update(overrides)
    return LeadUnderstanding.model_validate(base)


def _undetermined(**overrides: object) -> LeadUnderstanding:
    defaults: dict[str, object] = {
        "primary_area": LegalArea.UNDETERMINED,
        "subject": "undetermined",
        "urgency": UrgencyLevel.NORMAL,
        "confidence": 0.95,
        "safety": SafetyAssessment(
            level=UrgencyLevel.NORMAL,
            reason="sem risco",
            detected_risks=[],
            recommend_handoff=False,
        ),
    }
    defaults.update(overrides)
    return _understanding(**defaults)


# --- 1. Violência doméstica efetiva + urgência alta + safety normal ---


def test_policy_version_is_v7() -> None:
    assert POLICY_VERSION == "conservative_action.v7"
    assert DOMESTIC_VIOLENCE_HANDOFF_FLAG in MANDATORY_POLICY_FLAGS
    assert UNDETERMINED_CLARIFICATION_FLAG in MANDATORY_POLICY_FLAGS


def test_domestic_violence_high_urgency_forces_handoff_even_with_safety_normal() -> None:
    understanding = _understanding()
    assert understanding.safety.level == UrgencyLevel.NORMAL
    assert understanding.safety.recommend_handoff is False

    step = decide_conservative_action(understanding, effective_risks=DV_RISKS)

    assert step.action == TriageAction.HUMAN_HANDOFF
    assert step.requires_human_handoff is True
    assert step.handoff_reason == HandoffReason.SENSITIVE_SITUATION
    assert step.policy_flags == [DOMESTIC_VIOLENCE_HANDOFF_FLAG]


def test_domestic_violence_immediate_urgency_keeps_stronger_existing_rule() -> None:
    """Urgência imediata continua na regra anterior, mais grave, sem duplicar."""
    step = decide_conservative_action(
        _understanding(urgency=UrgencyLevel.IMMEDIATE),
        effective_risks=DV_RISKS,
    )
    assert step.policy_flags == ["immediate_urgency_blocks_auto_route"]
    assert step.requires_human_handoff is True


def test_domestic_violence_rule_skips_next_inference_and_is_mandatory() -> None:
    resolution = resolve_conservative_policy(
        _understanding(),
        request=_request(),
        effective_safety=None,
    )
    # Sem effective_safety, a política usa detected_risks do modelo (compat).
    assert resolution.policy_rule_id == DOMESTIC_VIOLENCE_HANDOFF_FLAG
    assert resolution.mandatory is True
    assert resolution.skips_model_inference is True
    assert resolution.policy_version == POLICY_VERSION


def test_domestic_violence_normal_urgency_does_not_trigger_rule() -> None:
    understanding = _understanding(urgency=UrgencyLevel.NORMAL)
    assert requires_domestic_violence_handoff(understanding, DV_RISKS) is False
    step = decide_conservative_action(understanding, effective_risks=DV_RISKS)
    assert step.policy_flags != [DOMESTIC_VIOLENCE_HANDOFF_FLAG]


def test_domestic_violence_only_as_candidate_does_not_trigger_rule() -> None:
    """Risco não promovido a operacional não ativa a regra (sem menção histórica)."""
    understanding = _understanding()
    assert requires_domestic_violence_handoff(understanding, NO_RISK) is False
    step = decide_conservative_action(understanding, effective_risks=NO_RISK)
    assert step.policy_flags != [DOMESTIC_VIOLENCE_HANDOFF_FLAG]


# --- 2. Urgência alta isolada não ativa a regra ---


def test_high_urgency_alone_does_not_trigger_domestic_violence_rule() -> None:
    understanding = _understanding(
        primary_area=LegalArea.LABOR,
        subject="wage_claim",
        urgency=UrgencyLevel.HIGH,
        safety=SafetyAssessment(
            level=UrgencyLevel.NORMAL,
            reason="sem risco",
            detected_risks=[],
            recommend_handoff=False,
        ),
    )
    assert requires_domestic_violence_handoff(understanding, NO_RISK) is False
    step = decide_conservative_action(understanding, effective_risks=NO_RISK)
    assert step.action == TriageAction.ROUTE_LEAD
    assert step.requires_human_handoff is False


# --- 3. Prisão categórica isolada não ativa a regra ---


def test_categorical_arrest_with_high_urgency_does_not_trigger_domestic_violence_rule() -> None:
    understanding = _understanding(
        primary_area=LegalArea.SOCIAL_SECURITY,
        subject="prison_allowance",
        urgency=UrgencyLevel.HIGH,
        safety=SafetyAssessment(
            level=UrgencyLevel.NORMAL,
            reason="prisao relatada",
            detected_risks=[RiskFlag.ARREST_OR_DETENTION],
            recommend_handoff=False,
        ),
    )
    risks = frozenset({RiskFlag.ARREST_OR_DETENTION})
    assert requires_domestic_violence_handoff(understanding, risks) is False
    step = decide_conservative_action(understanding, effective_risks=risks)
    assert step.policy_flags != [DOMESTIC_VIOLENCE_HANDOFF_FLAG]
    assert step.requires_human_handoff is False


# --- 4/5. Classificação totalmente indeterminada ---


def test_fully_undetermined_high_confidence_does_not_route() -> None:
    understanding = _undetermined()
    assert is_fully_undetermined_classification(understanding) is True

    step = decide_conservative_action(understanding, effective_risks=NO_RISK)

    assert step.action == TriageAction.ASK_QUESTION
    assert step.policy_flags == [UNDETERMINED_CLARIFICATION_FLAG]
    assert step.selected_missing_information == [LEGAL_ISSUE_DESCRIPTION_KEY]
    assert step.missing_information == [LEGAL_ISSUE_DESCRIPTION_KEY]
    assert step.proposed_question
    assert step.requires_human_handoff is False


def test_needs_confirmation_false_does_not_remove_undetermined_protection() -> None:
    understanding = _undetermined(
        ambiguity=Ambiguity(
            present=False,
            reason=None,
            needs_confirmation=False,
            alternative_area=None,
            alternative_subject=None,
        )
    )
    assert understanding.ambiguity.needs_confirmation is False
    step = decide_conservative_action(understanding, effective_risks=NO_RISK)
    assert step.policy_flags == [UNDETERMINED_CLARIFICATION_FLAG]


def test_partial_ambiguity_stays_advisory() -> None:
    """Área definida e assunto indeterminado não viram regra obrigatória."""
    understanding = _understanding(
        primary_area=LegalArea.LABOR,
        subject="undetermined",
        ambiguity=Ambiguity(
            present=True,
            reason="modalidade nao informada",
            needs_confirmation=True,
            alternative_area=None,
            alternative_subject=None,
        ),
        urgency=UrgencyLevel.NORMAL,
        safety=SafetyAssessment(
            level=UrgencyLevel.NORMAL,
            reason="sem risco",
            detected_risks=[],
            recommend_handoff=False,
        ),
    )
    resolution = resolve_conservative_policy(understanding, request=_request())
    assert resolution.mandatory is False
    assert resolution.policy_rule_id == "needs_clarification"


def test_reconciliation_refuses_model_route_lead_when_fully_undetermined() -> None:
    understanding = _undetermined(
        ambiguity=Ambiguity(
            present=True,
            reason="sem materia",
            needs_confirmation=False,
            alternative_area=None,
            alternative_subject=None,
        )
    )
    resolution = resolve_conservative_policy(understanding, request=_request())
    model_proposal = TriageNextStep(
        action=TriageAction.ROUTE_LEAD,
        priority="normal",
        missing_information=[],
        selected_missing_information=[],
        proposed_question=None,
        requires_human_handoff=False,
        handoff_reason=None,
        policy_flags=[],
    )

    step, source = reconcile_next_step(
        policy=resolution,
        model_proposal=model_proposal,
        understanding=understanding,
    )

    assert source == "deterministic_policy"
    assert step.action == TriageAction.ASK_QUESTION
    assert step.policy_flags == [UNDETERMINED_CLARIFICATION_FLAG]


def test_reconciliation_keeps_model_route_lead_when_classification_exists() -> None:
    understanding = _understanding(
        primary_area=LegalArea.LABOR,
        subject="wage_claim",
        urgency=UrgencyLevel.NORMAL,
        safety=SafetyAssessment(
            level=UrgencyLevel.NORMAL,
            reason="sem risco",
            detected_risks=[],
            recommend_handoff=False,
        ),
    )
    resolution = resolve_conservative_policy(understanding, request=_request())
    model_proposal = TriageNextStep(
        action=TriageAction.ROUTE_LEAD,
        priority="normal",
        missing_information=[],
        selected_missing_information=[],
        proposed_question=None,
        requires_human_handoff=False,
        handoff_reason=None,
        policy_flags=[],
    )
    step, source = reconcile_next_step(
        policy=resolution,
        model_proposal=model_proposal,
        understanding=understanding,
    )
    assert source == "model"
    assert step.action == TriageAction.ROUTE_LEAD


# --- 6. Precedência preservada ---


@pytest.mark.parametrize(
    ("overrides", "expected_flag"),
    [
        ({"intent": Intent.EXISTING_CLIENT_CASE_STATUS}, "existing_client_case_status"),
        ({"intent": Intent.EXISTING_CLIENT_OTHER_REQUEST}, "explicit_human_or_existing_client"),
        ({"intent": Intent.SPAM, "primary_area": LegalArea.UNDETERMINED}, "non_legal_or_spam"),
        (
            {"intent": Intent.NON_LEGAL_CONTACT, "primary_area": LegalArea.UNDETERMINED},
            "non_legal_or_spam",
        ),
    ],
)
def test_intent_rules_precede_undetermined_clarification(
    overrides: dict[str, object], expected_flag: str
) -> None:
    understanding = _undetermined(subject="undetermined", **overrides)
    step = decide_conservative_action(understanding, effective_risks=NO_RISK)
    assert step.policy_flags == [expected_flag]


def test_low_confidence_precedes_undetermined_clarification() -> None:
    step = decide_conservative_action(_undetermined(confidence=0.2), effective_risks=NO_RISK)
    assert step.policy_flags == ["low_confidence"]


def test_questions_limit_precedes_undetermined_clarification() -> None:
    step = decide_conservative_action(_undetermined(), questions_asked=99, effective_risks=NO_RISK)
    assert step.policy_flags == ["max_questions"]


def test_immediate_urgency_precedes_undetermined_clarification() -> None:
    step = decide_conservative_action(
        _undetermined(urgency=UrgencyLevel.IMMEDIATE), effective_risks=NO_RISK
    )
    assert step.policy_flags == ["immediate_urgency_blocks_auto_route"]


def test_explicit_human_request_precedes_undetermined_clarification() -> None:
    understanding = _undetermined(
        case_facts=[
            CaseFact(
                key="explicit_human_request",
                value="true",
                certainty=FactCertainty.EXPLICIT,
                source_message_ids=["m1"],
                from_trusted_crm_context=False,
            )
        ]
    )
    resolution = resolve_conservative_policy(understanding, request=_request())
    assert resolution.policy_rule_id == "explicit_human_or_existing_client"
    assert resolution.decision.action == TriageAction.HUMAN_HANDOFF
    assert resolution.decision.handoff_reason == HandoffReason.USER_REQUESTED_HUMAN
    assert resolution.skips_model_inference is True
