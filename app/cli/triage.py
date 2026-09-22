"""CLI de triagem — offline por padrão; --live exige AI_RUNTIME_ENABLED."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from app.application.context_builder import (
    build_next_step_context,
    build_understanding_context,
)
from app.application.services import (
    LeadUnderstandingService,
    TriageNextStepService,
    TriagePipelineService,
)
from app.clients.ai_runtime import AiRuntimeClient
from app.clients.errors import AiRuntimeError, AiRuntimeSchemaValidationError
from app.clients.schemas import (
    SCHEMA_NAME_LEAD,
    SCHEMA_NAME_NEXT_STEP,
    get_lead_understanding_schema,
    get_triage_next_step_schema,
)
from app.config import clear_settings_cache, get_settings
from app.playbooks import resolve_playbook
from app.prompts import load_lead_understanding_prompt, load_triage_next_step_prompt
from app.schemas.inbound import TriageAnalysisRequest
from app.schemas.proposal import NextStepOnlyEnvelope, ProposalStatus, SafeFallback
from app.taxonomy import get_taxonomy

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_FAIL_CLOSED = 3


def _load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("JSON de entrada deve ser objeto")
    return data


def _extract_request(data: dict[str, Any]) -> TriageAnalysisRequest:
    if "request" in data:
        return TriageAnalysisRequest.model_validate(data["request"])
    return TriageAnalysisRequest.model_validate(data)


def _write_output(path: Path | None, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path is None:
        # Evitar imprimir segredos; payload já sanitizado
        sys.stdout.write(text)
    else:
        path.write_text(text, encoding="utf-8")


def _failure_payload(
    *,
    stage: str,
    exc: AiRuntimeError,
    event_id: str | None = None,
) -> dict[str, Any]:
    details = getattr(exc, "details", None)
    error: dict[str, Any] = {
        "category": exc.category,
        "message": exc.message,
        "stage": stage,
    }
    if isinstance(details, list):
        error["details"] = details
    if exc.http_status is not None:
        error["http_status"] = exc.http_status
    if exc.request_id is not None:
        error["request_id"] = exc.request_id
    if exc.retry_count is not None:
        error["retry_count"] = exc.retry_count
    if exc.latency_ms is not None:
        error["latency_ms"] = exc.latency_ms
    # rejeitado/payload bruto nunca no dump operacional
    payload: dict[str, Any] = {
        "status": ProposalStatus.FAILED_CLOSED.value,
        "lead_understanding": None,
        "triage_next_step": None,
        "safe_fallback": SafeFallback(
            reason_category=exc.category,
            message=exc.message,
        ).model_dump(mode="json"),
        "error": error,
    }
    if event_id is not None:
        payload["event_id"] = event_id
    return payload


def _print_runtime_failure(exc: AiRuntimeError) -> None:
    print(f"Falha: {type(exc).__name__} category={exc.category}", file=sys.stderr)
    details = getattr(exc, "details", None)
    if isinstance(details, list) and details:
        print("Diagnóstico sanitizado (loc/type):", file=sys.stderr)
        for item in details:
            loc = item.get("loc", [])
            err_type = item.get("type", "validation_error")
            print(f"  - loc={loc} type={err_type}", file=sys.stderr)


def _offline_understanding(request: TriageAnalysisRequest) -> dict[str, Any]:
    prompt = load_lead_understanding_prompt()
    schema = get_lead_understanding_schema()
    client = AiRuntimeClient(get_settings())
    context = build_understanding_context(request)
    messages = [
        {"role": "system", "content": prompt.text},
        {"role": "user", "content": context},
    ]
    payload = client.build_structured_payload(json_schema=schema, messages=messages)

    return {
        "mode": "offline",
        "stage": "understanding",
        "event_id": request.event_id,
        "prompt_version": prompt.version,
        "prompt_hash": prompt.sha256,
        "schema_name": SCHEMA_NAME_LEAD,
        "message_count": len(request.messages),
        "endpoint": get_settings().ai_runtime_structured_generation_path,
        "taxonomy_version": get_taxonomy().taxonomy_version,
        "payload": {
            "messages": [
                {"role": m["role"], "content_chars": len(m["content"])} for m in payload["messages"]
            ],
            "max_tokens": payload["max_tokens"],
            "temperature": payload["temperature"],
            "schema_keys": sorted(payload["schema"].keys()),
            "has_model": "model" in payload,
            "has_response_format": "response_format" in payload,
            "user_content_has_taxonomy_catalog": "taxonomy_catalog"
            in payload["messages"][1]["content"],
        },
    }


def _offline_next_step(envelope: NextStepOnlyEnvelope) -> dict[str, Any]:
    prompt = load_triage_next_step_prompt()
    schema = get_triage_next_step_schema()
    playbook = resolve_playbook(
        envelope.lead_understanding.primary_area,
        envelope.lead_understanding.subject,
    )
    client = AiRuntimeClient(get_settings())
    context = build_next_step_context(
        request=envelope.request,
        understanding=envelope.lead_understanding,
        playbook_id=playbook.id if playbook else "unknown",
        playbook_version=playbook.version if playbook else "unknown",
        questions_asked=envelope.questions_asked,
        last_bot_question=envelope.last_bot_question,
        missing_information=envelope.missing_information,
    )
    messages = [
        {"role": "system", "content": prompt.text},
        {"role": "user", "content": context},
    ]
    payload = client.build_structured_payload(json_schema=schema, messages=messages)
    return {
        "mode": "offline",
        "stage": "next_step",
        "event_id": envelope.request.event_id,
        "prompt_version": prompt.version,
        "prompt_hash": prompt.sha256,
        "playbook_id": playbook.id if playbook else None,
        "schema_name": SCHEMA_NAME_NEXT_STEP,
        "endpoint": get_settings().ai_runtime_structured_generation_path,
        "payload": {
            "messages": [
                {"role": m["role"], "content_chars": len(m["content"])} for m in payload["messages"]
            ],
            "max_tokens": payload["max_tokens"],
            "temperature": payload["temperature"],
            "schema_keys": sorted(payload["schema"].keys()),
            "has_model": "model" in payload,
            "has_response_format": "response_format" in payload,
        },
    }


async def _run_live(args: argparse.Namespace) -> int:
    clear_settings_cache()
    settings = get_settings()
    if not settings.ai_runtime_enabled:
        print("AI_RUNTIME_ENABLED=false — use configuração adequada.", file=sys.stderr)
        return EXIT_CONFIG
    if not settings.ai_runtime_organization_id:
        print(
            "AI_RUNTIME_ORGANIZATION_ID obrigatório para --live "
            "(UUID confiável; não use tenant_id sem mapeamento).",
            file=sys.stderr,
        )
        return EXIT_CONFIG

    data = _load_json(Path(args.input))
    out_path = Path(args.output) if args.output else None
    async with AiRuntimeClient(settings) as client:
        if args.understanding_only:
            request = _extract_request(data)
            try:
                understanding, comp, ver, sha = await LeadUnderstandingService(
                    client, settings
                ).run(request)
            except AiRuntimeError as exc:
                _print_runtime_failure(exc)
                _write_output(
                    out_path,
                    _failure_payload(stage="understanding", exc=exc, event_id=request.event_id),
                )
                return EXIT_FAIL_CLOSED
            _write_output(
                out_path,
                {
                    "status": "success",
                    "event_id": request.event_id,
                    "lead_understanding": understanding.model_dump(mode="json"),
                    "metadata": {
                        "model": comp.model,
                        "prompt_version": ver,
                        "prompt_hash": sha,
                        "latency_ms": comp.latency_ms,
                        "retry_count": comp.retry_count,
                    },
                },
            )
            return EXIT_OK

        if args.next_step_only:
            envelope = NextStepOnlyEnvelope.model_validate(data)
            try:
                next_step, comp, playbook, ver, sha = await TriageNextStepService(
                    client, settings
                ).run(
                    request=envelope.request,
                    understanding=envelope.lead_understanding,
                    questions_asked=envelope.questions_asked,
                    last_bot_question=envelope.last_bot_question,
                    missing_information=envelope.missing_information,
                )
            except AiRuntimeError as exc:
                _print_runtime_failure(exc)
                _write_output(
                    out_path,
                    _failure_payload(
                        stage="next_step",
                        exc=exc,
                        event_id=envelope.request.event_id,
                    ),
                )
                return EXIT_FAIL_CLOSED
            _write_output(
                out_path,
                {
                    "status": "success",
                    "event_id": envelope.request.event_id,
                    "triage_next_step": next_step.model_dump(mode="json"),
                    "metadata": {
                        "model": comp.model,
                        "prompt_version": ver,
                        "prompt_hash": sha,
                        "playbook_id": playbook.id,
                        "latency_ms": comp.latency_ms,
                        "retry_count": comp.retry_count,
                    },
                },
            )
            return EXIT_OK

        request = _extract_request(data)
        proposal = await TriagePipelineService(client, settings).run(request)
        _write_output(out_path, proposal.model_dump(mode="json"))
        if proposal.status == ProposalStatus.FAILED_CLOSED:
            if proposal.error is not None and proposal.error.details:
                print(
                    f"Falha: AiRuntimeError category={proposal.error.category}",
                    file=sys.stderr,
                )
                print("Diagnóstico sanitizado (loc/type):", file=sys.stderr)
                for item in proposal.error.details:
                    print(
                        f"  - loc={item.get('loc', [])} type={item.get('type')}",
                        file=sys.stderr,
                    )
            return EXIT_FAIL_CLOSED
        if proposal.status == ProposalStatus.DEGRADED_SAFETY:
            # Understanding inválido; eventual handoff vem só do extrator.
            return EXIT_FAIL_CLOSED
        return EXIT_OK


def _run_offline(args: argparse.Namespace) -> int:
    data = _load_json(Path(args.input))
    if args.understanding_only:
        request = _extract_request(data)
        out = _offline_understanding(request)
    elif args.next_step_only:
        # Envelope próprio
        if "lead_understanding" not in data or "request" not in data:
            # permitir understanding embutido em exemplos
            if "lead_understanding" in data and "request" in data:
                pass
            else:
                print(
                    "--next-step-only exige envelope {request, lead_understanding, ...}",
                    file=sys.stderr,
                )
                return EXIT_CONFIG
        envelope = NextStepOnlyEnvelope.model_validate(
            {
                "request": data["request"],
                "lead_understanding": data["lead_understanding"],
                "questions_asked": data.get("questions_asked", 0),
                "last_bot_question": data.get("last_bot_question"),
                "missing_information": data.get("missing_information", []),
            }
        )
        out = _offline_next_step(envelope)
    else:
        request = _extract_request(data)
        understanding_payload = _offline_understanding(request)
        # Sem understanding real offline: montar payload da 1ª etapa apenas
        out = {
            "mode": "offline",
            "stages": ["understanding", "next_step"],
            "understanding_payload_summary": {
                k: understanding_payload[k]
                for k in (
                    "event_id",
                    "prompt_version",
                    "prompt_hash",
                    "schema_name",
                    "message_count",
                )
            },
            "note": (
                "Offline não chama o runtime. Use --live com AI_RUNTIME_ENABLED=true "
                "para inferência. Para next-step offline, use --next-step-only "
                "com envelope contendo lead_understanding."
            ),
        }
    _write_output(Path(args.output) if args.output else None, out)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AdvCRM Bot — CLI de triagem")
    parser.add_argument("--input", required=True, help="JSON de entrada")
    parser.add_argument("--output", default=None, help="Arquivo de saída JSON")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Chama o runtime (exige AI_RUNTIME_ENABLED=true)",
    )
    parser.add_argument(
        "--understanding-only",
        action="store_true",
        help="Somente compreensão do lead",
    )
    parser.add_argument(
        "--next-step-only",
        action="store_true",
        help="Somente próximo passo (envelope próprio)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.understanding_only and args.next_step_only:
        print(
            "--understanding-only e --next-step-only são mutuamente exclusivos",
            file=sys.stderr,
        )
        return EXIT_CONFIG
    try:
        if args.live:
            return asyncio.run(_run_live(args))
        return _run_offline(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Erro de configuração/entrada: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except AiRuntimeSchemaValidationError as exc:
        _print_runtime_failure(exc)
        return EXIT_FAIL_CLOSED
    except AiRuntimeError as exc:
        _print_runtime_failure(exc)
        return EXIT_FAIL_CLOSED
    except Exception as exc:
        # Não vazar stack com dados sensíveis
        print(f"Falha: {type(exc).__name__}", file=sys.stderr)
        return EXIT_CONFIG


if __name__ == "__main__":
    raise SystemExit(main())
