"""Métricas da suíte de avaliação — denominadores honestos e etapas separadas."""

from __future__ import annotations

import statistics
from collections import Counter
from typing import Any

from evaluations.stages import STAGE_KEYS, summarize_stage_counts

NOT_EVALUATED = "not_evaluated"
NOT_OBSERVED = "not_observed"
NOT_APPLICABLE = "not_applicable"

# Dependem de inferência / runtime — não fazem sentido no offline estrutural.
SEMANTIC_METRIC_KEYS: tuple[str, ...] = (
    "intent_accuracy_e2e",
    "intent_accuracy_valid_only",
    "primary_area_accuracy_e2e",
    "primary_area_accuracy_valid_only",
    "subject_accuracy_e2e",
    "subject_accuracy_valid_only",
    "handoff_recall",
    "model_risk_label_recall",
    "effective_risk_label_recall",
    "effective_risk_case_complete_rate",
    "effective_risk_label_recall_e2e",
    "effective_risk_case_complete_rate_e2e",
    "effective_risk_label_recall_observed",
    "effective_risk_case_complete_rate_observed",
    "risk_label_recall",  # alias legado = model_risk_label_recall
    "risk_case_complete_rate",  # alias legado = model case completeness
    "risk_recall",
    "forbidden_facts_hit_rate",
    "forbidden_risks_hit_rate",
    "required_facts_complete_rate",
    "fact_value_correct_rate",
    "policy_rule_match_rate",
    "model_inference_skipped_match_rate",
    "handoff_reason_match_rate",
    "priority_match_rate",
    "extractor_cues_match_rate",
    "risk_temporal_match_rate",
    "extractor_risks_match_rate",
    "action_match_rate",
    "repeated_question_rate",
    "fail_closed_rate",
    "degraded_safety_rate",
    "latency_avg_ms_all",
    "latency_avg_ms_success",
    "latency_p50_ms_all",
    "latency_p95_ms_all",
    "latency_p50_ms_success",
    "latency_p95_ms_success",
    "retries_total",
    "distribution_by_area",
)

STRUCTURAL_TARGET_KEYS: tuple[str, ...] = ("json_valid", "schema_valid")

SEMANTIC_TARGET_KEYS: tuple[str, ...] = (
    "handoff_recall",
    "model_risk_label_recall",
    "effective_risk_label_recall",
    "effective_risk_case_complete",
    "forbidden_facts",
    "forbidden_risks",
    "required_facts_complete",
    "fact_values_correct",
    "effective_action",
    "policy_rule_match",
    "model_inference_skipped_match",
    "critical_expectations",
    "undetermined_route_lead",
    "repeated_questions",
    "intent",
    "area",
    "subject",
)

# Metas cuja falha impede aprovação operacional mesmo com média geral alta.
BLOCKING_TARGET_KEYS: tuple[str, ...] = (
    "critical_expectations",
    "undetermined_route_lead",
)


def _pct(num: int, den: int) -> float | None:
    if den == 0:
        return None
    return round(100.0 * num / den, 2)


def _evaluable_matches(results: list[dict[str, Any]], key: str) -> tuple[int, int, int]:
    """Retorna (acertos, avaliados, não_aplicáveis).

    not_observed/false entram no denominador avaliável.
    """
    hits = 0
    evaluated = 0
    not_applicable = 0
    for row in results:
        value = row.get(key)
        if value == NOT_APPLICABLE:
            not_applicable += 1
            continue
        if value == NOT_OBSERVED:
            evaluated += 1
            continue
        evaluated += 1
        if value is True:
            hits += 1
    return hits, evaluated, not_applicable


def _percentile(data: list[float], p: float) -> float | None:
    if not data:
        return None
    ordered = sorted(data)
    k = (len(ordered) - 1) * p
    f = int(k)
    c = min(f + 1, len(ordered) - 1)
    if f == c:
        return round(ordered[f], 2)
    return round(ordered[f] + (ordered[c] - ordered[f]) * (k - f), 2)


