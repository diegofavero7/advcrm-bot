"""Pacote do cliente AdvCRM AI."""

from app.clients.ai_runtime import AiRuntimeClient
from app.clients.errors import AiRuntimeError
from app.clients.types import StructuredCompletionResult

__all__ = ["AiRuntimeClient", "AiRuntimeError", "StructuredCompletionResult"]
