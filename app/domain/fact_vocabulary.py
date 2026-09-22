"""Vocabulário canônico de case_facts / known_facts consumidos operacionalmente.

Chaves abertas no contrato v1 — este módulo documenta os nomes estáveis
usados por política, promoção e avaliação. Não fecha o schema em enum.
Não acumular aliases: o modelo deve emitir exatamente estas chaves.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- Chaves canônicas ---

EXPLICIT_HUMAN_REQUEST = "explicit_human_request"
RELATIONSHIP_TO_DETAINEE = "relationship_to_detainee"
DETAINEE_IMPRISONED = "detainee_imprisoned"
LEGAL_REQUEST_SUBJECT = "legal_request_subject"
UNAUTHORIZED_LOAN = "unauthorized_loan"
PRISON_CIRCUMSTANCES = "prison_circumstances"
VIOLENCE_TYPE = "violence_type"
MINOR_CHILD_DEPENDENT = "minor_child_dependent"

# Booleans canônicos são transportados como string (case_facts.value: str no v1).
# Domínio fechado e documentado: qualquer outro literal é inválido, não "falso".
TRUTHY_VALUES: frozenset[str] = frozenset({"true", "1", "yes", "sim"})
FALSY_VALUES: frozenset[str] = frozenset({"false", "0", "no", "nao", "não"})
BOOLEAN_FACT_VALUES: frozenset[str] = TRUTHY_VALUES | FALSY_VALUES

# Chaves cujo value é estritamente booleano no vocabulário canônico.
BOOLEAN_FACT_KEYS: frozenset[str] = frozenset(
    {
        EXPLICIT_HUMAN_REQUEST,
        DETAINEE_IMPRISONED,
    }
)

# Marcadores técnicos de pipeline — nunca são o valor de um fato.
PIPELINE_MARKER_VALUES: frozenset[str] = frozenset(
    {
        "inferred_from_context",
        "unknown_value",
        "n/a",
        "na",
        "lead_untrusted_data",
    }
)

# Ausência de informação — em case_facts.value equivale a omitir o fato.
# Restrito a case_facts: enums do contrato (documents_availability, participants.role,
# procedural_situation.stage) continuam aceitando "unknown" legitimamente.
ABSENCE_MARKER_VALUES: frozenset[str] = frozenset(
    {
        "unknown",
        "undefined",
        "unspecified",
        "not_specified",
        "not_informed",
        "desconhecido",
        "desconhecida",
        "nao_informado",
        "não informado",
        "nao informado",
        "null",
        "none",
    }
)

# Placeholders proibidos em case_facts.value (não são fatos).
TECHNICAL_PLACEHOLDER_VALUES: frozenset[str] = PIPELINE_MARKER_VALUES | ABSENCE_MARKER_VALUES


@dataclass(frozen=True, slots=True)
class CanonicalFactSpec:
    key: str
    meaning: str
    consumers: tuple[str, ...]
    notes: str = ""


CANONICAL_FACTS: tuple[CanonicalFactSpec, ...] = (
    CanonicalFactSpec(
        key=EXPLICIT_HUMAN_REQUEST,
        meaning="Lead pediu explicitamente atendente/humano (não genérico 'advogado').",
        consumers=("policy:explicit_human_or_existing_client",),
        notes="Booleano canônico (true|1|yes|sim / false|0|no|nao|não); outro literal é "
        "inválido. understanding: certainty=explicit + source_message_ids de declaração "
        "do lead. known_facts CRM: from_trusted_crm_context=true.",
    ),
    CanonicalFactSpec(
        key=RELATIONSHIP_TO_DETAINEE,
        meaning="Relação do lead com a pessoa presa (ex.: cônjuge).",
        consumers=("evaluation:ss_prison_allowance", "playbooks"),
    ),
    CanonicalFactSpec(
        key=DETAINEE_IMPRISONED,
        meaning="Pessoa referida está presa/detida (fato inequívoco).",
        consumers=("safety.promotion:prison_allowance",),
        notes="Booleano canônico; certainty=explicit.",
    ),
    CanonicalFactSpec(
        key=LEGAL_REQUEST_SUBJECT,
        meaning="Assunto jurídico solicitado, espelhando subject do catálogo.",
        consumers=("safety.promotion",),
        notes="Não usar chave legal_request. Valor = subject id exato.",
    ),
    CanonicalFactSpec(
        key=UNAUTHORIZED_LOAN,
        meaning="Empréstimo/contratação não autorizada sustentada.",
        consumers=("safety.promotion:banking_fraud",),
    ),
    CanonicalFactSpec(
        key=PRISON_CIRCUMSTANCES,
        meaning="Circunstância da prisão (ex.: flagrante).",
        consumers=("safety.promotion:flagrant_arrest",),
    ),
    CanonicalFactSpec(
        key=VIOLENCE_TYPE,
        meaning="Tipo/contexto de violência informado.",
        consumers=("safety.promotion:domestic_violence",),
    ),
    CanonicalFactSpec(
        key=MINOR_CHILD_DEPENDENT,
        meaning="Criança/menor diretamente envolvida ou dependente.",
        consumers=("safety.promotion:child_custody", "evaluation"),
    ),
)


def is_truthy_fact_value(value: str) -> bool:
    return value.strip().lower() in TRUTHY_VALUES


def is_technical_placeholder_value(value: str) -> bool:
    return value.strip().lower() in TECHNICAL_PLACEHOLDER_VALUES


def parse_canonical_boolean(value: str) -> bool | None:
    """Converte o literal string do contrato v1 em booleano canônico.

    ``None`` = fora do domínio documentado. Nunca usar truthiness genérica:
    ``"false"`` é falso e texto livre é inválido, não verdadeiro.
    """
    normalized = value.strip().lower()
    if normalized in TRUTHY_VALUES:
        return True
    if normalized in FALSY_VALUES:
        return False
    return None


def is_canonical_boolean_key(key: str) -> bool:
    return key in BOOLEAN_FACT_KEYS


def canonical_fact_value_out_of_domain(key: str, value: str) -> bool:
    """True quando a chave canônica exige domínio fechado e o valor não pertence a ele.

    Valor inválido é rejeitado com código estável; nunca renomeado nem convertido.
    """
    if not is_canonical_boolean_key(key):
        return False
    return parse_canonical_boolean(value) is None


def canonical_boolean_is_true(key: str, value: str) -> bool:
    """Consumo operacional: só valor canônico válido e verdadeiro."""
    if not is_canonical_boolean_key(key):
        return False
    return parse_canonical_boolean(value) is True