def compute_metrics(results: list[dict[str, Any]], *, mode: str = "live") -> dict[str, Any]:
    total = len(results)
    success = [r for r in results if r.get("pipeline_status") == "success"]
    degraded = [r for r in results if r.get("pipeline_status") == "degraded_safety"]
    failed = [
        r for r in results if r.get("fail_closed") or r.get("pipeline_status") == "failed_closed"
    ]
    valid_output = [
        r
        for r in results
        if r.get("contract_structural") is True and r.get("pipeline_status") == "success"
    ]

    # Etapas: não deduzir json_valid de success.
    json_passed = sum(1 for r in results if (r.get("stages") or {}).get("json_parse") == "passed")
    json_failed = sum(1 for r in results if (r.get("stages") or {}).get("json_parse") == "failed")
    json_not_obs = sum(
        1 for r in results if (r.get("stages") or {}).get("json_parse") == "not_observed"
    )
    structural_passed = sum(
        1 for r in results if (r.get("stages") or {}).get("contract_structural") == "passed"
    )
    structural_failed = sum(
        1 for r in results if (r.get("stages") or {}).get("contract_structural") == "failed"
    )
    structural_not_obs = sum(
        1 for r in results if (r.get("stages") or {}).get("contract_structural") == "not_observed"
    )

    intent_e2e_h, intent_e2e_n, intent_na = _evaluable_matches(results, "intent_match")
    area_e2e_h, area_e2e_n, area_na = _evaluable_matches(results, "area_match")
    subject_e2e_h, subject_e2e_n, subject_na = _evaluable_matches(results, "subject_match")

    intent_v_h, intent_v_n, _ = _evaluable_matches(valid_output, "intent_match")
    area_v_h, area_v_n, _ = _evaluable_matches(valid_output, "area_match")
    subject_v_h, subject_v_n, _ = _evaluable_matches(valid_output, "subject_match")

    handoff_needed = [r for r in results if r.get("expected_handoff") is True]
    handoff_hits, handoff_den, handoff_na = _evaluable_matches(handoff_needed, "handoff_match")

    risk_needed = [r for r in results if r.get("required_risks")]
    model_case_hits, model_case_den, _ = _evaluable_matches(risk_needed, "model_risks_match")
    if model_case_den == 0 and risk_needed:
        # Artefatos legados só tinham risks_match.
        model_case_hits, model_case_den, _ = _evaluable_matches(risk_needed, "risks_match")
    risk_na = sum(1 for r in results if not r.get("required_risks"))

    model_label_tp = sum(
        int(r.get("model_risk_label_hits") or r.get("risk_label_hits") or 0) for r in risk_needed
    )
    risk_label_req = sum(int(r.get("risk_label_required") or 0) for r in risk_needed)

    # Efetivos: e2e inclui casos com expectativa que falharam antes da composição
    # (not_observed = não entregue no denominador). Observado exclui not_observed.
    effective_case_hits_e2e, effective_case_den_e2e, _ = _evaluable_matches(
        risk_needed, "effective_risks_match"
    )
    effective_label_tp_e2e = sum(
        int(r.get("effective_risk_label_hits") or 0)
        for r in risk_needed
        if r.get("effective_risks_match") not in {None, NOT_OBSERVED}
    )
    effective_label_req_e2e = sum(int(r.get("risk_label_required") or 0) for r in risk_needed)

    effective_observed = [
        r for r in risk_needed if r.get("effective_risks_match") not in {None, NOT_OBSERVED}
    ]
    effective_case_hits_obs, effective_case_den_obs, _ = _evaluable_matches(
        effective_observed, "effective_risks_match"
    )
    effective_label_tp_obs = sum(
        int(r.get("effective_risk_label_hits") or 0) for r in effective_observed
    )
    effective_label_req_obs = sum(
        int(r.get("risk_label_required") or 0) for r in effective_observed
    )
    effective_not_observed_cases = sum(
        1 for r in risk_needed if r.get("effective_risks_match") == NOT_OBSERVED
    )

    # Denominadores de risco — três grandezas distintas, nunca intercambiáveis:
    #   * ocorrências requeridas = soma de len(required_risks) por caso;
    #   * categorias distintas    = união dos rótulos (|conjunto|);
    #   * casos aplicáveis        = casos com required_risks não vazio.
    # A soma por caso (ocorrências) é o denominador do recall de rótulo; o tamanho da
    # união (categorias) é só cobertura de vocabulário. Rotulá-las igual foi a origem
    # da divergência 8 vs 6 no relatório anterior.
    expected_risk_labels: set[str] = set()
    observed_risk_labels: set[str] = set()
    risk_required_occurrences = 0
    for row in risk_needed:
        required = row.get("required_risks") or []
        risk_required_occurrences += len(required)
        for label in required:
            expected_risk_labels.add(str(label))
        eff = row.get("effective_detected_risks")
        if isinstance(eff, list):
            for label in required:
                if label in eff:
                    observed_risk_labels.add(str(label))
    unobserved_risk_labels = sorted(expected_risk_labels - observed_risk_labels)

    forbidden_cases = [r for r in results if r.get("forbidden_facts_status") != NOT_APPLICABLE]
    forbidden_hits = sum(1 for r in forbidden_cases if r.get("forbidden_facts_hit") is True)
    forbidden_den = sum(
        1
        for r in forbidden_cases
        if r.get("forbidden_facts_hit") in {True, False}
        or r.get("forbidden_facts_status") == NOT_OBSERVED
    )

    # Falsos positivos de risco: só onde forbidden_risks ou risks_exhaustive anotados.
    forbidden_risk_cases = [
        r for r in results if r.get("forbidden_risks_status") not in {None, NOT_APPLICABLE}
    ]
    forbidden_risk_hits = sum(
        1 for r in forbidden_risk_cases if r.get("forbidden_risks_hit") is True
    )
    forbidden_risk_den = sum(
        1
        for r in forbidden_risk_cases
        if r.get("forbidden_risks_hit") in {True, False}
        or r.get("forbidden_risks_status") == NOT_OBSERVED
    )
    unevaluated_extra_risks = sorted(
        {str(x) for r in results for x in (r.get("unevaluated_extra_risks") or []) if x}
    )

    required_facts_cases = [
        r for r in results if r.get("required_facts_match") not in {None, NOT_APPLICABLE}
    ]
    required_facts_hits, required_facts_den, _ = _evaluable_matches(
        required_facts_cases, "required_facts_match"
    )

    handoff_forbidden = [r for r in results if r.get("expected_handoff") is False]
    # Denominador = casos realmente observados. NOT_OBSERVED não é "handoff correto".
    handoff_fp_den = sum(1 for r in handoff_forbidden if isinstance(r.get("handoff_match"), bool))
    # handoff_match True com expected=False = corretamente sem handoff indevido.
    # handoff_match False = handoff indevido.
    handoff_incorrect_forbidden = sum(
        1 for r in handoff_forbidden if r.get("handoff_match") is False
    )

    action_eval = [
        r for r in results if r.get("effective_action_match") not in {None, NOT_APPLICABLE}
    ]
    action_hits, action_den, action_na = _evaluable_matches(action_eval, "effective_action_match")

    rule_eval = [r for r in results if r.get("policy_rule_match") not in {None, NOT_APPLICABLE}]
    rule_hits, rule_den, _ = _evaluable_matches(rule_eval, "policy_rule_match")

    skipped_eval = [
        r for r in results if r.get("model_inference_skipped_match") not in {None, NOT_APPLICABLE}
    ]
    skipped_hits, skipped_den, _ = _evaluable_matches(skipped_eval, "model_inference_skipped_match")

    reason_eval = [
        r for r in results if r.get("handoff_reason_match") not in {None, NOT_APPLICABLE}
    ]
    reason_hits, reason_den, _ = _evaluable_matches(reason_eval, "handoff_reason_match")

    priority_eval = [r for r in results if r.get("priority_match") not in {None, NOT_APPLICABLE}]
    priority_hits, priority_den, _ = _evaluable_matches(priority_eval, "priority_match")

    cues_eval = [r for r in results if r.get("extractor_cues_match") not in {None, NOT_APPLICABLE}]
    cues_hits, cues_den, _ = _evaluable_matches(cues_eval, "extractor_cues_match")

    temporal_eval = [
        r for r in results if r.get("risk_temporal_match") not in {None, NOT_APPLICABLE}
    ]
    temporal_hits, temporal_den, _ = _evaluable_matches(temporal_eval, "risk_temporal_match")

    ext_risk_eval = [
        r for r in results if r.get("extractor_risks_match") not in {None, NOT_APPLICABLE}
    ]
    ext_risk_hits, ext_risk_den, _ = _evaluable_matches(ext_risk_eval, "extractor_risks_match")

    fact_value_eval = [
        r for r in results if r.get("fact_value_match") not in {None, NOT_APPLICABLE}
    ]
    fact_value_hits, fact_value_den, _ = _evaluable_matches(fact_value_eval, "fact_value_match")

    critical_failed_cases = sorted(
        str(r.get("case_id")) for r in results if r.get("critical_failures")
    )
    critical_failure_names = sorted(
        {str(name) for r in results for name in (r.get("critical_failures") or [])}
    )
    critical_annotated_cases = sum(1 for r in results if r.get("critical_expectations"))
    critical_unobserved_cases = sorted(
        str(r.get("case_id")) for r in results if r.get("critical_not_observed")
    )
    critical_undelivered_cases = sorted(
        str(r.get("case_id")) for r in results if r.get("critical_undelivered")
    )
    critical_misconfigured_cases = sorted(
        str(r.get("case_id")) for r in results if r.get("critical_misconfigured")
    )
    critical_passed_cases = sum(
        1
        for r in results
        if r.get("critical_expectations")
        and not r.get("critical_failures")
        and not r.get("critical_undelivered")
        and not r.get("critical_misconfigured")
        and any(
            (r.get("critical_expectation_status") or {}).get(name) is True
            for name in (r.get("critical_expectations") or [])
        )
    )
    # Cobertura: caso anotado só é avaliável quando ao menos uma checagem crítica saiu de
    # not_observed/not_applicable. Sem isso, "zero falhas" é vacuidade, não sucesso.
    critical_evaluable_cases = sum(
        1
        for r in results
        if r.get("critical_expectations")
        and any(
            (r.get("critical_expectation_status") or {}).get(name)
            not in {None, NOT_OBSERVED, NOT_APPLICABLE}
            for name in (r.get("critical_expectations") or [])
        )
    )
    critical_observed_cases = critical_evaluable_cases
    undetermined_route_cases = sorted(
        str(r.get("case_id")) for r in results if r.get("undetermined_route_violation")
    )
    # A invariante exige classificação E ação efetiva observadas no mesmo caso.
    undetermined_route_evaluable_cases = sum(
        1
        for r in results
        if r.get("predicted_area") is not None and r.get("predicted_action") is not None
    )

    unresolved_fact_cases = sorted(
        str(r.get("case_id")) for r in results if r.get("unresolved_annotated_facts")
    )
    fact_value_evaluable_cases = sum(
        1 for r in results if isinstance(r.get("fact_value_match"), bool)
    )
    unresolved_fact_keys = sorted(
        {str(k) for r in results for k in (r.get("unresolved_annotated_facts") or [])}
    )

    repeated_obs = [
        r for r in results if r.get("repeated_question") not in {NOT_APPLICABLE, NOT_OBSERVED}
    ]
    repeated = sum(1 for r in repeated_obs if r.get("repeated_question") is True)

    fail_closed = sum(1 for r in results if r.get("fail_closed"))

    lat_all = [float(r["latency_ms"]) for r in results if r.get("latency_ms") is not None]
    lat_ok = [float(r["latency_ms"]) for r in success if r.get("latency_ms") is not None]
    retries = sum(int(r.get("retry_count") or 0) for r in results)

    by_area = Counter(str(r.get("predicted_area") or "unknown") for r in results)

    metrics: dict[str, Any] = {
        "total_cases": total,
        "cases_success": len(success),
        "cases_degraded_safety": len(degraded),
        "cases_failed_closed": len(failed),
        "cases_valid_output": len(valid_output),
        "cases_not_evaluable_semantic": total - len(valid_output),
        "stage_counts": summarize_stage_counts(results),
        "json_parse_passed": json_passed,
        "json_parse_failed": json_failed,
        "json_parse_not_observed": json_not_obs,
        "contract_structural_passed": structural_passed,
        "contract_structural_failed": structural_failed,
        "contract_structural_not_observed": structural_not_obs,
        # Compat: taxas só sobre etapas observadas (não tratar not_observed como inválido).
        "json_valid_rate": _pct(json_passed, json_passed + json_failed),
        "schema_valid_rate": _pct(structural_passed, structural_passed + structural_failed),
        "intent_accuracy_e2e": _pct(intent_e2e_h, intent_e2e_n),
        "intent_accuracy_valid_only": _pct(intent_v_h, intent_v_n),
        "primary_area_accuracy_e2e": _pct(area_e2e_h, area_e2e_n),
        "primary_area_accuracy_valid_only": _pct(area_v_h, area_v_n),
        "subject_accuracy_e2e": _pct(subject_e2e_h, subject_e2e_n),
        "subject_accuracy_valid_only": _pct(subject_v_h, subject_v_n),
        "intent_not_applicable": intent_na,
        "area_not_applicable": area_na,
        "subject_not_applicable": subject_na,
        "handoff_recall": _pct(handoff_hits, handoff_den),
        "handoff_expected_count": len(handoff_needed),
        "handoff_not_applicable": handoff_na,
        "handoff_incorrect_when_forbidden": handoff_incorrect_forbidden,
        "handoff_forbidden_evaluated": handoff_fp_den,
        "model_risk_label_recall": _pct(model_label_tp, risk_label_req),
        "model_risk_label_true_positives": model_label_tp,
        "model_risk_label_required_total": risk_label_req,
        "model_risk_case_complete_rate": _pct(model_case_hits, model_case_den),
        "model_risk_case_applicable_count": model_case_den,
        # Metas operacionais = ponta a ponta (não excluir fail-closed do denominador).
        "effective_risk_label_recall": _pct(effective_label_tp_e2e, effective_label_req_e2e),
        "effective_risk_label_true_positives": effective_label_tp_e2e,
        "effective_risk_label_required_total": effective_label_req_e2e,
        "effective_risk_case_complete_rate": _pct(effective_case_hits_e2e, effective_case_den_e2e),
        "effective_risk_case_applicable_count": effective_case_den_e2e,
        "effective_risk_label_recall_e2e": _pct(effective_label_tp_e2e, effective_label_req_e2e),
        "effective_risk_case_complete_rate_e2e": _pct(
            effective_case_hits_e2e, effective_case_den_e2e
        ),
        "effective_risk_label_recall_observed": _pct(
            effective_label_tp_obs, effective_label_req_obs
        ),
        "effective_risk_label_true_positives_observed": effective_label_tp_obs,
        "effective_risk_label_required_total_observed": effective_label_req_obs,
        "effective_risk_case_complete_rate_observed": _pct(
            effective_case_hits_obs, effective_case_den_obs
        ),
        "effective_risk_case_applicable_count_observed": effective_case_den_obs,
        "effective_risk_cases_not_observed": effective_not_observed_cases,
        "risk_labels_expected": sorted(expected_risk_labels),
        "risk_labels_observed": sorted(observed_risk_labels),
        "risk_labels_not_observed": unobserved_risk_labels,
        # Denominadores explicitamente separados.
        "risk_required_occurrences": risk_required_occurrences,
        "risk_distinct_categories": len(expected_risk_labels),
        "risk_applicable_cases": len(risk_needed),
        "risk_categories_observed": len(observed_risk_labels),
        "risk_categories_not_observed": len(unobserved_risk_labels),
        "risk_expected_count": len(risk_needed),
        "risk_not_applicable": risk_na,
        # Aliases legados (informativos; deprecated nas metas operacionais):
        "risk_label_recall": _pct(model_label_tp, risk_label_req),
        "risk_label_true_positives": model_label_tp,
        "risk_label_required_total": risk_label_req,
        "risk_case_complete_rate": _pct(model_case_hits, model_case_den),
        "risk_case_applicable_count": model_case_den,
        "risk_recall": _pct(model_case_hits, model_case_den),
        "forbidden_facts_hit_rate": _pct(forbidden_hits, forbidden_den),
        "forbidden_facts_coverage_cases": forbidden_den,
        "forbidden_risks_hit_rate": _pct(forbidden_risk_hits, forbidden_risk_den),
        "forbidden_risks_coverage_cases": forbidden_risk_den,
        "unevaluated_extra_risks": unevaluated_extra_risks,
        "required_facts_complete_rate": _pct(required_facts_hits, required_facts_den),
        "required_facts_applicable_count": required_facts_den,
        "fact_value_correct_rate": _pct(fact_value_hits, fact_value_den),
        "fact_value_applicable_count": fact_value_den,
        "policy_rule_match_rate": _pct(rule_hits, rule_den),
        "policy_rule_applicable_count": rule_den,
        "model_inference_skipped_match_rate": _pct(skipped_hits, skipped_den),
        "model_inference_skipped_applicable_count": skipped_den,
        "handoff_reason_match_rate": _pct(reason_hits, reason_den),
        "handoff_reason_applicable_count": reason_den,
        "priority_match_rate": _pct(priority_hits, priority_den),
        "priority_applicable_count": priority_den,
        "extractor_cues_match_rate": _pct(cues_hits, cues_den),
        "extractor_cues_applicable_count": cues_den,
        "risk_temporal_match_rate": _pct(temporal_hits, temporal_den),
        "risk_temporal_applicable_count": temporal_den,
        "extractor_risks_match_rate": _pct(ext_risk_hits, ext_risk_den),
        "extractor_risks_applicable_count": ext_risk_den,
        "critical_annotated_cases": critical_annotated_cases,
        "critical_evaluable_cases": critical_evaluable_cases,
        "critical_observed_cases": critical_observed_cases,
        "critical_passed_cases": critical_passed_cases,
        "critical_failed_cases": critical_failed_cases,
        "critical_failure_names": critical_failure_names,
        "critical_unobserved_cases": critical_unobserved_cases,
        "critical_undelivered_cases": critical_undelivered_cases,
        "critical_misconfigured_cases": critical_misconfigured_cases,
        "undetermined_route_lead_cases": undetermined_route_cases,
        "undetermined_route_evaluable_cases": undetermined_route_evaluable_cases,
        "unresolved_fact_cases": unresolved_fact_cases,
        "unresolved_fact_keys": unresolved_fact_keys,
        "fact_value_evaluable_cases": fact_value_evaluable_cases,
        "action_match_rate": _pct(action_hits, action_den),
        "action_applicable_count": action_den,
        "action_not_applicable": action_na,
        "repeated_question_rate": _pct(repeated, len(repeated_obs)),
        "repeated_question_observed_cases": len(repeated_obs),
        "fail_closed_rate": _pct(fail_closed, total),
        "degraded_safety_rate": _pct(len(degraded), total),
        "latency_avg_ms_all": round(statistics.mean(lat_all), 2) if lat_all else None,
        "latency_p50_ms_all": _percentile(lat_all, 0.50),
        "latency_p95_ms_all": _percentile(lat_all, 0.95),
        "latency_avg_ms_success": round(statistics.mean(lat_ok), 2) if lat_ok else None,
        "latency_p50_ms_success": _percentile(lat_ok, 0.50),
        "latency_p95_ms_success": _percentile(lat_ok, 0.95),
        "latency_observed_all": len(lat_all),
        "latency_observed_success": len(lat_ok),
        "retries_total": retries,
        "distribution_by_area": dict(by_area),
        "targets": {
            "json_valid_rate": 100.0,
            "schema_valid_rate": 100.0,
            "handoff_recall": 100.0,
            "model_risk_label_recall": 100.0,
            "effective_risk_label_recall": 100.0,
            "effective_risk_case_complete_rate": 100.0,
            "forbidden_facts_hit_rate_max": 0.0,
            "forbidden_risks_hit_rate_max": 0.0,
            "repeated_question_rate_max": 0.0,
            # Metas próprias de fatos, ação e caminho da decisão.
            "required_facts_complete_rate": 100.0,
            "fact_value_correct_rate": 100.0,
            "action_match_rate": 100.0,
            "policy_rule_match_rate": 100.0,
            "model_inference_skipped_match_rate": 100.0,
            "critical_failed_cases_max": 0,
            "undetermined_route_lead_cases_max": 0,
            "intent_accuracy_min": 90.0,
            "primary_area_accuracy_min": 90.0,
            "subject_accuracy_min": 80.0,
        },
        # Aliases legados (e2e) para metas/report — não confundir com valid_only.
        "intent_accuracy": _pct(intent_e2e_h, intent_e2e_n),
        "primary_area_accuracy": _pct(area_e2e_h, area_e2e_n),
        "subject_accuracy": _pct(subject_e2e_h, subject_e2e_n),
        "invented_facts_rate": _pct(forbidden_hits, forbidden_den),
        "latency_avg_ms": round(statistics.mean(lat_all), 2) if lat_all else None,
        "latency_p50_ms": _percentile(lat_all, 0.50),
        "latency_p95_ms": _percentile(lat_all, 0.95),
    }

    if mode == "offline":
        for key in SEMANTIC_METRIC_KEYS:
            metrics[key] = NOT_EVALUATED
        metrics["intent_accuracy"] = NOT_EVALUATED
        metrics["primary_area_accuracy"] = NOT_EVALUATED
        metrics["subject_accuracy"] = NOT_EVALUATED
        metrics["invented_facts_rate"] = NOT_EVALUATED
        metrics["latency_avg_ms"] = NOT_EVALUATED
        metrics["latency_p50_ms"] = NOT_EVALUATED
        metrics["latency_p95_ms"] = NOT_EVALUATED

    return metrics


