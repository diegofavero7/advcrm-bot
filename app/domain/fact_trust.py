"""Proveniência de confiança CRM em ``case_facts`` — o modelo não cria autoridade.

``CaseFact.from_trusted_crm_context=true`` declara que o fato veio do contexto CRM
confiável do request (``known_facts``). Essa declaração só é aceita quando há suporte
verificável: mesma chave e valor equivalente em ``request.known_facts``.

Mensagens do lead, ``source_message_ids`` e a mera existência da chave com outro valor
**não** concedem confiança de CRM. Sem suporte, a saída é rejeitada fail-closed — o
flag não é rebaixado nem o fato apagado para passar a validação.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.domain.fact_state import fact_values_equivalent

# Código estável no diagnóstico sanitizado (loc + type).
UNVERIFIED_TRUSTED_CRM_CONTEXT = "unverified_trusted_crm_context"


def _known_supports(key: str, value: str, known_facts: Sequence[object]) -> bool:
    for known in known_facts:
        known_key = str(getattr(known, "key", ""))
        if known_key != key:
            continue
        known_value = str(getattr(known, "value", ""))
        if fact_values_equivalent(key, value, known_value):
            return True
    return False


def trusted_crm_origin_violations(
    case_facts: Sequence[object],
    known_facts: Sequence[object],
) -> list[dict[str, Any]]:
    """Detalhes sanitizados (``loc`` + ``type``) para fatos com CRM não suportado.

    Só inspeciona fatos com ``from_trusted_crm_context=true``. Fatos do lead
    (``from_trusted_crm_context=false`` + ``source_message_ids``) não passam por esta
    regra. A lista ``known_facts`` do request é a única fonte confiável admitida.
    """
    details: list[dict[str, Any]] = []
    for index, fact in enumerate(case_facts):
        if not bool(getattr(fact, "from_trusted_crm_context", False)):
            continue
        key = str(getattr(fact, "key", ""))
        value = str(getattr(fact, "value", ""))
        if _known_supports(key, value, known_facts):
            continue
        details.append(
            {
                "loc": ["case_facts", index, "from_trusted_crm_context"],
                "type": UNVERIFIED_TRUSTED_CRM_CONTEXT,
            }
        )
    return details
