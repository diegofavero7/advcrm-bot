"""Taxonomia tipada de erros do runtime AI — sem texto de conversa."""

from __future__ import annotations


class AiRuntimeError(Exception):
    """Erro base do cliente AdvCRM AI."""

    category: str = "runtime_error"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class AiRuntimeConnectionError(AiRuntimeError):
    category = "connection"


class AiRuntimeTimeoutError(AiRuntimeError):
    category = "timeout"


class AiRuntimeAuthenticationError(AiRuntimeError):
    category = "authentication"


class AiRuntimeRateLimitError(AiRuntimeError):
    category = "rate_limit"

    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(message)


class AiRuntimeServerError(AiRuntimeError):
    category = "server"


class AiRuntimeProtocolError(AiRuntimeError):
    category = "protocol"


class AiRuntimeInvalidJsonError(AiRuntimeError):
    category = "invalid_json"


class AiRuntimeSchemaValidationError(AiRuntimeError):
    category = "schema_validation"


class AiRuntimeContractVersionError(AiRuntimeError):
    category = "contract_version"


class AiRuntimeSemanticValidationError(AiRuntimeError):
    category = "semantic_validation"


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
