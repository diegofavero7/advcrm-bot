"""Expectativas `eval_expected.v3`: caminho da decisão, fatos vigentes e denominadores.

Cobre a divergência "8 vs 6" do relatório anterior (ocorrências requeridas e
categorias distintas eram renderizadas com o mesmo rótulo) e o portão de
expectativas críticas.
"""

from __future__ import annotations

import json
from pathlib import Path

from evaluations.metrics import (
    BLOCKING_TARGET_KEYS,
    NOT_APPLICABLE,
    NOT_EVALUATED,
    NOT_OBSERVED,
    blocking_failures,
    compute_metrics,
    evaluate_targets,
    render_report,
)
from evaluations.runner import resolved_fact_values, score_case
from evaluations.stages import stages_for_success
from scripts.generate_eval_cases import EXPECTATIONS_VERSION

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_DIR = ROOT / "evaluations" / "expected"


def _expected(**kwargs: object) -> dict:
    data: dict = {
        "case_id": "x",
        "expectations_version": EXPECTATIONS_VERSION,
        "acceptable_intents": ["new_legal_lead"],
        "acceptable_primary_areas": ["family"],
        "acceptable_subjects": ["domestic_violence"],
        "acceptable_actions": [],
        "acceptable_policy_rules": [],
        "require_model_inference_skipped": None,
        "handoff_required": None,
        "required_risks": [],
        "forbidden_risks": [],
        "risks_exhaustive": False,
        "required_fact_keys": [],
        "forbidden_fact_keys": ["win_probability"],
        "required_fact_values": {},
        "forbidden_fact_values": {},
        "critical_expectations": [],
        "annotation_rationale": None,
    }
    data.update(kwargs)
    return data


def _understanding(**kwargs: object) -> dict:
    data: dict = {
        "intent": "new_legal_lead",
        "primary_area": "family",
        "subject": "domestic_violence",
        "case_facts": [],
        "safety": {"detected_risks": []},
    }
    data.update(kwargs)
    return data


def _score(**kwargs: object) -> dict:
    payload: dict = {
        "expected": _expected(),
        "understanding": _understanding(),
        "next_step": {"action": "human_handoff", "requires_human_handoff": True},
        "fail_closed": False,
        "latency_ms": 1.0,
        "retry_count": 0,
        "stages": stages_for_success(),
        "pipeline_status": "success",
    }
    payload.update(kwargs)
    return score_case(**payload)  # type: ignore[arg-type]


# --- Versões das expectativas ---


def test_every_expected_file_declares_the_same_version() -> None:
    files = sorted(EXPECTED_DIR.glob("*.json"))
    assert files
    versions = {
        json.loads(p.read_text(encoding="utf-8")).get("expectations_version") for p in files
    }
    assert versions == {EXPECTATIONS_VERSION}
    assert EXPECTATIONS_VERSION == "eval_expected.v5"


