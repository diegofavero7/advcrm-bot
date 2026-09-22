"""Contratos safety_signals — extrator estreito (paralelo ao understanding).

``safety_signals.v1``: risk + source_message_ids + evidence_summary (preservado).
``safety_signals.v2``: ocorrências com asserção/temporalidade + cues explícitos
de pedido urgente de ajuda e perigo imediato. O modelo descreve; a política decide.

Correspondência literal de ``evidence_quote`` prova existência do trecho nas mensagens
autorizadas — **não** prova que a interpretação semântica esteja correta.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from app.domain.enums import RiskFlag
from app.domain.models import StrictModel

SCHEMA_VERSION_V1: Literal["safety_signals.v1"] = "safety_signals.v1"
SCHEMA_VERSION_V2: Literal["safety_signals.v2"] = "safety_signals.v2"


# --- v1 (preservado) ---------------------------------------------------------


class DetectedSafetyRisk(StrictModel):
    """Um risco categórico com evidência explícita nas mensagens do lead (v1)."""

    risk: RiskFlag
    source_message_ids: list[str] = Field(min_length=1)
    evidence_summary: str = Field(min_length=1, max_length=500)


class SafetySignals(StrictModel):
    """Saída do extrator v1 — não classifica área/subject/intent nem decide handoff."""

    schema_version: Literal["safety_signals.v1"] = SCHEMA_VERSION_V1
    detected_risks: list[DetectedSafetyRisk]

    @model_validator(mode="after")
    def unique_risks_deterministic(self) -> SafetySignals:
        seen: set[RiskFlag] = set()
        for item in self.detected_risks:
            if item.risk in seen:
                raise PydanticCustomError(
                    "duplicate_safety_risk",
                    "detected_risks must not repeat the same risk value",
                )
            seen.add(item.risk)
        return self


# --- v2 ----------------------------------------------------------------------


class RiskAssertion(StrEnum):
    """Como o lead enquadra o risco."""

    AFFIRMED = "affirmed"  # relatada como existente
    DENIED = "denied"
    HYPOTHETICAL = "hypothetical"


class TemporalContext(StrEnum):
    """Contexto temporal declarado (ou ausência de informação)."""

    ONGOING = "ongoing"  # atual / em curso
    NEAR_FUTURE = "near_future"  # futuro próximo (prazos, atos)
    HISTORICAL = "historical"
    UNKNOWN = "unknown"  # continuidade não informada ≠ histórico


class ExplicitCueState(StrEnum):
    """Estado de um cue explícito — ausência de informação ≠ negação."""

    PRESENT = "present"
    NOT_INFORMED = "not_informed"


class SafetyOccurrence(StrictModel):
    """Uma ocorrência distinta de risco (pessoas/eventos/tempos não fundidos)."""

    occurrence_id: str = Field(min_length=1, max_length=64)
    risk: RiskFlag
    assertion: RiskAssertion
    temporal_context: TemporalContext
    source_message_ids: list[str] = Field(min_length=1)
    # Trecho curto verificável no texto original (validação literal no serviço).
    evidence_quote: str = Field(min_length=1, max_length=240)
    evidence_summary: str = Field(min_length=1, max_length=400)


class ExplicitSafetyCue(StrictModel):
    """Pedido urgente de ajuda ou indicação de perigo imediato — evidência própria."""

    state: ExplicitCueState
    source_message_ids: list[str] = Field(default_factory=list)
    evidence_quote: str | None = None
    evidence_summary: str | None = None
    # Associa o cue a ocorrências; vazio = não relacionado a risco categórico específico.
    related_occurrence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def cue_coherence(self) -> ExplicitSafetyCue:
        if self.state == ExplicitCueState.NOT_INFORMED:
            if self.source_message_ids or self.evidence_quote or self.evidence_summary:
                raise PydanticCustomError(
                    "explicit_cue_not_informed_must_be_empty",
                    "not_informed cue must not carry evidence fields",
                )
            if self.related_occurrence_ids:
                raise PydanticCustomError(
                    "explicit_cue_not_informed_must_be_empty",
                    "not_informed cue must not relate to occurrences",
                )
            return self
        # present
        if not self.source_message_ids:
            raise PydanticCustomError(
                "explicit_cue_present_requires_evidence",
                "present cue requires source_message_ids",
            )
        if not self.evidence_quote or not self.evidence_summary:
            raise PydanticCustomError(
                "explicit_cue_present_requires_evidence",
                "present cue requires evidence_quote and evidence_summary",
            )
        return self


class SafetySignalsV2(StrictModel):
    """Saída do extrator v2 — descreve conteúdo; não escolhe ação/handoff."""

    schema_version: Literal["safety_signals.v2"] = SCHEMA_VERSION_V2
    occurrences: list[SafetyOccurrence]
    urgent_help_request: ExplicitSafetyCue
    immediate_danger: ExplicitSafetyCue

    @model_validator(mode="after")
    def occurrence_ids_unique_and_refs_valid(self) -> SafetySignalsV2:
        seen: set[str] = set()
        for item in self.occurrences:
            if item.occurrence_id in seen:
                raise PydanticCustomError(
                    "duplicate_occurrence_id",
                    "occurrences must have unique occurrence_id",
                )
            seen.add(item.occurrence_id)
        for cue in (self.urgent_help_request, self.immediate_danger):
            for ref in cue.related_occurrence_ids:
                if ref not in seen:
                    raise PydanticCustomError(
                        "unknown_related_occurrence_id",
                        "related_occurrence_ids must reference occurrences",
                    )
        return self


AnySafetySignals = Annotated[
    SafetySignals | SafetySignalsV2,
    Field(discriminator="schema_version"),
]


def is_safety_signals_v2(signals: SafetySignals | SafetySignalsV2) -> bool:
    return isinstance(signals, SafetySignalsV2)


def aggregate_affirmed_risks(signals: SafetySignals | SafetySignalsV2) -> list[RiskFlag]:
    """Riscos categóricos agregados para métricas/composição operacional.

    v1: todos os detected_risks.
    v2: ocorrências com assertion=affirmed (negadas/hipotéticas ficam só na auditoria).
    Ocorrências históricas afirmadas entram no agregado categórico sem implicar urgência.
    """
    if isinstance(signals, SafetySignalsV2):
        flags = [occ.risk for occ in signals.occurrences if occ.assertion == RiskAssertion.AFFIRMED]
    else:
        flags = [item.risk for item in signals.detected_risks]
    ordered = list(RiskFlag)
    present = set(flags)
    return [r for r in ordered if r in present]
