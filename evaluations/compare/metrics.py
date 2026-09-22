"""Métricas de understanding e comparação pareada entre variantes.

Reutiliza ``score_case`` / convenções de ``evaluations.metrics``. Não recalcula
handoff, política, riscos efetivos nem sucesso do pipeline completo.
"""

from __future__ import annotations

from typing import Any

from evaluations.metrics import (
    NOT_APPLICABLE,
    NOT_EVALUATED,
    NOT_OBSERVED,
    _evaluable_matches,
    _format_value,
    _pct,
)

# Critérios pareados — cada um produz both_pass / baseline_only / …
PAIRED_CRITERIA: tuple[str, ...] = (
    "intent_match",
    "area_match",
    "subject_match",
    "required_facts_match",
    "fact_value_match",
    "forbidden_facts_clean",
    "model_risks_match",
    "structural_ok",
    "semantic_ok",
    "crm_provenance_clean",
)

OUTCOME_BOTH_PASS = "both_pass"
OUTCOME_BASELINE_ONLY = "baseline_only"
OUTCOME_CANDIDATE_ONLY = "candidate_only"
OUTCOME_BOTH_FAIL = "both_fail"
OUTCOME_NOT_EVALUABLE = "not_evaluable"


def _truthy_match(value: Any) -> bool | None:
    """True/False avaliável; None = não avaliável (n/a, not_observed, ausente)."""
    if value in {None, NOT_APPLICABLE, NOT_OBSERVED, NOT_EVALUATED}:
        return None
    if isinstance(value, bool):
        return value
    return None


def _structural_ok(row: dict[str, Any]) -> bool | str:
    stages = row.get("stages") or {}
    status = stages.get("contract_structural")
    if status == "passed":
        return True
    if status == "failed":
        return False
    return NOT_OBSERVED


def _semantic_ok(row: dict[str, Any]) -> bool | str:
    stages = row.get("stages") or {}
    status = stages.get("semantic_validation")
    if status == "passed":
        return True
    if status == "failed":
        return False
    return NOT_OBSERVED


def _forbidden_clean(row: dict[str, Any]) -> bool | str:
    hit = row.get("forbidden_facts_hit")
    if hit in {None, NOT_APPLICABLE, NOT_OBSERVED}:
        return hit if hit is not None else NOT_APPLICABLE
    return hit is False


def _crm_clean(row: dict[str, Any]) -> bool | str:
    viol = row.get("crm_provenance_violation")
    if viol in {None, NOT_APPLICABLE, NOT_OBSERVED}:
        return viol if viol is not None else NOT_APPLICABLE
    return viol is False


def criterion_value(row: dict[str, Any], criterion: str) -> bool | str:
    if criterion == "intent_match":
        return row.get("intent_match", NOT_OBSERVED)
    if criterion == "area_match":
        return row.get("area_match", NOT_OBSERVED)
    if criterion == "subject_match":
        return row.get("subject_match", NOT_OBSERVED)
    if criterion == "required_facts_match":
        return row.get("required_facts_match", NOT_APPLICABLE)
    if criterion == "fact_value_match":
        return row.get("fact_value_match", NOT_APPLICABLE)
    if criterion == "forbidden_facts_clean":
        return _forbidden_clean(row)
    if criterion == "model_risks_match":
        return row.get("model_risks_match", NOT_APPLICABLE)
    if criterion == "structural_ok":
        return _structural_ok(row)
    if criterion == "semantic_ok":
        return _semantic_ok(row)
    if criterion == "crm_provenance_clean":
        return _crm_clean(row)
    return NOT_OBSERVED


def paired_outcome(baseline_val: Any, candidate_val: Any) -> str:
    b = _truthy_match(baseline_val)
    c = _truthy_match(candidate_val)
    if b is None or c is None:
        return OUTCOME_NOT_EVALUABLE
    if b and c:
        return OUTCOME_BOTH_PASS
    if b and not c:
        return OUTCOME_BASELINE_ONLY
    if c and not b:
        return OUTCOME_CANDIDATE_ONLY
    return OUTCOME_BOTH_FAIL


