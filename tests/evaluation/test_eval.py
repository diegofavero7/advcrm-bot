"""Testes da suíte de avaliação offline e instrumentação."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from evaluations.metrics import (
    NOT_APPLICABLE,
    NOT_EVALUATED,
    NOT_OBSERVED,
    SEMANTIC_METRIC_KEYS,
    SEMANTIC_TARGET_KEYS,
    compute_metrics,
    evaluate_targets,
    render_report,
)
from evaluations.reanalyze import reanalyze
from evaluations.runner import load_cases, run_offline_structure_check, score_case
from evaluations.stages import stages_for_failure, stages_for_offline_request, stages_for_success

ROOT = Path(__file__).resolve().parents[2]


def test_eval_cases_load_and_structure() -> None:
    pairs = load_cases()
    assert len(pairs) >= 36
    results = run_offline_structure_check(pairs)
    assert all(r["schema_valid"] is True for r in results)
    assert all(r["json_valid"] == NOT_OBSERVED for r in results)
    assert all(r["stages"]["http_transport"] == NOT_OBSERVED for r in results)


def test_metrics_and_report() -> None:
    results = [
        score_case(
            expected={
                "case_id": "x",
                "acceptable_intents": ["spam"],
                "acceptable_primary_areas": ["other"],
                "acceptable_subjects": ["other"],
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
            stages=stages_for_success(),
            pipeline_status="success",
        )
    ]
    metrics = compute_metrics(results, mode="live")
    checks = evaluate_targets(metrics, mode="live")
    report = render_report(metrics, checks, mode="live")
    assert "Intenção e2e" in report
    assert metrics["total_cases"] == 1
    assert metrics["intent_accuracy_e2e"] == 100.0
    assert checks["intent"] is True
    assert "PASS" in report or "FAIL" in report


def test_offline_report_marks_semantic_as_not_evaluated() -> None:
    """Offline não deve classificar métricas/metas semânticas como PASS/FAIL."""
    pairs = load_cases()
    results = run_offline_structure_check(pairs)
    metrics = compute_metrics(results, mode="offline")
    checks = evaluate_targets(metrics, mode="offline")
    report = render_report(metrics, checks, mode="offline")

    assert metrics["schema_valid_rate"] == 100.0
    assert metrics["json_valid_rate"] is None
    for key in SEMANTIC_METRIC_KEYS:
        assert metrics[key] == NOT_EVALUATED

    assert checks["schema_valid"] is True
    assert checks["json_valid"] == NOT_EVALUATED
    for key in SEMANTIC_TARGET_KEYS:
        assert checks[key] == NOT_EVALUATED

    assert "Modo: offline" in report
    assert f"intent: {NOT_EVALUATED}" in report
    assert "intent: PASS" not in report
    assert "intent: FAIL" not in report

    live_metrics = compute_metrics(results, mode="live")
    assert live_metrics["intent_accuracy_e2e"] is not None
    assert live_metrics["intent_accuracy_e2e"] != NOT_EVALUATED


def test_empty_subject_expectation_is_not_applicable_not_hit() -> None:
    row = score_case(
        expected={
            "case_id": "audio_only",
            "acceptable_intents": ["undetermined"],
            "acceptable_primary_areas": ["undetermined"],
            "acceptable_subjects": [],
            "handoff_required": None,
            "required_risks": [],
            "forbidden_fact_keys": ["win_probability"],
        },
        understanding=None,
        next_step=None,
        fail_closed=True,
        latency_ms=1200.0,
        retry_count=2,
        stages=stages_for_failure("schema_validation", failure_stage="understanding"),
        pipeline_status="failed_closed",
        error={"category": "schema_validation", "stage": "understanding", "retry_count": 2},
    )
    assert row["subject_match"] == NOT_APPLICABLE
    assert row["predicted_subject"] is None
    assert row["intent_match"] == NOT_OBSERVED
    assert row["forbidden_facts_hit"] == NOT_OBSERVED
    assert row["invented_facts"] is False
    assert row["retry_count"] == 2
    assert row["latency_ms"] == 1200.0
    assert row["stages"]["contract_structural"] == "failed"
    assert row["stages"]["http_transport"] == "passed"
    assert row["stages"]["semantic_validation"] == NOT_OBSERVED


def test_json_valid_not_inferred_from_success_status() -> None:
    row = score_case(
        expected={
            "case_id": "y",
            "acceptable_intents": ["spam"],
            "acceptable_primary_areas": ["other"],
            "acceptable_subjects": ["other"],
            "handoff_required": None,
            "required_risks": [],
            "forbidden_fact_keys": [],
        },
        understanding=None,
        next_step=None,
        fail_closed=True,
        latency_ms=500.0,
        retry_count=2,
        stages=stages_for_failure("server", failure_stage="understanding"),
        pipeline_status="failed_closed",
    )
    assert row["pipeline_status"] == "failed_closed"
    assert row["json_valid"] == NOT_OBSERVED
    assert row["stages"]["json_parse"] == NOT_OBSERVED
    assert row["stages"]["http_transport"] == "failed"
    assert row["forbidden_facts_status"] == NOT_OBSERVED


def test_forbidden_facts_empty_list_not_applicable() -> None:
    row = score_case(
        expected={
            "case_id": "z",
            "acceptable_intents": ["spam"],
            "acceptable_primary_areas": ["other"],
            "acceptable_subjects": ["other"],
            "handoff_required": None,
            "required_risks": [],
            "forbidden_fact_keys": [],
        },
        understanding={
            "intent": "spam",
            "primary_area": "other",
            "subject": "other",
            "case_facts": [{"key": "anything", "value": "x"}],
            "safety": {"detected_risks": []},
        },
        next_step={"action": "ignore", "requires_human_handoff": False},
        fail_closed=False,
        latency_ms=1.0,
        retry_count=0,
        stages=stages_for_success(),
        pipeline_status="success",
    )
    assert row["forbidden_facts_status"] == NOT_APPLICABLE
    assert row["required_facts_match"] == NOT_APPLICABLE
    assert row["risks_match"] == NOT_APPLICABLE


def test_internal_fallback_is_not_attendance_handoff() -> None:
    row = score_case(
        expected={
            "case_id": "h",
            "acceptable_intents": ["new_legal_lead"],
            "acceptable_primary_areas": ["civil"],
            "acceptable_subjects": [],
            "handoff_required": True,
            "required_risks": [],
            "forbidden_fact_keys": ["win_probability"],
        },
        understanding=None,
        next_step=None,
        fail_closed=True,
        latency_ms=100.0,
        retry_count=0,
        stages=stages_for_failure("schema_validation", failure_stage="understanding"),
        pipeline_status="failed_closed",
        safe_fallback={"recommended_internal_action": "request_human_review"},
    )
    assert row["handoff_match"] == NOT_OBSERVED
    assert row["internal_review_fallback"] is True
    assert row["subject_match"] == NOT_APPLICABLE


def test_denominators_separate_e2e_and_valid_only() -> None:
    ok = score_case(
        expected={
            "case_id": "ok",
            "acceptable_intents": ["spam"],
            "acceptable_primary_areas": ["other"],
            "acceptable_subjects": ["other"],
            "handoff_required": None,
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
        next_step={"action": "ignore", "requires_human_handoff": False},
        fail_closed=False,
        latency_ms=10.0,
        retry_count=0,
        stages=stages_for_success(),
        pipeline_status="success",
    )
    fail = score_case(
        expected={
            "case_id": "fail",
            "acceptable_intents": ["spam"],
            "acceptable_primary_areas": ["other"],
            "acceptable_subjects": ["other"],
            "handoff_required": None,
            "required_risks": [],
            "forbidden_fact_keys": ["win_probability"],
        },
        understanding=None,
        next_step=None,
        fail_closed=True,
        latency_ms=2000.0,
        retry_count=2,
        stages=stages_for_failure("server", failure_stage="understanding"),
        pipeline_status="failed_closed",
    )
    metrics = compute_metrics([ok, fail], mode="live")
    assert metrics["cases_success"] == 1
    assert metrics["cases_failed_closed"] == 1
    assert metrics["intent_accuracy_valid_only"] == 100.0
    assert metrics["intent_accuracy_e2e"] == 50.0
    assert metrics["retries_total"] == 2
    assert metrics["latency_avg_ms_all"] == 1005.0
    assert metrics["latency_avg_ms_success"] == 10.0


def test_reanalyze_preserves_original_and_marks_unavailable(tmp_path: Path) -> None:
    source = ROOT / "artifacts/evaluations/20260921T150823Z_live"
    assert (source / "results.json").is_file()
    original_report = (source / "report.md").read_text(encoding="utf-8")
    out = tmp_path / "reanalysis"
    reanalyze(source, out)
    # Original intact
    assert (source / "report.md").read_text(encoding="utf-8") == original_report
    re_md = (out / "reanalysis.md").read_text(encoding="utf-8")
    re_json = json.loads((out / "reanalysis.json").read_text(encoding="utf-8"))
    assert "indisponíveis" in re_md.lower() or "unavailable" in re_md.lower()
    assert re_json["original_preserved"] is True
    assert re_json["legacy_anomaly_counts"]["subject_match_without_predicted_subject"] == 5
    assert re_json["legacy_anomaly_counts"]["fail_closed_latency_null"] == 20
    # Reanálise não inventa retries
    assert re_json["metrics_recomputed"]["retries_total"] == 0
    spam = next(r for r in re_json["results_rescored"] if r["case_id"] == "spam")
    assert spam["subject_match"] == NOT_APPLICABLE
    assert spam["data_availability"]["proposal"] == "unavailable"


def test_offline_stages_helper() -> None:
    stages = stages_for_offline_request(True)
    assert stages["contract_structural"] == "passed"
    assert stages["http_transport"] == NOT_OBSERVED


def test_eval_offline_includes_case_hash_and_request_snapshot() -> None:
    from evaluations.runner import write_artifacts

    pairs = load_cases()
    results = run_offline_structure_check(pairs[:1])
    assert results[0]["case_hash"]
    assert results[0]["request_snapshot"] is not None
    assert "acceptable_intents" not in json.dumps(results[0]["request_snapshot"])
    out = write_artifacts(results, mode="offline")
    case_files = list((out / "cases").glob("*.json"))
    assert case_files
    payload = json.loads(case_files[0].read_text(encoding="utf-8"))
    assert payload["case_hash"]
    assert payload["request_snapshot"]
    assert "expected" in payload


def test_case_selection_subset_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from evaluations import runner as runner_mod
    from evaluations.metrics import compute_metrics

    monkeypatch.setattr(runner_mod, "ROOT", tmp_path)
    ids = ["cons_financing", "family_support", "labor_dismissal", "traffic_area"]
    pairs = load_cases(case_ids=ids)
    assert [c["case_id"] for c, _ in pairs] == ids
    results = run_offline_structure_check(pairs)
    assert len(results) == 4
    metrics = compute_metrics(results, mode="offline")
    assert metrics["total_cases"] == 4
    out = runner_mod.write_artifacts(results, mode="offline", selected_case_ids=ids)
    index = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert index["selection"]["partial"] is True
    assert index["selection"]["selected_case_ids"] == ids
    assert index["metrics"]["total_cases"] == 4
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "parcial" in report.lower()
    assert "cons_financing" in report
    assert len(list((out / "cases").glob("*.json"))) == 4


def test_case_selection_dedupes_preserving_order() -> None:
    pairs = load_cases(case_ids=["spam", "non_legal", "spam", "undetermined", "non_legal"])
    assert [c["case_id"] for c, _ in pairs] == ["spam", "non_legal", "undetermined"]


def test_case_selection_default_loads_all() -> None:
    all_pairs = load_cases()
    selected_none = load_cases(case_ids=None)
    selected_empty = load_cases(case_ids=[])
    assert len(all_pairs) == len(selected_none) == len(selected_empty)
    assert len(all_pairs) >= 36


def test_invalid_case_id_exits_2_without_running(monkeypatch: pytest.MonkeyPatch) -> None:
    from evaluations.runner import main

    calls = {"offline": 0, "live": 0}

    def boom_offline(*_a: object, **_k: object) -> list[dict[str, object]]:
        calls["offline"] += 1
        return []

    async def boom_live(*_a: object, **_k: object) -> list[dict[str, object]]:
        calls["live"] += 1
        return []

    monkeypatch.setattr("evaluations.runner.run_offline_structure_check", boom_offline)
    monkeypatch.setattr("evaluations.runner.run_live", boom_live)
    code = main(["--case", "does_not_exist_xyz"])
    assert code == 2
    assert calls["offline"] == 0
    assert calls["live"] == 0
    code_live = main(["--live", "--case", "also_missing"])
    assert code_live == 2
    assert calls["live"] == 0


def test_missing_expected_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from evaluations import runner as runner_mod

    cases_dir = tmp_path / "cases"
    expected_dir = tmp_path / "expected"
    cases_dir.mkdir()
    expected_dir.mkdir()
    (cases_dir / "orphan.json").write_text(
        json.dumps({"case_id": "orphan", "request": {}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(runner_mod, "CASES_DIR", cases_dir)
    monkeypatch.setattr(runner_mod, "EXPECTED_DIR", expected_dir)
    calls = {"n": 0}

    def boom_offline(*_a: object, **_k: object) -> list[dict[str, object]]:
        calls["n"] += 1
        return []

    monkeypatch.setattr(runner_mod, "run_offline_structure_check", boom_offline)
    assert runner_mod.main(["--case", "orphan"]) == 2
    assert calls["n"] == 0
