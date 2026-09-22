"""Promoção segura de riscos taxonômicos por evidência estruturada."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.enums import (
    ContentType,
    FactCertainty,
    Intent,
    LegalArea,
    MessageDirection,
    MessageRole,
    RiskFlag,
    TriageAction,
    TriageState,
    UrgencyLevel,
)
from app.policies import decide_conservative_action
from app.policies.resolution import POLICY_VERSION, resolve_conservative_policy
from app.safety.compose import (
    SafetySignalSource,
    compose_effective_safety_signals,
)
from app.safety.promotion import TAXONOMY_PROMOTION_RULES, PromotionReason
from app.safety.taxonomy_risks import derive_risks_from_validated_subject
from app.schemas.inbound import ConversationMessage, TriageAnalysisRequest
from app.schemas.lead_understanding import (
    Ambiguity,
    CaseFact,
    LeadUnderstanding,
    ProceduralSituation,
    SafetyAssessment,
)
from app.schemas.safety_signals import DetectedSafetyRisk, SafetySignals


def _fact(
    key: str,
    value: str,
    *,
    certainty: FactCertainty = FactCertainty.EXPLICIT,
    message_ids: list[str] | None = None,
) -> CaseFact:
    return CaseFact(
        key=key,
        value=value,
        certainty=certainty,
        source_message_ids=message_ids if message_ids is not None else ["m1"],
        from_trusted_crm_context=False,
    )


def _understanding(**kwargs: object) -> LeadUnderstanding:
    base = {
        "schema_version": "lead_understanding.v1",
        "intent": Intent.NEW_LEGAL_LEAD,
        "language": "pt-BR",
        "primary_area": LegalArea.FAMILY,
        "secondary_area": None,
        "subject": "child_custody",
        "subsubjects": [],
        "confidence": 0.9,
        "reasoning_summary": "resumo",
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


def _request() -> TriageAnalysisRequest:
    return TriageAnalysisRequest(
        event_id="e1",
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
                text="msg",
                created_at=datetime(2026, 9, 20, 14, 0, tzinfo=UTC),
                reply_to_message_id=None,
            )
        ],
        known_facts=[],
        previous_decision=None,
    )


def test_promotion_rules_cover_minimum_subjects() -> None:
    subjects = {r.subject for r in TAXONOMY_PROMOTION_RULES}
    assert subjects >= {
        "banking_fraud",
        "flagrant_arrest",
        "child_custody",
        "domestic_violence",
        "prison_allowance",
    }


def test_child_custody_explicit_fact_promotes() -> None:
    tax = derive_risks_from_validated_subject(LegalArea.FAMILY, "child_custody")
    composed = compose_effective_safety_signals(
        model_detected=[],
        taxonomy_derived=tax,
        extractor=SafetySignals(detected_risks=[]),
        subject="child_custody",
        case_facts=[_fact("legal_request_subject", "child_custody")],
        allowed_message_ids=frozenset({"m1"}),
    )
    assert RiskFlag.CHILD_OR_VULNERABLE_PERSON in composed.effective_flags()
    entry = composed.effective[0]
    assert entry.promotion_reason == PromotionReason.EXPLICIT_STRUCTURED_EVIDENCE
    assert entry.sources == [SafetySignalSource.TAXONOMY]


def test_child_custody_without_explicit_fact_does_not_promote() -> None:
    tax = derive_risks_from_validated_subject(LegalArea.FAMILY, "child_custody")
    composed = compose_effective_safety_signals(
        model_detected=[],
        taxonomy_derived=tax,
        extractor=SafetySignals(detected_risks=[]),
        subject="child_custody",
        case_facts=[],
        allowed_message_ids=frozenset({"m1"}),
    )
    assert composed.effective == []
    assert RiskFlag.CHILD_OR_VULNERABLE_PERSON in composed.candidate_flags()


def test_inferred_fact_does_not_promote() -> None:
    tax = derive_risks_from_validated_subject(LegalArea.FAMILY, "child_custody")
    composed = compose_effective_safety_signals(
        model_detected=[],
        taxonomy_derived=tax,
        extractor=SafetySignals(detected_risks=[]),
        subject="child_custody",
        case_facts=[
            _fact(
                "legal_request_subject",
                "child_custody",
                certainty=FactCertainty.INFERRED,
            )
        ],
        allowed_message_ids=frozenset({"m1"}),
    )
    assert composed.effective == []


def test_invalid_source_message_id_does_not_promote() -> None:
    tax = derive_risks_from_validated_subject(LegalArea.FAMILY, "child_custody")
    composed = compose_effective_safety_signals(
        model_detected=[],
        taxonomy_derived=tax,
        extractor=SafetySignals(detected_risks=[]),
        subject="child_custody",
        case_facts=[_fact("legal_request_subject", "child_custody", message_ids=["ghost"])],
        allowed_message_ids=frozenset({"m1"}),
    )
    assert composed.effective == []


def test_extractor_confirms_without_taxonomy_evidence() -> None:
    composed = compose_effective_safety_signals(
        model_detected=[],
        taxonomy_derived=[],
        extractor=SafetySignals(
            detected_risks=[
                DetectedSafetyRisk(
                    risk=RiskFlag.IMMINENT_DEADLINE,
                    source_message_ids=["m1"],
                    evidence_summary="audiência",
                )
            ]
        ),
        subject="undetermined",
        case_facts=[],
        allowed_message_ids=frozenset({"m1"}),
    )
    assert composed.effective_flags() == frozenset({RiskFlag.IMMINENT_DEADLINE})
    assert composed.effective[0].promotion_reason == PromotionReason.EXTRACTOR_CONFIRMED


def test_banking_fraud_unauthorized_loan_promotes() -> None:
    tax = derive_risks_from_validated_subject(LegalArea.CONSUMER, "banking_fraud")
    composed = compose_effective_safety_signals(
        model_detected=[],
        taxonomy_derived=tax,
        extractor=SafetySignals(detected_risks=[]),
        subject="banking_fraud",
        case_facts=[_fact("unauthorized_loan", "sim")],
        allowed_message_ids=frozenset({"m1"}),
    )
    assert RiskFlag.FRAUD_OR_SCAM in composed.effective_flags()
    assert composed.effective[0].promotion_reason == PromotionReason.EXPLICIT_STRUCTURED_EVIDENCE


def test_flagrant_prison_circumstances_promotes() -> None:
    tax = derive_risks_from_validated_subject(LegalArea.CRIMINAL, "flagrant_arrest")
    composed = compose_effective_safety_signals(
        model_detected=[],
        taxonomy_derived=tax,
        extractor=SafetySignals(detected_risks=[]),
        subject="flagrant_arrest",
        case_facts=[_fact("prison_circumstances", "flagrante")],
        allowed_message_ids=frozenset({"m1"}),
    )
    assert RiskFlag.ARREST_OR_DETENTION in composed.effective_flags()


def test_domestic_violence_multilabel_promotes() -> None:
    tax = derive_risks_from_validated_subject(LegalArea.FAMILY, "domestic_violence")
    composed = compose_effective_safety_signals(
        model_detected=[],
        taxonomy_derived=tax,
        extractor=SafetySignals(detected_risks=[]),
        subject="domestic_violence",
        case_facts=[_fact("violence_type", "doméstica")],
        allowed_message_ids=frozenset({"m1"}),
    )
    assert composed.effective_flags() == frozenset(
        {RiskFlag.DOMESTIC_VIOLENCE, RiskFlag.VIOLENCE_OR_THREAT}
    )


def test_prison_allowance_extractor_confirms() -> None:
    tax = derive_risks_from_validated_subject(LegalArea.SOCIAL_SECURITY, "prison_allowance")
    composed = compose_effective_safety_signals(
        model_detected=[],
        taxonomy_derived=tax,
        extractor=SafetySignals(
            detected_risks=[
                DetectedSafetyRisk(
                    risk=RiskFlag.ARREST_OR_DETENTION,
                    source_message_ids=["m1"],
                    evidence_summary="cônjuge preso",
                )
            ]
        ),
        subject="prison_allowance",
        case_facts=[_fact("relationship_to_detainee", "cônjuge")],
        allowed_message_ids=frozenset({"m1"}),
    )
    assert RiskFlag.ARREST_OR_DETENTION in composed.effective_flags()
    assert composed.effective[0].promotion_reason == PromotionReason.EXTRACTOR_CONFIRMED
    assert SafetySignalSource.TAXONOMY in composed.effective[0].sources
    assert SafetySignalSource.EXTRACTOR in composed.effective[0].sources


def test_false_prison_allowance_inferred_date_does_not_promote_arrest() -> None:
    """Regressão urgency_deadline: subject previdenciário + prison_date inferred."""
    tax = derive_risks_from_validated_subject(LegalArea.SOCIAL_SECURITY, "prison_allowance")
    composed = compose_effective_safety_signals(
        model_detected=[],
        taxonomy_derived=tax,
        extractor=SafetySignals(
            detected_risks=[
                DetectedSafetyRisk(
                    risk=RiskFlag.IMMINENT_DEADLINE,
                    source_message_ids=["m1"],
                    evidence_summary="audiência amanhã",
                )
            ]
        ),
        subject="prison_allowance",
        case_facts=[
            _fact("prison_date", "amanhã", certainty=FactCertainty.INFERRED),
        ],
        allowed_message_ids=frozenset({"m1"}),
    )
    assert composed.effective_flags() == frozenset({RiskFlag.IMMINENT_DEADLINE})
    assert RiskFlag.ARREST_OR_DETENTION in composed.candidate_flags()
    assert RiskFlag.ARREST_OR_DETENTION not in composed.effective_flags()
    assert composed.effective[0].promotion_reason == PromotionReason.EXTRACTOR_CONFIRMED


def test_model_only_imminent_deadline_does_not_handoff() -> None:
    u = _understanding(
        primary_area=LegalArea.UNDETERMINED,
        subject="undetermined",
        safety=SafetyAssessment(
            level=UrgencyLevel.HIGH,
            reason="prazo",
            detected_risks=[RiskFlag.IMMINENT_DEADLINE],
            recommend_handoff=False,
        ),
    )
    composed = compose_effective_safety_signals(
        model_detected=list(u.safety.detected_risks),
        taxonomy_derived=[],
        extractor=SafetySignals(detected_risks=[]),
        subject=u.subject,
        case_facts=[],
        allowed_message_ids=frozenset({"m1"}),
    )
    assert RiskFlag.IMMINENT_DEADLINE not in composed.effective_flags()
    resolution = resolve_conservative_policy(u, request=_request(), effective_safety=composed)
    assert "imminent_deadline_requires_handoff" not in resolution.policy_flags


def test_extractor_imminent_still_handoffs() -> None:
    u = _understanding(
        primary_area=LegalArea.UNDETERMINED,
        subject="undetermined",
        safety=SafetyAssessment(
            level=UrgencyLevel.NORMAL,
            reason="ok",
            detected_risks=[],
            recommend_handoff=False,
        ),
        ambiguity=Ambiguity(
            present=True,
            reason="genérico",
            needs_confirmation=True,
            alternative_area=None,
            alternative_subject=None,
        ),
    )
    composed = compose_effective_safety_signals(
        model_detected=[],
        taxonomy_derived=[],
        extractor=SafetySignals(
            detected_risks=[
                DetectedSafetyRisk(
                    risk=RiskFlag.IMMINENT_DEADLINE,
                    source_message_ids=["m1"],
                    evidence_summary="prazo",
                )
            ]
        ),
        subject=u.subject,
        case_facts=[],
        allowed_message_ids=frozenset({"m1"}),
    )
    step = decide_conservative_action(u, effective_risks=composed.effective_flags())
    assert step.action == TriageAction.HUMAN_HANDOFF
    assert "imminent_deadline_requires_handoff" in step.policy_flags


def test_flagrant_subject_still_mandatory_without_extractor() -> None:
    u = _understanding(
        primary_area=LegalArea.CRIMINAL,
        subject="flagrant_arrest",
        safety=SafetyAssessment(
            level=UrgencyLevel.NORMAL,
            reason="ok",
            detected_risks=[],
            recommend_handoff=False,
        ),
    )
    composed = compose_effective_safety_signals(
        model_detected=[],
        taxonomy_derived=derive_risks_from_validated_subject(LegalArea.CRIMINAL, "flagrant_arrest"),
        extractor=SafetySignals(detected_risks=[]),
        subject="flagrant_arrest",
        case_facts=[],
        allowed_message_ids=frozenset({"m1"}),
    )
    # Sem evidência explícita: arrest não é operacional — mas subject protege.
    assert RiskFlag.ARREST_OR_DETENTION not in composed.effective_flags()
    resolution = resolve_conservative_policy(u, request=_request(), effective_safety=composed)
    assert resolution.policy_rule_id == "flagrant_arrest_requires_handoff"
    assert POLICY_VERSION == "conservative_action.v7"


def test_dedup_and_order_deterministic() -> None:
    tax = derive_risks_from_validated_subject(LegalArea.FAMILY, "domestic_violence")
    a = compose_effective_safety_signals(
        model_detected=[RiskFlag.VIOLENCE_OR_THREAT],
        taxonomy_derived=tax,
        extractor=SafetySignals(detected_risks=[]),
        subject="domestic_violence",
        case_facts=[_fact("violence_type", "agressão")],
        allowed_message_ids=frozenset({"m1"}),
    )
    b = compose_effective_safety_signals(
        model_detected=[RiskFlag.VIOLENCE_OR_THREAT],
        taxonomy_derived=tax,
        extractor=SafetySignals(detected_risks=[]),
        subject="domestic_violence",
        case_facts=[_fact("violence_type", "agressão")],
        allowed_message_ids=frozenset({"m1"}),
    )
    assert [e.risk for e in a.effective] == [e.risk for e in b.effective]
    assert [e.risk for e in a.effective] == [
        RiskFlag.VIOLENCE_OR_THREAT,
        RiskFlag.DOMESTIC_VIOLENCE,
    ]


def test_detainee_imprisoned_explicit_can_promote_prison_allowance() -> None:
    tax = derive_risks_from_validated_subject(LegalArea.SOCIAL_SECURITY, "prison_allowance")
    composed = compose_effective_safety_signals(
        model_detected=[],
        taxonomy_derived=tax,
        extractor=SafetySignals(detected_risks=[]),
        subject="prison_allowance",
        case_facts=[_fact("detainee_imprisoned", "true")],
        allowed_message_ids=frozenset({"m1"}),
    )
    assert RiskFlag.ARREST_OR_DETENTION in composed.effective_flags()
    assert composed.effective[0].promotion_reason == PromotionReason.EXPLICIT_STRUCTURED_EVIDENCE
