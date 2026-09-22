"""Categorias de falha do understanding que ainda permitem analisar safety.

Somente rejeição de **conteúdo de saída** (estrutural/semântica) após request válido.
``context_limit`` e ``contract_version`` NÃO liberam o extrator: não são rejeição de
payload classificatório e não devem virar segunda chamada disfarçada.

Falhas de transporte/auth/protocolo mantêm o fallback existente sem safety.
"""

from __future__ import annotations

# Continuam para o extrator (uma vez, request autorizado apenas).
UNDERSTANDING_FAILURES_ALLOWING_SAFETY: frozenset[str] = frozenset(
    {
        "schema_validation",
        "structured_validation",
        "semantic_validation",
    }
)

# Abortam o pipeline sem chamar safety.
UNDERSTANDING_FAILURES_BLOCKING_SAFETY: frozenset[str] = frozenset(
    {
        "connection",
        "timeout",
        "authentication",
        "rate_limit",
        "server",
        "protocol",
        "invalid_json",
        "truncated",
        "refusal",
        "disabled",
        "contract_version",
        "context_limit",
    }
)


def allows_safety_after_understanding_failure(category: str | None) -> bool:
    if not category:
        return False
    return category in UNDERSTANDING_FAILURES_ALLOWING_SAFETY
