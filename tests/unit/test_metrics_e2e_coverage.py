"""Métricas e2e vs observadas + forbidden_risks."""

from __future__ import annotations

from evaluations.metrics import NOT_EVALUATED, NOT_OBSERVED, compute_metrics, evaluate_targets
from evaluations.runner import score_case
from evaluations.stages import stages_for_failure, stages_for_success


def _base_expected(**kwargs: object) -> dict:
    data: dict = {
        "case_id": "x",
        "acceptable_intents": ["new_legal_lead"],
        "acceptable_primary_areas": ["family"],
        "acceptable_subjects": ["child_custody"],
        "acceptable_actions": [],
        "handoff_required": None,
        "required_risks": ["child_or_vulnerable_person"],
        "forbidden_risks": [],
        "risks_exhaustive": False,
        "required_fact_keys": [],
        "forbidden_fact_keys": ["win_probability"],
    }
    data.update(kwargs)
    return data


def test_effective_e2e_counts_not_observed_as_miss() -> None:
    observed = score_case(
        expected=_base_expected(),
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
            "effective_safety_signals": {
                "effective": [
                    {
                        "risk": "child_or_vulnerable_person",
                        "sources": ["extractor_detected"],
                    }
                ]
            }
        },
    )
    blocked = score_case(
        expected=_base_expected(case_id="blocked"),
        understanding=None,
        next_step=None,
        fail_closed=True,
        latency_ms=1.0,
        retry_count=0,
        stages=stages_for_failure("schema_validation", failure_stage="semantic_validation"),
        pipeline_status="failed_closed",
    )
    assert blocked["effective_risks_match"] == NOT_OBSERVED
    metrics = compute_metrics([observed, blocked], mode="live")
    assert metrics["effective_risk_label_required_total"] == 2
    assert metrics["effective_risk_label_true_positives"] == 1
    assert metrics["effective_risk_label_recall_e2e"] == 50.0
    assert metrics["effective_risk_label_recall_observed"] == 100.0
    assert metrics["effective_risk_case_complete_rate_e2e"] == 50.0
    assert metrics["effective_risk_case_complete_rate_observed"] == 100.0
    checks = evaluate_targets(metrics, mode="live")
    assert checks["effective_risk_label_recall"] is False
    assert checks["effective_risk_case_complete"] is False


def test_empty_required_risks_does_not_forbid_all() -> None:
    row = score_case(
        expected=_base_expected(required_risks=[], forbidden_risks=[]),
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
                "effective": [{"risk": "fraud_or_scam", "sources": ["extractor_detected"]}]
            }
        },
    )
    assert row["forbidden_risks_status"] == "not_applicable"
    assert "fraud_or_scam" in row["unevaluated_extra_risks"]


def test_forbidden_risks_detect_false_positive() -> None:
    row = score_case(
        expected=_base_expected(
            required_risks=[],
            forbidden_risks=["fraud_or_scam"],
            expectations_version="eval_expected.v2",
        ),
        understanding={
            "intent": "new_legal_lead",
            "primary_area": "criminal",
            "subject": "police_inquiry",
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
                "effective": [{"risk": "fraud_or_scam", "sources": ["extractor_detected"]}]
            }
        },
    )
    assert row["forbidden_risks_hit"] is True
    assert "fraud_or_scam" in row["forbidden_risk_hits"]


def test_prompt_injection_expected_requires_vulnerable() -> None:
    import json
    from pathlib import Path

    path = Path("evaluations/expected/prompt_injection.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "child_or_vulnerable_person" in data["required_risks"]
    assert "fraud_or_scam" in data["forbidden_risks"]
    assert data.get("expectations_version") == "eval_expected.v5"


def test_offline_semantic_targets_not_evaluated() -> None:
    metrics = compute_metrics([], mode="offline")
    checks = evaluate_targets(metrics, mode="offline")
    assert checks["handoff_recall"] == NOT_EVALUATED
