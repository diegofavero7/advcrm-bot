"""Composição de sinais: candidatos vs operacionais (actionable).

Regra de confiança (fluxo normal — ``effective_safety.v3``):
- Candidatos (auditoria): model_detected, taxonomy_derived, extractor_detected.
- Operacionais (`effective`) quando:
  1. confirmado pelo extrator (riscos afirmados agregados); OU
  2. taxonômico E sustentado por fato estruturado explícito compatível
     (certainty=explicit + source_message_ids válidos).
- Subject válido no catálogo sozinho NÃO promove.
- certainty=inferred NÃO promove.
- model_detected sozinho NÃO promove (inclui imminent_deadline).
- v2: ocorrências negadas/hipotéticas NÃO entram em extractor_detected/effective;
  históricas afirmadas entram no agregado categórico sem implicar urgência.
- Caminho degradado: composição extrator-only (model/taxonomy vazios) — ver
  ``degraded_safety.v2`` / ``ExtractorOnlySafety``.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from app.domain.enums import RiskFlag
from app.domain.models import StrictModel
from app.safety.promotion import (
    PromotionReason,
    risks_promotable_by_explicit_evidence,
)
from app.schemas.lead_understanding import CaseFact
from app.schemas.safety_signals import (
    RiskAssertion,
    SafetyOccurrence,
    SafetySignals,
    SafetySignalsV2,
    aggregate_affirmed_risks,
)

_RISK_ORDER: tuple[RiskFlag, ...] = tuple(RiskFlag)

# v3: agregação categórica a partir de v1 ou v2 (affirmed-only).
COMPOSITION_VERSION = "effective_safety.v3"


class SafetySignalSource(StrEnum):
    MODEL = "model_detected"
    TAXONOMY = "taxonomy_derived"
    EXTRACTOR = "extractor_detected"


class RiskProvenanceEntry(StrictModel):
    """Um risco com origens e, se operacional, motivo de promoção."""

    risk: RiskFlag
    sources: list[SafetySignalSource] = Field(min_length=1)
    evidence_summary: str | None = None
    source_message_ids: list[str] = Field(default_factory=list)
    promotion_reason: PromotionReason | None = None


class EffectiveSafetySignals(StrictModel):
    """Candidatos + sinais operacionais — não altera lead_understanding.safety."""

    model_detected: list[RiskFlag]
    taxonomy_derived: list[RiskFlag]
    extractor_detected: list[RiskFlag]
    candidate: list[RiskProvenanceEntry] = Field(default_factory=list)
    effective: list[RiskProvenanceEntry] = Field(default_factory=list)

    def effective_flags(self) -> frozenset[RiskFlag]:
        return frozenset(entry.risk for entry in self.effective)

    def candidate_flags(self) -> frozenset[RiskFlag]:
        return frozenset(entry.risk for entry in self.candidate)


def _ordered_unique(flags: list[RiskFlag] | tuple[RiskFlag, ...]) -> list[RiskFlag]:
    present = set(flags)
    return [r for r in _RISK_ORDER if r in present]


def _extractor_evidence_by_risk(
    extractor: SafetySignals | SafetySignalsV2 | None,
) -> dict[RiskFlag, tuple[str | None, list[str]]]:
    """Primeira evidência agregada por risco (métricas/auditoria resumida)."""
    out: dict[RiskFlag, tuple[str | None, list[str]]] = {}
    if extractor is None:
        return out
    if isinstance(extractor, SafetySignalsV2):
        for occ in extractor.occurrences:
            if occ.risk in out:
                continue
            if occ.assertion != RiskAssertion.AFFIRMED:
                continue
            out[occ.risk] = (occ.evidence_summary, list(occ.source_message_ids))
        return out
    for item in extractor.detected_risks:
        out.setdefault(item.risk, (item.evidence_summary, list(item.source_message_ids)))
    return out


def compose_effective_safety_signals(
    *,
    model_detected: list[RiskFlag] | tuple[RiskFlag, ...],
    taxonomy_derived: list[RiskFlag] | tuple[RiskFlag, ...],
    extractor: SafetySignals | SafetySignalsV2 | None,
    subject: str | None = None,
    case_facts: list[CaseFact] | None = None,
    allowed_message_ids: frozenset[str] | None = None,
) -> EffectiveSafetySignals:
    """Compõe candidatos e sinais operacionais com proveniência."""
    model_list = _ordered_unique(list(model_detected))
    tax_list = _ordered_unique(list(taxonomy_derived))
    extractor_flags = (
        _ordered_unique(aggregate_affirmed_risks(extractor)) if extractor is not None else []
    )
    evidence_by_risk = _extractor_evidence_by_risk(extractor)

    model_set = set(model_list)
    tax_set = set(tax_list)
    ext_set = set(extractor_flags)

    facts = list(case_facts or [])
    msg_ids = allowed_message_ids if allowed_message_ids is not None else frozenset()
    evidence_promoted: dict[RiskFlag, CaseFact] = {}
    if subject:
        evidence_promoted = risks_promotable_by_explicit_evidence(
            subject=subject,
            taxonomy_derived=tax_list,
            case_facts=facts,
            allowed_message_ids=msg_ids,
        )

    candidate: list[RiskProvenanceEntry] = []
    effective: list[RiskProvenanceEntry] = []
    for risk in _RISK_ORDER:
        sources: list[SafetySignalSource] = []
        if risk in model_set:
            sources.append(SafetySignalSource.MODEL)
        if risk in tax_set:
            sources.append(SafetySignalSource.TAXONOMY)
        if risk in ext_set:
            sources.append(SafetySignalSource.EXTRACTOR)
        if not sources:
            continue

        evidence_summary: str | None = None
        source_message_ids: list[str] = []
        if risk in evidence_by_risk:
            evidence_summary, source_message_ids = evidence_by_risk[risk]
        elif risk in evidence_promoted:
            supporting_fact = evidence_promoted[risk]
            evidence_summary = f"{supporting_fact.key}={supporting_fact.value}"
            source_message_ids = list(supporting_fact.source_message_ids)

        entry = RiskProvenanceEntry(
            risk=risk,
            sources=sources,
            evidence_summary=evidence_summary,
            source_message_ids=source_message_ids,
            promotion_reason=None,
        )
        candidate.append(entry)

        if risk in ext_set:
            effective.append(
                entry.model_copy(update={"promotion_reason": PromotionReason.EXTRACTOR_CONFIRMED})
            )
        elif risk in evidence_promoted and risk in tax_set:
            op_sources: list[SafetySignalSource] = [
                s for s in sources if s != SafetySignalSource.EXTRACTOR
            ]
            if SafetySignalSource.TAXONOMY not in op_sources:
                continue
            effective.append(
                RiskProvenanceEntry(
                    risk=risk,
                    sources=op_sources,
                    evidence_summary=evidence_summary,
                    source_message_ids=source_message_ids,
                    promotion_reason=PromotionReason.EXPLICIT_STRUCTURED_EVIDENCE,
                )
            )

    return EffectiveSafetySignals(
        model_detected=model_list,
        taxonomy_derived=tax_list,
        extractor_detected=extractor_flags,
        candidate=candidate,
        effective=effective,
    )


def list_v2_occurrences_for_audit(
    extractor: SafetySignalsV2 | None,
) -> list[SafetyOccurrence]:
    if extractor is None:
        return []
    return list(extractor.occurrences)
