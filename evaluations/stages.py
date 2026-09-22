"""Etapas observáveis da avaliação live — sem inferir sucesso a partir do status."""

from __future__ import annotations

from typing import Any

NOT_OBSERVED = "not_observed"
PASSED = "passed"
FAILED = "failed"

STAGE_KEYS: tuple[str, ...] = (
    "http_transport",
    "json_parse",
    "api_envelope",
    "contract_structural",
    "semantic_validation",
    "policy_application",
)

_HTTP_FAIL = frozenset(
    {"connection", "timeout", "server", "rate_limit", "authentication", "disabled"}
)
_JSON_FAIL = frozenset({"invalid_json"})
_ENVELOPE_FAIL = frozenset({"protocol", "truncated", "refusal"})
_STRUCTURAL_FAIL = frozenset({"schema_validation", "structured_validation"})
_SEMANTIC_FAIL = frozenset({"semantic_validation", "contract_version", "context_limit"})
_POLICY_FAIL = frozenset({"policy_error"})


def empty_stages() -> dict[str, str]:
    return {key: NOT_OBSERVED for key in STAGE_KEYS}


def stages_for_success(*, policy_applied: bool = True) -> dict[str, str]:
    """Sucesso ponta a ponta incluindo aplicação de política quando alcançada."""
    stages = {key: PASSED for key in STAGE_KEYS}
    if not policy_applied:
        stages["policy_application"] = NOT_OBSERVED
    return stages


def stages_for_failure(category: str | None, *, failure_stage: str | None) -> dict[str, str]:
    stages = empty_stages()
    if not category:
        return stages

    if category in _HTTP_FAIL:
        stages["http_transport"] = FAILED
        return stages

    stages["http_transport"] = PASSED

    if category in _JSON_FAIL:
        stages["json_parse"] = FAILED
        return stages

    stages["json_parse"] = PASSED

    if category in _ENVELOPE_FAIL:
        stages["api_envelope"] = FAILED
        return stages

    stages["api_envelope"] = PASSED

    if category in _STRUCTURAL_FAIL:
        stages["contract_structural"] = FAILED
        return stages

    stages["contract_structural"] = PASSED

    if category in _SEMANTIC_FAIL:
        stages["semantic_validation"] = FAILED
        return stages

    stages["semantic_validation"] = PASSED

    if category in _POLICY_FAIL or failure_stage in {"policy", "next_step"}:
        stages["policy_application"] = FAILED
    return stages


def stages_for_understanding_success() -> dict[str, str]:
    """Understanding válido: política/next-step/extrator permanecem not_observed."""
    stages = {key: PASSED for key in STAGE_KEYS}
    stages["policy_application"] = NOT_OBSERVED
    return stages


def stages_for_understanding_failure(
    category: str | None, *, failure_stage: str | None = None
) -> dict[str, str]:
    """Falha na etapa de understanding — política nunca é alcançada."""
    stages = stages_for_failure(category, failure_stage=failure_stage)
    stages["policy_application"] = NOT_OBSERVED
    return stages


def stages_for_degraded_safety(category: str | None) -> dict[str, str]:
    """Understanding rejeitado + safety válido + política degradada aplicada.

    Preserva a falha estrutural/semântica do understanding; marca política como
    aplicada (``degraded_safety``). Não inventa sucesso de contrato.
    """
    stages = stages_for_failure(category, failure_stage="understanding")
    # Política degradada alcançada após safety válido.
    if category in _STRUCTURAL_FAIL | _SEMANTIC_FAIL:
        stages["policy_application"] = PASSED
    elif category in _POLICY_FAIL:
        stages["policy_application"] = FAILED
    else:
        # Categorias que não deveriam entrar no caminho degradado.
        stages["policy_application"] = NOT_OBSERVED
    return stages


def stages_for_offline_request(request_schema_ok: bool) -> dict[str, str]:
    stages = empty_stages()
    stages["contract_structural"] = PASSED if request_schema_ok else FAILED
    return stages


def map_category_to_failed_stage(category: str | None) -> str | None:
    if category is None:
        return None
    if category in _HTTP_FAIL:
        return "http_transport"
    if category in _JSON_FAIL:
        return "json_parse"
    if category in _ENVELOPE_FAIL:
        return "api_envelope"
    if category in _STRUCTURAL_FAIL:
        return "contract_structural"
    if category in _SEMANTIC_FAIL:
        return "semantic_validation"
    if category in _POLICY_FAIL:
        return "policy_application"
    return None


def summarize_stage_counts(results: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {
        key: {PASSED: 0, FAILED: 0, NOT_OBSERVED: 0} for key in STAGE_KEYS
    }
    for row in results:
        stages = row.get("stages") or {}
        for key in STAGE_KEYS:
            status = stages.get(key, NOT_OBSERVED)
            bucket = out[key]
            if status in bucket:
                bucket[status] += 1
            else:
                bucket[NOT_OBSERVED] += 1
    return out