def compute_understanding_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Métricas só de understanding — e2e vs condicionais a saída válida."""
    total = len(results)
    valid = [
        r
        for r in results
        if r.get("understanding_present") is True and r.get("fail_closed") is not True
    ]
    failed = [r for r in results if r.get("fail_closed")]

    intent_e2e_h, intent_e2e_n, intent_na = _evaluable_matches(results, "intent_match")
    area_e2e_h, area_e2e_n, area_na = _evaluable_matches(results, "area_match")
    subject_e2e_h, subject_e2e_n, subject_na = _evaluable_matches(results, "subject_match")

    intent_v_h, intent_v_n, _ = _evaluable_matches(valid, "intent_match")
    area_v_h, area_v_n, _ = _evaluable_matches(valid, "area_match")
    subject_v_h, subject_v_n, _ = _evaluable_matches(valid, "subject_match")

    req_facts = [r for r in results if r.get("required_facts_match") not in {None, NOT_APPLICABLE}]
    req_h, req_n, _ = _evaluable_matches(req_facts, "required_facts_match")

    fact_val = [r for r in results if r.get("fact_value_match") not in {None, NOT_APPLICABLE}]
    fv_h, fv_n, _ = _evaluable_matches(fact_val, "fact_value_match")

    forbidden_cov = [
        r for r in results if r.get("forbidden_facts_hit") not in {None, NOT_APPLICABLE}
    ]
    forbidden_hits = sum(1 for r in forbidden_cov if r.get("forbidden_facts_hit") is True)

    risk_needed = [r for r in results if r.get("required_risks")]
    model_risk_h, model_risk_n, _ = _evaluable_matches(risk_needed, "model_risks_match")

    unresolved = sorted(
        str(r.get("case_id")) for r in results if r.get("unresolved_annotated_facts")
    )
    crm_violations = sorted(
        str(r.get("case_id")) for r in results if r.get("crm_provenance_violation") is True
    )

    structural_failed = sum(
        1 for r in results if (r.get("stages") or {}).get("contract_structural") == "failed"
    )
    semantic_failed = sum(
        1 for r in results if (r.get("stages") or {}).get("semantic_validation") == "failed"
    )

    lat_all = [float(r["latency_ms"]) for r in results if r.get("latency_ms") is not None]
    lat_ok = [float(r["latency_ms"]) for r in valid if r.get("latency_ms") is not None]

    return {
        "scope": "understanding_only",
        "total_cases": total,
        "cases_understanding_ok": len(valid),
        "cases_failed_closed": len(failed),
        "structural_failed_cases": structural_failed,
        "semantic_failed_cases": semantic_failed,
        "crm_provenance_violation_cases": crm_violations,
        "unresolved_fact_cases": unresolved,
        "intent_accuracy_e2e": _pct(intent_e2e_h, intent_e2e_n),
        "intent_accuracy_valid_only": _pct(intent_v_h, intent_v_n),
        "area_accuracy_e2e": _pct(area_e2e_h, area_e2e_n),
        "area_accuracy_valid_only": _pct(area_v_h, area_v_n),
        "subject_accuracy_e2e": _pct(subject_e2e_h, subject_e2e_n),
        "subject_accuracy_valid_only": _pct(subject_v_h, subject_v_n),
        "intent_e2e_denominator": intent_e2e_n,
        "area_e2e_denominator": area_e2e_n,
        "subject_e2e_denominator": subject_e2e_n,
        "intent_not_applicable": intent_na,
        "area_not_applicable": area_na,
        "subject_not_applicable": subject_na,
        "required_facts_complete_rate": _pct(req_h, req_n),
        "required_facts_applicable_count": req_n,
        "fact_value_correct_rate": _pct(fv_h, fv_n),
        "fact_value_applicable_count": fv_n,
        "forbidden_facts_hit_rate": _pct(forbidden_hits, len(forbidden_cov)),
        "forbidden_facts_coverage_cases": len(forbidden_cov),
        "model_risk_case_complete_rate": _pct(model_risk_h, model_risk_n),
        "model_risk_applicable_cases": model_risk_n,
        "latency_ms_mean_all": (
            round(sum(lat_all) / len(lat_all), 2) if lat_all else NOT_EVALUATED
        ),
        "latency_ms_mean_ok": (round(sum(lat_ok) / len(lat_ok), 2) if lat_ok else NOT_EVALUATED),
        "note": (
            "e2e inclui fail-closed no denominador. valid_only condiciona a understanding "
            "aceito. Sem handoff/política/riscos efetivos neste escopo."
        ),
    }


def paired_comparison(
    baseline_results: list[dict[str, Any]],
    candidate_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Comparação pareada por caso e critério — sem declarar vencedor."""
    b_by_id = {str(r.get("case_id")): r for r in baseline_results}
    c_by_id = {str(r.get("case_id")): r for r in candidate_results}
    case_ids = sorted(set(b_by_id) | set(c_by_id))

    by_criterion: dict[str, dict[str, int]] = {
        crit: {
            OUTCOME_BOTH_PASS: 0,
            OUTCOME_BASELINE_ONLY: 0,
            OUTCOME_CANDIDATE_ONLY: 0,
            OUTCOME_BOTH_FAIL: 0,
            OUTCOME_NOT_EVALUABLE: 0,
        }
        for crit in PAIRED_CRITERIA
    }
    per_case: list[dict[str, Any]] = []

    for case_id in case_ids:
        b = b_by_id.get(case_id)
        c = c_by_id.get(case_id)
        case_entry: dict[str, Any] = {"case_id": case_id, "criteria": {}}
        if b is None or c is None:
            case_entry["status"] = "missing_variant"
            for crit in PAIRED_CRITERIA:
                by_criterion[crit][OUTCOME_NOT_EVALUABLE] += 1
                case_entry["criteria"][crit] = OUTCOME_NOT_EVALUABLE
            per_case.append(case_entry)
            continue
        for crit in PAIRED_CRITERIA:
            outcome = paired_outcome(criterion_value(b, crit), criterion_value(c, crit))
            by_criterion[crit][outcome] += 1
            case_entry["criteria"][crit] = outcome
        per_case.append(case_entry)

    return {
        "case_count": len(case_ids),
        "by_criterion": by_criterion,
        "per_case": per_case,
        "no_automatic_winner": True,
    }


