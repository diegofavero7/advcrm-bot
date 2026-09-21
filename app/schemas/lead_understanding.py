"""Contrato lead_understanding.v1 — compreensão estruturada do lead."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.domain.enums import (
    DocumentAvailability,
    FactCertainty,
    Intent,
    Language,
    LegalArea,
    ParticipantRole,
    RiskFlag,
    UrgencyLevel,
)
from app.domain.models import StrictModel
from app.domain.validators import secondary_differs_from_primary
from app.taxonomy import get_taxonomy

SCHEMA_VERSION: Literal["lead_understanding.v1"] = "lead_understanding.v1"


class Participant(StrictModel):
    role: ParticipantRole
    relationship: str | None = Field(default=None, max_length=256)
    # Sem nomes ou documentos pessoais nesta fase


class CaseFact(StrictModel):
    key: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1, max_length=2000)
    certainty: FactCertainty
    source_message_ids: list[str] = Field(default_factory=list)
    from_trusted_crm_context: bool = False

    @model_validator(mode="after")
    def require_source_or_trusted(self) -> CaseFact:
        if not self.source_message_ids and not self.from_trusted_crm_context:
            raise ValueError("Fato sem source_message_ids exige from_trusted_crm_context=true")
        return self


class ProceduralSituation(StrictModel):
    stage: str | None = Field(default=None, max_length=256)
    prior_request: bool | None
    prior_denial: bool | None
    existing_case: bool | None
    prior_attempts: str | None = Field(default=None, max_length=1000)


class MentionedDocument(StrictModel):
    label: str = Field(min_length=1, max_length=256)
    availability: DocumentAvailability


class Ambiguity(StrictModel):
    present: bool
    reason: str | None = Field(default=None, max_length=500)
    needs_confirmation: bool
    alternative_area: LegalArea | None
    alternative_subject: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def ambiguity_coherence(self) -> Ambiguity:
        if self.present and not self.reason:
            raise ValueError("ambiguity.present=true exige reason")
        if not self.present and (
            self.alternative_area is not None or self.alternative_subject is not None
        ):
            raise ValueError("Sem ambiguidade, alternative_area/subject devem ser null")
        return self


class SafetyAssessment(StrictModel):
    level: UrgencyLevel
    reason: str = Field(min_length=1, max_length=500)
    detected_risks: list[RiskFlag]
    recommend_handoff: bool


class LeadUnderstanding(StrictModel):
    """Compreensão estruturada v1 — sem mérito jurídico conclusivo."""

    schema_version: Literal["lead_understanding.v1"] = SCHEMA_VERSION
    intent: Intent
    language: Language
    primary_area: LegalArea
    secondary_area: LegalArea | None
    subject: str = Field(min_length=1, max_length=128)
    subsubjects: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning_summary: str = Field(min_length=1, max_length=500)
    participants: list[Participant]
    case_facts: list[CaseFact]
    procedural_situation: ProceduralSituation
    mentioned_documents: list[MentionedDocument]
    documents_availability: DocumentAvailability
    ambiguity: Ambiguity
    urgency: UrgencyLevel
    safety: SafetyAssessment

    @field_validator("reasoning_summary")
    @classmethod
    def no_chain_of_thought_markers(cls, value: str) -> str:
        lowered = value.lower()
        banned = ("passo a passo", "chain of thought", "thinking process")
        if any(marker in lowered for marker in banned):
            raise ValueError("reasoning_summary não deve conter cadeia de pensamento")
        return value

    @model_validator(mode="after")
    def taxonomy_and_area_rules(self) -> LeadUnderstanding:
        if not secondary_differs_from_primary(self.primary_area, self.secondary_area):
            raise ValueError("secondary_area não pode ser igual a primary_area")

        taxonomy = get_taxonomy()
        if not taxonomy.is_valid_subject(self.primary_area, self.subject):
            raise ValueError(
                f"Assunto '{self.subject}' inválido para área '{self.primary_area.value}'"
            )

        allowed_subs = taxonomy.subsubjects_for(self.primary_area, self.subject)
        for sub in self.subsubjects:
            if allowed_subs:
                if sub not in allowed_subs:
                    raise ValueError(
                        f"Subassunto '{sub}' inválido para {self.primary_area.value}/{self.subject}"
                    )
            elif sub not in {"other", "undetermined"}:
                raise ValueError(f"Subassunto '{sub}' não permitido sem catálogo detalhado")

        return self
