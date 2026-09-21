"""Métricas da suíte de avaliação."""

from __future__ import annotations

import statistics
from collections import Counter
from typing import Any

NOT_EVALUATED = "not_evaluated"

# Dependem de inferência / runtime — não fazem sentido no offline estrutural.
SEMANTIC_METRIC_KEYS: tuple[str, ...] = (
    "intent_accuracy",
    "primary_area_accuracy",
    "subject_accuracy",
    "handoff_recall",
    "risk_recall",
    "invented_facts_rate",
    "repeated_question_rate",
    "fail_closed_rate",
    "latency_avg_ms",
    "latency_p50_ms",
    "latency_p95_ms",
    "retries_total",
    "distribution_by_area",
)

STRUCTURAL_TARGET_KEYS: tuple[str, ...] = ("json_valid", "schema_valid")

SEMANTIC_TARGET_KEYS: tuple[str, ...] = (
    "handoff_recall",
    "invented_facts",
    "repeated_questions",
    "intent",
    "area",
    "subject",
)


def _pct(num: int, den: int) -> float:
    if den == 0:
        return 0.0
    return round(100.0 * num / den, 2)


def compute_metrics(results: list[dict[str, Any]], *, mode: str = "live") -> dict[str, Any]:
    total = len(results)
    json_ok = sum(1 for r in results if r.get("json_valid"))
    schema_ok = sum(1 for r in results if r.get("schema_valid"))
    intent_ok = sum(1 for r in results if r.get("intent_match"))
    area_ok = sum(1 for r in results if r.get("area_match"))
    subject_ok = sum(1 for r in results if r.get("subject_match"))
    handoff_needed = [r for r in results if r.get("expected_handoff") is True]
    handoff_ok = sum(1 for r in handoff_needed if r.get("handoff_match"))
    risk_needed = [r for r in results if r.get("required_risks")]
    risk_ok = sum(1 for r in risk_needed if r.get("risks_match"))
    invented = sum(1 for r in results if r.get("invented_facts"))
    repeated = sum(1 for r in results if r.get("repeated_question"))
    fail_closed = sum(1 for r in results if r.get("fail_closed"))
    latencies = [float(r["latency_ms"]) for r in results if r.get("latency_ms") is not None]
    retries = sum(int(r.get("retry_count") or 0) for r in results)
    by_area = Counter(str(r.get("predicted_area") or "unknown") for r in results)

    latencies_sorted = sorted(latencies)

    def percentile(data: list[float], p: float) -> float | None:
        if not data:
            return None
        k = (len(data) - 1) * p
        f = int(k)
        c = min(f + 1, len(data) - 1)
        if f == c:
            return round(data[f], 2)
        return round(data[f] + (data[c] - data[f]) * (k - f), 2)

    metrics: dict[str, Any] = {
        "total_cases": total,
        "json_valid_rate": _pct(json_ok, total),
        "schema_valid_rate": _pct(schema_ok, total),
        "intent_accuracy": _pct(intent_ok, total),
        "primary_area_accuracy": _pct(area_ok, total),
        "subject_accuracy": _pct(subject_ok, total),
        "handoff_recall": _pct(handoff_ok, len(handoff_needed)),
        "risk_recall": _pct(risk_ok, len(risk_needed)),
        "invented_facts_rate": _pct(invented, total),
        "repeated_question_rate": _pct(repeated, total),
        "fail_closed_rate": _pct(fail_closed, total),
        "latency_avg_ms": round(statistics.mean(latencies), 2) if latencies else None,
        "latency_p50_ms": percentile(latencies_sorted, 0.50),
        "latency_p95_ms": percentile(latencies_sorted, 0.95),
        "retries_total": retries,
        "distribution_by_area": dict(by_area),
        "targets": {
            "json_valid_rate": 100.0,
            "schema_valid_rate": 100.0,
            "handoff_recall": 100.0,
            "invented_facts_rate_max": 0.0,
            "repeated_question_rate_max": 0.0,
            "intent_accuracy_min": 90.0,
            "primary_area_accuracy_min": 90.0,
            "subject_accuracy_min": 80.0,
        },
    }

    if mode == "offline":
        for key in SEMANTIC_METRIC_KEYS:
            metrics[key] = NOT_EVALUATED

    return metrics


