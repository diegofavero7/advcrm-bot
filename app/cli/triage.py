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
from app.schemas.proposal import NextStepOnlyEnvelope, ProposalStatus

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


def _offline_understanding(request: TriageAnalysisRequest) -> dict[str, Any]:
    prompt = load_lead_understanding_prompt()
    schema = get_lead_understanding_schema()
    client = AiRuntimeClient(get_settings())
    context = build_understanding_context(request)
    messages = [
        {"role": "system", "content": prompt.text},
        {"role": "user", "content": context},
    ]
    payload = client.build_chat_payload(
        schema_name=SCHEMA_NAME_LEAD,
        json_schema=schema,
        messages=messages,
        allow_missing_model=True,
    )
    # Remover schema enorme do dump offline resumido? manter para debug controlado
    return {
        "mode": "offline",
        "stage": "understanding",
        "event_id": request.event_id,
        "prompt_version": prompt.version,
        "prompt_hash": prompt.sha256,
        "schema_name": SCHEMA_NAME_LEAD,
        "message_count": len(request.messages),
        "payload_model": payload.get("model"),
        "response_format": payload["response_format"],
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
    payload = client.build_chat_payload(
        schema_name=SCHEMA_NAME_NEXT_STEP,
        json_schema=schema,
        messages=messages,
        allow_missing_model=True,
    )
    return {
        "mode": "offline",
        "stage": "next_step",
        "event_id": envelope.request.event_id,
        "prompt_version": prompt.version,
        "prompt_hash": prompt.sha256,
        "playbook_id": playbook.id if playbook else None,
        "schema_name": SCHEMA_NAME_NEXT_STEP,
        "payload_model": payload.get("model"),
        "response_format": payload["response_format"],
    }


async def _run_live(args: argparse.Namespace) -> int:
    clear_settings_cache()
    settings = get_settings()
    if not settings.ai_runtime_enabled:
        print("AI_RUNTIME_ENABLED=false — use configuração adequada.", file=sys.stderr)
        return EXIT_CONFIG
    if not settings.ai_runtime_model:
        print("AI_RUNTIME_MODEL obrigatório para --live.", file=sys.stderr)
        return EXIT_CONFIG

    data = _load_json(Path(args.input))
    async with AiRuntimeClient(settings) as client:
        if args.understanding_only:
            request = _extract_request(data)
            understanding, comp, ver, sha = await LeadUnderstandingService(client, settings).run(
                request
            )
            _write_output(
                Path(args.output) if args.output else None,
                {
                    "status": "success",
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
            next_step, comp, playbook, ver, sha = await TriageNextStepService(client, settings).run(
                request=envelope.request,
                understanding=envelope.lead_understanding,
                questions_asked=envelope.questions_asked,
                last_bot_question=envelope.last_bot_question,
                missing_information=envelope.missing_information,
            )
            _write_output(
                Path(args.output) if args.output else None,
                {
                    "status": "success",
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
        _write_output(
            Path(args.output) if args.output else None,
            proposal.model_dump(mode="json"),
        )
        if proposal.status == ProposalStatus.FAILED_CLOSED:
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
    except Exception as exc:
        # Não vazar stack com dados sensíveis
        print(f"Falha: {type(exc).__name__}", file=sys.stderr)
        return EXIT_CONFIG


if __name__ == "__main__":
    raise SystemExit(main())
