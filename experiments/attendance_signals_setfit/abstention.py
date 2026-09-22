"""Abstenção por sinal — limiares só na validação; scores ≠ probabilidade."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from experiments.attendance_signals_setfit.labels import Decision


@dataclass(frozen=True, slots=True)
class SignalThresholds:
    """Intervalo de abstenção por sinal (escolhido na validação).

    Decisão:
    - score >= positive_min → positive
    - score <= negative_max → negative
    - caso contrário → inconclusive

    Scores do classificador **não** são probabilidades calibradas.
    """

    positive_min: float
    negative_max: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.negative_max <= self.positive_min <= 1.0:
            raise ValueError(
                f"thresholds inválidos: negative_max={self.negative_max}, "
                f"positive_min={self.positive_min}"
            )


@dataclass(frozen=True, slots=True)
class ScoredDecision:
    signal: str
    score: float
    decision: Decision
    abstained: bool


def decide_from_score(score: float, thresholds: SignalThresholds) -> ScoredDecision:
    if score >= thresholds.positive_min:
        decision: Decision = "positive"
        abstained = False
    elif score <= thresholds.negative_max:
        decision = "negative"
        abstained = False
    else:
        decision = "inconclusive"
        abstained = True
    return ScoredDecision(
        signal="",
        score=score,
        decision=decision,
        abstained=abstained,
    )


def apply_thresholds(
    scores: Mapping[str, float],
    thresholds: Mapping[str, SignalThresholds],
) -> dict[str, ScoredDecision]:
    out: dict[str, ScoredDecision] = {}
    for signal, score in scores.items():
        thr = thresholds[signal]
        decided = decide_from_score(score, thr)
        out[signal] = ScoredDecision(
            signal=signal,
            score=decided.score,
            decision=decided.decision,
            abstained=decided.abstained,
        )
    return out


def select_thresholds_on_validation(
    *,
    y_true: list[bool | None],
    scores: list[float],
    candidates: list[SignalThresholds] | None = None,
) -> SignalThresholds:
    """Escolhe limiar na validação maximizando F1 sobre exemplos *não* ambíguos.

    Cobertura entra como desempate (maior cobertura). Nunca usar o conjunto de
    teste aqui. Empate final → primeiro candidato (ordem estável).
    """
    if candidates is None:
        candidates = _default_candidates()

    best = candidates[0]
    best_key = (-1.0, -1.0)
    for candidate in candidates:
        f1, coverage = _f1_and_coverage(y_true, scores, candidate)
        key = (f1, coverage)
        if key > best_key:
            best_key = key
            best = candidate
    return best


def _default_candidates() -> list[SignalThresholds]:
    # Grades modestas — por sinal, não um limiar universal.
    out: list[SignalThresholds] = []
    for neg in (0.30, 0.35, 0.40, 0.45):
        for pos in (0.55, 0.60, 0.65, 0.70):
            if neg < pos:
                out.append(SignalThresholds(positive_min=pos, negative_max=neg))
    # Sem abstenção (limiar único 0.5).
    out.append(SignalThresholds(positive_min=0.5, negative_max=0.5))
    return out


def _f1_and_coverage(
    y_true: list[bool | None],
    scores: list[float],
    thresholds: SignalThresholds,
) -> tuple[float, float]:
    tp = fp = fn = 0
    decided = 0
    eligible = 0
    for truth, score in zip(y_true, scores, strict=True):
        if truth is None:
            continue
        eligible += 1
        decision = decide_from_score(score, thresholds).decision
        if decision == "inconclusive":
            continue
        decided += 1
        predicted_pos = decision == "positive"
        if predicted_pos and truth:
            tp += 1
        elif predicted_pos and not truth:
            fp += 1
        elif not predicted_pos and truth:
            fn += 1
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    coverage = decided / eligible if eligible else 0.0
    return f1, coverage
