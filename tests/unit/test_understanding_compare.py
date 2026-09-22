"""Infraestrutura de comparação de understanding — clientes falsos, sem rede."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.config import Settings
from evaluations.compare.manifest import (
    COMPARE_KIND,
    compare_manifests_compatible,
)
from evaluations.compare.metrics import (
    OUTCOME_BASELINE_ONLY,
    OUTCOME_BOTH_PASS,
    OUTCOME_CANDIDATE_ONLY,
    compute_understanding_metrics,
    paired_comparison,
    paired_outcome,
)
from evaluations.compare.runner import (
    CompareConfigError,
    RecordingClient,
    context_fingerprint_from_calls,
    load_compare_pairs,
    load_synthetic_map,
    require_live_variant_config,
    run_understanding_variant,
)

ROOT = Path(__file__).resolve().parents[2]
COMPARE_ROOT = ROOT / "evaluations" / "compare"
SYNTHETIC_BASELINE = COMPARE_ROOT / "synthetic" / "baseline_responses.json"
SYNTHETIC_CANDIDATE = COMPARE_ROOT / "synthetic" / "candidate_responses.json"


@pytest.fixture(scope="module", autouse=True)
def _ensure_reserved_cases() -> None:
    cases_dir = COMPARE_ROOT / "cases"
    if not cases_dir.is_dir() or not list(cases_dir.glob("*.json")):
        import runpy

        runpy.run_path(str(ROOT / "scripts" / "generate_compare_cases.py"), run_name="__main__")


@pytest.mark.asyncio
async def test_same_input_context_for_both_variants() -> None:
    pairs = load_compare_pairs(case_ids=["cmp_generic_lawyer"])
    synth = load_synthetic_map(SYNTHETIC_BASELINE)
    responses = [synth["cmp_generic_lawyer"], synth["cmp_generic_lawyer"]]
    client_a = RecordingClient(responses[:1])
    client_b = RecordingClient(responses[1:])
    settings = Settings(ai_runtime_enabled=False)
    await run_understanding_variant(
        variant_id="baseline",
        pairs=pairs,
        client=client_a,
        settings=settings,
        mode="offline",
        requested_model="synthetic",
        runtime_base_url="offline://a",
    )
    await run_understanding_variant(
        variant_id="candidate",
        pairs=pairs,
        client=client_b,
        settings=settings,
        mode="offline",
        requested_model="synthetic",
        runtime_base_url="offline://b",
    )
    fp_a = context_fingerprint_from_calls(client_a.calls)
    fp_b = context_fingerprint_from_calls(client_b.calls)
    assert fp_a == fp_b
    assert len(fp_a) == 1
    # Mesmo prompt+contexto: conteúdo user idêntico
    assert client_a.calls[0]["messages"] == client_b.calls[0]["messages"]
    assert client_a.calls[0]["contract_label"] == "lead_understanding"
    assert client_a.network_calls == 0
    assert client_b.network_calls == 0


@pytest.mark.asyncio
async def test_crm_provenance_validation_is_applied() -> None:
    pairs = load_compare_pairs(case_ids=["cmp_crm_empty_fake_trust"])
    synth = load_synthetic_map(SYNTHETIC_BASELINE)
    client = RecordingClient([synth["cmp_crm_empty_fake_trust"]])
    results, manifest, _ = await run_understanding_variant(
        variant_id="baseline",
        pairs=pairs,
        client=client,
        settings=Settings(ai_runtime_enabled=False),
        mode="offline",
        requested_model="synthetic",
        runtime_base_url="offline://synthetic",
    )
    assert len(results) == 1
    row = results[0]
    assert row["fail_closed"] is True
    assert row["crm_provenance_violation"] is True
    assert row["understanding_present"] is False
    assert (row.get("stages") or {}).get("semantic_validation") == "failed"
    assert (row.get("stages") or {}).get("policy_application") == "not_observed"
    assert manifest["kind"] == COMPARE_KIND


@pytest.mark.asyncio
async def test_e2e_denominator_keeps_failures() -> None:
    pairs = load_compare_pairs(case_ids=["cmp_crm_empty_fake_trust", "cmp_generic_lawyer"])
    synth = load_synthetic_map(SYNTHETIC_BASELINE)
    client = RecordingClient([synth["cmp_crm_empty_fake_trust"], synth["cmp_generic_lawyer"]])
    results, _, _ = await run_understanding_variant(
        variant_id="baseline",
        pairs=pairs,
        client=client,
        settings=Settings(ai_runtime_enabled=False),
        mode="offline",
        requested_model="synthetic",
        runtime_base_url="offline://synthetic",
    )
    metrics = compute_understanding_metrics(results)
    assert metrics["total_cases"] == 2
    assert metrics["cases_failed_closed"] == 1
    # e2e: falha entra no denominador (não some para inflar taxa)
    assert metrics["intent_e2e_denominator"] == 2
    assert metrics["cases_understanding_ok"] == 1


def test_paired_comparison_outcomes() -> None:
    baseline = [
        {
            "case_id": "a",
            "intent_match": True,
            "area_match": True,
            "subject_match": True,
            "required_facts_match": "not_applicable",
            "fact_value_match": "not_applicable",
            "forbidden_facts_hit": False,
            "model_risks_match": "not_applicable",
            "stages": {"contract_structural": "passed", "semantic_validation": "passed"},
            "crm_provenance_violation": False,
        },
        {
            "case_id": "b",
            "intent_match": True,
            "area_match": False,
            "subject_match": True,
            "required_facts_match": "not_applicable",
            "fact_value_match": "not_applicable",
            "forbidden_facts_hit": False,
            "model_risks_match": "not_applicable",
            "stages": {"contract_structural": "passed", "semantic_validation": "passed"},
            "crm_provenance_violation": False,
        },
    ]
    candidate = [
        {
            "case_id": "a",
            "intent_match": True,
            "area_match": True,
            "subject_match": True,
            "required_facts_match": "not_applicable",
            "fact_value_match": "not_applicable",
            "forbidden_facts_hit": False,
            "model_risks_match": "not_applicable",
            "stages": {"contract_structural": "passed", "semantic_validation": "passed"},
            "crm_provenance_violation": False,
        },
        {
            "case_id": "b",
            "intent_match": False,
            "area_match": True,
            "subject_match": True,
            "required_facts_match": "not_applicable",
            "fact_value_match": "not_applicable",
            "forbidden_facts_hit": True,
            "model_risks_match": "not_applicable",
            "stages": {"contract_structural": "passed", "semantic_validation": "passed"},
            "crm_provenance_violation": False,
        },
    ]
    paired = paired_comparison(baseline, candidate)
    assert paired["no_automatic_winner"] is True
    assert paired["by_criterion"]["intent_match"][OUTCOME_BOTH_PASS] == 1
    assert paired["by_criterion"]["intent_match"][OUTCOME_BASELINE_ONLY] == 1
    assert paired["by_criterion"]["area_match"][OUTCOME_CANDIDATE_ONLY] == 1
    assert paired_outcome(True, False) == OUTCOME_BASELINE_ONLY


def test_incompatible_manifests_rejected() -> None:
    baseline = {
        "kind": COMPARE_KIND,
        "shared": {
            "prompt_version": "lead_understanding.v9",
            "prompt_hash": "aaa",
            "schema_version": "lead_understanding.v1",
            "schema_hash": "s1",
            "taxonomy_version": "legal_subjects.v1",
            "taxonomy_hash": "t1",
            "expectations_version": "eval_expected.v3",
            "reserved_suite": "understanding_compare_reserved.v1",
            "temperature": 0.0,
            "max_tokens": 2048,
            "case_ids": ["a"],
            "case_hashes": {"a": "h1"},
        },
    }
    candidate = json.loads(json.dumps(baseline))
    candidate["shared"]["prompt_hash"] = "bbb"
    candidate["shared"]["case_hashes"] = {"a": "h2"}
    ok, reasons = compare_manifests_compatible(baseline, candidate)
    assert ok is False
    assert any("prompt_hash" in r for r in reasons)
    assert any("case_hashes" in r for r in reasons)


def test_missing_live_config_errors_without_silent_fallback() -> None:
    settings = Settings(ai_runtime_enabled=True)
    with pytest.raises(CompareConfigError, match="configuração ausente"):
        require_live_variant_config(variant_id="baseline", settings=settings, variant_settings=None)
    with pytest.raises(CompareConfigError, match="base_url"):
        require_live_variant_config(
            variant_id="baseline",
            settings=settings,
            variant_settings={"requested_model": "alias-only"},
        )


def test_metrics_without_coverage_are_not_evaluated() -> None:
    metrics = compute_understanding_metrics([])
    assert metrics["total_cases"] == 0
    assert metrics["intent_accuracy_e2e"] is None  # _pct(0,0) → None → not_evaluated no report
    assert metrics["required_facts_applicable_count"] == 0


@pytest.mark.asyncio
async def test_offline_makes_no_network_and_skips_policy_safety_next_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"safety": 0, "policy": 0, "next": 0}

    def boom_safety(*_a: object, **_k: object) -> None:
        calls["safety"] += 1
        raise AssertionError("safety não deve ser chamado")

    def boom_policy(*_a: object, **_k: object) -> None:
        calls["policy"] += 1
        raise AssertionError("política não deve ser chamada")

    monkeypatch.setattr(
        "app.application.services.SafetySignalsService.run", boom_safety, raising=False
    )
    monkeypatch.setattr(
        "app.policies.resolution.resolve_conservative_policy", boom_policy, raising=False
    )

    pairs = load_compare_pairs(case_ids=["cmp_generic_lawyer"])
    synth = load_synthetic_map(SYNTHETIC_BASELINE)
    client = RecordingClient([synth["cmp_generic_lawyer"]])
    results, manifest, recorder = await run_understanding_variant(
        variant_id="baseline",
        pairs=pairs,
        client=client,
        settings=Settings(ai_runtime_enabled=False),
        mode="offline",
        requested_model="synthetic",
        runtime_base_url="offline://synthetic",
    )
    assert recorder is not None
    assert recorder.network_calls == 0
    assert calls["safety"] == 0
    assert calls["policy"] == 0
    assert results[0]["stages"]["policy_application"] == "not_observed"
    assert "Extrator de safety" in " ".join(manifest.get("notes") or [])


def test_cli_offline_end_to_end(tmp_path: Path) -> None:
    import subprocess
    import sys

    out_b = tmp_path / "baseline"
    out_c = tmp_path / "candidate"
    py = sys.executable
    r1 = subprocess.run(
        [
            py,
            "-m",
            "evaluations.compare",
            "run",
            "--variant",
            "baseline",
            "--synthetic",
            str(SYNTHETIC_BASELINE),
            "--case-ids",
            "cmp_generic_lawyer",
            "cmp_disputed_charge",
            "--out",
            str(out_b),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r1.returncode == 0, r1.stderr
    assert (out_b / "manifest.json").is_file()
    assert (out_b / "report.md").is_file()
    r2 = subprocess.run(
        [
            py,
            "-m",
            "evaluations.compare",
            "run",
            "--variant",
            "candidate",
            "--synthetic",
            str(SYNTHETIC_CANDIDATE),
            "--case-ids",
            "cmp_generic_lawyer",
            "cmp_disputed_charge",
            "--out",
            str(out_c),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r2.returncode == 0, r2.stderr
    out_diff = tmp_path / "diff"
    r3 = subprocess.run(
        [
            py,
            "-m",
            "evaluations.compare",
            "diff",
            "--baseline",
            str(out_b),
            "--candidate",
            str(out_c),
            "--out",
            str(out_diff),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r3.returncode == 0, r3.stderr
    report = (out_diff / "report.md").read_text(encoding="utf-8")
    assert "Comparação pareada" in report
    assert "Sem vencedor automático" in report
    paired = json.loads((out_diff / "paired.json").read_text(encoding="utf-8"))
    assert paired["compatible"] is True
    criteria = paired["paired"]["by_criterion"]
    # candidate erra generic_lawyer (forbidden fact value) e/ou disputed_charge (risco)
    assert (
        criteria["fact_value_match"][OUTCOME_BASELINE_ONLY] >= 1
        or criteria["forbidden_facts_clean"][OUTCOME_BASELINE_ONLY] >= 1
        or sum(criteria["model_risks_match"].values()) >= 1
    )


def test_artifact_without_manifest_is_incompatible(tmp_path: Path) -> None:
    from evaluations.compare.__main__ import main

    bad = tmp_path / "legacy"
    bad.mkdir()
    (bad / "results.json").write_text('{"results": []}\n', encoding="utf-8")
    code = main(["diff", "--baseline", str(bad), "--candidate", str(bad)])
    assert code == 2


@pytest.mark.asyncio
async def test_supported_crm_fact_passes_validation() -> None:
    pairs = load_compare_pairs(case_ids=["cmp_crm_supported"])
    synth = load_synthetic_map(SYNTHETIC_BASELINE)
    client = RecordingClient([synth["cmp_crm_supported"]])
    results, _, _ = await run_understanding_variant(
        variant_id="baseline",
        pairs=pairs,
        client=client,
        settings=Settings(ai_runtime_enabled=False),
        mode="offline",
        requested_model="synthetic",
        runtime_base_url="offline://synthetic",
    )
    assert results[0]["fail_closed"] is False
    assert results[0]["crm_provenance_violation"] is False
    assert results[0]["understanding_present"] is True
    assert results[0]["fact_value_match"] is True
