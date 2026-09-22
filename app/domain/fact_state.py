"""Estado factual em ``case_facts`` / ``known_facts`` — resolvido vs NÃO resolvido.

O contrato ``lead_understanding.v1`` transporta ``case_facts`` como lista plana. Os
campos disponíveis por fato são ``key``, ``value``, ``certainty``,
``source_message_ids`` e ``from_trusted_crm_context``. **Nenhum** deles representa
correção, nem identifica o alvo factual (pessoa, período, evento).

Por isso este módulo NÃO decide vencedor entre valores divergentes.

## O que o contrato não prova

``ConversationMessage.reply_to_message_id`` é ponteiro de **contexto de resposta**. Uma
resposta pode corrigir, complementar, mudar de assunto ou falar de outra pessoa — o
contrato não distingue esses casos. Encadeamento, recência, ``role`` igual e chave igual,
isolados ou combinados, não são evidência de substituição. A versão anterior deste módulo
(``fact_state.v1``) tratava "mesma chave + ancestralidade + mesmo role" como correção
explícita; isso era inferência disfarçada de regra determinística e foi removido.

## Regra de resolução (``fact_state.v2``)

Para cada chave, considerando os valores **distintos** presentes:

- exatamente um valor distinto → chave **resolvida** com esse valor;
- dois ou mais valores distintos → chave **não resolvida**; todos os fatos são
  preservados e nenhum é escolhido. Consumidores de valor único devem tratar a chave
  como ausente de autorização, não como "o valor mais recente".

Duplicatas não geram conflito: valores iguais (após normalização) são o mesmo valor. Para
chaves booleanas canônicas a comparação usa o booleano **parseado** — ``"true"`` e
``"sim"`` são o mesmo valor; ``"true"`` e ``"false"`` são conflito.

Substituição determinística só seria aplicável se o contrato representasse
explicitamente a correção **e** o mesmo alvo factual. Ver ``CONTRACT_GAP`` e
``docs/contracts.md``. Até lá não há caminho de supersessão, e não se recorre a matching
textual das mensagens para adivinhar correção.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

FACT_STATE_VERSION = "fact_state.v2"

CONTRACT_GAP = (
    "lead_understanding.v1 não representa correção de fato nem identidade do alvo "
    "factual (pessoa/período/evento). Resolver substituição exigiria campos novos "
    "(ex.: referência ao fato corrigido + identificador de alvo), o que é mudança de "
    "contrato externo e está fora desta revisão. Até então, valores divergentes para a "
    "mesma chave permanecem NÃO RESOLVIDOS."
)


@dataclass(frozen=True, slots=True)
class FactRef:
    """Projeção mínima de um fato, comum a ``CaseFact``, ``KnownFact`` e JSON."""

    index: int
    key: str
    value: str
    source_message_ids: tuple[str, ...] = ()
    from_trusted_crm_context: bool = False


@dataclass(frozen=True, slots=True)
class FactConflict:
    """Chave com valores divergentes e sem evidência contratual para resolver."""

    key: str
    values: tuple[str, ...]
    fact_indexes: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class FactStateResolution:
    """Fatos preservados + chaves resolvidas + chaves não resolvidas.

    Nada é removido, reordenado ou eleito. ``resolved`` contém apenas as chaves com um
    único valor distinto.
    """

    facts: tuple[FactRef, ...]
    resolved: tuple[tuple[str, str], ...]
    conflicts: tuple[FactConflict, ...]

    def resolved_value(self, key: str) -> str | None:
        """Valor único da chave, ou ``None`` se ausente **ou** não resolvida."""
        for candidate, value in self.resolved:
            if candidate == key:
                return value
        return None

    def is_unresolved(self, key: str) -> bool:
        return any(conflict.key == key for conflict in self.conflicts)

    def unresolved_keys(self) -> tuple[str, ...]:
        return tuple(conflict.key for conflict in self.conflicts)

    def values_for(self, key: str) -> tuple[str, ...]:
        """Todos os valores registrados para a chave, na ordem original."""
        return tuple(fact.value for fact in self.facts if fact.key == key)


def _comparison_token(key: str, value: str) -> str:
    """Identidade de valor para detectar divergência.

    Booleanos canônicos comparam pelo booleano parseado (domínio fechado do
    vocabulário), não pelo literal: ``"sim"`` e ``"true"`` são o mesmo valor. Demais
    chaves comparam pelo texto normalizado. Não há sinonímia livre nem interpretação
    semântica do conteúdo.
    """
    from app.domain.fact_vocabulary import is_canonical_boolean_key, parse_canonical_boolean

    if is_canonical_boolean_key(key):
        parsed = parse_canonical_boolean(value)
        if parsed is not None:
            return f"bool:{parsed}"
        # Fora do domínio: o schema já rejeita: aqui não se normaliza para sucesso.
        return f"invalid:{value.strip().lower()}"
    return value.strip().lower()


def fact_values_equivalent(key: str, left: str, right: str) -> bool:
    """True quando ``left`` e ``right`` são o mesmo valor sob a normalização v2.

    Reusa o token de ``fact_state`` / domínio booleano canônico. Não introduz
    sinonímia livre.
    """
    return _comparison_token(key, left) == _comparison_token(key, right)


def resolve_fact_state(facts: Sequence[FactRef]) -> FactStateResolution:
    """Separa chaves resolvidas de chaves não resolvidas, sem eleger vencedor.

    Opera sobre **uma** lista homogênea (``case_facts`` do modelo ou ``known_facts`` do
    CRM). A precedência entre CRM e declaração do lead é decisão do consumidor, não
    deste módulo.
    """
    ordered_keys: list[str] = []
    by_key: dict[str, list[FactRef]] = {}
    for fact in facts:
        if fact.key not in by_key:
            by_key[fact.key] = []
            ordered_keys.append(fact.key)
        by_key[fact.key].append(fact)

    resolved: list[tuple[str, str]] = []
    conflicts: list[FactConflict] = []
    for key in ordered_keys:
        entries = by_key[key]
        tokens: dict[str, str] = {}
        for entry in entries:
            tokens.setdefault(_comparison_token(key, entry.value), entry.value)
        if len(tokens) == 1:
            resolved.append((key, next(iter(tokens.values()))))
            continue
        conflicts.append(
            FactConflict(
                key=key,
                values=tuple(tokens.values()),
                fact_indexes=tuple(entry.index for entry in entries),
            )
        )

    return FactStateResolution(
        facts=tuple(facts),
        resolved=tuple(resolved),
        conflicts=tuple(conflicts),
    )


def fact_refs_from_facts(facts: Sequence[object]) -> tuple[FactRef, ...]:
    """Adapta ``CaseFact`` / ``KnownFact`` (pydantic) sem acoplar ao schema."""
    refs: list[FactRef] = []
    for index, fact in enumerate(facts):
        refs.append(
            FactRef(
                index=index,
                key=str(getattr(fact, "key", "")),
                value=str(getattr(fact, "value", "")),
                source_message_ids=tuple(getattr(fact, "source_message_ids", ()) or ()),
                from_trusted_crm_context=bool(getattr(fact, "from_trusted_crm_context", False)),
            )
        )
    return tuple(refs)


def fact_refs_from_dicts(facts: Sequence[dict[str, object]]) -> tuple[FactRef, ...]:
    """Mesma resolução a partir de artefatos JSON (avaliação/reanálise)."""
    refs: list[FactRef] = []
    for index, fact in enumerate(facts):
        if not isinstance(fact, dict):
            continue
        raw_ids = fact.get("source_message_ids") or []
        ids = tuple(str(mid) for mid in raw_ids) if isinstance(raw_ids, list) else ()
        refs.append(
            FactRef(
                index=index,
                key=str(fact.get("key", "")),
                value=str(fact.get("value", "")),
                source_message_ids=ids,
                from_trusted_crm_context=bool(fact.get("from_trusted_crm_context", False)),
            )
        )
    return tuple(refs)