def evaluate_targets(
    metrics: dict[str, Any],
    *,
    mode: str = "live",
) -> dict[str, bool | str]:
    t = metrics["targets"]

    def _rate_ok(value: object, minimum: float) -> bool:
        return isinstance(value, (int, float)) and float(value) >= minimum

    def _rate_max_ok(value: object, maximum: float) -> bool:
        if value is None:
            # Sem casos avaliáveis: não marca PASS enganoso.
            return False
        return isinstance(value, (int, float)) and float(value) <= maximum

    structural: dict[str, bool | str] = {
        "json_valid": _rate_ok(metrics["json_valid_rate"], t["json_valid_rate"]),
        "schema_valid": _rate_ok(metrics["schema_valid_rate"], t["schema_valid_rate"]),
    }
    if mode == "offline":
        # Offline: taxa None (tudo not_observed em json_parse) → estrutural do request.
        if metrics.get("json_valid_rate") is None:
            structural["json_valid"] = NOT_EVALUATED
        req_ok = metrics.get("contract_structural_passed", 0)
        req_fail = metrics.get("contract_structural_failed", 0)
        if req_ok + req_fail > 0:
            structural["schema_valid"] = req_fail == 0
        return {
            **structural,
            **{key: NOT_EVALUATED for key in SEMANTIC_TARGET_KEYS},
        }

    return {
        **structural,
        "handoff_recall": (
            NOT_EVALUATED
            if metrics["handoff_recall"] is None
            else _rate_ok(metrics["handoff_recall"], t["handoff_recall"])
        ),
        "model_risk_label_recall": (
            NOT_EVALUATED
            if metrics.get("model_risk_label_recall") is None
            else _rate_ok(metrics["model_risk_label_recall"], t["model_risk_label_recall"])
        ),
        "effective_risk_label_recall": (
            NOT_EVALUATED
            if metrics.get("effective_risk_label_recall") is None
            else _rate_ok(metrics["effective_risk_label_recall"], t["effective_risk_label_recall"])
        ),
        "effective_risk_case_complete": (
            NOT_EVALUATED
            if metrics.get("effective_risk_case_complete_rate") is None
            else _rate_ok(
                metrics["effective_risk_case_complete_rate"],
                t["effective_risk_case_complete_rate"],
            )
        ),
        "forbidden_facts": _rate_max_ok(
            metrics["forbidden_facts_hit_rate"], t["forbidden_facts_hit_rate_max"]
        ),
        "forbidden_risks": (
            NOT_EVALUATED
            if metrics.get("forbidden_risks_hit_rate") is None
            else _rate_max_ok(
                metrics["forbidden_risks_hit_rate"], t["forbidden_risks_hit_rate_max"]
            )
        ),
        "required_facts_complete": (
            NOT_EVALUATED
            if metrics.get("required_facts_complete_rate") is None
            else _rate_ok(
                metrics["required_facts_complete_rate"], t["required_facts_complete_rate"]
            )
        ),
        "fact_values_correct": (
            NOT_EVALUATED
            if metrics.get("fact_value_correct_rate") is None
            else _rate_ok(metrics["fact_value_correct_rate"], t["fact_value_correct_rate"])
        ),
        "effective_action": (
            NOT_EVALUATED
            if metrics.get("action_match_rate") is None
            else _rate_ok(metrics["action_match_rate"], t["action_match_rate"])
        ),
        "policy_rule_match": (
            NOT_EVALUATED
            if metrics.get("policy_rule_match_rate") is None
            else _rate_ok(metrics["policy_rule_match_rate"], t["policy_rule_match_rate"])
        ),
        "model_inference_skipped_match": (
            NOT_EVALUATED
            if metrics.get("model_inference_skipped_match_rate") is None
            else _rate_ok(
                metrics["model_inference_skipped_match_rate"],
                t["model_inference_skipped_match_rate"],
            )
        ),
        # Bloqueantes: independem de média e de limiar geral, mas exigem cobertura.
        # Offline: sem inferência → not_evaluated (não reprova o modelo).
        # Live: falhas + não entregues (pipeline) + misconfig bloqueiam aprovação.
        "critical_expectations": (
            NOT_EVALUATED
            if mode == "offline"
            or (
                metrics.get("critical_annotated_cases", 0) == 0
                and metrics.get("critical_evaluable_cases", 0) == 0
            )
            else (
                len(metrics.get("critical_failed_cases") or []) == 0
                and len(metrics.get("critical_undelivered_cases") or []) == 0
                and len(metrics.get("critical_misconfigured_cases") or []) == 0
                and (
                    metrics.get("critical_evaluable_cases", 0) > 0
                    or metrics.get("critical_annotated_cases", 0) == 0
                )
            )
        ),
        "undetermined_route_lead": (
            NOT_EVALUATED
            if metrics.get("undetermined_route_evaluable_cases", 0) == 0
            else len(metrics.get("undetermined_route_lead_cases") or [])
            <= t["undetermined_route_lead_cases_max"]
        ),
        "repeated_questions": (
            NOT_EVALUATED
            if metrics.get("repeated_question_observed_cases", 0) == 0
            else _rate_max_ok(metrics["repeated_question_rate"], t["repeated_question_rate_max"])
        ),
        "intent": _rate_ok(metrics["intent_accuracy_e2e"], t["intent_accuracy_min"]),
        "area": _rate_ok(metrics["primary_area_accuracy_e2e"], t["primary_area_accuracy_min"]),
        "subject": _rate_ok(metrics["subject_accuracy_e2e"], t["subject_accuracy_min"]),
    }


