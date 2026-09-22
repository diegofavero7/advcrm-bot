"""Dublês para testes de infraestrutura — sem SetFit, torch, download ou rede."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from experiments.attendance_signals_setfit import SIGNAL_IDS


@dataclass
class StubBinaryModel:
    """Classificador binário falso: score determinístico a partir do texto."""

    signal: str
    positive_token: str

    def predict_proba(self, texts: Sequence[str]) -> list[list[float]]:
        rows: list[list[float]] = []
        for text in texts:
            positive = self.positive_token.lower() in text.lower()
            score = 0.9 if positive else 0.1
            rows.append([1.0 - score, score])
        return rows


@dataclass
class StubArtifact:
    models: Mapping[str, StubBinaryModel]
    thresholds: Mapping[str, Mapping[str, float]]

    @classmethod
    def default(cls) -> StubArtifact:
        models = {
            "explicit_human_request": StubBinaryModel("explicit_human_request", "ATENDENTE_HUMANO"),
            "existing_client_declaration": StubBinaryModel(
                "existing_client_declaration", "SOU_CLIENTE"
            ),
            "case_status_request": StubBinaryModel("case_status_request", "ANDAMENTO_PROCESSO"),
        }
        thresholds = {signal: {"positive_min": 0.6, "negative_max": 0.4} for signal in SIGNAL_IDS}
        return cls(models=models, thresholds=thresholds)

    def predict_scores(self, texts: Sequence[str]) -> list[dict[str, float]]:
        results: list[dict[str, float]] = []
        for text in texts:
            scores: dict[str, float] = {}
            for signal, model in self.models.items():
                proba = model.predict_proba([text])[0]
                scores[signal] = float(proba[1])
            results.append(scores)
        return results


def stub_training_run(*, seed: int, n_train: int) -> dict[str, object]:
    """Simula saída de treino sem carregar pesos."""
    return {
        "status": "stubbed",
        "seed": seed,
        "n_train": n_train,
        "downloaded": False,
        "network_used": False,
        "models": {signal: f"stub://{signal}" for signal in SIGNAL_IDS},
    }
