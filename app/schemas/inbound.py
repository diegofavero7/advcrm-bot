"""Contrato de entrada TriageAnalysisRequest e estruturas fechadas relacionadas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.domain.enums import (
    ContentType,
    FactCertainty,
    HandoffReason,
    Intent,
    LegalArea,
    MessageDirection,
    MessageRole,
    RequestSource,
    TriageAction,
    TriageState,
)
from app.domain.models import StrictModel

SCHEMA_VERSION_PREVIOUS: Literal["previous_decision.v1"] = "previous_decision.v1"


class KnownFact(StrictModel):
    """Fato conhecido com origem rastreável — não é dict livre."""

    key: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1, max_length=2000)
    certainty: FactCertainty
    source_message_ids: list[str] = Field(default_factory=list)
    from_trusted_crm_context: bool = False

    @model_validator(mode="after")
    def require_source_or_trusted_context(self) -> KnownFact:
        if not self.source_message_ids and not self.from_trusted_crm_context:
            raise ValueError("Fato sem source_message_ids exige from_trusted_crm_context=true")
        return self


class PreviousDecisionSummary(StrictModel):
    """Resumo fechado da decisão anterior — sem payload livre."""

    schema_version: Literal["previous_decision.v1"] = SCHEMA_VERSION_PREVIOUS
    intent: Intent
    primary_area: LegalArea
    subject: str = Field(min_length=1, max_length=128)
    action: TriageAction
    requires_human_handoff: bool
    handoff_reason: HandoffReason | None

    @model_validator(mode="after")
    def handoff_reason_coherence(self) -> PreviousDecisionSummary:
        if self.requires_human_handoff and self.handoff_reason is None:
            raise ValueError("requires_human_handoff=true exige handoff_reason")
        if not self.requires_human_handoff and self.handoff_reason is not None:
            raise ValueError("requires_human_handoff=false exige handoff_reason=null")
        return self


class ConversationMessage(StrictModel):
    message_id: str = Field(min_length=1, max_length=128)
    role: MessageRole
    direction: MessageDirection
    content_type: ContentType
    text: str | None
    created_at: datetime
    reply_to_message_id: str | None

    @field_validator("created_at")
    @classmethod
    def must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at deve ser timezone-aware (ISO 8601 com offset)")
        return value

    @model_validator(mode="after")
    def text_required_for_text_content(self) -> ConversationMessage:
        if self.content_type == ContentType.TEXT and (self.text is None or not self.text.strip()):
            raise ValueError("content_type=text exige text não vazio")
        return self


class TriageAnalysisRequest(StrictModel):
    """Contrato de entrada autorizado pelo AdvCRM."""

    event_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    lead_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=128)
    triage_state: TriageState
    source: RequestSource
    messages: list[ConversationMessage] = Field(min_length=1)
    known_facts: list[KnownFact]
    previous_decision: PreviousDecisionSummary | None

    @model_validator(mode="after")
    def message_invariants(self) -> TriageAnalysisRequest:
        ids = [m.message_id for m in self.messages]
        if len(ids) != len(set(ids)):
            raise ValueError("message_id devem ser únicos no request")

        id_set = set(ids)
        for message in self.messages:
            if (
                message.reply_to_message_id is not None
                and message.reply_to_message_id not in id_set
            ):
                raise ValueError(f"reply_to_message_id inválido: {message.reply_to_message_id}")

        # known_facts: source_message_ids devem existir se presentes
        for fact in self.known_facts:
            for mid in fact.source_message_ids:
                if mid not in id_set:
                    raise ValueError(
                        f"known_fact.source_message_ids referencia id inexistente: {mid}"
                    )

        return self

    def lead_declaration_message_ids(self) -> list[str]:
        """IDs de mensagens do lead — system/agent não são declaração do lead."""
        return [
            m.message_id
            for m in self.messages
            if m.role == MessageRole.LEAD and m.content_type == ContentType.TEXT
        ]