def blocking_failures(checks: dict[str, bool | str]) -> list[str]:
    """Metas bloqueantes reprovadas.

    Aprovação operacional é vetada por estas metas independentemente das demais: uma
    expectativa crítica falha reprova o conjunto mesmo com todas as taxas médias acima
    do limiar. ``not_evaluated`` não é falha (ausência de expectativa ≠ violação).
    """
    return [key for key in BLOCKING_TARGET_KEYS if checks.get(key) is False]


def _format_value(value: object, *, percent: bool = False) -> str:
    if value in {NOT_EVALUATED, NOT_OBSERVED, NOT_APPLICABLE}:
        return str(value)
    if value is None:
        return "n/a"
    if percent and isinstance(value, (int, float)):
        return f"{value}%"
    return str(value)


def _format_check(value: bool | str) -> str:
    if value in {NOT_EVALUATED, NOT_OBSERVED, NOT_APPLICABLE}:
        return str(value)
    return "PASS" if value else "FAIL"


def _coverage_status(evaluable: int, failures: int) -> str:
    """Contagem de falhas só é informativa com cobertura.

    Sem caso avaliável (ex.: offline, sem inferência nem aplicação de política),
    ``0 falhas`` não comprova sucesso — a linha mostra ``not_evaluated``.
    """
    if evaluable == 0:
        return NOT_EVALUATED
    return f"{failures} falhas em {evaluable} avaliáveis"