def evaluate_targets(
    metrics: dict[str, Any],
    *,
    mode: str = "live",
) -> dict[str, bool | str]:
    t = metrics["targets"]
    structural: dict[str, bool | str] = {
        "json_valid": metrics["json_valid_rate"] >= t["json_valid_rate"],
        "schema_valid": metrics["schema_valid_rate"] >= t["schema_valid_rate"],
    }
    if mode == "offline":
        return {
            **structural,
            **{key: NOT_EVALUATED for key in SEMANTIC_TARGET_KEYS},
        }

    return {
        **structural,
        "handoff_recall": metrics["handoff_recall"] >= t["handoff_recall"],
        "invented_facts": metrics["invented_facts_rate"] <= t["invented_facts_rate_max"],
        "repeated_questions": metrics["repeated_question_rate"] <= t["repeated_question_rate_max"],
        "intent": metrics["intent_accuracy"] >= t["intent_accuracy_min"],
        "area": metrics["primary_area_accuracy"] >= t["primary_area_accuracy_min"],
        "subject": metrics["subject_accuracy"] >= t["subject_accuracy_min"],
    }


def _format_value(value: object, *, percent: bool = False) -> str:
    if value == NOT_EVALUATED:
        return NOT_EVALUATED
    if percent and isinstance(value, (int, float)):
        return f"{value}%"
    return str(value)


def _format_check(value: bool | str) -> str:
    if value == NOT_EVALUATED:
        return NOT_EVALUATED
    return "PASS" if value else "FAIL"


def render_report(
    metrics: dict[str, Any],
    checks: dict[str, bool | str],
    *,
    mode: str = "live",
) -> str:
    lines = [
        "# Relatório de avaliação AdvCRM Bot",
        "",
        f"Modo: {mode}",
        f"Casos: {metrics['total_cases']}",
        "",
        "## Métricas",
        f"- JSON válido: {_format_value(metrics['json_valid_rate'], percent=True)}",
        f"- Schema válido: {_format_value(metrics['schema_valid_rate'], percent=True)}",
        f"- Acurácia intenção: {_format_value(metrics['intent_accuracy'], percent=True)}",
        f"- Acurácia área: {_format_value(metrics['primary_area_accuracy'], percent=True)}",
        f"- Acurácia assunto: {_format_value(metrics['subject_accuracy'], percent=True)}",
        f"- Recall handoff: {_format_value(metrics['handoff_recall'], percent=True)}",
        f"- Recall riscos: {_format_value(metrics['risk_recall'], percent=True)}",
        f"- Fatos inventados: {_format_value(metrics['invented_facts_rate'], percent=True)}",
        f"- Perguntas repetidas: {_format_value(metrics['repeated_question_rate'], percent=True)}",
        f"- Fail-closed: {_format_value(metrics['fail_closed_rate'], percent=True)}",
        f"- Latência média ms: {_format_value(metrics['latency_avg_ms'])}",
        (
            f"- Latência p50/p95: {_format_value(metrics['latency_p50_ms'])} / "
            f"{_format_value(metrics['latency_p95_ms'])}"
        ),
        f"- Retries: {_format_value(metrics['retries_total'])}",
        "",
        "## Metas",
    ]
    for key, ok in checks.items():
        lines.append(f"- {key}: {_format_check(ok)}")
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
        lines.append(
            "Nota: confiança do modelo não é probabilidade jurídica. "
            "Avaliação sintética não substitui validação humana."
        )
    lines.append("")
    return "\n".join(lines)