def render_understanding_report(
    *,
    variant_id: str,
    metrics: dict[str, Any],
    manifest: dict[str, Any],
) -> str:
    lines = [
        f"# Comparação de understanding — variante `{variant_id}`",
        "",
        "> Artefato sintético/offline quando `mode=offline`. Não declara melhoria de qualidade.",
        "",
        "## Manifesto",
        f"- run_id: {manifest.get('run_id')}",
        f"- mode: {manifest.get('mode')}",
        f"- code_revision: {manifest.get('code_revision')} (dirty={manifest.get('code_dirty')})",
    ]
    identity = manifest.get("identity") or {}
    lines.extend(
        [
            f"- requested_model: {identity.get('requested_model', NOT_EVALUATED)}",
            f"- reported_models: {identity.get('reported_models') or []}",
            f"- runtime_base_url: {identity.get('runtime_base_url', NOT_EVALUATED)}",
            f"- checkpoint: {identity.get('checkpoint', NOT_EVALUATED)}",
            f"- quantization: {identity.get('quantization', NOT_EVALUATED)}",
            "",
            "## Métricas (understanding only)",
            (
                f"- Casos: total={metrics.get('total_cases')}; "
                f"ok={metrics.get('cases_understanding_ok')}; "
                f"fail-closed={metrics.get('cases_failed_closed')}"
            ),
            (
                f"- Estrutural falhou: {metrics.get('structural_failed_cases')}; "
                f"semântico falhou: {metrics.get('semantic_failed_cases')}"
            ),
            (f"- CRM provenance violations: {metrics.get('crm_provenance_violation_cases') or []}"),
            (f"- Fatos anotados NÃO resolvidos: {metrics.get('unresolved_fact_cases') or []}"),
            (
                f"- Intent e2e / valid_only: "
                f"{_format_value(metrics.get('intent_accuracy_e2e'), percent=True)} / "
                f"{_format_value(metrics.get('intent_accuracy_valid_only'), percent=True)} "
                f"[e2e_den={metrics.get('intent_e2e_denominator')}]"
            ),
            (
                f"- Área e2e / valid_only: "
                f"{_format_value(metrics.get('area_accuracy_e2e'), percent=True)} / "
                f"{_format_value(metrics.get('area_accuracy_valid_only'), percent=True)}"
            ),
            (
                f"- Assunto e2e / valid_only: "
                f"{_format_value(metrics.get('subject_accuracy_e2e'), percent=True)} / "
                f"{_format_value(metrics.get('subject_accuracy_valid_only'), percent=True)}"
            ),
            (
                f"- Fatos obrigatórios: "
                f"{_format_value(metrics.get('required_facts_complete_rate'), percent=True)} "
                f"[aplicáveis={metrics.get('required_facts_applicable_count')}]"
            ),
            (
                f"- Valores de fato: "
                f"{_format_value(metrics.get('fact_value_correct_rate'), percent=True)} "
                f"[aplicáveis={metrics.get('fact_value_applicable_count')}]"
            ),
            (
                f"- Fatos proibidos (hit): "
                f"{_format_value(metrics.get('forbidden_facts_hit_rate'), percent=True)} "
                f"[cobertura={metrics.get('forbidden_facts_coverage_cases')}]"
            ),
            (
                f"- Riscos do modelo (completude por caso): "
                f"{_format_value(metrics.get('model_risk_case_complete_rate'), percent=True)} "
                f"[aplicáveis={metrics.get('model_risk_applicable_cases')}]"
            ),
            (
                f"- Latência média ms (todos / ok): "
                f"{metrics.get('latency_ms_mean_all')} / {metrics.get('latency_ms_mean_ok')}"
            ),
            "",
            f"Nota: {metrics.get('note')}",
        ]
    )
    return "\n".join(lines) + "\n"


