"""Testes da suíte de avaliação offline."""

from __future__ import annotations

from evaluations.metrics import (
    NOT_EVALUATED,
    SEMANTIC_METRIC_KEYS,
    SEMANTIC_TARGET_KEYS,
    compute_metrics,
    evaluate_targets,
    render_report,
)
from evaluations.runner import load_cases, run_offline_structure_check, score_case


def test_eval_cases_load_and_structure() -> None:
    pairs = load_cases()
    assert len(pairs) >= 36
    results = run_offline_structure_check(pairs)
    assert all(r["json_valid"] for r in results)
    assert all(r["schema_valid"] for r in results)


def test_metrics_and_report() -> None:
    results = [
        score_case(
            expected={
                "case_id": "x",
                "acceptable_intents": ["spam"],
                "acceptable_primary_areas": ["other"],
                "acceptable_subjects": [],
                "handoff_required": False,
                "required_risks": [],
                "forbidden_fact_keys": ["win_probability"],
            },
            understanding={
                "intent": "spam",
                "primary_area": "other",
                "subject": "other",
                "case_facts": [],
                "safety": {"detected_risks": []},
            },
            next_step={
                "action": "ignore",
                "requires_human_handoff": False,
            },
            fail_closed=False,
            latency_ms=10.0,
            retry_count=0,
            json_valid=True,
            schema_valid=True,
        )
    ]
    metrics = compute_metrics(results, mode="live")
    checks = evaluate_targets(metrics, mode="live")
    report = render_report(metrics, checks, mode="live")
    assert "Acurácia intenção" in report
    assert metrics["total_cases"] == 1
    assert metrics["intent_accuracy"] == 100.0
    assert checks["intent"] is True
    assert "PASS" in report or "FAIL" in report


def test_offline_report_marks_semantic_as_not_evaluated() -> None:
    """Offline não deve classificar métricas/metas semânticas como PASS/FAIL."""
    pairs = load_cases()
    results = run_offline_structure_check(pairs)
    metrics = compute_metrics(results, mode="offline")
    checks = evaluate_targets(metrics, mode="offline")
    report = render_report(metrics, checks, mode="offline")

    assert metrics["json_valid_rate"] == 100.0
    assert metrics["schema_valid_rate"] == 100.0
    for key in SEMANTIC_METRIC_KEYS:
        assert metrics[key] == NOT_EVALUATED

    assert checks["json_valid"] is True
    assert checks["schema_valid"] is True
    for key in SEMANTIC_TARGET_KEYS:
        assert checks[key] == NOT_EVALUATED

    assert "Modo: offline" in report
    assert f"Acurácia intenção: {NOT_EVALUATED}" in report
    assert f"intent: {NOT_EVALUATED}" in report
    assert f"handoff_recall: {NOT_EVALUATED}" in report
    assert "intent: PASS" not in report
    assert "intent: FAIL" not in report
    assert "area: PASS" not in report
    assert "area: FAIL" not in report

    # Live continua calculando valores semânticos a partir dos mesmos resultados.
    live_metrics = compute_metrics(results, mode="live")
    assert isinstance(live_metrics["intent_accuracy"], float)
    assert live_metrics["intent_accuracy"] != NOT_EVALUATED