def render_report(
    metrics: dict[str, Any],
    checks: dict[str, bool | str],
    *,
    mode: str = "live",
    selection: dict[str, Any] | None = None,
) -> str:
    lines = [
        "# Relatório de avaliação AdvCRM Bot",
        "",
        f"Modo: {mode}",
        f"Casos: {metrics['total_cases']}",
        f"- Sucesso pipeline: {metrics.get('cases_success', 'n/a')}",
        f"- Fail-closed: {metrics.get('cases_failed_closed', 'n/a')}",
        f"- Saída válida (condicional): {metrics.get('cases_valid_output', 'n/a')}",
    ]
    if selection and selection.get("partial"):
        selected = selection.get("selected_case_ids") or []
        lines.extend(
            [
                "",
                "## Seleção",
                "Avaliação **parcial** (subconjunto de casos).",
                f"IDs selecionados ({len(selected)}): {', '.join(str(x) for x in selected)}",
            ]
        )
    lines.extend(
        [
            "",
            "## Etapas (contagens)",
        ]
    )
    stage_counts = metrics.get("stage_counts") or {}
    if stage_counts == NOT_EVALUATED:
        lines.append(f"- {NOT_EVALUATED}")
    else:
        for key in STAGE_KEYS:
            counts = stage_counts.get(key) or {}
            lines.append(
                f"- {key}: passed={counts.get('passed', 0)} "
                f"failed={counts.get('failed', 0)} "
                f"not_observed={counts.get('not_observed', 0)}"
            )

    eff_obs_recall = _format_value(
        metrics.get("effective_risk_label_recall_observed"), percent=True
    )
    eff_e2e_complete = _format_value(
        metrics.get("effective_risk_case_complete_rate_e2e"), percent=True
    )
    eff_obs_complete = _format_value(
        metrics.get("effective_risk_case_complete_rate_observed"), percent=True
    )
    critical_evaluable = int(metrics.get("critical_evaluable_cases") or 0)
    critical_failed = len(metrics.get("critical_failed_cases") or [])
    undetermined_evaluable = int(metrics.get("undetermined_route_evaluable_cases") or 0)
    undetermined_failed = len(metrics.get("undetermined_route_lead_cases") or [])
    fact_value_evaluable = int(metrics.get("fact_value_evaluable_cases") or 0)
    unresolved_failed = len(metrics.get("unresolved_fact_cases") or [])

    lines.extend(
        [
            "",
            "## Métricas",
            (
                f"- JSON parse (observado): "
                f"{_format_value(metrics['json_valid_rate'], percent=True)}"
            ),
            (
                f"- Contrato estrutural (observado): "
                f"{_format_value(metrics['schema_valid_rate'], percent=True)}"
            ),
            (
                f"- Intenção e2e / válida: "
                f"{_format_value(metrics.get('intent_accuracy_e2e'), percent=True)} / "
                f"{_format_value(metrics.get('intent_accuracy_valid_only'), percent=True)}"
            ),
            (
                f"- Área e2e / válida: "
                f"{_format_value(metrics.get('primary_area_accuracy_e2e'), percent=True)} / "
                f"{_format_value(metrics.get('primary_area_accuracy_valid_only'), percent=True)}"
            ),
            (
                f"- Assunto e2e / válida: "
                f"{_format_value(metrics.get('subject_accuracy_e2e'), percent=True)} / "
                f"{_format_value(metrics.get('subject_accuracy_valid_only'), percent=True)}"
            ),
            f"- Recall handoff (atendimento): "
            f"{_format_value(metrics['handoff_recall'], percent=True)} "
            f"[esperados={metrics.get('handoff_expected_count', 0)}]",
            (
                f"- Recall riscos do modelo (rótulo): "
                f"{_format_value(metrics.get('model_risk_label_recall'), percent=True)} "
                f"[TP={metrics.get('model_risk_label_true_positives', 0)}/"
                f"req={metrics.get('model_risk_label_required_total', 0)}]"
            ),
            (
                f"- Recall riscos efetivos e2e (rótulo): "
                f"{_format_value(metrics.get('effective_risk_label_recall_e2e'), percent=True)} "
                f"[TP={metrics.get('effective_risk_label_true_positives', 0)}/"
                f"req={metrics.get('effective_risk_label_required_total', 0)}; "
                f"casos_não_obs={metrics.get('effective_risk_cases_not_observed', 0)}]"
            ),
            (
                f"- Recall riscos efetivos observados (rótulo): {eff_obs_recall} "
                f"[TP={metrics.get('effective_risk_label_true_positives_observed', 0)}/"
                f"req={metrics.get('effective_risk_label_required_total_observed', 0)}]"
            ),
            (
                f"- Completude efetiva e2e por caso: {eff_e2e_complete} "
                f"[aplicáveis={metrics.get('effective_risk_case_applicable_count', 0)}]"
            ),
            (
                f"- Completude efetiva observada por caso: {eff_obs_complete} "
                f"[aplicáveis={metrics.get('effective_risk_case_applicable_count_observed', 0)}]"
            ),
            (
                f"- Denominadores de risco — ocorrências requeridas: "
                f"{metrics.get('risk_required_occurrences', 0)}; "
                f"categorias distintas: {metrics.get('risk_distinct_categories', 0)}; "
                f"casos aplicáveis: {metrics.get('risk_applicable_cases', 0)}"
            ),
            (
                f"- Categorias de risco observadas/não observadas: "
                f"{metrics.get('risk_categories_observed', 0)}/"
                f"{metrics.get('risk_categories_not_observed', 0)} "
                f"(de {metrics.get('risk_distinct_categories', 0)} distintas)"
            ),
            (
                f"- Fatos obrigatórios (presença da chave): "
                f"{_format_value(metrics.get('required_facts_complete_rate'), percent=True)} "
                f"[aplicáveis={metrics.get('required_facts_applicable_count', 0)}]"
            ),
            (
                f"- Fatos resolvidos (valor correto): "
                f"{_format_value(metrics.get('fact_value_correct_rate'), percent=True)} "
                f"[aplicáveis={metrics.get('fact_value_applicable_count', 0)}]"
            ),
            (
                f"- Fatos proibidos (hit rate; não é alucinação geral): "
                f"{_format_value(metrics.get('forbidden_facts_hit_rate'), percent=True)} "
                f"[cobertura={metrics.get('forbidden_facts_coverage_cases', 0)}]"
            ),
            (
                f"- Riscos proibidos (hit rate; só com anotação): "
                f"{_format_value(metrics.get('forbidden_risks_hit_rate'), percent=True)} "
                f"[cobertura={metrics.get('forbidden_risks_coverage_cases', 0)}]"
            ),
            (
                f"- Riscos extras não avaliados (revisão): "
                f"{', '.join(metrics.get('unevaluated_extra_risks') or []) or 'nenhum'}"
            ),
            (
                f"- Ação efetiva vs expected: "
                f"{_format_value(metrics.get('action_match_rate'), percent=True)} "
                f"[aplicáveis={metrics.get('action_applicable_count', 0)}]"
            ),
            (
                f"- Regra de política vs expected: "
                f"{_format_value(metrics.get('policy_rule_match_rate'), percent=True)} "
                f"[aplicáveis={metrics.get('policy_rule_applicable_count', 0)}]"
            ),
            (
                f"- Inferência pulada vs expected: "
                f"{_format_value(metrics.get('model_inference_skipped_match_rate'), percent=True)} "
                f"[aplicáveis={metrics.get('model_inference_skipped_applicable_count', 0)}]"
            ),
            (
                f"- Expectativas críticas: {NOT_EVALUATED} "
                f"[anotados={metrics.get('critical_annotated_cases', 0)}; "
                f"avaliáveis={critical_evaluable}]"
                if mode == "offline"
                else (
                    f"- Expectativas críticas: "
                    f"anotados={metrics.get('critical_annotated_cases', 0)}; "
                    f"observados={metrics.get('critical_observed_cases', 0)}; "
                    f"aprovados={metrics.get('critical_passed_cases', 0)}; "
                    f"reprovados={critical_failed}; "
                    f"não_entregues={len(metrics.get('critical_undelivered_cases') or [])}; "
                    f"misconfig={len(metrics.get('critical_misconfigured_cases') or [])}; "
                    f"status={_coverage_status(critical_evaluable, critical_failed)}"
                    + (
                        f" → fail {', '.join(metrics.get('critical_failed_cases') or [])}"
                        if metrics.get("critical_failed_cases")
                        else ""
                    )
                    + (
                        f" → undelivered "
                        f"{', '.join(metrics.get('critical_undelivered_cases') or [])}"
                        if metrics.get("critical_undelivered_cases")
                        else ""
                    )
                )
            ),
            (
                f"- route_lead totalmente indeterminado (invariante): "
                f"{_coverage_status(undetermined_evaluable, undetermined_failed)} "
                f"[avaliáveis={undetermined_evaluable}]"
                + (
                    f" → {', '.join(metrics.get('undetermined_route_lead_cases') or [])}"
                    if metrics.get("undetermined_route_lead_cases")
                    else ""
                )
            ),
            (
                f"- Fatos anotados NÃO resolvidos (valores divergentes): "
                f"{_coverage_status(fact_value_evaluable, unresolved_failed)}"
                + (
                    f" → {', '.join(metrics.get('unresolved_fact_keys') or [])}"
                    if metrics.get("unresolved_fact_keys")
                    else ""
                )
            ),
            (
                "- Handoffs indevidos (só com handoff_required=false): "
                + _coverage_status(
                    int(metrics.get("handoff_forbidden_evaluated") or 0),
                    int(metrics.get("handoff_incorrect_when_forbidden") or 0),
                )
            ),
            (
                f"- Perguntas repetidas: "
                f"{_format_value(metrics['repeated_question_rate'], percent=True)} "
                f"[observados={metrics.get('repeated_question_observed_cases', 0)}]"
            ),
            f"- Fail-closed: {_format_value(metrics['fail_closed_rate'], percent=True)}",
            (
                f"- Degraded-safety: "
                f"{_format_value(metrics.get('degraded_safety_rate'), percent=True)} "
                f"[casos={metrics.get('cases_degraded_safety', 0)}]"
            ),
            (
                f"- Latência média ms (todos / sucessos): "
                f"{_format_value(metrics.get('latency_avg_ms_all'))} / "
                f"{_format_value(metrics.get('latency_avg_ms_success'))}"
            ),
            (
                f"- Latência p50/p95 todos: "
                f"{_format_value(metrics.get('latency_p50_ms_all'))} / "
                f"{_format_value(metrics.get('latency_p95_ms_all'))}"
            ),
            (
                f"- Latência p50/p95 sucessos: "
                f"{_format_value(metrics.get('latency_p50_ms_success'))} / "
                f"{_format_value(metrics.get('latency_p95_ms_success'))}"
            ),
            f"- Retries (soma): {_format_value(metrics['retries_total'])}",
            "",
            "## Metas",
        ]
    )
    for key, ok in checks.items():
        blocking = " (bloqueante)" if key in BLOCKING_TARGET_KEYS else ""
        lines.append(f"- {key}{blocking}: {_format_check(ok)}")
    blocked = blocking_failures(checks)
    unevaluated_gates = [
        key for key in BLOCKING_TARGET_KEYS if checks.get(key) in {NOT_EVALUATED, NOT_OBSERVED}
    ]
    if blocked:
        approval = "BLOQUEADA → " + ", ".join(blocked)
    elif unevaluated_gates:
        # Portão não exercido não aprova nada: ausência de falha ≠ evidência de acerto.
        approval = f"{NOT_EVALUATED} → sem cobertura em: " + ", ".join(unevaluated_gates)
    else:
        approval = "sem bloqueio por meta crítica"
    lines.append("")
    lines.append("Aprovação operacional: " + approval)
    lines.append("")
    lines.append("## Distribuição por área")
    dist = metrics["distribution_by_area"]
    if dist == NOT_EVALUATED:
        lines.append(f"- {NOT_EVALUATED}")
    else:
        for area, count in sorted(dist.items()):
            lines.append(f"- {area}: {count}")
    lines.append("")
    if mode == "offline":
        lines.append(
            "Nota: modo offline avalia apenas estrutura, pareamento, schemas e runner. "
            "Métricas semânticas dependem de inferência e ficam como not_evaluated."
        )
    else:
        lines.extend(
            [
                "Nota: confiança do modelo não é probabilidade jurídica.",
                "Avaliação sintética não substitui validação humana.",
                "safe_fallback/request_human_review interno ≠ handoff de atendimento.",
                "forbidden_facts cobre só chaves proibidas listadas no expected.",
                "required_risks=[] NÃO proíbe riscos; use forbidden_risks ou risks_exhaustive.",
                "effective e2e inclui casos não observados como não entregues; observado não.",
                "Ocorrências requeridas ≠ categorias distintas ≠ casos aplicáveis.",
                "Falha em expectativa crítica reprova mesmo com as demais metas em PASS.",
                "Presença da chave do fato ≠ correção do valor vigente.",
                "Ver METRICS_COVERAGE.md no artefato para limites de cada checagem.",
            ]
        )
    lines.append("")
    return "\n".join(lines)