def test_annotated_critical_cases_carry_a_rationale() -> None:
    for path in sorted(EXPECTED_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("critical_expectations"):
            assert data.get("annotation_rationale"), path.name


# --- Regra de política e inferência pulada ---


def test_policy_rule_expectation_matches_provenance() -> None:
    row = _score(
        expected=_expected(
            acceptable_policy_rules=["domestic_violence_urgency_requires_handoff"],
            require_model_inference_skipped=True,
        ),
        decision_provenance={
            "policy_rule_id": "domestic_violence_urgency_requires_handoff",
            "policy_flags": ["domestic_violence_urgency_requires_handoff"],
            "model_inference_skipped": True,
        },
    )
    assert row["policy_rule_match"] is True
    assert row["model_inference_skipped_match"] is True


def test_handoff_from_another_rule_does_not_satisfy_rule_expectation() -> None:
    """`explicit_human` não passa só porque outra regra pediu handoff."""
    row = _score(
        expected=_expected(
            handoff_required=True,
            acceptable_policy_rules=["explicit_human_or_existing_client"],
            critical_expectations=["policy_rule"],
        ),
        decision_provenance={
            "policy_rule_id": "non_legal_or_spam",
            "policy_flags": ["non_legal_or_spam"],
            "model_inference_skipped": True,
        },
        next_step={"action": "request_human_review", "requires_human_handoff": True},
    )
    assert row["handoff_match"] is True
    assert row["policy_rule_match"] is False
    assert row["critical_failures"] == ["policy_rule"]


def test_missing_rule_annotation_is_not_applicable() -> None:
    row = _score(decision_provenance={"policy_rule_id": "non_legal_or_spam"})
    assert row["policy_rule_match"] == NOT_APPLICABLE
    assert row["model_inference_skipped_match"] == NOT_APPLICABLE
    assert row["critical_failures"] == []


def test_rule_expectation_without_provenance_is_not_observed() -> None:
    row = _score(expected=_expected(acceptable_policy_rules=["max_questions"]))
    assert row["policy_rule_match"] == NOT_OBSERVED


# --- Valores de fatos vigentes ---


def _facts(*pairs: tuple[str, str, list[str]]) -> list[dict]:
    return [
        {
            "key": key,
            "value": value,
            "certainty": "explicit",
            "source_message_ids": ids,
            "from_trusted_crm_context": False,
        }
        for key, value, ids in pairs
    ]


def test_resolved_fact_values_do_not_elect_a_winner() -> None:
    understanding = _understanding(
        case_facts=_facts(
            ("contributed_years", "2", ["m1"]),
            ("contributed_years", "5", ["m2"]),
        )
    )
    resolved, unresolved, all_values = resolved_fact_values(understanding)
    assert resolved == {}
    assert unresolved == ["contributed_years"]
    assert all_values == {"contributed_years": ["2", "5"]}


def test_conflicting_values_are_recorded_as_unresolved_not_success() -> None:
    """Achar "5" entre dois valores conflitantes não conta como acerto."""
    row = _score(
        expected=_expected(
            required_fact_values={"contributed_years": ["5", "5 anos"]},
            forbidden_fact_values={"contributed_years": ["2", "2 anos"]},
            critical_expectations=["fact_values"],
        ),
        understanding=_understanding(
            case_facts=_facts(
                ("contributed_years", "2", ["m1"]),
                ("contributed_years", "5", ["m2"]),
            )
        ),
        request_snapshot={
            "messages": [
                {"message_id": "m1", "reply_to_message_id": None, "role": "lead"},
                {"message_id": "m2", "reply_to_message_id": "m1", "role": "lead"},
            ]
        },
    )
    assert row["fact_value_match"] is False
    assert row["unresolved_annotated_facts"] == ["contributed_years"]
    assert row["critical_failures"] == ["fact_values"]


def test_single_current_value_satisfies_the_value_expectation() -> None:
    row = _score(
        expected=_expected(
            required_fact_values={"contributed_years": ["5", "5 anos"]},
            forbidden_fact_values={"contributed_years": ["2", "2 anos"]},
            critical_expectations=["fact_values"],
        ),
        understanding=_understanding(case_facts=_facts(("contributed_years", "5", ["m2"]))),
        request_snapshot={
            "messages": [
                {"message_id": "m1", "reply_to_message_id": None, "role": "lead"},
                {"message_id": "m2", "reply_to_message_id": "m1", "role": "lead"},
            ]
        },
    )
    assert row["fact_value_match"] is True
    assert row["unresolved_annotated_facts"] == []
    assert row["critical_failures"] == []


def test_stale_value_without_correction_fails_the_value_expectation() -> None:
    row = _score(
        expected=_expected(
            required_fact_values={"contributed_years": ["5"]},
            forbidden_fact_values={"contributed_years": ["2"]},
            critical_expectations=["fact_values"],
        ),
        understanding=_understanding(case_facts=_facts(("contributed_years", "2", ["m1"]))),
        request_snapshot={
            "messages": [{"message_id": "m1", "reply_to_message_id": None, "role": "lead"}]
        },
    )
    assert row["fact_value_match"] is False
    assert row["wrong_fact_values"] == ["contributed_years"]
    assert row["forbidden_fact_value_hits"] == ["contributed_years"]
    assert row["critical_failures"] == ["fact_values"]


def test_key_presence_and_value_correctness_are_distinct_metrics() -> None:
    row = _score(
        expected=_expected(
            required_fact_keys=["relationship_to_detainee"],
            required_fact_values={"relationship_to_detainee": ["conjuge"]},
        ),
        understanding=_understanding(
            case_facts=_facts(("relationship_to_detainee", "irmao", ["m1"]))
        ),
        request_snapshot={
            "messages": [{"message_id": "m1", "reply_to_message_id": None, "role": "lead"}]
        },
    )
    assert row["required_facts_match"] is True
    assert row["fact_value_match"] is False


def test_unsupported_canonical_fact_keys_are_caught_as_forbidden() -> None:
    row = _score(
        expected=_expected(
            forbidden_fact_keys=["explicit_human_request", "detainee_imprisoned"],
            critical_expectations=["required_facts"],
        ),
        understanding=_understanding(case_facts=_facts(("detainee_imprisoned", "false", ["m1"]))),
    )
    assert row["forbidden_facts_hit"] is True
    assert row["critical_failures"] == ["required_facts"]


# --- Invariante derivada e portão crítico ---


def test_undetermined_route_lead_is_flagged_without_annotation() -> None:
    row = _score(
        understanding=_understanding(primary_area="undetermined", subject="undetermined"),
        next_step={"action": "route_lead", "requires_human_handoff": False},
    )
    assert row["undetermined_route_violation"] is True
    assert "undetermined_route_lead" in row["critical_failures"]

    metrics = compute_metrics([row], mode="live")
    checks = evaluate_targets(metrics, mode="live")
    assert checks["undetermined_route_lead"] is False
    assert "undetermined_route_lead" in blocking_failures(checks)


def test_offline_shows_not_evaluated_instead_of_vacuous_zero() -> None:
    """Sem inferência nem política, zero falhas não comprova sucesso."""
    row = _score(
        expected=_expected(critical_expectations=["policy_rule", "fact_values"]),
        understanding=None,
        next_step=None,
        pipeline_status="validated_offline",
    )
    metrics = compute_metrics([row], mode="offline")
    assert metrics["critical_annotated_cases"] == 1
    assert metrics["critical_evaluable_cases"] == 0
    assert metrics["undetermined_route_evaluable_cases"] == 0
    assert metrics["critical_failed_cases"] == []

    checks = evaluate_targets(metrics, mode="offline")
    assert checks["critical_expectations"] == NOT_EVALUATED
    assert checks["undetermined_route_lead"] == NOT_EVALUATED
    assert blocking_failures(checks) == []

    report = render_report(metrics, checks, mode="offline")
    assert f"Expectativas críticas: {NOT_EVALUATED}" in report
    assert f"invariante): {NOT_EVALUATED}" in report
    assert f"NÃO resolvidos (valores divergentes): {NOT_EVALUATED}" in report
    assert "avaliáveis=0" in report
    # Portão não exercido não aprova: a linha de aprovação não pode afirmar sucesso.
    assert f"Aprovação operacional: {NOT_EVALUATED} → sem cobertura em:" in report
    assert "sem bloqueio por meta crítica" not in report


def test_handoff_required_false_without_next_step_is_correct_absence() -> None:
    """Sem triage_next_step e handoff_required=false = ausência correta de handoff.

    Revisão interna / fail-closed sem next_step não é handoff de atendimento.
    Offline ainda marca a meta agregada como not_evaluated.
    """
    row = _score(
        expected=_expected(handoff_required=False),
        understanding=None,
        next_step=None,
        pipeline_status="validated_offline",
    )
    assert row["handoff_match"] is True
    metrics = compute_metrics([row], mode="offline")
    assert metrics["handoff_forbidden_evaluated"] == 1
    assert metrics.get("handoff_incorrect_when_forbidden", 0) == 0
    report = render_report(metrics, evaluate_targets(metrics, mode="offline"), mode="offline")
    assert "Handoffs indevidos (só com handoff_required=false):" in report


def test_live_coverage_reports_failures_instead_of_not_evaluated() -> None:
    row = _score(
        understanding=_understanding(primary_area="undetermined", subject="undetermined"),
        next_step={"action": "route_lead", "requires_human_handoff": False},
    )
    metrics = compute_metrics([row], mode="live")
    assert metrics["undetermined_route_evaluable_cases"] == 1
    report = render_report(metrics, evaluate_targets(metrics, mode="live"), mode="live")
    assert "1 falhas em 1 avaliáveis" in report


def test_blocking_target_keys_are_evaluated_targets() -> None:
    metrics = compute_metrics([_score()], mode="live")
    checks = evaluate_targets(metrics, mode="live")
    for key in BLOCKING_TARGET_KEYS:
        assert key in checks


def test_critical_failure_blocks_even_with_other_targets_passing() -> None:
    row = _score(
        expected=_expected(
            acceptable_actions=["human_handoff"],
            acceptable_policy_rules=["explicit_human_or_existing_client"],
            critical_expectations=["policy_rule"],
        ),
        decision_provenance={"policy_rule_id": "non_legal_or_spam", "policy_flags": []},
    )
    metrics = compute_metrics([row], mode="live")
    checks = evaluate_targets(metrics, mode="live")
    assert metrics["action_match_rate"] == 100.0
    assert checks["effective_action"] is True
    assert checks["critical_expectations"] is False
    assert blocking_failures(checks) == ["critical_expectations"]


# --- Denominadores de risco separados ---


def test_risk_denominators_are_reported_separately() -> None:
    """Ocorrências (soma) ≠ categorias distintas (união) ≠ casos aplicáveis."""
    rows = [
        _score(
            expected=_expected(
                case_id="a",
                required_risks=["domestic_violence", "violence_or_threat"],
            ),
            proposal={
                "effective_safety_signals": {
                    "effective": [
                        {"risk": "domestic_violence", "sources": ["extractor_detected"]},
                        {"risk": "violence_or_threat", "sources": ["extractor_detected"]},
                    ]
                }
            },
        ),
        _score(
            expected=_expected(case_id="b", required_risks=["domestic_violence"]),
            proposal={
                "effective_safety_signals": {
                    "effective": [{"risk": "domestic_violence", "sources": ["extractor_detected"]}]
                }
            },
        ),
        _score(
            expected=_expected(case_id="c", required_risks=["arrest_or_detention"]),
            proposal={"effective_safety_signals": {"effective": []}},
        ),
    ]
    metrics = compute_metrics(rows, mode="live")

    assert metrics["risk_required_occurrences"] == 4
    assert metrics["risk_distinct_categories"] == 3
    assert metrics["risk_applicable_cases"] == 3
    assert metrics["risk_categories_observed"] == 2
    assert metrics["risk_categories_not_observed"] == 1
    assert metrics["effective_risk_label_required_total"] == metrics["risk_required_occurrences"]

    report = render_report(metrics, evaluate_targets(metrics, mode="live"), mode="live")
    assert "ocorrências requeridas: 4" in report
    assert "categorias distintas: 3" in report
    assert "casos aplicáveis: 3" in report
