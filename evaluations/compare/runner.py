"""Runner da comparação: somente understanding.

Não chama extrator de safety, política nem next-step.
Offline usa cliente sintético; live exige ``--live`` explícito e configuração completa.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evaluations.compare.manifest import (
    UNKNOWN,
    VariantIdentity,
    build_shared_fingerprint,
    new_run_manifest,
)
from evaluations.runner import case_request_hash, score_case
from evaluations.stages import (
    stages_for_understanding_failure,
    stages_for_understanding_success,
)

ROOT = Path(__file__).resolve().parents[2]
COMPARE_CASES = Path(__file__).resolve().parent / "cases"
COMPARE_EXPECTED = Path(__file__).resolve().parent / "expected"
RESERVED_SUITE = "understanding_compare_reserved.v1"
SCHEMA_PATH = ROOT / "artifacts" / "schemas" / "lead_understanding.v1.json"
TAXONOMY_PATH = ROOT / "app" / "taxonomy" / "data" / "legal_subjects.v1.yaml"


class CompareConfigError(ValueError):
    """Configuração incompleta ou ambígua — sem fallback silencioso."""


@dataclass(frozen=True, slots=True)
class SyntheticResponse:
    """Resposta canned para offline: payload de understanding ou erro forçado."""

    parsed_content: dict[str, Any] | None = None
    error_category: str | None = None
    error_message: str | None = None
    model: str = "synthetic-model"
    latency_ms: float = 1.0


class RecordingClient:
    """Cliente falso que grava o contexto enviado e devolve respostas sintéticas.

    Não abre conexão de rede. Levanta se a fila de respostas acabar.
    """

    def __init__(self, responses: Sequence[SyntheticResponse | Exception]) -> None:
        self._queue = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.network_calls = 0

    async def complete_structured(
        self,
        *,
        schema_name: str,
        json_schema: dict[str, object],
        messages: list[dict[str, str]],
        contract_label: str,
        organization_id: str | None = None,
    ) -> Any:
        from app.clients.types import StructuredCompletionResult

        self.calls.append(
            {
                "schema_name": schema_name,
                "contract_label": contract_label,
                "messages": list(messages),
                "organization_id": organization_id,
                "schema_keys": sorted(json_schema.keys()) if isinstance(json_schema, dict) else [],
            }
        )
        if not self._queue:
            raise RuntimeError("RecordingClient: respostas sintéticas esgotadas")
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        if item.error_category:
            from app.clients.errors import (
                AiRuntimeConnectionError,
                AiRuntimeSchemaValidationError,
                AiRuntimeSemanticValidationError,
            )

            if item.error_category == "semantic_validation":
                raise AiRuntimeSemanticValidationError(
                    item.error_message or "synthetic semantic failure",
                    details=[{"loc": ["synthetic"], "type": "synthetic_error"}],
                    rejected_payload=item.parsed_content,
                    latency_ms=item.latency_ms,
                    retry_count=0,
                    request_id="synthetic",
                    model=item.model,
                )
            if item.error_category == "schema_validation":
                raise AiRuntimeSchemaValidationError(
                    item.error_message or "synthetic schema failure",
                    details=[{"loc": ["synthetic"], "type": "synthetic_schema"}],
                    rejected_payload=item.parsed_content,
                    latency_ms=item.latency_ms,
                    retry_count=0,
                    request_id="synthetic",
                    model=item.model,
                )
            raise AiRuntimeConnectionError(
                item.error_message or "synthetic failure",
                latency_ms=item.latency_ms,
                retry_count=0,
                request_id="synthetic",
                model=item.model,
            )
        if item.parsed_content is None:
            raise RuntimeError("SyntheticResponse sem parsed_content nem error_category")
        return StructuredCompletionResult(
            parsed_content=item.parsed_content,
            model=item.model,
            finish_reason="stop",
            latency_ms=item.latency_ms,
            retry_count=0,
            request_id="synthetic",
            usage=None,
        )


def load_compare_pairs(
    *,
    cases_dir: Path = COMPARE_CASES,
    expected_dir: Path = COMPARE_EXPECTED,
    case_ids: list[str] | None = None,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Carrega pares (case, expected) do conjunto reservado."""
    if not cases_dir.is_dir() or not expected_dir.is_dir():
        raise CompareConfigError(
            f"conjunto reservado ausente: {cases_dir} / {expected_dir}. "
            "Execute scripts/generate_compare_cases.py"
        )
    ids = sorted(p.stem for p in cases_dir.glob("*.json"))
    if case_ids is not None:
        wanted = list(case_ids)
        missing = [c for c in wanted if c not in ids]
        if missing:
            raise CompareConfigError(f"casos inexistentes no conjunto reservado: {missing}")
        ids = wanted
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for case_id in ids:
        case_path = cases_dir / f"{case_id}.json"
        exp_path = expected_dir / f"{case_id}.json"
        if not exp_path.is_file():
            raise CompareConfigError(f"expected ausente para {case_id}")
        case = json.loads(case_path.read_text(encoding="utf-8"))
        expected = json.loads(exp_path.read_text(encoding="utf-8"))
        if not isinstance(case, dict) or "request" not in case:
            raise CompareConfigError(f"case inválido: {case_id}")
        if not isinstance(expected, dict):
            raise CompareConfigError(f"expected inválido: {case_id}")
        pairs.append((case, expected))
    if not pairs:
        raise CompareConfigError("nenhum caso no conjunto reservado")
    return pairs


