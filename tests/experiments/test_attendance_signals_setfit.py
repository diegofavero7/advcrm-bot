"""Testes de infraestrutura do experimento SetFit (sem rede, sem pesos)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from experiments.attendance_signals_setfit import SIGNAL_IDS
from experiments.attendance_signals_setfit.abstention import (
    SignalThresholds,
    apply_thresholds,
    select_thresholds_on_validation,
)
from experiments.attendance_signals_setfit.dataset import AnnotatedExample, MessageTurn, load_jsonl
from experiments.attendance_signals_setfit.evaluate import (
    evaluate_with_stub,
    thresholds_from_mapping,
)
from experiments.attendance_signals_setfit.labels import normalize_label, validate_labels
from experiments.attendance_signals_setfit.manifest import build_base_manifest
from experiments.attendance_signals_setfit.metrics import compute_signal_metrics, evaluate_examples
from experiments.attendance_signals_setfit.runtime import (
    CheckpointMissingError,
    IndependentBinaryArtifact,
)
from experiments.attendance_signals_setfit.split import assert_no_leakage, split_examples
from experiments.attendance_signals_setfit.stubs import StubArtifact, stub_training_run
from experiments.attendance_signals_setfit.train import train_stub

PILOT = (
    Path(__file__).resolve().parents[2]
    / "experiments"
    / "attendance_signals_setfit"
    / "data"
    / "pilot_v1"
    / "examples.jsonl"
)
FIXTURES = (
    Path(__file__).resolve().parents[2]
    / "experiments"
    / "attendance_signals_setfit"
    / "fixtures"
    / "mock_scores.json"
)


def test_pilot_loads_and_has_multi_signal_examples() -> None:
    dataset = load_jsonl(PILOT)
    assert len(dataset.examples) >= 20
    multi = [ex for ex in dataset.examples if sum(1 for v in ex.labels.values() if v is True) >= 2]
    assert multi, "piloto deve representar múltiplos sinais coexistentes"


def test_ambiguous_not_converted_to_negative() -> None:
    dataset = load_jsonl(PILOT)
    ambiguous = [ex for ex in dataset.examples if ex.ambiguous_signals]
    assert ambiguous
    for ex in ambiguous:
        for signal in ex.ambiguous_signals:
            assert ex.labels[signal] is None
            assert normalize_label(None) is None


def test_validate_labels_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="desconhecidos"):
        validate_labels(
            {
                "explicit_human_request": False,
                "existing_client_declaration": False,
                "case_status_request": False,
                "outro": True,
            }
        )


def test_no_group_leakage_across_splits() -> None:
    dataset = load_jsonl(PILOT)
    split = split_examples(dataset.examples, seed=42)
    assert_no_leakage(split.assignments)

    conv_splits: dict[str, set[str]] = {}
    group_splits: dict[str, set[str]] = {}
    for a in split.assignments:
        conv_splits.setdefault(a.conversation_id, set()).add(a.split)
        group_splits.setdefault(a.paraphrase_group, set()).add(a.split)
    assert all(len(v) == 1 for v in conv_splits.values())
    assert all(len(v) == 1 for v in group_splits.values())
    # Componentes conexos: paráfrases do mesmo grupo não podem cair em splits distintos
    # mesmo com conversation_ids diferentes.
    assert len(split.train) + len(split.validation) + len(split.test) == len(dataset.examples)


def test_metrics_and_abstention_coverage() -> None:
    y_true: list[bool | None] = [True, True, False, False, None]
    decisions = ["positive", "inconclusive", "negative", "positive", "inconclusive"]
    metrics = compute_signal_metrics(
        signal="explicit_human_request", y_true=y_true, decisions=decisions
    )
    assert metrics.n_total == 5
    assert metrics.n_abstained == 2
    assert metrics.coverage == pytest.approx(3 / 5)
    # Positivo abstido reduz recall global (support_pos=2, tp=1 → recall 0.5).
    assert metrics.recall == pytest.approx(0.5)
    assert metrics.support_ambiguous == 1
    # Variante só-decididos existe, mas não substitui a global.
    assert metrics.f1_on_decided is not None
    assert metrics.coverage < 1.0


def test_evaluate_examples_with_fixture_scores() -> None:
    raw = json.loads(FIXTURES.read_text(encoding="utf-8"))
    thresholds = thresholds_from_mapping(raw["thresholds"])

    def ex(eid: str, labels: dict[str, bool | None]) -> AnnotatedExample:
        return AnnotatedExample(
            example_id=eid,
            conversation_id=f"c_{eid}",
            paraphrase_group=f"g_{eid}",
            messages=(MessageTurn(role="lead", text=eid),),
            labels=validate_labels(labels),
            justification="fixture",
            difficulty_groups=("fixture",),
        )

    examples = [
        ex(
            "fixture_pos_human",
            {
                "explicit_human_request": True,
                "existing_client_declaration": False,
                "case_status_request": False,
            },
        ),
        ex(
            "fixture_neg",
            {
                "explicit_human_request": False,
                "existing_client_declaration": False,
                "case_status_request": False,
            },
        ),
        ex(
            "fixture_abstain",
            {
                "explicit_human_request": True,
                "existing_client_declaration": False,
                "case_status_request": False,
            },
        ),
        ex(
            "fixture_multi",
            {
                "explicit_human_request": True,
                "existing_client_declaration": True,
                "case_status_request": True,
            },
        ),
    ]
    report = evaluate_examples(
        examples=examples,
        scores_by_example=raw["scores"],
        thresholds=thresholds,
        signal_ids=SIGNAL_IDS,
        split="fixture",
    )
    hum = report.signals["explicit_human_request"]
    assert hum.n_abstained >= 1
    assert hum.coverage < 1.0
    assert "fixture" in report.by_difficulty


def test_threshold_selection_uses_validation_only_api() -> None:
    y_true = [True, False, True, False, True, False]
    scores = [0.9, 0.1, 0.55, 0.45, 0.8, 0.2]
    selected = select_thresholds_on_validation(y_true=y_true, scores=scores)
    assert isinstance(selected, SignalThresholds)
    decided = apply_thresholds(
        {"explicit_human_request": 0.5},
        {"explicit_human_request": selected},
    )
    assert decided["explicit_human_request"].decision in {
        "positive",
        "negative",
        "inconclusive",
    }


def test_manifest_reproducible_fields(tmp_path: Path) -> None:
    # Copiar para tmp para hash estável do path usado
    target = tmp_path / "examples.jsonl"
    target.write_bytes(PILOT.read_bytes())
    manifest = build_base_manifest(dataset_path=target, seed=42, mode="infrastructure")
    payload = manifest.to_dict()
    assert payload["kind"].startswith("attendance_signals_setfit")
    assert payload["seed"] == 42
    assert payload["device"] == "cpu"
    assert payload["base_checkpoint_license"] == "apache-2.0"
    assert payload["dataset_sha256"]
    assert payload["dataset_sha256"] != "unknown"
    assert "evaluations/compare" in str(payload["forbidden_data_sources"])
    out = tmp_path / "manifest.json"
    manifest.write(out)
    reloaded = json.loads(out.read_text(encoding="utf-8"))
    assert reloaded["classifier_strategy"] == "independent_binary_setfit"


def test_missing_artifact_clear_error(tmp_path: Path) -> None:
    missing = tmp_path / "no_artifact"
    with pytest.raises(CheckpointMissingError, match="ausente"):
        IndependentBinaryArtifact.load(missing)


def test_stub_train_and_eval_no_network(tmp_path: Path) -> None:
    dataset = load_jsonl(PILOT)
    result = stub_training_run(seed=42, n_train=len(dataset.examples))
    assert result["network_used"] is False
    assert result["downloaded"] is False

    out = tmp_path / "stub_train"
    train_stub(train_examples=dataset.examples[:5], seed=7, output_dir=out)
    report, scores, resources = evaluate_with_stub(dataset.examples[:5], split="stub")
    assert resources.get("n_examples") == 5
    assert set(scores[dataset.examples[0].example_id]) == set(SIGNAL_IDS)
    assert report.signals
    assert (out / "stub_train_result.json").is_file()


def test_stub_predictor_tokens() -> None:
    stub = StubArtifact.default()
    scores = stub.predict_scores(
        [
            "preciso ATENDENTE_HUMANO agora",
            "SOU_CLIENTE do escritório",
            "quero ANDAMENTO_PROCESSO",
            "olá",
        ]
    )
    assert scores[0]["explicit_human_request"] > 0.5
    assert scores[1]["existing_client_declaration"] > 0.5
    assert scores[2]["case_status_request"] > 0.5
    assert scores[3]["explicit_human_request"] < 0.5


def test_pipeline_modules_untouched_by_experiment_imports() -> None:
    """Importar o experimento não deve puxar SetFit nem alterar política."""
    import importlib
    import sys

    banned = {"setfit", "torch", "sentence_transformers", "transformers"}
    before = {name for name in banned if name in sys.modules}
    importlib.import_module("experiments.attendance_signals_setfit.dataset")
    importlib.import_module("experiments.attendance_signals_setfit.metrics")
    importlib.import_module("experiments.attendance_signals_setfit.split")
    after = {name for name in banned if name in sys.modules}
    assert after == before

    from app.policies.resolution import POLICY_VERSION

    assert POLICY_VERSION == "conservative_action.v7"


def test_cli_prepare_data(tmp_path: Path) -> None:
    from experiments.attendance_signals_setfit.__main__ import main

    out = tmp_path / "prepare"
    code = main(["prepare-data", "--dataset", str(PILOT), "--out", str(out), "--seed", "42"])
    assert code == 0
    assert (out / "manifest.json").is_file()
    assert (out / "split_assignments.json").is_file()
    assert (out / "distribution.json").is_file()


def test_cli_download_refuses_without_flag() -> None:
    from experiments.attendance_signals_setfit.__main__ import main

    code = main(["download-checkpoint", "--out", "/tmp/should_not_download_setfit"])
    assert code == 2


def test_cli_load_artifact_missing(tmp_path: Path) -> None:
    from experiments.attendance_signals_setfit.__main__ import main

    code = main(["load-artifact", "--artifact", str(tmp_path / "missing")])
    assert code == 2


def test_cli_train_stub_default(tmp_path: Path) -> None:
    from experiments.attendance_signals_setfit.__main__ import main

    out = tmp_path / "train_out"
    code = main(["train", "--dataset", str(PILOT), "--out", str(out), "--seed", "42"])
    assert code == 0
    assert (out / "stub" / "stub_train_result.json").is_file()
