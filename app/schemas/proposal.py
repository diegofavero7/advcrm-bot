"""TriageProposal — contrato interno do bot (não é grammar do modelo)."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from app.domain.models import StrictModel
from app.safety.compose import EffectiveSafetySignals
from app.schemas.inbound import TriageAnalysisRequest
from app.schemas.lead_understanding import LeadUnderstanding
from app.schemas.safety_signals import SafetySignals, SafetySignalsV2
from app.schemas.triage_next_step import TriageNextStep


class ProposalStatus(StrEnum):
    SUCCESS = "success"
    FAILED_CLOSED = "failed_closed"
    # Understanding rejeitado + safety válido; decisão eventual só a partir do extrator.
    # Não implica understanding válido nem sucesso semântico completo.
    DEGRADED_SAFETY = "degraded_safety"


class SafeFallback(StrictModel):
    """Revisão humana interna determinística — não é TriageNextStep artificial."""

    source: Literal["deterministic_policy"] = "deterministic_policy"
    recommended_internal_action: Literal["request_human_review"] = "request_human_review"
    reason_category: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=500)


class ProposalError(StrictModel):
    category: str
    message: str
    stage: str
    # Diagnóstico sanitizado (loc/type) — opcional; nunca inclui input/ctx/msg.
    details: list[dict[str, object]] | None = None
    http_status: int | None = None
    request_id: str | None = None
    retry_count: int | None = None
    latency_ms: float | None = None
    # Payload rejeitado (eval sintético local). Não logar em operações.
    rejected_payload: dict[str, object] | None = None


class DecisionProvenance(StrictModel):
    """Origem da decisão efetiva — distinto do contrato enviado à IA."""

    source: Literal["deterministic_policy", "model"]
    policy_version: str
    policy_rule_id: str | None
    policy_flags: list[str] = Field(default_factory=list)
    advisory_flags: list[str] = Field(default_factory=list)
    policy_status: Literal[
        "applied_mandatory",
        "advisory_recorded",
        "not_reached",
        "failed",
    ]
    model_inference_skipped: bool
    # Refs opacas (occurrence_id / cue) da decisão degradada — sem quotes sensíveis.
    supporting_signal_refs: list[str] = Field(default_factory=list)


class ProposalMetadata(StrictModel):
    model: str | None
    prompt_version_understanding: str | None
    prompt_version_next_step: str | None
    prompt_version_safety_signals: str | None = None
    prompt_hash_understanding: str | None
    prompt_hash_next_step: str | None
    prompt_hash_safety_signals: str | None = None
    schema_version_understanding: str
    schema_version_next_step: str
    schema_version_safety_signals: str | None = None
    taxonomy_version: str
    playbook_id: str | None
    playbook_version: str | None
    understanding_latency_ms: float | None
    next_step_latency_ms: float | None
    safety_signals_latency_ms: float | None = None
    understanding_retry_count: int | None
    next_step_retry_count: int | None
    safety_signals_retry_count: int | None = None
    request_id_understanding: str | None
    request_id_next_step: str | None
    request_id_safety_signals: str | None = None
    policy_version: str | None = None
    policy_rule_id: str | None = None
    composition_version: str | None = None


class TriageProposal(StrictModel):
    event_id: str
    status: ProposalStatus
    lead_understanding: LeadUnderstanding | None
    # Decisão efetiva (política ou modelo reconciliado) — não afirmar proveniência pelo campo.
    triage_next_step: TriageNextStep | None
    # Proposta bruta do modelo para next_step, quando a 2ª inferência ocorreu.
    model_next_step_proposal: TriageNextStep | None = None
    # Extrator (v1 ou v2) — não altera lead_understanding.safety.detected_risks.
    safety_signals: SafetySignals | SafetySignalsV2 | None = None
    effective_safety_signals: EffectiveSafetySignals | None = None
    decision_provenance: DecisionProvenance | None = None
    safe_fallback: SafeFallback | None
    error: ProposalError | None
    metadata: ProposalMetadata


class NextStepOnlyEnvelope(StrictModel):
    """Envelope para CLI --next-step-only."""

    request: TriageAnalysisRequest
    lead_understanding: LeadUnderstanding
    questions_asked: int = Field(default=0, ge=0)
    last_bot_question: str | None = None
    missing_information: list[str] = Field(default_factory=list)
