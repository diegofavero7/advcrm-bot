"""Métricas por sinal com cobertura/abstenção no denominador global."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from experiments.attendance_signals_setfit.abstention import SignalThresholds, apply_thresholds
from experiments.attendance_signals_setfit.dataset import AnnotatedExample
from experiments.attendance_signals_setfit.labels import Decision, LabelValue


@dataclass(frozen=True, slots=True)
class ConfusionCounts:
    tp: int = 0
    tn: int = 0
    fp: int = 0
    fn: int = 0

    def as_matrix(self) -> dict[str, int]:
        return {"tp": self.tp, "tn": self.tn, "fp": self.fp, "fn": self.fn}


@dataclass
class SignalMetrics:
    signal: str
    precision: float | None
    recall: float | None
    f1: float | None
    support_positive: int
    support_negative: int
    support_ambiguous: int
    confusion: ConfusionCounts
    n_total: int
    n_decided: int
    n_abstained: int
    coverage: float
    abstention_rate: float
    # Métricas só nos aceitos (auditoria) — NÃO substituem as globais.
    precision_on_decided: float | None = None
    recall_on_decided: float | None = None
    f1_on_decided: float | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["confusion"] = self.confusion.as_matrix()
        return payload


@dataclass
class EvaluationReport:
    split: str
    signals: dict[str, SignalMetrics] = field(default_factory=dict)
    by_difficulty: dict[str, dict[str, SignalMetrics]] = field(default_factory=dict)
    per_example_errors: list[dict[str, Any]] = field(default_factory=list)
    uncovered_cases: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "split": self.split,
            "signals": {k: v.to_dict() for k, v in self.signals.items()},
            "by_difficulty": {
                group: {sig: m.to_dict() for sig, m in metrics.items()}
                for group, metrics in self.by_difficulty.items()
            },
            "per_example_errors": self.per_example_errors,
            "uncovered_cases": self.uncovered_cases,
            "notes": self.notes,
        }


def _safe_div(num: float, den: float) -> float | None:
    if den == 0:
        return None
    return num / den


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None:
        return None
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def compute_signal_metrics(
    *,
    signal: str,
    y_true: Sequence[LabelValue],
    decisions: Sequence[Decision],
) -> SignalMetrics:
    """Métricas binárias com abstenção no denominador de cobertura.

    Ambíguos (y_true is None) não entram em precision/recall/F1 nem confusão;
    entram em support_ambiguous e, se abstidos/decididos, na cobertura global.
    """
    if len(y_true) != len(decisions):
        raise ValueError("y_true e decisions com tamanhos diferentes")

    tp = tn = fp = fn = 0
    support_pos = support_neg = support_amb = 0
    n_decided = n_abstained = 0

    for truth, decision in zip(y_true, decisions, strict=True):
        abstained = decision == "inconclusive"
        if abstained:
            n_abstained += 1
        else:
            n_decided += 1

        if truth is None:
            support_amb += 1
            continue

        if truth:
            support_pos += 1
        else:
            support_neg += 1

        if abstained:
            # Abstenção em exemplo rotulado: não conta em confusão, mas reduz cobertura.
            continue

        predicted_pos = decision == "positive"
        if predicted_pos and truth:
            tp += 1
        elif predicted_pos and not truth:
            fp += 1
        elif (not predicted_pos) and truth:
            fn += 1
        else:
            tn += 1

    n_total = len(y_true)
    coverage = n_decided / n_total if n_total else 0.0
    abstention_rate = n_abstained / n_total if n_total else 0.0

    # Precision/recall/F1 sobre o conjunto *inteiro* de rotulados não ambíguos:
    # abstenções em positivos = FN efetivos para recall global; em negativos não
    # aumentam FP. Isso evita reportar só os aceitos como resultado global.
    labeled = support_pos + support_neg
    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, support_pos) if support_pos else None
    # Quando há abstenção em positivos, recall usa support_pos (inclui abstidos).
    f1 = _f1(precision, recall)

    # Variante só-decididos (transparência, não métrica global).
    d_tp = d_fp = d_fn = 0
    for truth, decision in zip(y_true, decisions, strict=True):
        if truth is None or decision == "inconclusive":
            continue
        predicted_pos = decision == "positive"
        if predicted_pos and truth:
            d_tp += 1
        elif predicted_pos and not truth:
            d_fp += 1
        elif (not predicted_pos) and truth:
            d_fn += 1
    precision_d = _safe_div(d_tp, d_tp + d_fp)
    recall_d = _safe_div(d_tp, d_tp + d_fn)
    f1_d = _f1(precision_d, recall_d)

    _ = labeled  # documentado: denominador de cobertura é n_total

    return SignalMetrics(
        signal=signal,
        precision=precision,
        recall=recall,
        f1=f1,
        support_positive=support_pos,
        support_negative=support_neg,
        support_ambiguous=support_amb,
        confusion=ConfusionCounts(tp=tp, tn=tn, fp=fp, fn=fn),
        n_total=n_total,
        n_decided=n_decided,
        n_abstained=n_abstained,
        coverage=coverage,
        abstention_rate=abstention_rate,
        precision_on_decided=precision_d,
        recall_on_decided=recall_d,
        f1_on_decided=f1_d,
    )


def evaluate_examples(
    *,
    examples: Sequence[AnnotatedExample],
    scores_by_example: Mapping[str, Mapping[str, float]],
    thresholds: Mapping[str, SignalThresholds],
    signal_ids: Sequence[str],
    split: str,
) -> EvaluationReport:
    report = EvaluationReport(
        split=split,
        notes=[
            "Scores do classificador não são probabilidades calibradas.",
            "Abstenção não autoriza handoff nem decisão operacional.",
            "precision/recall/F1 globais incluem efeito de abstenção no recall "
            "(positivos abstidos reduzem recall); coverage usa o conjunto inteiro.",
        ],
    )

    for signal in signal_ids:
        y_true: list[LabelValue] = []
        decisions: list[Decision] = []
        for ex in examples:
            scores = scores_by_example.get(ex.example_id, {})
            if signal not in scores:
                report.uncovered_cases.append(
                    {
                        "example_id": ex.example_id,
                        "signal": signal,
                        "reason": "score_ausente",
                    }
                )
                y_true.append(ex.labels[signal])
                decisions.append("inconclusive")
                continue
            decided = apply_thresholds({signal: scores[signal]}, {signal: thresholds[signal]})[
                signal
            ]
            y_true.append(ex.labels[signal])
            decisions.append(decided.decision)
            truth = ex.labels[signal]
            if truth is not None and decided.decision != "inconclusive":
                predicted_pos = decided.decision == "positive"
                if predicted_pos != truth:
                    report.per_example_errors.append(
                        {
                            "example_id": ex.example_id,
                            "signal": signal,
                            "truth": truth,
                            "decision": decided.decision,
                            "score": decided.score,
                            "difficulty_groups": list(ex.difficulty_groups),
                        }
                    )
        report.signals[signal] = compute_signal_metrics(
            signal=signal, y_true=y_true, decisions=decisions
        )

    # Por grupo de dificuldade.
    groups: dict[str, list[AnnotatedExample]] = defaultdict(list)
    for ex in examples:
        for group in ex.difficulty_groups or ("unspecified",):
            groups[group].append(ex)
    for group, subset in sorted(groups.items()):
        report.by_difficulty[group] = {}
        for signal in signal_ids:
            y_true = [ex.labels[signal] for ex in subset]
            decisions = []
            for ex in subset:
                scores = scores_by_example.get(ex.example_id, {})
                if signal not in scores:
                    decisions.append("inconclusive")
                    continue
                decisions.append(
                    apply_thresholds({signal: scores[signal]}, {signal: thresholds[signal]})[
                        signal
                    ].decision
                )
            report.by_difficulty[group][signal] = compute_signal_metrics(
                signal=signal, y_true=y_true, decisions=decisions
            )

    return report
