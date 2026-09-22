"""Carregamento local do artefato treinado — sem download acidental."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from experiments.attendance_signals_setfit import SIGNAL_IDS


class CheckpointMissingError(FileNotFoundError):
    """Checkpoint/artefato local ausente — mensagem explícita, sem fallback de rede."""


class ExperimentalDependencyMissingError(RuntimeError):
    """Deps do extra ``setfit-experiment`` não instaladas."""


class Predictor(Protocol):
    def predict_scores(self, texts: Sequence[str]) -> list[dict[str, float]]: ...


@dataclass(frozen=True, slots=True)
class ArtifactPaths:
    root: Path
    manifest: Path
    thresholds: Path

    def signal_dir(self, signal: str) -> Path:
        return self.root / "models" / signal


def resolve_artifact_paths(root: Path) -> ArtifactPaths:
    return ArtifactPaths(
        root=root,
        manifest=root / "manifest.json",
        thresholds=root / "thresholds.json",
    )


def assert_local_dir(path: Path, *, what: str) -> Path:
    if not path.exists():
        raise CheckpointMissingError(
            f"{what} ausente em {path}. "
            "Baixe/treine localmente antes; este experimento não faz download automático."
        )
    if not path.is_dir():
        raise CheckpointMissingError(f"{what} não é diretório: {path}")
    return path


def load_setfit_model_local(model_dir: Path, *, device: str = "cpu") -> Any:
    """Carrega um SetFitModel de diretório local com ``local_files_only=True``."""
    assert_local_dir(model_dir, what="modelo SetFit")
    try:
        from setfit import SetFitModel
    except ImportError as exc:
        raise ExperimentalDependencyMissingError(
            "Pacote setfit não instalado. Use: pip install -e '.[setfit-experiment]'"
        ) from exc

    return SetFitModel.from_pretrained(
        str(model_dir),
        local_files_only=True,
        device=device,
    )


class IndependentBinaryArtifact:
    """Três classificadores binários independentes + limiares por sinal."""

    def __init__(
        self,
        models: Mapping[str, Any],
        thresholds: Mapping[str, Mapping[str, float]],
        *,
        allow_network: bool = False,
    ) -> None:
        if allow_network:
            raise ValueError("allow_network=True é proibido neste experimento nesta etapa")
        self._models = dict(models)
        self._thresholds = {
            signal: thresholds[signal] for signal in SIGNAL_IDS if signal in thresholds
        }
        missing = [s for s in SIGNAL_IDS if s not in self._models]
        if missing:
            raise CheckpointMissingError(f"modelos ausentes no artefato: {missing}")

    @classmethod
    def load(cls, root: Path, *, device: str = "cpu") -> IndependentBinaryArtifact:
        paths = resolve_artifact_paths(root)
        assert_local_dir(root, what="artefato do experimento")
        if not paths.thresholds.is_file():
            raise CheckpointMissingError(f"thresholds ausentes: {paths.thresholds}")

        import json

        thresholds = json.loads(paths.thresholds.read_text(encoding="utf-8"))
        models: dict[str, Any] = {}
        for signal in SIGNAL_IDS:
            models[signal] = load_setfit_model_local(paths.signal_dir(signal), device=device)
        return cls(models=models, thresholds=thresholds, allow_network=False)

    def predict_scores(self, texts: Sequence[str]) -> list[dict[str, float]]:
        results: list[dict[str, float]] = [{signal: 0.0 for signal in SIGNAL_IDS} for _ in texts]
        for signal, model in self._models.items():
            # predict_proba: coluna da classe positiva (label 1).
            proba = model.predict_proba(list(texts))
            for idx, row in enumerate(proba):
                score = float(row[1]) if len(row) > 1 else float(row[0])
                results[idx][signal] = score
        return results

    @property
    def thresholds(self) -> Mapping[str, Mapping[str, float]]:
        return self._thresholds