def render_paired_report(
    *,
    paired: dict[str, Any],
    compatibility_ok: bool,
    compatibility_reasons: list[str],
    baseline_id: str,
    candidate_id: str,
) -> str:
    lines = [
        "# Comparação pareada de understanding",
        "",
        f"- baseline: `{baseline_id}`",
        f"- candidate: `{candidate_id}`",
        f"- compatível: {'sim' if compatibility_ok else 'NÃO'}",
    ]
    if not compatibility_ok:
        lines.append("- razões de incompatibilidade:")
        for reason in compatibility_reasons:
            lines.append(f"  - {reason}")
        lines.append("")
        lines.append(
            "Execuções incompatíveis **não** constituem experimento controlado. "
            "Pareamento abaixo é apenas diagnóstico parcial."
        )
        lines.append("")
    else:
        lines.append("")
        lines.append(
            "Sem vencedor automático. Contagens por critério "
            "(both_pass / baseline_only / candidate_only / both_fail / not_evaluable):"
        )
        lines.append("")

    lines.append("| Critério | both_pass | baseline_only | candidate_only | both_fail | n/a |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for crit, counts in (paired.get("by_criterion") or {}).items():
        lines.append(
            f"| {crit} | {counts.get(OUTCOME_BOTH_PASS, 0)} | "
            f"{counts.get(OUTCOME_BASELINE_ONLY, 0)} | "
            f"{counts.get(OUTCOME_CANDIDATE_ONLY, 0)} | "
            f"{counts.get(OUTCOME_BOTH_FAIL, 0)} | "
            f"{counts.get(OUTCOME_NOT_EVALUABLE, 0)} |"
        )
    lines.append("")
    lines.append("## Por caso")
    for entry in paired.get("per_case") or []:
        lines.append(f"- `{entry.get('case_id')}`: {entry.get('criteria')}")
    lines.append("")
    return "\n".join(lines)
