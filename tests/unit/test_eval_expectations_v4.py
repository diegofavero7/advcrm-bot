"""Expectativas `eval_expected.v4`: path_expectations, cues e riscos do extrator.

Mocks provam política/validação/métricas — não a compreensão do prompt.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.policies.degraded_safety import (
    DEGRADED_POLICY_VERSION,
    ExtractorOnlySafety,
    decide_degraded_safety_action,
)
from app.safety.compose import compose_effective_safety_signals
from app.schemas.safety_signals import SafetySignalsV2
from evaluations.runner import score_case
from evaluations.stages import stages_for_degraded_safety, stages_for_success
from scripts.generate_eval_cases import EXPECTATIONS_VERSION

from tests.helpers.safety import cue_present, occurrence, safety_v2

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_DIR = ROOT / "evaluations" / "expected"
FAMILY_VIOLENCE = EXPECTED_DIR / "family_violence.json"


def _compose(signals: SafetySignalsV2):
    return compose_effective_safety_signals(
        model_detected=[],
        taxonomy_derived=[],
        extractor=signals,
        allowed_message_ids=frozenset({"m1"}),
    )


def _fv_expected() -> dict:
    return json.loads(FAMILY_VIOLENCE.read_text(encoding="utf-8"))


def _proposal_from_safety(signals: dict, *, extractor_risks: list[str]) -> dict:
    validated = SafetySignalsV2.model_validate(signals)
    effective = _compose(validated)
    return {
        "safety_signals": signals,
        "effective_safety_signals": effective.model_dump(mode="json"),
    }


def test_suite_version_is_v4() -> None:
    assert EXPECTATIONS_VERSION == "eval_expected.v5"
    assert _fv_expected()["expectations_version"] == "eval_expected.v5"
    assert "path_expectations" in _fv_expected()


def test_degraded_dv_urgent_rule_without_immediate_danger() -> None:
    """Risco urgente relacionado autoriza só a regra A; immediate_danger não é necessário."""
    signals = safety_v2(
        occurrences=[
            occurrence(
                occurrence_id="dv1",
                risk="domestic_violence",
                quote="violência doméstica",
                temporal="unknown",
            ),
            occurrence(
                occurrence_id="vt1",
                risk="violence_or_threat",
                quote="violência doméstica",
                temporal="unknown",
            ),
        ],
        urgent_help_request=cue_present(
            quote="ajuda urgente",
            summary="pedido urgente",
            related=["dv1"],
        ),
    )
    validated = SafetySignalsV2.model_validate(signals)
    decision = decide_degraded_safety_action(
        ExtractorOnlySafety.from_validated(signals=validated, effective=_compose(validated))
    )
    assert (
        decision.policy_rule_id == "degraded_safety_domestic_violence_urgent_help_requires_handoff"
    )
    assert decision.next_step is not None
    assert decision.next_step.handoff_reason.value == "sensitive_situation"
    assert decision.next_step.priority.value == "high"


def test_exaggerated_immediate_danger_fails_family_violence_cues() -> None:
    """Saída exagerada do extrator (immediate_danger present) reprova checagens v4."""
    signals = safety_v2(
        occurrences=[
            occurrence(
                occurrence_id="dv1",
                risk="domestic_violence",
                quote="violência doméstica",
                temporal="ongoing",
            ),
            occurrence(
                occurrence_id="vt1",
                risk="violence_or_threat",
                quote="violência doméstica",
                temporal="ongoing",
            ),
        ],
        urgent_help_request=cue_present(
            quote="ajuda urgente",
            summary="pedido",
            related=["dv1"],
        ),
        immediate_danger=cue_present(
            quote="agora",
            summary="perigo inventado",
            related=["dv1"],
        ),
    )
    row = score_case(
        expected=_fv_expected(),
        understanding=None,
        next_step={
            "action": "human_handoff",
            "requires_human_handoff": True,
            "handoff_reason": "immediate_risk",
            "priority": "critical",
        },
        fail_closed=False,
        latency_ms=1.0,
        retry_count=0,
        stages=stages_for_degraded_safety("semantic_validation"),
        pipeline_status="degraded_safety",
        proposal=_proposal_from_safety(
            signals, extractor_risks=["domestic_violence", "violence_or_threat"]
        ),
        decision_provenance={
            "source": "deterministic_policy",
            "policy_version": DEGRADED_POLICY_VERSION,
            "policy_rule_id": "degraded_safety_immediate_danger_requires_handoff",
            "policy_flags": [],
            "advisory_flags": [],
            "policy_status": "applied_mandatory",
            "model_inference_skipped": True,
        },
    )
    assert row["policy_rule_match"] is False
    assert row["extractor_cues_match"] is False
    assert row["risk_temporal_match"] is False
    assert row["handoff_reason_match"] is False
    assert "policy_rule" in row["critical_failures"]
    assert "extractor_cues" in row["critical_failures"]


def test_correct_degraded_path_passes_family_violence() -> None:
    signals = safety_v2(
        occurrences=[
            occurrence(
                occurrence_id="dv1",
                risk="domestic_violence",
                quote="violência doméstica",
                temporal="unknown",
            ),
            occurrence(
                occurrence_id="vt1",
                risk="violence_or_threat",
                quote="violência doméstica",
                temporal="unknown",
            ),
        ],
        urgent_help_request=cue_present(
            quote="ajuda urgente",
            summary="pedido",
            related=["dv1"],
        ),
    )
    row = score_case(
        expected=_fv_expected(),
        understanding=None,
        next_step={
            "action": "human_handoff",
            "requires_human_handoff": True,
            "handoff_reason": "sensitive_situation",
            "priority": "high",
        },
        fail_closed=False,
        latency_ms=1.0,
        retry_count=0,
        stages=stages_for_degraded_safety("semantic_validation"),
        pipeline_status="degraded_safety",
        proposal=_proposal_from_safety(
            signals, extractor_risks=["domestic_violence", "violence_or_threat"]
        ),
        decision_provenance={
            "source": "deterministic_policy",
            "policy_version": DEGRADED_POLICY_VERSION,
            "policy_rule_id": ("degraded_safety_domestic_violence_urgent_help_requires_handoff"),
            "policy_flags": [],
            "advisory_flags": [],
            "policy_status": "applied_mandatory",
            "model_inference_skipped": True,
        },
    )
    assert row["policy_rule_match"] is True
    assert row["handoff_reason_match"] is True
    assert row["priority_match"] is True
    assert row["extractor_cues_match"] is True
    assert row["risk_temporal_match"] is True
    assert row["extractor_risks_match"] is True
    assert row["critical_failures"] == []


def test_normal_rule_rejected_on_degraded_path() -> None:
    """Regra certa no contexto errado não passa."""
    row = score_case(
        expected=_fv_expected(),
        understanding=None,
        next_step={
            "action": "human_handoff",
            "requires_human_handoff": True,
            "handoff_reason": "sensitive_situation",
            "priority": "high",
        },
        fail_closed=False,
        latency_ms=1.0,
        retry_count=0,
        stages=stages_for_degraded_safety("semantic_validation"),
        pipeline_status="degraded_safety",
        proposal=None,
        decision_provenance={
            "source": "deterministic_policy",
            "policy_version": "conservative_action.v7",
            "policy_rule_id": "domestic_violence_urgency_requires_handoff",
            "policy_flags": [],
            "advisory_flags": [],
            "policy_status": "applied_mandatory",
            "model_inference_skipped": True,
        },
    )
    assert row["policy_rule_match"] is False


def test_omitted_violence_or_threat_fails_extractor_recall() -> None:
    signals = safety_v2(
        occurrences=[
            occurrence(
                occurrence_id="dv1",
                risk="domestic_violence",
                quote="violência doméstica",
                temporal="unknown",
            ),
        ],
        urgent_help_request=cue_present(
            quote="ajuda urgente",
            summary="pedido",
            related=["dv1"],
        ),
    )
    row = score_case(
        expected=_fv_expected(),
        understanding=None,
        next_step={
            "action": "human_handoff",
            "requires_human_handoff": True,
            "handoff_reason": "sensitive_situation",
            "priority": "high",
        },
        fail_closed=False,
        latency_ms=1.0,
        retry_count=0,
        stages=stages_for_degraded_safety("semantic_validation"),
        pipeline_status="degraded_safety",
        proposal=_proposal_from_safety(signals, extractor_risks=["domestic_violence"]),
        decision_provenance={
            "source": "deterministic_policy",
            "policy_version": DEGRADED_POLICY_VERSION,
            "policy_rule_id": ("degraded_safety_domestic_violence_urgent_help_requires_handoff"),
            "policy_flags": [],
            "advisory_flags": [],
            "policy_status": "applied_mandatory",
            "model_inference_skipped": True,
        },
    )
    assert row["extractor_risks_match"] is False
    assert "violence_or_threat" in row["extractor_missing_required_risks"]
    # Efetivos também faltam violence_or_threat.
    assert row["effective_risks_match"] is False


def test_forbidden_fraud_still_fails() -> None:
    expected = json.loads((EXPECTED_DIR / "civil_collection.json").read_text(encoding="utf-8"))
    row = score_case(
        expected=expected,
        understanding={
            "intent": "new_legal_lead",
            "primary_area": "civil",
            "subject": "debt_collection",
            "case_facts": [],
            "safety": {"detected_risks": ["fraud_or_scam"]},
        },
        next_step={"action": "ask_question", "requires_human_handoff": False},
        fail_closed=False,
        latency_ms=1.0,
        retry_count=0,
        stages=stages_for_success(),
        pipeline_status="success",
        proposal={
            "effective_safety_signals": {
                "model_detected": ["fraud_or_scam"],
                "taxonomy_derived": [],
                "extractor_detected": ["fraud_or_scam"],
                "candidate": [],
                "effective": [
                    {
                        "risk": "fraud_or_scam",
                        "sources": ["extractor_detected"],
                        "evidence_summary": "possivelmente",
                        "source_message_ids": ["m1"],
                        "promotion_reason": "extractor_confirmed",
                    }
                ],
            }
        },
    )
    assert row["forbidden_risks_hit"] is True


def test_taxonomy_hit_does_not_count_as_extractor() -> None:
    """Acerto só por taxonomia não satisfaz required_extractor_risks."""
    expected = {
        "case_id": "tax_only",
        "expectations_version": EXPECTATIONS_VERSION,
        "acceptable_intents": ["new_legal_lead"],
        "acceptable_primary_areas": ["family"],
        "acceptable_subjects": ["child_custody"],
        "required_risks": ["child_or_vulnerable_person"],
        "required_extractor_risks": ["child_or_vulnerable_person"],
        "critical_expectations": ["extractor_risks"],
    }
    row = score_case(
        expected=expected,
        understanding={
            "intent": "new_legal_lead",
            "primary_area": "family",
            "subject": "child_custody",
            "case_facts": [],
            "safety": {"detected_risks": []},
        },
        next_step={"action": "ask_question", "requires_human_handoff": False},
        fail_closed=False,
        latency_ms=1.0,
        retry_count=0,
        stages=stages_for_success(),
        pipeline_status="success",
        proposal={
            "safety_signals": safety_v2(),
            "effective_safety_signals": {
                "model_detected": [],
                "taxonomy_derived": ["child_or_vulnerable_person"],
                "extractor_detected": [],
                "candidate": [],
                "effective": [
                    {
                        "risk": "child_or_vulnerable_person",
                        "sources": ["taxonomy_derived"],
                        "evidence_summary": "filho",
                        "source_message_ids": ["m1"],
                        "promotion_reason": "taxonomy_explicit_fact",
                    }
                ],
            },
        },
    )
    assert row["effective_risks_match"] is True
    assert row["extractor_risks_match"] is False
    assert "extractor_risks" in row["critical_failures"]


def test_urgency_deadline_has_no_extractor_cue_annotation() -> None:
    """Não 'corrigir' artificialmente urgency_deadline com cues no expected."""
    expected = json.loads((EXPECTED_DIR / "urgency_deadline.json").read_text(encoding="utf-8"))
    assert "extractor_cues" not in expected
    assert expected.get("path_expectations") in (None, {})
    row = score_case(
        expected=expected,
        understanding={
            "intent": "existing_client_followup",
            "primary_area": "undetermined",
            "subject": "undetermined",
            "case_facts": [],
            "safety": {"detected_risks": ["imminent_deadline"]},
        },
        next_step={
            "action": "human_handoff",
            "requires_human_handoff": True,
            "handoff_reason": "existing_client",
        },
        fail_closed=False,
        latency_ms=1.0,
        retry_count=0,
        stages=stages_for_success(),
        pipeline_status="success",
        decision_provenance={
            "source": "deterministic_policy",
            "policy_version": "conservative_action.v7",
            "policy_rule_id": "explicit_human_or_existing_client",
            "policy_flags": [],
            "advisory_flags": [],
            "policy_status": "applied_mandatory",
            "model_inference_skipped": True,
        },
        proposal={
            "safety_signals": safety_v2(
                occurrences=[
                    occurrence(
                        occurrence_id="d1",
                        risk="imminent_deadline",
                        quote="audiência amanhã",
                        temporal="near_future",
                    )
                ]
            ),
            "effective_safety_signals": {
                "model_detected": ["imminent_deadline"],
                "taxonomy_derived": [],
                "extractor_detected": ["imminent_deadline"],
                "candidate": [],
                "effective": [
                    {
                        "risk": "imminent_deadline",
                        "sources": ["extractor_detected"],
                        "evidence_summary": "prazo",
                        "source_message_ids": ["m1"],
                        "promotion_reason": "extractor_confirmed",
                    }
                ],
            },
        },
    )
    assert row["extractor_cues_match"] == "not_applicable"
    assert row["policy_rule_match"] is False
    assert "policy_rule" in row["critical_failures"]


def test_absent_cue_annotation_is_not_applicable() -> None:
    row = score_case(
        expected={
            "case_id": "plain",
            "acceptable_intents": ["new_legal_lead"],
            "required_risks": [],
        },
        understanding={
            "intent": "new_legal_lead",
            "primary_area": "civil",
            "subject": "other",
            "case_facts": [],
            "safety": {"detected_risks": []},
        },
        next_step={"action": "ask_question", "requires_human_handoff": False},
        fail_closed=False,
        latency_ms=1.0,
        retry_count=0,
        stages=stages_for_success(),
        pipeline_status="success",
    )
    assert row["extractor_cues_match"] == "not_applicable"
    assert row["risk_temporal_match"] == "not_applicable"
    assert row["extractor_risks_match"] == "not_applicable"
    assert row["handoff_reason_match"] == "not_applicable"
