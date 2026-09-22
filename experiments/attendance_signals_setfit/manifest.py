"""Manifesto reprodutível do experimento (dados, deps, checkpoint, seed)."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from experiments.attendance_signals_setfit import (
    DATASET_PILOT_ID,
    EXPERIMENT_ID,
    PROPOSED_CHECKPOINT,
    PROPOSED_CHECKPOINT_LICENSE,
    PROPOSED_CHECKPOINT_SOURCES,
    SIGNAL_IDS,
)

UNKNOWN = "unknown"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def content_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def code_revision() -> tuple[str, bool | None]:
    try:
        rev = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True
        ).strip()
        dirty_out = subprocess.check_output(
            ["git", "status", "--porcelain"], stderr=subprocess.DEVNULL, text=True
        )
        return rev, bool(dirty_out.strip())
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return UNKNOWN, None


@dataclass
class ExperimentManifest:
    kind: str = EXPERIMENT_ID
    created_at: str = ""
    mode: str = "infrastructure"  # infrastructure | train | evaluate | predict
    seed: int = 42
    device: str = "cpu"
    dataset_id: str = DATASET_PILOT_ID
    dataset_path: str = ""
    dataset_sha256: str = UNKNOWN
    signal_ids: tuple[str, ...] = SIGNAL_IDS
    classifier_strategy: str = "independent_binary_setfit"
    strategy_justification: str = (
        "Três classificadores SetFit binários independentes: limiares e bandas de "
        "abstenção escolhidos por sinal na validação; sinais coexistentes sem "
        "acoplar cabeças; retreino isolado. Multilabel one-vs-rest do SetFit é "
        "equivalente em espírito, mas acoplaria o ciclo de treino do corpo."
    )
    base_checkpoint: str = PROPOSED_CHECKPOINT
    base_checkpoint_license: str = PROPOSED_CHECKPOINT_LICENSE
    base_checkpoint_sources: tuple[str, ...] = PROPOSED_CHECKPOINT_SOURCES
    checkpoint_local_path: str = ""
    trained_artifact_path: str = ""
    setfit_version: str = UNKNOWN
    torch_version: str = UNKNOWN
    sentence_transformers_version: str = UNKNOWN
    train_params: dict[str, Any] = field(default_factory=dict)
    thresholds: dict[str, dict[str, float]] = field(default_factory=dict)
    split_counts: dict[str, int] = field(default_factory=dict)
    code_revision: str = UNKNOWN
    code_dirty: bool | None = None
    notes: list[str] = field(default_factory=list)
    forbidden_data_sources: tuple[str, ...] = (
        "evaluations/cases (live histórico)",
        "evaluations/compare (understanding_compare_reserved.v1)",
        "artifacts/evaluations/*_live",
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def build_base_manifest(
    *,
    dataset_path: Path | None = None,
    seed: int = 42,
    mode: str = "infrastructure",
) -> ExperimentManifest:
    rev, dirty = code_revision()
    manifest = ExperimentManifest(
        created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        mode=mode,
        seed=seed,
        code_revision=rev,
        code_dirty=dirty,
        notes=[
            "Experimento de avaliação apenas — não altera política nem handoff.",
            "Qualidade desconhecida até treino + avaliação em dados separados.",
            "Não baixar pesos nem treinar nesta etapa de infraestrutura.",
            "Scores ≠ probabilidade calibrada.",
        ],
    )
    if dataset_path is not None and dataset_path.is_file():
        manifest.dataset_path = str(dataset_path)
        manifest.dataset_sha256 = file_sha256(dataset_path)
    return manifest


def package_versions_optional() -> dict[str, str]:
    """Versões das deps experimentais — sem importar torch/setfit no caminho normal."""
    versions = {
        "setfit": UNKNOWN,
        "torch": UNKNOWN,
        "sentence_transformers": UNKNOWN,
    }
    for name in list(versions):
        try:
            mod = __import__(name if name != "sentence_transformers" else "sentence_transformers")
            versions[name] = getattr(mod, "__version__", UNKNOWN)
        except ImportError:
            pass
    return versions
