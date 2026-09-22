"""Reanálise de artefatos live antigos — sem inventar dados ausentes."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from evaluations.metrics import NOT_APPLICABLE, NOT_OBSERVED, compute_metrics, render_report
from evaluations.runner import score_case
from evaluations.stages import stages_for_degraded_safety, stages_for_failure, stages_for_success

ROOT = Path(__file__).resolve().parents[1]

POLICY_AUDIT = """## Auditoria de políticas (casos pedidos)

Fontes: results.json original + expected/cases. Propostas completas **indisponíveis**
no artefato antigo (não foram salvas).

### existing_status
- Expected: intent `existing_client_case_status`, handoff_required=true, subjects=[].
- Resultado live: intent ok, subject_match=true (bug: lista vazia contava como acerto),
  action=`ask_question`, handoff_match=false.
- Política `decide_conservative_action` **existe** e, com esse intent, forçaria
  `human_handoff` — mas **não é executada** no pipeline live (só o LLM decide next_step).
  Gate `must_block_question_when_handoff_required` só age se o modelo já marcar handoff.
- Conclusão: regra presente no código, **não aplicada** no caminho live; não depende
  de sinal omitido do entendimento (intent estava correto).

### urgency_deadline
- Expected: handoff + risk `imminent_deadline`.
- Resultado: risks_match=false, handoff=false (`ask_question`), urgency/risk omitidos.
- `decide_conservative_action` só handoff se `urgency==immediate` ou `recommend_handoff`.
- Conclusão: mesmo se a política rodasse, faltaram sinais no understanding; e a política
  determinística **não roda** no pipeline. Não alterar regras só para o expected.

### crim_flagrant
- Expected: subject `flagrant_arrest`, handoff, risk `arrest_or_detention`.
- Resultado: subject/area ok, risks_match=false, action=`route_lead`, handoff=false.
- Não há regra específica “flagrante → handoff”; depende de urgency/recommend_handoff.
- Conclusão: sinais de risco omitidos pelo entendimento; política específica ausente;
  conservative action não aplicada. Não mudar negócio nesta tarefa.

### ss_prison_allowance
- Expected: social_security/prison_allowance, fact relationship, risk arrest; handoff null.
- Resultado: área/assunto ok, intent predito `spam` (erro de modelo), risks_match=false,
  required_facts_match=true no artefato antigo.
- Conclusão: falha de fidelidade do entendimento (intent/risco), não ausência de política
  de handoff (não exigido).