def load_synthetic_map(path: Path) -> dict[str, SyntheticResponse]:
    """Mapa case_id → resposta sintética (JSON)."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise CompareConfigError("arquivo sintético deve ser objeto case_id → payload")
    out: dict[str, SyntheticResponse] = {}
    for case_id, entry in raw.items():
        if not isinstance(entry, dict):
            raise CompareConfigError(f"entrada sintética inválida: {case_id}")
        out[str(case_id)] = SyntheticResponse(
            parsed_content=entry.get("lead_understanding"),
            error_category=entry.get("error_category"),
            error_message=entry.get("error_message"),
            model=str(entry.get("model") or "synthetic-model"),
            latency_ms=float(entry.get("latency_ms") or 1.0),
        )
    return out


def _crm_provenance_violated(error: dict[str, Any] | None) -> bool | str:
    from evaluations.metrics import NOT_OBSERVED

    if error is None:
        return False
    details = error.get("details")
    if details is None:
        return NOT_OBSERVED if error else False
    if not isinstance(details, list):
        return NOT_OBSERVED
    for item in details:
        if isinstance(item, dict) and item.get("type") == "unverified_trusted_crm_context":
            return True
    return False


def _error_public(error: dict[str, Any] | None) -> dict[str, Any] | None:
    if not error:
        return None
    return {
        "category": error.get("category"),
        "message": error.get("message"),
        "stage": error.get("stage"),
        "details": error.get("details"),
        "http_status": error.get("http_status"),
    }


async def run_understanding_variant(
    *,
    variant_id: str,
    pairs: Sequence[tuple[dict[str, Any], dict[str, Any]]],
    client: Any,
    settings: Any,
    mode: str,
    requested_model: str | None,
    runtime_base_url: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any], RecordingClient | None]:
    """Executa só LeadUnderstandingService para cada caso (sequencial)."""
    from app.application.services import LeadUnderstandingService
    from app.clients.errors import AiRuntimeError
    from app.clients.schemas import get_lead_understanding_schema
    from app.prompts import load_lead_understanding_prompt
    from app.schemas.inbound import TriageAnalysisRequest
    from app.taxonomy import get_taxonomy

    prompt = load_lead_understanding_prompt()
    taxonomy = get_taxonomy()
    # Schema carregado para garantir artefato presente (fingerprint usa o arquivo).
    if not SCHEMA_PATH.is_file():
        raise CompareConfigError(
            f"schema ausente: {SCHEMA_PATH}. Execute make schemas antes da comparação."
        )
    get_lead_understanding_schema()

    case_ids = [str(case["case_id"]) for case, _ in pairs]
    case_hashes = {str(case["case_id"]): case_request_hash(case["request"]) for case, _ in pairs}
    expectations_versions = {str(exp.get("expectations_version") or UNKNOWN) for _, exp in pairs}
    if len(expectations_versions) != 1:
        raise CompareConfigError(
            f"expectations_version inconsistente no conjunto: {sorted(expectations_versions)}"
        )
    expectations_version = next(iter(expectations_versions))

    shared = build_shared_fingerprint(
        prompt_version=prompt.version,
        prompt_hash=prompt.sha256,
        schema_version="lead_understanding.v1",
        schema_path=SCHEMA_PATH,
        taxonomy_version=taxonomy.taxonomy_version,
        taxonomy_path=TAXONOMY_PATH,
        expectations_version=expectations_version,
        case_ids=case_ids,
        case_hashes=case_hashes,
        temperature=getattr(settings, "ai_runtime_temperature", None),
        max_tokens=getattr(settings, "ai_runtime_max_tokens", None),
        reserved_suite=RESERVED_SUITE,
    )

    recorder = client if isinstance(client, RecordingClient) else None
    service = LeadUnderstandingService(client, settings)
    results: list[dict[str, Any]] = []
    reported_models: list[str] = []

    for case, expected in pairs:
        case_id = str(case["case_id"])
        request = TriageAnalysisRequest.model_validate(case["request"])
        understanding: dict[str, Any] | None = None
        error_raw: dict[str, Any] | None = None
        fail_closed = False
        latency_ms: float | None = None
        retry_count = 0
        reported_model = UNKNOWN
        prompt_version = prompt.version
        prompt_hash = prompt.sha256
        rejected: dict[str, Any] | None = None

        try:
            u_obj, completion, prompt_version, prompt_hash = await service.run(request)
            understanding = u_obj.model_dump(mode="json")
            latency_ms = completion.latency_ms
            retry_count = completion.retry_count
            reported_model = completion.model or UNKNOWN
            stages = stages_for_understanding_success()
            pipeline_status = "understanding_ok"
        except AiRuntimeError as exc:
            fail_closed = True
            latency_ms = exc.latency_ms
            retry_count = int(exc.retry_count or 0)
            reported_model = exc.model or UNKNOWN
            prompt_version = exc.prompt_version or prompt_version
            prompt_hash = exc.prompt_hash or prompt_hash
            rejected = getattr(exc, "rejected_payload", None)
            if isinstance(rejected, dict):
                pass
            else:
                rejected = None
            error_raw = {
                "category": exc.category,
                "message": exc.message,
                "stage": "understanding",
                "details": getattr(exc, "details", None),
                "http_status": getattr(exc, "http_status", None),
            }
            stages = stages_for_understanding_failure(exc.category, failure_stage="understanding")
            pipeline_status = "failed_closed"
        except Exception as exc:
            fail_closed = True
            error_raw = {
                "category": "protocol",
                "message": f"erro inesperado na comparacao: {type(exc).__name__}",
                "stage": "understanding",
                "details": None,
                "http_status": None,
            }
            stages = stages_for_understanding_failure("protocol")
            pipeline_status = "failed_closed"

        if reported_model != UNKNOWN:
            reported_models.append(reported_model)

        row = score_case(
            expected=expected,
            understanding=understanding,
            next_step=None,
            model_next_step=None,
            decision_provenance=None,
            fail_closed=fail_closed,
            latency_ms=latency_ms,
            retry_count=retry_count,
            stages=stages,
            pipeline_status=pipeline_status,
            error=_error_public(error_raw),
            safe_fallback=None,
            metadata={
                "model": reported_model,
                "prompt_version_understanding": prompt_version,
                "prompt_hash_understanding": prompt_hash,
                "schema_version_understanding": "lead_understanding.v1",
                "understanding_latency_ms": latency_ms,
                "understanding_retry_count": retry_count,
                "variant_id": variant_id,
                "compare_kind": "understanding_only",
            },
            proposal=None,
            request_snapshot=case.get("request"),
            case_hash=case_hashes[case_id],
        )
        row["case_id"] = case_id
        row["understanding"] = understanding
        row["crm_provenance_violation"] = _crm_provenance_violated(error_raw)
        row["reported_model"] = reported_model
        row["understanding_present"] = understanding is not None
        if rejected is not None:
            row["rejected_payload"] = rejected
        row["compare_scope"] = "understanding_only"
        results.append(row)

    identity = VariantIdentity(
        variant_id=variant_id,
        requested_model=requested_model or UNKNOWN,
        reported_models=tuple(dict.fromkeys(reported_models)),
        runtime_base_url=runtime_base_url or UNKNOWN,
        checkpoint=UNKNOWN,
        quantization=UNKNOWN,
    )
    manifest = new_run_manifest(
        variant_id=variant_id,
        mode=mode,
        identity=identity,
        shared=shared,
        repo_root=ROOT,
        notes=[
            "Comparação restrita à etapa de understanding.",
            "Extrator de safety, política e next-step não foram chamados.",
            "checkpoint/quantization=unknown: runtime não expõe esses campos no envelope.",
        ],
    )
    return results, manifest.to_dict(), recorder


def require_live_variant_config(
    *,
    variant_id: str,
    settings: Any,
    variant_settings: Mapping[str, Any] | None,
) -> tuple[str, str]:
    """Garante configuração explícita da variante para live — sem fallback silencioso.

    O runtime AdvCRM AI escolhe o modelo carregado no servidor; o bot não envia
    ``model`` no payload. Cada variante live precisa de ``base_url`` distinto
    (ou settings explícitos) e não pode reutilizar silenciosamente o default.
    """
    if variant_settings is None:
        raise CompareConfigError(
            f"variante {variant_id!r}: configuração ausente. "
            "Forneça --variant-config JSON com base_url (e opcionalmente "
            "requested_model apenas como rótulo). O campo model do bot NÃO "
            "seleciona o checkpoint no runtime."
        )
    base_url = variant_settings.get("base_url")
    if not base_url or not isinstance(base_url, str):
        raise CompareConfigError(
            f"variante {variant_id!r}: base_url obrigatório e explícito "
            "(o runtime serve um modelo por endpoint; sem base_url não há "
            "identidade verificável da variante)."
        )
    default_url = getattr(settings, "ai_runtime_base_url", None)
    # Permitir igual ao default apenas se o usuário o escreveu explicitamente
    # no variant-config — já está explícito. Mas exigir enabled.
    if not getattr(settings, "ai_runtime_enabled", False) and not variant_settings.get(
        "force_enabled"
    ):
        raise CompareConfigError(
            "AI_RUNTIME_ENABLED deve ser true para comparação live, "
            "ou force_enabled=true no variant-config (nunca implícito)."
        )
    requested = variant_settings.get("requested_model")
    if requested is not None and not isinstance(requested, str):
        raise CompareConfigError(f"variante {variant_id!r}: requested_model deve ser string")
    _ = default_url  # documentado: não usado como fallback de identidade
    return base_url.rstrip("/"), (requested or UNKNOWN)


def context_fingerprint_from_calls(calls: Sequence[dict[str, Any]]) -> list[str]:
    """Hashes estáveis do conteúdo user enviado — para provar igualdade entre variantes."""
    fingerprints: list[str] = []
    for call in calls:
        messages = call.get("messages") or []
        blob = json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        from evaluations.compare.manifest import content_sha256

        fingerprints.append(content_sha256(blob))
    return fingerprints
