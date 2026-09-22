"""Erros tipados do runtime AI — telemetria sem bodies/credenciais/conversa."""

from __future__ import annotations

from typing import Any


class AiRuntimeError(Exception):
    """Erro base do cliente AdvCRM AI."""

    category: str = "runtime_error"

    def __init__(
        self,
        message: str,
        *,
        latency_ms: float | None = None,
        retry_count: int | None = None,
        http_status: int | None = None,
        request_id: str | None = None,
        model: str | None = None,
        prompt_version: str | None = None,
        prompt_hash: str | None = None,
    ) -> None:
        self.message = message
        self.latency_ms = latency_ms
        self.retry_count = retry_count
        self.http_status = http_status
        self.request_id = request_id
        self.model = model
        self.prompt_version = prompt_version
        self.prompt_hash = prompt_hash
        super().__init__(message)


class AiRuntimeConnectionError(AiRuntimeError):
    category = "connection"


class AiRuntimeTimeoutError(AiRuntimeError):
    category = "timeout"


class AiRuntimeAuthenticationError(AiRuntimeError):
    category = "authentication"


class AiRuntimeRateLimitError(AiRuntimeError):
    category = "rate_limit"

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float | None = None,
        latency_ms: float | None = None,
        retry_count: int | None = None,
        http_status: int | None = None,
        request_id: str | None = None,
        model: str | None = None,
        prompt_version: str | None = None,
        prompt_hash: str | None = None,
    ) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            message,
            latency_ms=latency_ms,
            retry_count=retry_count,
            http_status=http_status,
            request_id=request_id,
            model=model,
            prompt_version=prompt_version,
            prompt_hash=prompt_hash,
        )


class AiRuntimeServerError(AiRuntimeError):
    category = "server"


class AiRuntimeProtocolError(AiRuntimeError):
    category = "protocol"


class AiRuntimeInvalidJsonError(AiRuntimeError):
    category = "invalid_json"


class AiRuntimeSchemaValidationError(AiRuntimeError):
    """Falha em model_validate após resposta estruturada do runtime.

    ``details`` contém apenas ``loc`` e ``type`` sanitizados.
    ``rejected_payload`` é opcional para artefatos sintéticos locais — não logar.
    """

    category = "schema_validation"

    def __init__(
        self,
        message: str,
        *,
        details: list[dict[str, Any]] | None = None,
        rejected_payload: dict[str, Any] | None = None,
        latency_ms: float | None = None,
        retry_count: int | None = None,
        http_status: int | None = None,
        request_id: str | None = None,
        model: str | None = None,
        prompt_version: str | None = None,
        prompt_hash: str | None = None,
    ) -> None:
        self.details: list[dict[str, Any]] = list(details or [])
        self.rejected_payload = rejected_payload
        super().__init__(
            message,
            latency_ms=latency_ms,
            retry_count=retry_count,
            http_status=http_status,
            request_id=request_id,
            model=model,
            prompt_version=prompt_version,
            prompt_hash=prompt_hash,
        )


class AiRuntimeStructuredValidationError(AiRuntimeError):
    """HTTP 502 com code=structured_validation_failed (AdvCRM AI).

    Fail-closed sem retry de transporte. ``details`` só com identificadores
    sanitizados (code/validator/paths) — sem valores de instância nem detail livre.
    """

    category = "structured_validation"

    def __init__(
        self,
        message: str = "Saída estruturada rejeitada pelo runtime (schema)",
        *,
        details: list[dict[str, Any]] | None = None,
        latency_ms: float | None = None,
        retry_count: int | None = None,
        http_status: int | None = None,
        request_id: str | None = None,
        model: str | None = None,
        prompt_version: str | None = None,
        prompt_hash: str | None = None,
    ) -> None:
        self.details: list[dict[str, Any]] = list(details or [])
        super().__init__(
            message,
            latency_ms=latency_ms,
            retry_count=retry_count,
            http_status=http_status,
            request_id=request_id,
            model=model,
            prompt_version=prompt_version,
            prompt_hash=prompt_hash,
        )


class AiRuntimeContractVersionError(AiRuntimeError):
    category = "contract_version"


class AiRuntimeSemanticValidationError(AiRuntimeError):
    """Falha de coerência semântica (taxonomia/ambiguity/etc.), inclusive pós-model_validate."""

    category = "semantic_validation"

    def __init__(
        self,
        message: str,
        *,
        details: list[dict[str, Any]] | None = None,
        rejected_payload: dict[str, Any] | None = None,
        latency_ms: float | None = None,
        retry_count: int | None = None,
        http_status: int | None = None,
        request_id: str | None = None,
        model: str | None = None,
        prompt_version: str | None = None,
        prompt_hash: str | None = None,
    ) -> None:
        self.details: list[dict[str, Any]] = list(details or [])
        self.rejected_payload = rejected_payload
        super().__init__(
            message,
            latency_ms=latency_ms,
            retry_count=retry_count,
            http_status=http_status,
            request_id=request_id,
            model=model,
            prompt_version=prompt_version,
            prompt_hash=prompt_hash,
        )


class AiRuntimePolicyError(AiRuntimeError):
    """Falha ao aplicar política determinística — fail-closed."""

    category = "policy_error"


class AiRuntimeTruncatedError(AiRuntimeError):
    """finish_reason=length — resposta truncada; nunca validar parcial."""

    category = "truncated"


class AiRuntimeRefusalError(AiRuntimeError):
    """finish_reason=refusal — falha controlada."""

    category = "refusal"


class AiRuntimeDisabledError(AiRuntimeError):
    category = "disabled"


class ContextLimitExceededError(AiRuntimeError):
    category = "context_limit"