"""


def _re_score_legacy_row(row: dict[str, Any], expected: dict[str, Any] | None) -> dict[str, Any]:
    """Reconstrói score com regras novas a partir do que o artefato antigo tem."""
    exp = expected or {
        "case_id": row.get("case_id"),
        "acceptable_intents": [],
        "acceptable_primary_areas": [],
        "acceptable_subjects": [],
        "handoff_required": row.get("expected_handoff"),
        "required_fact_keys": [],
        "required_risks": row.get("required_risks") or [],
        "forbidden_fact_keys": ["win_probability", "eligibility_confirmed"],
    }

    understanding = None
    if row.get("predicted_intent") or row.get("predicted_area") or row.get("predicted_subject"):
        understanding = {
            "intent": row.get("predicted_intent"),
            "primary_area": row.get("predicted_area"),
            "subject": row.get("predicted_subject"),
            "case_facts": [],  # chaves de fatos indisponíveis no artefato antigo
            "safety": {"detected_risks": []},
        }
        # required_facts_match antigo True sem facts → marcar limitação
    next_step = None
    if row.get("predicted_action") is not None:
        next_step = {
            "action": row.get("predicted_action"),
            "requires_human_handoff": None,  # indisponível
            "proposed_question": None,
        }

    fail_closed = bool(row.get("fail_closed"))
    if fail_closed:
        # Categoria/etapa indisponíveis no results antigo.
        stages = stages_for_failure(None, failure_stage=None)
        # Heurística mínima a partir de evidências externas (logs): não inventar category.
        availability_category = "unavailable"
    else:
        stages = stages_for_success()
        availability_category = "recovered_as_success_path"

    rescored = score_case(
        expected=exp,
        understanding=understanding,
        next_step=next_step,
        fail_closed=fail_closed,
        latency_ms=row.get("latency_ms"),
        retry_count=int(row.get("retry_count") or 0),
        stages=stages,
        pipeline_status="failed_closed" if fail_closed else "success",
        error=None,
        safe_fallback=None,
        metadata=None,
        proposal=None,
    )
    rescored["legacy"] = {
        "subject_match_legacy": row.get("subject_match"),
        "json_valid_legacy": row.get("json_valid"),
        "schema_valid_legacy": row.get("schema_valid"),
        "invented_facts_legacy": row.get("invented_facts"),
        "retry_count_legacy": row.get("retry_count"),
        "latency_ms_legacy": row.get("latency_ms"),
    }
    rescored["data_availability"] = {
        "proposal": "unavailable",
        "error_category": availability_category if fail_closed else "n/a",
        "error_details_loc_type": "unavailable",
        "retry_count_on_failure": "unavailable_in_artifact",
        "latency_on_failure": (
            "unavailable_in_artifact"
            if fail_closed and row.get("latency_ms") is None
            else "present"
        ),
        "case_fact_keys": "unavailable",
        "detected_risks": "unavailable",
        "requires_human_handoff": "unavailable",
        "note": (
            "Reanálise usa apenas campos do results.json legado. "
            "Não reconstrói payloads descartados. "
            "Logs operacionais (retries=2 em server) não foram mesclados automaticamente."
        ),
    }
    # required_facts no legado podia ser True sem facts —
    # forçar not_observed se sem understanding facts
    if understanding is not None and not (exp.get("required_fact_keys") or []):
        rescored["required_facts_match"] = NOT_APPLICABLE
    elif understanding is not None and (exp.get("required_fact_keys") or []):
        rescored["required_facts_match"] = NOT_OBSERVED
        rescored["data_availability"]["required_facts_match"] = (
            "unavailable_cannot_verify_without_case_facts"
        )
    if understanding is not None and (exp.get("required_risks") or []):
        rescored["risks_match"] = NOT_OBSERVED
        rescored["data_availability"]["risks_match"] = (
            "unavailable_cannot_verify_without_detected_risks"
        )
    return rescored


def _re_score_from_case_artifact(
    case_payload: dict[str, Any],
    expected: dict[str, Any],
) -> dict[str, Any]:
    """Reanalisa com proposal/error do artefato moderno — sem nova inferência."""
    proposal = case_payload.get("proposal")
    understanding = None
    next_step = None
    provenance = None
    meta = case_payload.get("metadata")
    error = case_payload.get("error")
    safe_fallback = None
    pipeline_status = "failed_closed"
    if isinstance(proposal, dict):
        understanding = proposal.get("lead_understanding")
        next_step = proposal.get("triage_next_step")
        provenance = proposal.get("decision_provenance")
        safe_fallback = proposal.get("safe_fallback")
        pipeline_status = str(proposal.get("status") or pipeline_status)
        if meta is None:
            meta = proposal.get("metadata")
        if error is None:
            error = proposal.get("error")
    fail_closed = pipeline_status == "failed_closed"
    if pipeline_status == "degraded_safety":
        stages = stages_for_degraded_safety(
            error.get("category") if isinstance(error, dict) else None
        )
    elif fail_closed:
        stages = stages_for_failure(
            error.get("category") if isinstance(error, dict) else None,
            failure_stage=error.get("stage") if isinstance(error, dict) else None,
        )
    else:
        stages = stages_for_success()

    rescored = score_case(
        expected=expected,
        understanding=understanding if isinstance(understanding, dict) else None,
        next_step=next_step if isinstance(next_step, dict) else None,
        fail_closed=fail_closed,
        latency_ms=None,
        retry_count=0,
        stages=stages,
        pipeline_status=pipeline_status,
        error=error if isinstance(error, dict) else None,
        safe_fallback=safe_fallback if isinstance(safe_fallback, dict) else None,
        metadata=meta if isinstance(meta, dict) else None,
        proposal=proposal if isinstance(proposal, dict) else None,
        decision_provenance=provenance if isinstance(provenance, dict) else None,
        request_snapshot=case_payload.get("request_snapshot"),
        case_hash=case_payload.get("case_hash"),
    )
    # Latência/retries a partir do metadata do artefato quando disponível.
    if isinstance(meta, dict):
        total = 0.0
        observed = False
        for key in (
            "understanding_latency_ms",
            "safety_signals_latency_ms",
            "next_step_latency_ms",
        ):
            if meta.get(key) is not None:
                total += float(meta[key])
                observed = True
        if observed:
            rescored["latency_ms"] = total
        retries = 0
        for key in (
            "understanding_retry_count",
            "safety_signals_retry_count",
            "next_step_retry_count",
        ):
            if meta.get(key) is not None:
                retries += int(meta[key])
        rescored["retry_count"] = retries
    rescored["data_availability"] = {
        "proposal": "present" if proposal is not None else "unavailable",
        "source": "case_artifact",
        "original_preserved": True,
    }
    return rescored


def reanalyze(source_dir: Path, out_dir: Path) -> None:
    payload = json.loads((source_dir / "results.json").read_text(encoding="utf-8"))
    expected_dir = ROOT / "evaluations" / "expected"
    cases_dir = source_dir / "cases"
    rescored: list[dict[str, Any]] = []
    limitations: list[str] = []

    for row in payload.get("results") or []:
        case_id = row.get("case_id")
        expected = None
        expected_path = expected_dir / f"{case_id}.json"
        if expected_path.is_file():
            expected = json.loads(expected_path.read_text(encoding="utf-8"))
        else:
            limitations.append(f"expected ausente para {case_id}")

        case_path = cases_dir / f"{case_id}.json"
        if case_path.is_file() and expected is not None:
            case_payload = json.loads(case_path.read_text(encoding="utf-8"))
            rescored.append(_re_score_from_case_artifact(case_payload, expected))
        else:
            if not case_path.is_file():
                limitations.append(f"proposal completa indisponível para {case_id} (sem cases/)")
            rescored.append(_re_score_legacy_row(row, expected))

    # Contagens do artefato legado (evidência bruta)
    legacy_results = payload.get("results") or []
    subject_true_no_pred = [
        r["case_id"]
        for r in legacy_results
        if r.get("subject_match") and not r.get("predicted_subject")
    ]
    retries_report = (payload.get("metrics") or {}).get("retries_total")
    latency_null_failures = sum(
        1 for r in legacy_results if r.get("fail_closed") and r.get("latency_ms") is None
    )

    metrics = compute_metrics(rescored, mode="live")
    # Marcadores de indisponibilidade no report
    metrics["retries_total_note"] = (
        f"legado reportou retries_total={retries_report}; "
        "falhas sem retry_count no artefato — valor reanalisado não recupera retries dos logs"
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    report = render_report(metrics, {}, mode="live")
    extra = [
        "",
        "# Reanálise (separada do relatório original)",
        "",
        f"Fonte: `{source_dir}`",
        "",
        "## Limitações de dados",
        "- Propostas completas: **indisponíveis** (não estavam no results.json).",
        "- Detalhes sanitizados loc/type: **indisponíveis**.",
        "- category/stage/http_status/request_id por falha: **indisponíveis** no artefato.",
        (
            f"- Falhas com latency_ms=null no legado: {latency_null_failures} "
            "(não recuperável sem telemetria)."
        ),
        (
            f"- subject_match=true sem predicted_subject no legado: "
            f"{len(subject_true_no_pred)} → {subject_true_no_pred}"
        ),
        f"- Retries: {metrics['retries_total_note']}",
        "",
        "## Correções de medição aplicadas na reanálise",
        "- Expectativa de subject vazia → not_applicable (não acerto).",
        "- Ausência de predicted_* → not_observed para matches.",
        "- forbidden_facts sem saída → not_observed (não 0% de alucinação).",
        "- Handoff: sem next_step → not_observed (safe_fallback interno ≠ atendimento).",
        "",
        POLICY_AUDIT,
        "",
        "## Contagem legado (bruta)",
        f"- total: {len(legacy_results)}",
        f"- fail_closed: {sum(1 for r in legacy_results if r.get('fail_closed'))}",
        f"- json_valid legado true: {sum(1 for r in legacy_results if r.get('json_valid'))}",
        "",
    ]
    if limitations:
        extra.append("## Outras limitações")
        extra.extend(f"- {item}" for item in limitations)
        extra.append("")

    (out_dir / "reanalysis.md").write_text(report + "\n".join(extra), encoding="utf-8")
    (out_dir / "reanalysis.json").write_text(
        json.dumps(
            {
                "source": str(source_dir),
                "original_preserved": True,
                "metrics_recomputed": metrics,
                "results_rescored": rescored,
                "legacy_anomaly_counts": {
                    "subject_match_without_predicted_subject": len(subject_true_no_pred),
                    "fail_closed_latency_null": latency_null_failures,
                    "retries_total_legacy": retries_report,
                },
                "data_availability_summary": dict(
                    Counter(
                        avail
                        for row in rescored
                        for avail in (
                            row.get("data_availability") or {"proposal": "unavailable"}
                        ).values()
                        if isinstance(avail, str)
                    )
                ),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reanálise de artefato live legado")
    parser.add_argument(
        "--source",
        type=Path,
        default=ROOT / "artifacts/evaluations/20260921T150823Z_live",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Diretório de saída (padrão: <source>_reanalysis)",
    )
    args = parser.parse_args(argv)
    source = args.source
    out = args.out or Path(str(source) + "_reanalysis_v5")
    if not (source / "results.json").is_file():
        raise SystemExit(f"results.json ausente em {source}")
    reanalyze(source, out)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
