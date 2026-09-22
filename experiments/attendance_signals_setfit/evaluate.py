"""Avaliação e exportação de previsões (scores + decisões com abstenção)."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from experiments.attendance_signals_setfit import SIGNAL_IDS
from experiments.attendance_signals_setfit.abstention import SignalThresholds, apply_thresholds
from experiments.attendance_signals_setfit.dataset import AnnotatedExample
from experiments.attendance_signals_setfit.metrics import EvaluationReport, evaluate_examples
from experiments.attendance_signals_setfit.stubs import StubArtifact


def thresholds_from_mapping(
    raw: Mapping[str, Mapping[str, float]],
) -> dict[str, SignalThresholds]:
    return {
        signal: SignalThresholds(
            positive_min=float(values["positive_min"]),
            negative_max=float(values["negative_max"]),
        )
        for signal, values in raw.items()
    }


def collect_scores(
    *,
    examples: Sequence[AnnotatedExample],
    predictor: Any,
) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    texts = [ex.text for ex in examples]
    t0 = time.perf_counter()
    scored = predictor.predict_scores(texts)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    by_id = {ex.example_id: scores for ex, scores in zip(examples, scored, strict=True)}
    resources = {
        "latency_ms_total": round(elapsed_ms, 3),
        "latency_ms_per_example": round(elapsed_ms / max(len(examples), 1), 3),
        "n_examples": len(examples),
        "memory_rss_mb": _optional_rss_mb(),
        "note": "Latência/memória medidas nesta execução; ausentes se stub sem carga real.",
    }
    return by_id, resources


def _optional_rss_mb() -> float | None:
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux: kB; macOS: bytes — reportar kB/1024 como MB aproximado em Linux.
        return round(usage / 1024.0, 2)
    except Exception:
        return None


def run_evaluation(
    *,
    examples: Sequence[AnnotatedExample],
    scores_by_example: Mapping[str, Mapping[str, float]],
    thresholds: Mapping[str, SignalThresholds],
    split: str,
) -> EvaluationReport:
    return evaluate_examples(
        examples=examples,
        scores_by_example=scores_by_example,
        thresholds=thresholds,
        signal_ids=SIGNAL_IDS,
        split=split,
    )


def export_predictions(
    *,
    examples: Sequence[AnnotatedExample],
    scores_by_example: Mapping[str, Mapping[str, float]],
    thresholds: Mapping[str, SignalThresholds],
    path: Path,
) -> None:
    rows: list[dict[str, Any]] = []
    for ex in examples:
        scores = dict(scores_by_example.get(ex.example_id, {}))
        decisions = apply_thresholds(scores, thresholds) if scores else {}
        rows.append(
            {
                "example_id": ex.example_id,
                "conversation_id": ex.conversation_id,
                "paraphrase_group": ex.paraphrase_group,
                "labels": {k: v for k, v in ex.labels.items()},
                "scores": scores,
                "decisions": {
                    signal: {
                        "decision": decided.decision,
                        "abstained": decided.abstained,
                        "score": decided.score,
                    }
                    for signal, decided in decisions.items()
                },
                "note": "score ≠ probabilidade calibrada; abstenção ≠ decisão operacional",
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def evaluate_with_stub(
    examples: Sequence[AnnotatedExample],
    *,
    split: str = "fixture",
) -> tuple[EvaluationReport, dict[str, dict[str, float]], dict[str, Any]]:
    stub = StubArtifact.default()
    scores, resources = collect_scores(examples=examples, predictor=stub)
    report = run_evaluation(
        examples=examples,
        scores_by_example=scores,
        thresholds=thresholds_from_mapping(stub.thresholds),
        split=split,
    )
    return report, scores, resources
