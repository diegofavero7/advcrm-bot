"""Regressões: fidelidade, riscos, política conservative_action.v3."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from app.application.services import TriagePipelineService
from app.clients.types import StructuredCompletionResult
from app.clients.validation_diagnostics import sanitize_validation_error
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
from app.policies import (
    LEGAL_ISSUE_DESCRIPTION_KEY,
    UNDETERMINED_CLARIFICATION_QUESTION,
    decide_conservative_action,
    is_fully_undetermined_classification,
)
from app.policies.resolution import (
    ADVISORY_POLICY_FLAGS,
    MANDATORY_POLICY_FLAGS,
    POLICY_VERSION,
    resolve_conservative_policy,
)
from app.prompts import clear_prompt_cache, load_lead_understanding_prompt
from app.schemas.inbound import ConversationMessage, TriageAnalysisRequest
from app.schemas.lead_understanding import (
    Ambiguity,
    LeadUnderstanding,
    ProceduralSituation,
    SafetyAssessment,
)
from app.schemas.proposal import ProposalStatus
from evaluations.metrics import NOT_EVALUATED, compute_metrics, evaluate_targets, render_report
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


def _safety_payload(**overrides):
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


def test_prompt_is_lead_understanding_v6() -> None:
    clear_prompt_cache()
    prompt = load_lead_understanding_prompt()
    assert prompt.version == "lead_understanding.v9"
    assert "existing_client_case_status" in prompt.text
    assert "Nunca repita o subject dentro de subsubjects" in prompt.text
    assert "fraud_or_scam" in prompt.text
    assert "employment_recognition" in prompt.text
    assert "domestic_violence E violence_or_threat" in prompt.text or (
        "domestic_violence" in prompt.text and "violence_or_threat" in prompt.text
    )
    assert POLICY_VERSION == "conservative_action.v7"


def test_subject_repeated_as_subsubject_rejected() -> None:
    with pytest.raises(ValidationError) as caught:
        _understanding(
            primary_area=LegalArea.CONSUMER,
            subject="vehicle_purchase_irregularities",
            subsubjects=["vehicle_purchase_irregularities"],
        )
    details = sanitize_validation_error(caught.value)
    assert any(d["type"] == "subsubject_not_in_catalog" for d in details)


def test_vehicle_empty_subsubjects_ok() -> None:
    u = _understanding(
        primary_area=LegalArea.CONSUMER,
        subject="vehicle_purchase_irregularities",
        subsubjects=[],
    )
    assert u.subsubjects == []


def test_flagrant_policy_mandatory_skips_inference() -> None:
    u = _understanding(
        primary_area=LegalArea.CRIMINAL,
        subject="flagrant_arrest",
        confidence=0.9,
        urgency=UrgencyLevel.NORMAL,
        safety=SafetyAssessment(
            level=UrgencyLevel.NORMAL,
            reason="flagrante",
            detected_risks=[RiskFlag.ARREST_OR_DETENTION],
            recommend_handoff=False,
        ),
    )
    step = decide_conservative_action(u)
    assert step.action == TriageAction.HUMAN_HANDOFF
    assert step.requires_human_handoff is True
    assert "flagrant_arrest_requires_handoff" in step.policy_flags
    assert "flagrant_arrest_requires_handoff" in MANDATORY_POLICY_FLAGS
    resolution = resolve_conservative_policy(u, request=_request())
    assert resolution.mandatory is True
    assert resolution.skips_model_inference is True
    assert resolution.policy_rule_id == "flagrant_arrest_requires_handoff"
    assert resolution.policy_version == POLICY_VERSION


def test_imminent_deadline_policy_mandatory() -> None:
    u = _understanding(
        primary_area=LegalArea.UNDETERMINED,
        subject="undetermined",
        confidence=0.7,
        safety=SafetyAssessment(
            level=UrgencyLevel.HIGH,
            reason="prazo",
            detected_risks=[RiskFlag.IMMINENT_DEADLINE],
            recommend_handoff=False,
        ),
    )
    step = decide_conservative_action(u)
    assert step.action == TriageAction.HUMAN_HANDOFF
    assert "imminent_deadline_requires_handoff" in step.policy_flags
    resolution = resolve_conservative_policy(u, request=_request())
    assert resolution.skips_model_inference is True


def test_undetermined_classification_mandatory_ask_independent_of_confidence() -> None:
    u = _understanding(
        primary_area=LegalArea.UNDETERMINED,
        subject="undetermined",
        confidence=0.95,
        ambiguity=Ambiguity(
            present=True,
            reason="sem matéria",
            needs_confirmation=True,
            alternative_area=None,
            alternative_subject=None,
        ),
    )
    assert is_fully_undetermined_classification(u) is True
    step = decide_conservative_action(u)
    assert step.action == TriageAction.ASK_QUESTION
    assert step.missing_information == [LEGAL_ISSUE_DESCRIPTION_KEY]
    assert step.selected_missing_information == [LEGAL_ISSUE_DESCRIPTION_KEY]
    assert step.proposed_question == UNDETERMINED_CLARIFICATION_QUESTION
    assert step.requires_human_handoff is False
    assert step.handoff_reason is None
    assert step.proposed_question is not None
    assert step.proposed_question.count("?") <= 1
    assert "undetermined_classification_requires_clarification" in MANDATORY_POLICY_FLAGS
    resolution = resolve_conservative_policy(u, request=_request())
    assert resolution.mandatory is True
    assert resolution.skips_model_inference is True


def test_partial_ambiguity_remains_advisory() -> None:
    u = _understanding(
        primary_area=LegalArea.FAMILY,
        subject="child_support",
        confidence=0.95,
        ambiguity=Ambiguity(
            present=True,
            reason="confirmar detalhes",
            needs_confirmation=True,
            alternative_area=None,
            alternative_subject=None,
        ),
    )
    step = decide_conservative_action(u)
    assert "needs_clarification" in step.policy_flags
    assert "needs_clarification" in ADVISORY_POLICY_FLAGS
    resolution = resolve_conservative_policy(u, request=_request())
    assert resolution.mandatory is False
    assert resolution.skips_model_inference is False


def test_prison_allowance_arrest_risk_no_flagrant_handoff() -> None:
    u = _understanding(
        primary_area=LegalArea.SOCIAL_SECURITY,
        subject="prison_allowance",
        subsubjects=["spouse_relationship"],
        confidence=0.9,
        urgency=UrgencyLevel.NORMAL,
        safety=SafetyAssessment(
            level=UrgencyLevel.NORMAL,
            reason="prisão",
            detected_risks=[RiskFlag.ARREST_OR_DETENTION],
            recommend_handoff=False,
        ),
    )
    step = decide_conservative_action(u)
    assert step.action != TriageAction.HUMAN_HANDOFF or "flagrant_arrest" not in step.policy_flags
    assert "flagrant_arrest_requires_handoff" not in step.policy_flags
    assert "imminent_deadline_requires_handoff" not in step.policy_flags


def test_existing_client_status_still_mandatory() -> None:
    u = _understanding(
        intent=Intent.EXISTING_CLIENT_CASE_STATUS,
        primary_area=LegalArea.UNDETERMINED,
        subject="undetermined",
    )
    resolution = resolve_conservative_policy(u, request=_request())
    assert resolution.mandatory is True
    assert resolution.decision.action == TriageAction.HUMAN_HANDOFF


def test_existing_client_other_request_mandatory() -> None:
    u = _understanding(
        intent=Intent.EXISTING_CLIENT_OTHER_REQUEST,
        primary_area=LegalArea.UNDETERMINED,
        subject="undetermined",
    )
    resolution = resolve_conservative_policy(u, request=_request())
    assert resolution.policy_rule_id == "explicit_human_or_existing_client"
    assert resolution.skips_model_inference is True


@pytest.mark.asyncio
async def test_pipeline_undetermined_skips_second_inference() -> None:
    payload = _understanding_dict(
        primary_area="undetermined",
        subject="undetermined",
        confidence=0.95,
        ambiguity={
            "present": True,
            "reason": "genérico",
            "needs_confirmation": True,
            "alternative_area": None,
            "alternative_subject": None,
        },
    )
    client = FakeClient([_completion(payload), _completion(_safety_payload())])
    proposal = await TriagePipelineService(client, Settings()).run(_request("Preciso de ajuda"))
    assert client.calls == ["lead_understanding", "safety_signals"]
    assert proposal.status == ProposalStatus.SUCCESS
    assert proposal.triage_next_step is not None
    assert proposal.triage_next_step.action == TriageAction.ASK_QUESTION
    assert proposal.decision_provenance is not None
    assert proposal.decision_provenance.model_inference_skipped is True
    assert (
        proposal.decision_provenance.policy_rule_id
        == "undetermined_classification_requires_clarification"
    )


@pytest.mark.asyncio
async def test_pipeline_flagrant_skips_second_inference() -> None:
    payload = _understanding_dict(
        primary_area="criminal",
        subject="flagrant_arrest",
        safety={
            "level": "normal",
            "reason": "flagrante",
            "detected_risks": ["arrest_or_detention"],
            "recommend_handoff": False,
        },
    )
    client = FakeClient([_completion(payload), _completion(_safety_payload())])
    proposal = await TriagePipelineService(client, Settings()).run(_request())
    assert client.calls == ["lead_understanding", "safety_signals"]
    assert proposal.triage_next_step is not None
    assert proposal.triage_next_step.requires_human_handoff is True


def test_risk_label_recall_vs_case_completeness() -> None:
    rows = [
        score_case(
            expected={
                "case_id": "a",
                "acceptable_intents": ["new_legal_lead"],
                "required_risks": ["fraud_or_scam", "imminent_deadline"],
                "forbidden_fact_keys": [],
            },
            understanding={
                "intent": "new_legal_lead",
                "primary_area": "consumer",
                "subject": "banking_fraud",
                "case_facts": [],
                "safety": {"detected_risks": ["fraud_or_scam"]},
            },
            next_step={"action": "route_lead", "requires_human_handoff": False},
            fail_closed=False,
            latency_ms=1.0,
            retry_count=0,
            stages=stages_for_success(),
            pipeline_status="success",
        ),
        score_case(
            expected={
                "case_id": "b",
                "acceptable_intents": ["new_legal_lead"],
                "required_risks": ["arrest_or_detention"],
                "forbidden_fact_keys": [],
            },
            understanding={
                "intent": "new_legal_lead",
                "primary_area": "criminal",
                "subject": "flagrant_arrest",
                "case_facts": [],
                "safety": {"detected_risks": ["arrest_or_detention"]},
            },
            next_step={"action": "human_handoff", "requires_human_handoff": True},
            fail_closed=False,
            latency_ms=1.0,
            retry_count=0,
            stages=stages_for_success(),
            pipeline_status="success",
        ),
        score_case(
            expected={
                "case_id": "c",
                "acceptable_intents": ["spam"],
                "required_risks": [],
                "forbidden_fact_keys": [],
            },
            understanding={
                "intent": "spam",
                "primary_area": "other",
                "subject": "other",
                "case_facts": [],
                "safety": {"detected_risks": []},
            },
            next_step={"action": "ignore", "requires_human_handoff": False},
            fail_closed=False,
            latency_ms=1.0,
            retry_count=0,
            stages=stages_for_success(),
            pipeline_status="success",
        ),
    ]
    assert rows[0]["risk_label_hits"] == 1
    assert rows[0]["risk_label_required"] == 2
    assert rows[0]["missing_required_risks"] == ["imminent_deadline"]
    assert rows[0]["risks_match"] is False
    assert rows[1]["risks_match"] is True
    assert rows[2]["risks_match"] == "not_applicable"

    metrics = compute_metrics(rows, mode="live")
    # rótulos: 1+1 = 2 TP / 2+1 = 3 required → 66.67%
    assert metrics["risk_label_true_positives"] == 2
    assert metrics["risk_label_required_total"] == 3
    assert metrics["model_risk_label_recall"] == 66.67
    assert metrics["risk_label_recall"] == 66.67
    # completude modelo: 1 de 2 casos aplicáveis
    assert metrics["risk_case_complete_rate"] == 50.0
    assert metrics["model_risk_case_applicable_count"] == 2
    assert metrics["risk_not_applicable"] == 1
    # Sem effective_safety no proposal → effective not_observed;
    # e2e conta como miss (0%), observado fica sem denominador (None).
    assert metrics["effective_risk_label_recall"] == 0.0
    assert metrics["effective_risk_label_recall_e2e"] == 0.0
    assert metrics["effective_risk_label_recall_observed"] is None
    assert metrics["effective_risk_case_complete_rate"] == 0.0
    assert metrics["effective_risk_case_complete_rate_observed"] is None
    assert metrics["effective_risk_cases_not_observed"] == 2


def test_metrics_na_targets_are_not_evaluated() -> None:
    rows = [
        score_case(
            expected={
                "case_id": "x",
                "acceptable_intents": ["spam"],
                "handoff_required": None,
                "required_risks": [],
                "forbidden_fact_keys": [],
            },
            understanding={
                "intent": "spam",
                "primary_area": "other",
                "subject": "other",
                "case_facts": [],
                "safety": {"detected_risks": []},
            },
            next_step={"action": "ignore", "requires_human_handoff": False},
            fail_closed=False,
            latency_ms=1.0,
            retry_count=0,
            stages=stages_for_success(),
            pipeline_status="success",
        )
    ]
    metrics = compute_metrics(rows, mode="live")
    assert metrics["handoff_recall"] is None
    assert metrics["model_risk_label_recall"] is None
    assert metrics["effective_risk_label_recall"] is None
    assert metrics["risk_label_recall"] is None
    assert metrics["risk_case_complete_rate"] is None
    checks = evaluate_targets(metrics, mode="live")
    assert checks["handoff_recall"] == NOT_EVALUATED
    assert checks["model_risk_label_recall"] == NOT_EVALUATED
    assert checks["effective_risk_label_recall"] == NOT_EVALUATED
    assert checks["effective_risk_case_complete"] == NOT_EVALUATED
    assert "risk_label_recall" not in checks
    assert "risk_case_complete" not in checks
    assert checks["repeated_questions"] == NOT_EVALUATED
    report = render_report(metrics, checks, mode="live")
    assert "Recall riscos do modelo" in report
    assert "Recall riscos efetivos" in report
    assert "handoff_recall: not_evaluated" in report


def test_no_textual_matching_in_policy_module() -> None:
    import inspect

    import app.policies as pol

    src = inspect.getsource(pol.decide_conservative_action)
    for needle in ("flagrante", "amanhã", "urgente", ".lower()", " in text", "startswith"):
        assert needle not in src
