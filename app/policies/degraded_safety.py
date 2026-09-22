"""Política do caminho degradado: understanding inválido + safety v2 validado.

Versão ``degraded_safety.v2``. Exige entrada tipada só com extrator validado
(``ExtractorOnlySafety``). Não usa payload rejeitado do understanding.

Precedência:
1. perigo imediato confirmado (B);
2. prazo jurídico iminente (C);
3. violência doméstica + pedido urgente de ajuda (A);
4. revisão interna (D).

v1 de safety (só rótulo) **não** autoriza handoff neste caminho — sem inventar
temporalidade/urgência.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.enums import HandoffReason, Priority, RiskFlag, TriageAction
from app.safety.compose import EffectiveSafetySignals, SafetySignalSource
from app.schemas.safety_signals import (
    ExplicitCueState,
    RiskAssertion,
    SafetyOccurrence,
    SafetySignals,
    SafetySignalsV2,
    TemporalContext,
)
from app.schemas.triage_next_step import TriageNextStep

DEGRADED_POLICY_VERSION = "degraded_safety.v2"
# Legado (documentação / artefatos): degraded_safety.v1 não é mais aplicado.

DEGRADED_POLICY_FLAG_PREFIX = "degraded_safety_"

_IMMEDIATE_RISK_CATEGORIES: frozenset[RiskFlag] = frozenset(
    {
        RiskFlag.VIOLENCE_OR_THREAT,
        RiskFlag.DOMESTIC_VIOLENCE,
        RiskFlag.SELF_HARM,
        RiskFlag.MEDICAL_EMERGENCY,
    }
)


@dataclass(frozen=True, slots=True)
class ExtractorOnlySafety:
    """Entrada restrita à política degradada: extrator v2 + composição consistente.

    Rejeita model/taxonomy na composição e exige o payload validado do extrator.
    """

    signals: SafetySignalsV2
    effective: EffectiveSafetySignals

    @staticmethod
    def from_validated(
        *,
        signals: SafetySignals | SafetySignalsV2,
        effective: EffectiveSafetySignals,
    ) -> ExtractorOnlySafety:
        if not isinstance(signals, SafetySignalsV2):
            raise ValueError("degraded_safety.v2 requer safety_signals.v2 validado")
        if effective.model_detected:
            raise ValueError("composição degradada não pode incluir model_detected")
        if effective.taxonomy_derived:
            raise ValueError("composição degradada não pode incluir taxonomy_derived")
        for entry in effective.effective:
            if entry.sources != [SafetySignalSource.EXTRACTOR]:
                raise ValueError(
                    "composição degradada inconsistente: effective sem origem exclusiva extractor"
                )
        return ExtractorOnlySafety(signals=signals, effective=effective)


@dataclass(frozen=True, slots=True)
class DegradedSafetyDecision:
    """Resultado da política degradada — handoff de atendimento ou revisão interna."""

    next_step: TriageNextStep | None
    policy_rule_id: str
    policy_flags: tuple[str, ...]
    requires_internal_review: bool
    supporting_occurrence_ids: tuple[str, ...] = ()
    supporting_cue: str | None = None
    policy_version: str = DEGRADED_POLICY_VERSION


def _occ_by_id(signals: SafetySignalsV2) -> dict[str, SafetyOccurrence]:
    return {o.occurrence_id: o for o in signals.occurrences}


def _related_affirmed(
    signals: SafetySignalsV2,
    related_ids: list[str],
    *,
    allowed_risks: frozenset[RiskFlag],
) -> list[SafetyOccurrence]:
    by_id = _occ_by_id(signals)
    out: list[SafetyOccurrence] = []
    for oid in related_ids:
        occ = by_id.get(oid)
        if occ is None:
            continue
        if occ.assertion != RiskAssertion.AFFIRMED:
            continue
        if occ.risk not in allowed_risks:
            continue
        out.append(occ)
    return out


def _handoff(
    *,
    rule: str,
    reason: HandoffReason,
    priority: Priority,
    occurrence_ids: tuple[str, ...],
    cue: str | None,
    extra_flags: tuple[str, ...] = (),
) -> DegradedSafetyDecision:
    flags = (rule, *extra_flags)
    return DegradedSafetyDecision(
        next_step=TriageNextStep(
            action=TriageAction.HUMAN_HANDOFF,
            priority=priority,
            missing_information=[],
            selected_missing_information=[],
            proposed_question=None,
            requires_human_handoff=True,
            handoff_reason=reason,
            policy_flags=list(flags),
        ),
        policy_rule_id=rule,
        policy_flags=flags,
        requires_internal_review=False,
        supporting_occurrence_ids=occurrence_ids,
        supporting_cue=cue,
    )


def _internal_review(*audit_flags: str) -> DegradedSafetyDecision:
    rule = "degraded_safety_internal_review"
    flags = (rule, *audit_flags) if audit_flags else (rule,)
    return DegradedSafetyDecision(
        next_step=None,
        policy_rule_id=rule,
        policy_flags=flags,
        requires_internal_review=True,
    )


def decide_degraded_safety_action(payload: ExtractorOnlySafety) -> DegradedSafetyDecision:
    """Decide handoff ou revisão interna só com extrator v2 tipado."""
    signals = payload.signals
    audit: list[str] = []

    # Auditoria: preservar presença de sinais mesmo quando outra regra vence.
    if any(o.risk == RiskFlag.DOMESTIC_VIOLENCE for o in signals.occurrences):
        audit.append("audit_domestic_violence_present")
    if signals.immediate_danger.state == ExplicitCueState.PRESENT:
        audit.append("audit_immediate_danger_present")
    if signals.urgent_help_request.state == ExplicitCueState.PRESENT:
        audit.append("audit_urgent_help_present")

    # --- B: perigo imediato + risco afirmado relacionado ---
    if signals.immediate_danger.state == ExplicitCueState.PRESENT:
        related = _related_affirmed(
            signals,
            signals.immediate_danger.related_occurrence_ids,
            allowed_risks=_IMMEDIATE_RISK_CATEGORIES,
        )
        # Histórico isolado sem cue não chega aqui; com cue relacionado, perigo é atual.
        if related:
            return _handoff(
                rule=f"{DEGRADED_POLICY_FLAG_PREFIX}immediate_danger_requires_handoff",
                reason=HandoffReason.IMMEDIATE_RISK,
                priority=Priority.CRITICAL,
                occurrence_ids=tuple(o.occurrence_id for o in related),
                cue="immediate_danger",
                extra_flags=tuple(audit),
            )

    # --- C: prazo jurídico iminente ---
    deadline_hits = [
        o
        for o in signals.occurrences
        if o.risk == RiskFlag.IMMINENT_DEADLINE
        and o.assertion == RiskAssertion.AFFIRMED
        and o.temporal_context in {TemporalContext.NEAR_FUTURE, TemporalContext.ONGOING}
    ]
    if deadline_hits:
        return _handoff(
            rule=f"{DEGRADED_POLICY_FLAG_PREFIX}imminent_deadline_requires_handoff",
            reason=HandoffReason.LEGAL_DEADLINE_RISK,
            priority=Priority.CRITICAL,
            occurrence_ids=tuple(o.occurrence_id for o in deadline_hits),
            cue=None,
            extra_flags=tuple(audit),
        )

    # --- A: DV afirmada + pedido urgente de ajuda relacionado ---
    if signals.urgent_help_request.state == ExplicitCueState.PRESENT:
        related_dv = _related_affirmed(
            signals,
            signals.urgent_help_request.related_occurrence_ids,
            allowed_risks=frozenset({RiskFlag.DOMESTIC_VIOLENCE}),
        )
        # Não exigir violence_or_threat; não declarar immediate_risk.
        if related_dv:
            return _handoff(
                rule=f"{DEGRADED_POLICY_FLAG_PREFIX}domestic_violence_urgent_help_requires_handoff",
                reason=HandoffReason.SENSITIVE_SITUATION,
                priority=Priority.HIGH,
                occurrence_ids=tuple(o.occurrence_id for o in related_dv),
                cue="urgent_help_request",
                extra_flags=tuple(audit),
            )

    return _internal_review(*audit)
