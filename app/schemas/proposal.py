"""TriageProposal — contrato interno do bot (não é grammar do modelo)."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from app.domain.models import StrictModel
from app.schemas.inbound import TriageAnalysisRequest
from app.schemas.lead_understanding import LeadUnderstanding
from app.schemas.triage_next_step import TriageNextStep


class ProposalStatus(StrEnum):
    SUCCESS = "success"
    FAILED_CLOSED = "failed_closed"


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


class ProposalMetadata(StrictModel):
    model: str | None
    prompt_version_understanding: str | None
    prompt_version_next_step: str | None
    prompt_hash_understanding: str | None
    prompt_hash_next_step: str | None
    schema_version_understanding: str
    schema_version_next_step: str
    taxonomy_version: str
    playbook_id: str | None
    playbook_version: str | None
    understanding_latency_ms: float | None
    next_step_latency_ms: float | None
    understanding_retry_count: int | None
    next_step_retry_count: int | None
    request_id_understanding: str | None
    request_id_next_step: str | None


class TriageProposal(StrictModel):
    event_id: str
    status: ProposalStatus
    lead_understanding: LeadUnderstanding | None
    triage_next_step: TriageNextStep | None
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
