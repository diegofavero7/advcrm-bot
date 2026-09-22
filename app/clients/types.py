"""Resultado estruturado de uma completion do runtime AI."""

from __future__ import annotations

from typing import Any

from app.domain.models import StrictModel


class TokenUsage(StrictModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class StructuredCompletionResult(StrictModel):
    """Retorno tipado do Protocol — nunca resposta parcial."""

    parsed_content: dict[str, Any]
    model: str
    finish_reason: str
    latency_ms: float
    retry_count: int
    request_id: str | None
    usage: TokenUsage | None
