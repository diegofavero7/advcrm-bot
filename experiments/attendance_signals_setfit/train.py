"""Treinamento SetFit — só quando deps e checkpoint local estão disponíveis.

Nesta etapa de infraestrutura, ``train_real`` não é chamado pelos testes.
O caminho padrão da CLI exige ``--allow-train`` e checkpoint local pré-baixado.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from experiments.attendance_signals_setfit import SIGNAL_IDS
from experiments.attendance_signals_setfit.abstention import (
    select_thresholds_on_validation,
)
from experiments.attendance_signals_setfit.dataset import AnnotatedExample
from experiments.attendance_signals_setfit.labels import is_train_eligible
from experiments.attendance_signals_setfit.manifest import (
    build_base_manifest,
    package_versions_optional,
)
from experiments.attendance_signals_setfit.runtime import (
    CheckpointMissingError,
    ExperimentalDependencyMissingError,
    assert_local_dir,
)
from experiments.attendance_signals_setfit.stubs import stub_training_run


def _eligible_pairs(
    examples: Sequence[AnnotatedExample], signal: str
) -> tuple[list[str], list[int]]:
    texts: list[str] = []
    labels: list[int] = []
    for ex in examples:
        label = ex.labels[signal]
        if not is_train_eligible(label):
            continue
        texts.append(ex.text)
        labels.append(1 if label else 0)
    return texts, labels


def train_stub(
    *,
    train_examples: Sequence[AnnotatedExample],
    seed: int,
    output_dir: Path,
) -> dict[str, Any]:
    """Treino falso para validar a CLI sem rede."""
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = stub_training_run(seed=seed, n_train=len(train_examples))
    (output_dir / "stub_train_result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def train_real(
    *,
    train_examples: Sequence[AnnotatedExample],
    validation_examples: Sequence[AnnotatedExample],
    base_checkpoint_dir: Path,
    output_dir: Path,
    seed: int = 42,
    device: str = "cpu",
    num_epochs: int = 1,
    batch_size: int = 8,
) -> dict[str, Any]:
    """Treina três SetFit binários independentes a partir de checkpoint *local*."""
    assert_local_dir(base_checkpoint_dir, what="checkpoint base")
    try:
        from datasets import Dataset
        from setfit import SetFitModel, Trainer, TrainingArguments
    except ImportError as exc:
        raise ExperimentalDependencyMissingError(
            "Deps experimentais ausentes. pip install -e '.[setfit-experiment]'"
        ) from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    models_root = output_dir / "models"
    models_root.mkdir(parents=True, exist_ok=True)

    thresholds: dict[str, dict[str, float]] = {}
    trained: dict[str, str] = {}

    for signal in SIGNAL_IDS:
        texts, labels = _eligible_pairs(train_examples, signal)
        if len(set(labels)) < 2:
            raise ValueError(
                f"sinal {signal}: treino precisa de positivos e negativos "
                f"(após excluir ambíguos); got labels={set(labels)}"
            )
        train_ds = Dataset.from_dict({"text": texts, "label": labels})
        model = SetFitModel.from_pretrained(
            str(base_checkpoint_dir),
            local_files_only=True,
            labels=["negative", "positive"],
        )
        # Forçar CPU por padrão — não disputar GPU com Qwen.
        if hasattr(model, "to"):
            model.to(device)

        args = TrainingArguments(
            output_dir=str(models_root / f"{signal}_runs"),
            batch_size=batch_size,
            num_epochs=num_epochs,
            seed=seed,
            report_to="none",
        )
        trainer = Trainer(model=model, args=args, train_dataset=train_ds)
        trainer.train()

        signal_dir = models_root / signal
        model.save_pretrained(str(signal_dir))
        trained[signal] = str(signal_dir)

        # Limiar só na validação.
        val_texts, val_labels_int = _eligible_pairs(validation_examples, signal)
        y_true: list[bool | None] = []
        scores: list[float] = []
        # Incluir ambíguos da validação com None (não usados no F1 de seleção).
        for ex in validation_examples:
            label = ex.labels[signal]
            y_true.append(None if label is None else bool(label))
            proba = model.predict_proba([ex.text])[0]
            scores.append(float(proba[1]) if len(proba) > 1 else float(proba[0]))
        selected = select_thresholds_on_validation(y_true=y_true, scores=scores)
        thresholds[signal] = {
            "positive_min": selected.positive_min,
            "negative_max": selected.negative_max,
        }
        _ = val_texts, val_labels_int

    (output_dir / "thresholds.json").write_text(
        json.dumps(thresholds, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    versions = package_versions_optional()
    manifest = build_base_manifest(seed=seed, mode="train")
    manifest.device = device
    manifest.checkpoint_local_path = str(base_checkpoint_dir)
    manifest.trained_artifact_path = str(output_dir)
    manifest.setfit_version = versions["setfit"]
    manifest.torch_version = versions["torch"]
    manifest.sentence_transformers_version = versions["sentence_transformers"]
    manifest.train_params = {
        "num_epochs": num_epochs,
        "batch_size": batch_size,
        "strategy": "independent_binary_setfit",
    }
    manifest.thresholds = thresholds
    manifest.write(output_dir / "manifest.json")

    return {
        "status": "trained",
        "models": trained,
        "thresholds": thresholds,
        "output_dir": str(output_dir),
        "network_used": False,
    }


def require_checkpoint_or_explain(path: Path) -> None:
    if not path.exists():
        raise CheckpointMissingError(
            f"Checkpoint local ausente: {path}\n"
            "Comando futuro (após revisão humana dos dados):\n"
            "  python -m experiments.attendance_signals_setfit download-checkpoint "
            f"--out {path}\n"
            "Nesta etapa de infraestrutura o download não é executado."
        )
