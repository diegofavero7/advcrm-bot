#!/usr/bin/env python3
"""CLI do experimento SetFit de sinais de atendimento.

Comandos (infraestrutura — sem download/treino real por padrão):

  prepare-data
  download-checkpoint   # documentado; exige --i-understand-network
  train                 # stub por padrão; --allow-train para treino real
  evaluate              # stub/fixture por padrão
  load-artifact         # falha clara se artefato local ausente
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from experiments.attendance_signals_setfit import (
    DATASET_PILOT_ID,
    PROPOSED_CHECKPOINT,
    PROPOSED_CHECKPOINT_LICENSE,
    PROPOSED_CHECKPOINT_SOURCES,
)
from experiments.attendance_signals_setfit.dataset import distribution_report, load_jsonl
from experiments.attendance_signals_setfit.evaluate import (
    collect_scores,
    evaluate_with_stub,
    export_predictions,
    run_evaluation,
    thresholds_from_mapping,
)
from experiments.attendance_signals_setfit.manifest import build_base_manifest
from experiments.attendance_signals_setfit.runtime import (
    CheckpointMissingError,
    IndependentBinaryArtifact,
)
from experiments.attendance_signals_setfit.split import split_counts, split_examples
from experiments.attendance_signals_setfit.train import (
    require_checkpoint_or_explain,
    train_real,
    train_stub,
)

ROOT = Path(__file__).resolve().parent
DEFAULT_DATA = ROOT / "data" / "pilot_v1" / "examples.jsonl"
DEFAULT_ARTIFACT = Path("artifacts") / "experiments" / "attendance_signals_setfit" / "latest"
DEFAULT_CHECKPOINT_DIR = (
    Path("artifacts")
    / "experiments"
    / "attendance_signals_setfit"
    / "checkpoints"
    / "paraphrase-multilingual-MiniLM-L12-v2"
)


def _cmd_prepare_data(args: argparse.Namespace) -> int:
    dataset = load_jsonl(Path(args.dataset))
    report = distribution_report(dataset)
    split = split_examples(dataset.examples, seed=args.seed)
    counts = split_counts(split)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    assignments = [
        {
            "example_id": a.example_id,
            "conversation_id": a.conversation_id,
            "paraphrase_group": a.paraphrase_group,
            "split": a.split,
        }
        for a in split.assignments
    ]
    (out_dir / "split_assignments.json").write_text(
        json.dumps(assignments, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "distribution.json").write_text(
        json.dumps({**report, "split_counts": counts}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    manifest = build_base_manifest(
        dataset_path=Path(args.dataset), seed=args.seed, mode="prepare-data"
    )
    manifest.dataset_id = dataset.dataset_id or DATASET_PILOT_ID
    manifest.split_counts = counts
    manifest.notes.append(
        "Revise REVIEW.md antes de qualquer treino real. "
        "Exemplos com needs_human_review=true exigem anotação humana."
    )
    manifest.write(out_dir / "manifest.json")

    review_needed = [e.example_id for e in dataset.examples if e.needs_human_review]
    print(
        json.dumps(
            {
                "dataset_id": dataset.dataset_id,
                "n_examples": len(dataset.examples),
                "split_counts": counts,
                "needs_human_review_ids": review_needed,
                "out": str(out_dir),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _cmd_download_checkpoint(args: argparse.Namespace) -> int:
    if not args.i_understand_network:
        print(
            "Recusado: download exige --i-understand-network.\n"
            f"Checkpoint proposto: {PROPOSED_CHECKPOINT}\n"
            f"Licença: {PROPOSED_CHECKPOINT_LICENSE}\n"
            "Fontes:\n  - " + "\n  - ".join(PROPOSED_CHECKPOINT_SOURCES) + "\n"
            "Após autorizar, o comando usará huggingface_hub.snapshot_download "
            "para o diretório local (CPU). Não execute durante avaliação live do Qwen "
            "na mesma GPU.",
            file=sys.stderr,
        )
        return 2

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print(
            "huggingface_hub ausente. pip install -e '.[setfit-experiment]'",
            file=sys.stderr,
        )
        return 2

    snapshot_download(
        repo_id=PROPOSED_CHECKPOINT,
        local_dir=str(out),
        local_dir_use_symlinks=False,
    )
    print(json.dumps({"status": "downloaded", "path": str(out)}, indent=2))
    return 0


def _cmd_train(args: argparse.Namespace) -> int:
    dataset = load_jsonl(Path(args.dataset))
    split = split_examples(dataset.examples, seed=args.seed)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    if not args.allow_train:
        result = train_stub(train_examples=split.train, seed=args.seed, output_dir=out / "stub")
        print(
            json.dumps(
                {
                    **result,
                    "note": "Treino real desabilitado (use --allow-train + checkpoint local).",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    checkpoint = Path(args.checkpoint)
    try:
        require_checkpoint_or_explain(checkpoint)
        result = train_real(
            train_examples=split.train,
            validation_examples=split.validation,
            base_checkpoint_dir=checkpoint,
            output_dir=out,
            seed=args.seed,
            device=args.device,
            num_epochs=args.num_epochs,
            batch_size=args.batch_size,
        )
    except CheckpointMissingError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_evaluate(args: argparse.Namespace) -> int:
    dataset = load_jsonl(Path(args.dataset))
    split = split_examples(dataset.examples, seed=args.seed)
    examples = list(split.get(args.split))  # type: ignore[arg-type]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    if args.artifact:
        try:
            artifact = IndependentBinaryArtifact.load(Path(args.artifact), device=args.device)
        except CheckpointMissingError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        scores, resources = collect_scores(examples=examples, predictor=artifact)
        thresholds = thresholds_from_mapping(artifact.thresholds)
        report = run_evaluation(
            examples=examples,
            scores_by_example=scores,
            thresholds=thresholds,
            split=args.split,
        )
    else:
        report, scores, resources = evaluate_with_stub(examples, split=args.split)
        from experiments.attendance_signals_setfit.stubs import StubArtifact

        thresholds = thresholds_from_mapping(StubArtifact.default().thresholds)

    export_predictions(
        examples=examples,
        scores_by_example=scores,
        thresholds=thresholds,
        path=out / "predictions.json",
    )
    (out / "report.json").write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out / "resources.json").write_text(
        json.dumps(resources, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "split": args.split,
                "n_examples": len(examples),
                "out": str(out),
                "mode": "artifact" if args.artifact else "stub",
                "signals": {
                    s: {
                        "f1": m.f1,
                        "coverage": m.coverage,
                        "abstention_rate": m.abstention_rate,
                    }
                    for s, m in report.signals.items()
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _cmd_load_artifact(args: argparse.Namespace) -> int:
    path = Path(args.artifact)
    try:
        IndependentBinaryArtifact.load(path, device=args.device)
    except CheckpointMissingError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"status": "loaded", "artifact": str(path)}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m experiments.attendance_signals_setfit",
        description="Experimento SetFit de sinais de atendimento (avaliação apenas).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_prep = sub.add_parser("prepare-data", help="Validar dados, split e manifesto")
    p_prep.add_argument("--dataset", type=Path, default=DEFAULT_DATA)
    p_prep.add_argument(
        "--out",
        type=Path,
        default=Path("artifacts/experiments/attendance_signals_setfit/prepare"),
    )
    p_prep.add_argument("--seed", type=int, default=42)
    p_prep.set_defaults(func=_cmd_prepare_data)

    p_dl = sub.add_parser(
        "download-checkpoint",
        help="Baixar checkpoint base (exige flag explícita de rede)",
    )
    p_dl.add_argument("--out", type=Path, default=DEFAULT_CHECKPOINT_DIR)
    p_dl.add_argument(
        "--i-understand-network",
        action="store_true",
        help="Autoriza download Hugging Face (não usado na etapa de infraestrutura)",
    )
    p_dl.set_defaults(func=_cmd_download_checkpoint)

    p_train = sub.add_parser("train", help="Treinar (stub por padrão)")
    p_train.add_argument("--dataset", type=Path, default=DEFAULT_DATA)
    p_train.add_argument("--out", type=Path, default=DEFAULT_ARTIFACT)
    p_train.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT_DIR)
    p_train.add_argument("--seed", type=int, default=42)
    p_train.add_argument("--device", default="cpu")
    p_train.add_argument("--num-epochs", type=int, default=1)
    p_train.add_argument("--batch-size", type=int, default=8)
    p_train.add_argument(
        "--allow-train",
        action="store_true",
        help="Executa treino real (checkpoint local obrigatório)",
    )
    p_train.set_defaults(func=_cmd_train)

    p_eval = sub.add_parser("evaluate", help="Avaliar (stub/fixture por padrão)")
    p_eval.add_argument("--dataset", type=Path, default=DEFAULT_DATA)
    p_eval.add_argument("--split", choices=["train", "validation", "test"], default="test")
    p_eval.add_argument(
        "--out",
        type=Path,
        default=Path("artifacts/experiments/attendance_signals_setfit/eval_stub"),
    )
    p_eval.add_argument("--seed", type=int, default=42)
    p_eval.add_argument("--artifact", type=Path, default=None)
    p_eval.add_argument("--device", default="cpu")
    p_eval.set_defaults(func=_cmd_evaluate)

    p_load = sub.add_parser("load-artifact", help="Carregar artefato local treinado")
    p_load.add_argument("--artifact", type=Path, required=True)
    p_load.add_argument("--device", default="cpu")
    p_load.set_defaults(func=_cmd_load_artifact)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
