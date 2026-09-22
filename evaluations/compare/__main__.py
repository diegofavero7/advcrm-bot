"""CLI da comparação controlada de understanding.

Modos:
  run     — executa uma variante (offline com sintéticos, ou --live explícito)
  diff    — compara dois diretórios de artefatos (manifests + resultados)

Offline NÃO abre conexão de rede. Live exige configuração explícita por variante.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluations.compare.manifest import (
    UNKNOWN,
    compare_manifests_compatible,
    load_manifest,
)
from evaluations.compare.metrics import (
    compute_understanding_metrics,
    paired_comparison,
    render_paired_report,
    render_understanding_report,
)
from evaluations.compare.runner import (
    COMPARE_CASES,
    COMPARE_EXPECTED,
    CompareConfigError,
    RecordingClient,
    context_fingerprint_from_calls,
    load_compare_pairs,
    load_synthetic_map,
    require_live_variant_config,
    run_understanding_variant,
)

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts" / "evaluations"


def _write_variant_artifacts(
    *,
    results: list[dict[str, Any]],
    manifest: dict[str, Any],
    out_dir: Path,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    cases_dir = out_dir / "cases"
    cases_dir.mkdir(exist_ok=True)
    metrics = compute_understanding_metrics(results)

    index_rows: list[dict[str, Any]] = []
    for row in results:
        indexed = dict(row)
        rejected = indexed.pop("rejected_payload", None)
        case_id = str(indexed.get("case_id") or "unknown")
        case_payload = {
            "case_id": case_id,
            "case_hash": indexed.get("case_hash"),
            "request_snapshot": indexed.get("request_snapshot"),
            "expected": indexed.get("expected"),
            "understanding": indexed.get("understanding"),
            "error": indexed.get("error"),
            "rejected_payload": rejected,
            "metadata": indexed.get("metadata"),
            "stages": indexed.get("stages"),
            "crm_provenance_violation": indexed.get("crm_provenance_violation"),
            "reported_model": indexed.get("reported_model"),
            "score": {
                k: v
                for k, v in indexed.items()
                if k
                not in {
                    "request_snapshot",
                    "understanding",
                    "rejected_payload",
                }
            },
        }
        (cases_dir / f"{case_id}.json").write_text(
            json.dumps(case_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        slim = dict(indexed)
        slim["request_snapshot"] = None
        slim["understanding"] = None
        slim["case_artifact"] = f"cases/{case_id}.json"
        index_rows.append(slim)

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (out_dir / "results.json").write_text(
        json.dumps(
            {
                "manifest": manifest,
                "metrics": metrics,
                "results": index_rows,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    report = render_understanding_report(
        variant_id=str(manifest.get("variant_id")),
        metrics=metrics,
        manifest=manifest,
    )
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    return out_dir


async def _cmd_run(args: argparse.Namespace) -> int:
    from app.config import Settings, clear_settings_cache

    clear_settings_cache()
    settings = Settings()
    pairs = load_compare_pairs(
        cases_dir=Path(args.cases_dir) if args.cases_dir else COMPARE_CASES,
        expected_dir=Path(args.expected_dir) if args.expected_dir else COMPARE_EXPECTED,
        case_ids=args.case_ids,
    )

    if args.live:
        variant_cfg: dict[str, Any] | None = None
        if args.variant_config:
            variant_cfg = json.loads(Path(args.variant_config).read_text(encoding="utf-8"))
            if not isinstance(variant_cfg, dict):
                raise CompareConfigError("--variant-config deve ser um objeto JSON")
        base_url, requested = require_live_variant_config(
            variant_id=args.variant,
            settings=settings,
            variant_settings=variant_cfg,
        )
        live_settings = settings.model_copy(
            update={
                "ai_runtime_enabled": True,
                "ai_runtime_base_url": base_url,
                "ai_runtime_model": None if requested == UNKNOWN else requested,
            }
        )
        from app.clients.ai_runtime import AiRuntimeClient

        async with AiRuntimeClient(live_settings) as client:
            results, manifest, _ = await run_understanding_variant(
                variant_id=args.variant,
                pairs=pairs,
                client=client,
                settings=live_settings,
                mode="live",
                requested_model=None if requested == UNKNOWN else requested,
                runtime_base_url=base_url,
            )
    else:
        if not args.synthetic:
            raise CompareConfigError(
                "modo offline exige --synthetic PATH (mapa case_id → resposta). "
                "Sem --live, nenhuma conexão de rede é aberta."
            )
        synth_map = load_synthetic_map(Path(args.synthetic))
        responses = []
        for case, _ in pairs:
            case_id = str(case["case_id"])
            if case_id not in synth_map:
                raise CompareConfigError(f"resposta sintética ausente para {case_id}")
            responses.append(synth_map[case_id])
        client = RecordingClient(responses)
        results, manifest, recorder = await run_understanding_variant(
            variant_id=args.variant,
            pairs=pairs,
            client=client,
            settings=settings,
            mode="offline",
            requested_model="synthetic",
            runtime_base_url="offline://synthetic",
        )
        assert recorder is not None
        if recorder.network_calls != 0:
            raise RuntimeError("offline abriu rede - abortando")
        manifest["context_fingerprints"] = context_fingerprint_from_calls(recorder.calls)
        notes = list(manifest.get("notes") or [])
        notes.append("Execucao offline com respostas sinteticas. Nao mede fidelidade do modelo.")
        manifest["notes"] = notes

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out) if args.out else (ARTIFACTS / f"{stamp}_compare_{args.variant}")
    _write_variant_artifacts(results=results, manifest=manifest, out_dir=out)
    print(out)
    return 0


def _load_results(artifact_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    results_path = artifact_dir / "results.json"
    manifest_path = artifact_dir / "manifest.json"
    if not results_path.is_file():
        raise CompareConfigError(f"results.json ausente em {artifact_dir}")
    if not manifest_path.is_file():
        raise CompareConfigError(
            f"manifest.json ausente em {artifact_dir} — artefato incompatível "
            "com understanding_compare.v1"
        )
    blob = json.loads(results_path.read_text(encoding="utf-8"))
    manifest = load_manifest(manifest_path)
    rows = blob.get("results") if isinstance(blob, dict) else None
    if not isinstance(rows, list):
        raise CompareConfigError(f"results inválidos em {artifact_dir}")
    return manifest, rows


def _cmd_diff(args: argparse.Namespace) -> int:
    baseline_dir = Path(args.baseline)
    candidate_dir = Path(args.candidate)
    b_manifest, b_rows = _load_results(baseline_dir)
    c_manifest, c_rows = _load_results(candidate_dir)
    ok, reasons = compare_manifests_compatible(b_manifest, c_manifest)
    paired = paired_comparison(b_rows, c_rows)
    report = render_paired_report(
        paired=paired,
        compatibility_ok=ok,
        compatibility_reasons=reasons,
        baseline_id=str(b_manifest.get("variant_id") or baseline_dir.name),
        candidate_id=str(c_manifest.get("variant_id") or candidate_dir.name),
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out) if args.out else (ARTIFACTS / f"{stamp}_compare_diff")
    out.mkdir(parents=True, exist_ok=True)
    (out / "paired.json").write_text(
        json.dumps(
            {
                "compatible": ok,
                "compatibility_reasons": reasons,
                "baseline_manifest": b_manifest,
                "candidate_manifest": c_manifest,
                "paired": paired,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (out / "report.md").write_text(report, encoding="utf-8")
    print(out)
    if not ok:
        print("INCOMPATÍVEL: " + "; ".join(reasons), file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Comparação controlada da etapa de understanding (AdvCRM Bot)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Executa uma variante (understanding-only)")
    run_p.add_argument("--variant", required=True, help="Identificador: baseline|candidate|…")
    run_p.add_argument(
        "--live",
        action="store_true",
        help="Inferência real (explícito). Sem isto, offline apenas.",
    )
    run_p.add_argument(
        "--synthetic",
        help="JSON case_id→resposta para offline (obrigatório sem --live)",
    )
    run_p.add_argument(
        "--variant-config",
        help="JSON com base_url (obrigatório no --live) e requested_model opcional",
    )
    run_p.add_argument("--cases-dir", default=None)
    run_p.add_argument("--expected-dir", default=None)
    run_p.add_argument("--case-ids", nargs="*", default=None)
    run_p.add_argument("--out", default=None)

    diff_p = sub.add_parser("diff", help="Compara artefatos de duas variantes")
    diff_p.add_argument("--baseline", required=True, help="Diretório do artefato baseline")
    diff_p.add_argument("--candidate", required=True, help="Diretório do artefato candidate")
    diff_p.add_argument("--out", default=None)

    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return asyncio.run(_cmd_run(args))
        if args.command == "diff":
            return _cmd_diff(args)
    except CompareConfigError as exc:
        print(f"erro de configuração: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
