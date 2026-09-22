"""Safety signals: taxonomia, extrator, composição, política v4 e métricas."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from app.application.services import TriagePipelineService
from app.clients.types import StructuredCompletionResult
from app.config import Settings
from app.domain.enums import (
    ContentType,
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
from app.policies.resolution import (
    MANDATORY_POLICY_FLAGS,
    POLICY_VERSION,
    resolve_conservative_policy,
)
from app.prompts import clear_prompt_cache, load_safety_signals_prompt
from app.safety.compose import (
    SafetySignalSource,
    compose_effective_safety_signals,
)
from app.safety.taxonomy_risks import (
    SUBJECT_DERIVED_RISKS,
    derive_risks_from_validated_subject,
)
from app.schemas.inbound import ConversationMessage, TriageAnalysisRequest
from app.schemas.lead_understanding import (
    Ambiguity,
    LeadUnderstanding,
    ProceduralSituation,
    SafetyAssessment,
)
from app.schemas.proposal import ProposalStatus
from app.schemas.safety_signals import DetectedSafetyRisk, SafetySignals
from evaluations.metrics import compute_metrics, evaluate_targets, render_report
from evaluations.runner import score_case
from evaluations.stages import stages_for_success
from pydantic import ValidationError


def _understanding(**kwargs: object) -> LeadUnderstanding:
    base: dict[str, Any] = {
        "schema_version": "lead_understanding.v1",
        "intent": Intent.NEW_LEGAL_LEAD,
        "language": "pt-BR",
        "primary_area": LegalArea.CIVIL,
        "secondary_area": None,
        "subject": "debt_collection",
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


def _request(text: str = "msg") -> TriageAnalysisRequest:
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
                text=text,
                created_at=datetime(2026, 9, 20, 14, 0, tzinfo=UTC),
                reply_to_message_id=None,
            )
        ],
        known_facts=[],
        previous_decision=None,
    )


def _understanding_dict(**overrides: Any) -> dict[str, Any]:
    payload = _understanding().model_dump(mode="json")
    payload.update(overrides)
    return payload


def _safety_payload(**overrides: Any) -> dict[str, Any]:
    from tests.helpers.safety import safety_v2

    return safety_v2(**overrides)


class FakeClient:
    def __init__(self, completions: list[StructuredCompletionResult]) -> None:
        self._completions = list(completions)
        self.calls: list[str] = []

    async def complete_structured(self, **kwargs: object) -> StructuredCompletionResult:
        self.calls.append(str(kwargs.get("contract_label")))
        return self._completions.pop(0)


def _completion(data: dict[str, Any]) -> StructuredCompletionResult:
    return StructuredCompletionResult(
        parsed_content=data,
        model="fake",
        finish_reason="stop",
        latency_ms=1.0,
        retry_count=0,
        request_id="r",
        usage=None,
    )


# --- Taxonomia ---


@pytest.mark.parametrize(
    ("area", "subject", "expected"),
    [
        (LegalArea.CONSUMER, "banking_fraud", (RiskFlag.FRAUD_OR_SCAM,)),
        (LegalArea.CRIMINAL, "flagrant_arrest", (RiskFlag.ARREST_OR_DETENTION,)),
        (
            LegalArea.FAMILY,
            "child_custody",
            (RiskFlag.CHILD_OR_VULNERABLE_PERSON,),
        ),
        (
            LegalArea.FAMILY,
            "domestic_violence",
            (RiskFlag.VIOLENCE_OR_THREAT, RiskFlag.DOMESTIC_VIOLENCE),
        ),
        (
            LegalArea.SOCIAL_SECURITY,
            "prison_allowance",
            (RiskFlag.ARREST_OR_DETENTION,),
        ),
    ],
)
def test_taxonomy_derivation(area: LegalArea, subject: str, expected: tuple[RiskFlag, ...]) -> None:
    assert derive_risks_from_validated_subject(area, subject) == expected


def test_invalid_subject_does_not_derive() -> None:
    assert derive_risks_from_validated_subject(LegalArea.CONSUMER, "not_a_real_subject") == ()
    assert derive_risks_from_validated_subject(LegalArea.CRIMINAL, "banking_fraud") == ()


def test_compose_union_preserves_multi_source_and_order() -> None:
    extractor = SafetySignals(
        detected_risks=[
            DetectedSafetyRisk(
                risk=RiskFlag.FRAUD_OR_SCAM,
                source_message_ids=["m1"],
                evidence_summary="golpe informado",
            )
        ]
    )
    composed = compose_effective_safety_signals(
        model_detected=[RiskFlag.FRAUD_OR_SCAM],
        taxonomy_derived=[RiskFlag.FRAUD_OR_SCAM, RiskFlag.ARREST_OR_DETENTION],
        extractor=extractor,
    )
    # Operacional: só fraud (confirmado pelo extrator); arrest fica só candidato.
    assert [e.risk for e in composed.effective] == [RiskFlag.FRAUD_OR_SCAM]
    assert RiskFlag.ARREST_OR_DETENTION in composed.candidate_flags()
    assert RiskFlag.ARREST_OR_DETENTION not in composed.effective_flags()
    fraud = next(e for e in composed.effective if e.risk == RiskFlag.FRAUD_OR_SCAM)
    assert set(fraud.sources) == {
        SafetySignalSource.MODEL,
        SafetySignalSource.TAXONOMY,
        SafetySignalSource.EXTRACTOR,
    }
    assert fraud.evidence_summary == "golpe informado"
    assert fraud.source_message_ids == ["m1"]


def test_taxonomy_only_risk_is_candidate_not_operational() -> None:
    composed = compose_effective_safety_signals(
        model_detected=[],
        taxonomy_derived=[RiskFlag.ARREST_OR_DETENTION],
        extractor=SafetySignals(detected_risks=[]),
    )
    assert composed.taxonomy_derived == [RiskFlag.ARREST_OR_DETENTION]
    assert composed.effective == []
    assert RiskFlag.ARREST_OR_DETENTION in composed.candidate_flags()


def test_wrong_prison_subject_does_not_operationalize_arrest() -> None:
    """Subject incorreto + prazo no extrator: só imminent operacional."""
    composed = compose_effective_safety_signals(
        model_detected=[],
        taxonomy_derived=derive_risks_from_validated_subject(
            LegalArea.SOCIAL_SECURITY, "prison_allowance"
        ),
        extractor=SafetySignals(
            detected_risks=[
                DetectedSafetyRisk(
                    risk=RiskFlag.IMMINENT_DEADLINE,
                    source_message_ids=["m1"],
                    evidence_summary="audiência amanhã",
                )
            ]
        ),
    )
    assert RiskFlag.ARREST_OR_DETENTION in composed.taxonomy_derived
    assert RiskFlag.ARREST_OR_DETENTION not in composed.effective_flags()
    assert composed.effective_flags() == frozenset({RiskFlag.IMMINENT_DEADLINE})


def test_legitimate_prison_allowance_needs_extractor_for_operational() -> None:
    composed = compose_effective_safety_signals(
        model_detected=[],
        taxonomy_derived=[RiskFlag.ARREST_OR_DETENTION],
        extractor=SafetySignals(
            detected_risks=[
                DetectedSafetyRisk(
                    risk=RiskFlag.ARREST_OR_DETENTION,
                    source_message_ids=["m1"],
                    evidence_summary="cônjuge preso",
                )
            ]
        ),
    )
    assert RiskFlag.ARREST_OR_DETENTION in composed.effective_flags()
    entry = composed.effective[0]
    assert SafetySignalSource.TAXONOMY in entry.sources
    assert SafetySignalSource.EXTRACTOR in entry.sources


def test_subject_derived_map_covers_required_keys() -> None:
    assert set(SUBJECT_DERIVED_RISKS) >= {
        "banking_fraud",
        "flagrant_arrest",
        "child_custody",
        "domestic_violence",
        "prison_allowance",
    }


# --- Extrator / contrato ---


def test_safety_signals_prompt_and_hash() -> None:
    clear_prompt_cache()
    prompt = load_safety_signals_prompt("safety_signals.v2")
    assert prompt.version == "safety_signals.v4"
    assert len(prompt.sha256) == 64
    legacy = load_safety_signals_prompt("safety_signals.v1")
    assert legacy.version == "safety_signals.v2"
    assert "safety_signals.v1" in legacy.text
    assert "imminent_deadline" in prompt.text
    assert "fraud_or_scam" in prompt.text
    assert "dívida contestada" in prompt.text or "não reconheço" in prompt.text
    assert "affirmed" in prompt.text
    assert "hypothetical" in prompt.text
    assert "violence_or_threat" in prompt.text
    assert "pedido urgente" in prompt.text.lower() or "urgent_help_request" in prompt.text
    assert all(flag.value in prompt.text for flag in RiskFlag)


def test_safety_signals_rejects_unknown_risk() -> None:
    with pytest.raises(ValidationError):
        SafetySignals.model_validate(
            {
                "schema_version": "safety_signals.v1",
                "detected_risks": [
                    {
                        "risk": "not_a_risk",
                        "source_message_ids": ["m1"],
                        "evidence_summary": "x",
                    }
                ],
            }
        )


def test_safety_signals_rejects_duplicate_risk() -> None:
    with pytest.raises(ValidationError):
        SafetySignals.model_validate(
            {
                "schema_version": "safety_signals.v1",
                "detected_risks": [
                    {
                        "risk": "fraud_or_scam",
                        "source_message_ids": ["m1"],
                        "evidence_summary": "a",
                    },
                    {
                        "risk": "fraud_or_scam",
                        "source_message_ids": ["m1"],
                        "evidence_summary": "b",
                    },
                ],
            }
        )


def test_empty_risks_ok() -> None:
    signals = SafetySignals.model_validate(
        {"schema_version": "safety_signals.v1", "detected_risks": []}
    )
    assert signals.detected_risks == []


# --- Política v4 ---


def test_policy_version_is_v4() -> None:
    assert POLICY_VERSION == "conservative_action.v7"
    assert "imminent_deadline_requires_handoff" in MANDATORY_POLICY_FLAGS


def test_imminent_from_effective_risks_triggers_handoff() -> None:
    u = _understanding(
        primary_area=LegalArea.UNDETERMINED,
        subject="undetermined",
        safety=SafetyAssessment(
            level=UrgencyLevel.NORMAL,
            reason="sem risco no modelo",
            detected_risks=[],
            recommend_handoff=False,
        ),
    )
    step = decide_conservative_action(u, effective_risks=frozenset({RiskFlag.IMMINENT_DEADLINE}))
    assert step.action == TriageAction.HUMAN_HANDOFF
    assert "imminent_deadline_requires_handoff" in step.policy_flags


def test_imminent_from_understanding_still_works_without_effective_arg() -> None:
    u = _understanding(
        safety=SafetyAssessment(
            level=UrgencyLevel.HIGH,
            reason="prazo",
            detected_risks=[RiskFlag.IMMINENT_DEADLINE],
            recommend_handoff=False,
        )
    )
    step = decide_conservative_action(u)
    assert "imminent_deadline_requires_handoff" in step.policy_flags


def test_prison_allowance_arrest_no_handoff() -> None:
    u = _understanding(
        primary_area=LegalArea.SOCIAL_SECURITY,
        subject="prison_allowance",
        subsubjects=["spouse_relationship"],
        urgency=UrgencyLevel.NORMAL,
        safety=SafetyAssessment(
            level=UrgencyLevel.NORMAL,
            reason="prisão",
            detected_risks=[RiskFlag.ARREST_OR_DETENTION],
            recommend_handoff=False,
        ),
    )
    derived = derive_risks_from_validated_subject(u.primary_area, u.subject)
    composed = compose_effective_safety_signals(
        model_detected=list(u.safety.detected_risks),
        taxonomy_derived=derived,
        extractor=None,
    )
    resolution = resolve_conservative_policy(u, request=_request(), effective_safety=composed)
    assert "flagrant_arrest_requires_handoff" not in resolution.policy_flags
    assert "imminent_deadline_requires_handoff" not in resolution.policy_flags
    assert resolution.decision.action != TriageAction.HUMAN_HANDOFF or (
        resolution.policy_rule_id
        not in {"flagrant_arrest_requires_handoff", "imminent_deadline_requires_handoff"}
    )


def test_flagrant_still_subject_based() -> None:
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
    resolution = resolve_conservative_policy(
        u,
        request=_request(),
        effective_safety=compose_effective_safety_signals(
            model_detected=[],
            taxonomy_derived=derive_risks_from_validated_subject(u.primary_area, u.subject),
            extractor=None,
        ),
    )
    assert resolution.policy_rule_id == "flagrant_arrest_requires_handoff"
    assert resolution.skips_model_inference is True


def test_no_textual_matching_in_policy_v4() -> None:
    import inspect

    import app.policies as pol

    src = inspect.getsource(pol.decide_conservative_action)
    for needle in ("flagrante", "amanhã", "urgente", ".lower()", " in text"):
        assert needle not in src


@pytest.mark.asyncio
async def test_pipeline_extractor_imminent_skips_next_step() -> None:
    understanding = _understanding_dict(
        primary_area="undetermined",
        subject="undetermined",
        confidence=0.7,
        safety={
            "level": "normal",
            "reason": "sem risco modelo",
            "detected_risks": [],
            "recommend_handoff": False,
        },
        ambiguity={
            "present": True,
            "reason": "genérico",
            "needs_confirmation": True,
            "alternative_area": None,
            "alternative_subject": None,
        },
    )
    from tests.helpers.safety import occurrence, safety_v2

    safety = safety_v2(
        occurrences=[
            occurrence(
                occurrence_id="o1",
                risk="imminent_deadline",
                quote="audiência amanhã",
                summary="audiência amanhã",
                temporal="near_future",
            )
        ]
    )
    client = FakeClient([_completion(understanding), _completion(safety)])
    proposal = await TriagePipelineService(client, Settings()).run(
        _request("Tenho audiência amanhã")
    )
    assert client.calls == ["lead_understanding", "safety_signals"]
    assert proposal.status == ProposalStatus.SUCCESS
    assert proposal.triage_next_step is not None
    assert proposal.triage_next_step.requires_human_handoff is True
    assert (
        proposal.decision_provenance is not None
        and proposal.decision_provenance.policy_rule_id == "imminent_deadline_requires_handoff"
    )
    assert proposal.lead_understanding is not None
    assert proposal.lead_understanding.safety.detected_risks == []
    assert proposal.effective_safety_signals is not None
    assert RiskFlag.IMMINENT_DEADLINE in proposal.effective_safety_signals.effective_flags()


@pytest.mark.asyncio
async def test_pipeline_banking_fraud_taxonomy_derives_without_model_risk() -> None:
    understanding = _understanding_dict(
        primary_area="consumer",
        subject="banking_fraud",
        safety={
            "level": "normal",
            "reason": "ok",
            "detected_risks": [],
            "recommend_handoff": False,
        },
    )
    next_step = {
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
    # Sem extrator confirmando fraude: taxonomia só como candidato.
    client = FakeClient(
        [
            _completion(understanding),
            _completion(_safety_payload()),
            _completion(next_step),
        ]
    )
    proposal = await TriagePipelineService(client, Settings()).run(_request())
    assert proposal.effective_safety_signals is not None
    assert RiskFlag.FRAUD_OR_SCAM in proposal.effective_safety_signals.taxonomy_derived
    assert RiskFlag.FRAUD_OR_SCAM not in proposal.effective_safety_signals.effective_flags()
    assert proposal.lead_understanding is not None
    assert proposal.lead_understanding.safety.detected_risks == []


@pytest.mark.asyncio
async def test_pipeline_banking_fraud_operational_needs_extractor() -> None:
    understanding = _understanding_dict(
        primary_area="consumer",
        subject="banking_fraud",
        safety={
            "level": "normal",
            "reason": "ok",
            "detected_risks": [],
            "recommend_handoff": False,
        },
    )
    from tests.helpers.safety import occurrence, safety_v2

    safety = safety_v2(
        occurrences=[
            occurrence(
                occurrence_id="o1",
                risk="fraud_or_scam",
                quote="msg",
                summary="empréstimo não autorizado",
            )
        ]
    )
    next_step = {
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
    client = FakeClient([_completion(understanding), _completion(safety), _completion(next_step)])
    proposal = await TriagePipelineService(client, Settings()).run(_request())
    assert proposal.effective_safety_signals is not None
    assert RiskFlag.FRAUD_OR_SCAM in proposal.effective_safety_signals.effective_flags()
    entry = next(
        e for e in proposal.effective_safety_signals.effective if e.risk == RiskFlag.FRAUD_OR_SCAM
    )
    assert SafetySignalSource.TAXONOMY in entry.sources
    assert SafetySignalSource.EXTRACTOR in entry.sources


@pytest.mark.asyncio
async def test_pipeline_invalid_message_id_fail_closed() -> None:
    from tests.helpers.safety import occurrence, safety_v2

    understanding = _understanding_dict()
    safety = safety_v2(
        occurrences=[
            occurrence(
                occurrence_id="o1",
                risk="fraud_or_scam",
                quote="msg",
                summary="golpe",
                msg_ids=["ghost"],
            )
        ]
    )
    client = FakeClient([_completion(understanding), _completion(safety)])
    proposal = await TriagePipelineService(client, Settings()).run(_request())
    assert proposal.status == ProposalStatus.FAILED_CLOSED
    assert proposal.error is not None
    assert proposal.error.stage == "safety_signals"
    assert proposal.safe_fallback is not None
    assert proposal.safe_fallback.recommended_internal_action == "request_human_review"
    assert proposal.triage_next_step is None


# --- Métricas ---


def test_model_vs_effective_metrics() -> None:
    proposal = {
        "effective_safety_signals": {
            "model_detected": [],
            "taxonomy_derived": ["fraud_or_scam"],
            "extractor_detected": ["fraud_or_scam"],
            "candidate": [
                {
                    "risk": "fraud_or_scam",
                    "sources": ["taxonomy_derived", "extractor_detected"],
                    "evidence_summary": "golpe",
                    "source_message_ids": ["m1"],
                }
            ],
            "effective": [
                {
                    "risk": "fraud_or_scam",
                    "sources": ["taxonomy_derived", "extractor_detected"],
                    "evidence_summary": "golpe",
                    "source_message_ids": ["m1"],
                }
            ],
        }
    }
    row = score_case(
        expected={
            "case_id": "cons_banking_fraud",
            "acceptable_intents": ["new_legal_lead"],
            "required_risks": ["fraud_or_scam"],
            "forbidden_fact_keys": [],
        },
        understanding={
            "intent": "new_legal_lead",
            "primary_area": "consumer",
            "subject": "banking_fraud",
            "case_facts": [],
            "safety": {"detected_risks": []},
        },
        next_step={"action": "route_lead", "requires_human_handoff": False},
        fail_closed=False,
        latency_ms=1.0,
        retry_count=0,
        stages=stages_for_success(),
        pipeline_status="success",
        proposal=proposal,
    )
    assert row["model_risks_match"] is False
    assert row["model_risk_label_hits"] == 0
    assert row["effective_risks_match"] is True
    assert row["effective_risk_label_hits"] == 1

    metrics = compute_metrics([row], mode="live")
    assert metrics["model_risk_label_recall"] == 0.0
    assert metrics["effective_risk_label_recall"] == 100.0
    assert metrics["effective_risk_case_complete_rate"] == 100.0
    checks = evaluate_targets(metrics, mode="live")
    assert "risk_label_recall" not in checks
    assert "risk_case_complete" not in checks
    assert checks["effective_risk_label_recall"] is True


def test_legacy_artifact_without_effective_is_not_observed() -> None:
    row = score_case(
        expected={
            "case_id": "legacy",
            "acceptable_intents": ["new_legal_lead"],
            "required_risks": ["imminent_deadline"],
            "forbidden_fact_keys": [],
        },
        understanding={
            "intent": "new_legal_lead",
            "primary_area": "undetermined",
            "subject": "undetermined",
            "case_facts": [],
            "safety": {"detected_risks": []},
        },
        next_step={"action": "ask_question", "requires_human_handoff": False},
        fail_closed=False,
        latency_ms=1.0,
        retry_count=0,
        stages=stages_for_success(),
        pipeline_status="success",
        proposal=None,
    )
    assert row["effective_risks_match"] == "not_observed"
    metrics = compute_metrics([row], mode="live")
    assert metrics["model_risk_label_recall"] == 0.0
    # e2e: 0/1; observado: sem casos → None / not_evaluated na meta observada implícita.
    assert metrics["effective_risk_label_recall"] == 0.0
    assert metrics["effective_risk_label_recall_observed"] is None
    checks = evaluate_targets(metrics, mode="live")
    assert checks["effective_risk_label_recall"] is False
    report = render_report(metrics, checks, mode="live")
    assert "Recall riscos efetivos" in report
