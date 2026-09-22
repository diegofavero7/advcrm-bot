"""Promoção segura de riscos taxonômicos por evidência estruturada explícita.

Subject válido no catálogo NÃO basta. Exige fato com certainty=explicit e
source_message_ids válidos. Sem matching textual nas mensagens.

Chave com valores divergentes é estado NÃO RESOLVIDO (`fact_state.v2`) e não sustenta
promoção: fato conflitante não é evidência inequívoca.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.domain.enums import FactCertainty, RiskFlag
from app.domain.fact_state import fact_refs_from_facts, resolve_fact_state
from app.domain.fact_vocabulary import (
    DETAINEE_IMPRISONED,
    LEGAL_REQUEST_SUBJECT,
    MINOR_CHILD_DEPENDENT,
    PRISON_CIRCUMSTANCES,
    UNAUTHORIZED_LOAN,
    VIOLENCE_TYPE,
    is_truthy_fact_value,
)
from app.schemas.lead_understanding import CaseFact


class PromotionReason(StrEnum):
    EXTRACTOR_CONFIRMED = "extractor_confirmed"
    EXPLICIT_STRUCTURED_EVIDENCE = "explicit_structured_evidence"


@dataclass(frozen=True, slots=True)
class ExplicitFactRequirement:
    """Um fato canônico que sustenta promoção taxonômica."""

    key: str
    # None = qualquer valor não vazio; senão o value deve estar no conjunto.
    allowed_values: frozenset[str] | None = None


@dataclass(frozen=True, slots=True)
class TaxonomyPromotionRule:
    subject: str
    risks: frozenset[RiskFlag]
    # Qualquer requisito satisfeito basta (OR).
    evidence: tuple[ExplicitFactRequirement, ...]


# Regras mínimas — chaves do vocabulário canônico (app.domain.fact_vocabulary).
TAXONOMY_PROMOTION_RULES: tuple[TaxonomyPromotionRule, ...] = (
    TaxonomyPromotionRule(
        subject="banking_fraud",
        risks=frozenset({RiskFlag.FRAUD_OR_SCAM}),
        evidence=(
            ExplicitFactRequirement(key=UNAUTHORIZED_LOAN),
            ExplicitFactRequirement(
                key=LEGAL_REQUEST_SUBJECT,
                allowed_values=frozenset({"banking_fraud"}),
            ),
        ),
    ),
    TaxonomyPromotionRule(
        subject="flagrant_arrest",
        risks=frozenset({RiskFlag.ARREST_OR_DETENTION}),
        evidence=(
            ExplicitFactRequirement(key=PRISON_CIRCUMSTANCES),
            ExplicitFactRequirement(
                key=LEGAL_REQUEST_SUBJECT,
                allowed_values=frozenset({"flagrant_arrest"}),
            ),
        ),
    ),
    TaxonomyPromotionRule(
        subject="child_custody",
        risks=frozenset({RiskFlag.CHILD_OR_VULNERABLE_PERSON}),
        evidence=(
            ExplicitFactRequirement(
                key=LEGAL_REQUEST_SUBJECT,
                allowed_values=frozenset({"child_custody"}),
            ),
            ExplicitFactRequirement(key=MINOR_CHILD_DEPENDENT),
        ),
    ),
    TaxonomyPromotionRule(
        subject="domestic_violence",
        risks=frozenset({RiskFlag.DOMESTIC_VIOLENCE, RiskFlag.VIOLENCE_OR_THREAT}),
        evidence=(
            ExplicitFactRequirement(key=VIOLENCE_TYPE),
            ExplicitFactRequirement(
                key=LEGAL_REQUEST_SUBJECT,
                allowed_values=frozenset({"domestic_violence"}),
            ),
        ),
    ),
    # prison_allowance: só fato inequívoco de prisão (não prison_date / inferred).
    TaxonomyPromotionRule(
        subject="prison_allowance",
        risks=frozenset({RiskFlag.ARREST_OR_DETENTION}),
        evidence=(
            ExplicitFactRequirement(
                key=DETAINEE_IMPRISONED,
                allowed_values=frozenset({"true", "yes", "sim", "1"}),
            ),
        ),
    ),
)

_RULES_BY_SUBJECT: dict[str, TaxonomyPromotionRule] = {
    rule.subject: rule for rule in TAXONOMY_PROMOTION_RULES
}


def _fact_matches_requirement(
    fact: CaseFact,
    requirement: ExplicitFactRequirement,
    *,
    allowed_message_ids: frozenset[str],
) -> bool:
    if fact.certainty != FactCertainty.EXPLICIT:
        return False
    if not fact.source_message_ids:
        return False
    if any(mid not in allowed_message_ids for mid in fact.source_message_ids):
        return False
    if fact.key != requirement.key:
        return False
    if requirement.allowed_values is None:
        return bool(fact.value.strip())
    normalized = {v.lower() for v in requirement.allowed_values}
    value = fact.value.strip().lower()
    if value in normalized:
        return True
    # Truthy canônico quando a regra lista true/yes/sim/1.
    if normalized <= {"true", "yes", "sim", "1"}:
        return is_truthy_fact_value(fact.value)
    return False


def find_supporting_fact(
    *,
    subject: str,
    risk: RiskFlag,
    case_facts: list[CaseFact],
    allowed_message_ids: frozenset[str],
) -> CaseFact | None:
    """Retorna o primeiro fato explícito que sustenta promoção do risco para o subject.

    Chaves não resolvidas (valores divergentes na mesma chave) são descartadas: sem
    evidência contratual de correção, o consumidor não elege um dos valores.
    """
    rule = _RULES_BY_SUBJECT.get(subject)
    if rule is None or risk not in rule.risks:
        return None
    unresolved = set(resolve_fact_state(fact_refs_from_facts(case_facts)).unresolved_keys())
    for requirement in rule.evidence:
        if requirement.key in unresolved:
            continue
        for fact in case_facts:
            if _fact_matches_requirement(
                fact, requirement, allowed_message_ids=allowed_message_ids
            ):
                return fact
    return None


def risks_promotable_by_explicit_evidence(
    *,
    subject: str,
    taxonomy_derived: list[RiskFlag] | tuple[RiskFlag, ...],
    case_facts: list[CaseFact],
    allowed_message_ids: frozenset[str],
) -> dict[RiskFlag, CaseFact]:
    """Mapa risco → fato sustentador para riscos taxonômicos com evidência explícita."""
    tax_set = set(taxonomy_derived)
    promoted: dict[RiskFlag, CaseFact] = {}
    rule = _RULES_BY_SUBJECT.get(subject)
    if rule is None:
        return promoted
    for risk in rule.risks:
        if risk not in tax_set:
            continue
        fact = find_supporting_fact(
            subject=subject,
            risk=risk,
            case_facts=case_facts,
            allowed_message_ids=allowed_message_ids,
        )
        if fact is not None:
            promoted[risk] = fact
    return promoted
