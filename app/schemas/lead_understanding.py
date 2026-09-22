"""Contrato lead_understanding.v1 — compreensão estruturada do lead."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

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
from app.domain.fact_vocabulary import (
    TECHNICAL_PLACEHOLDER_VALUES,
    canonical_fact_value_out_of_domain,
)
from app.domain.models import StrictModel
from app.domain.validators import secondary_differs_from_primary
from app.taxonomy import get_taxonomy

SCHEMA_VERSION: Literal["lead_understanding.v1"] = "lead_understanding.v1"


class Participant(StrictModel):
    role: ParticipantRole
    relationship: str | None = Field(default=None, max_length=256)
    # Sem nomes ou documentos pessoais nesta fase


# Placeholders conhecidos — rejeição determinística (não prova fidelidade semântica).
_CASE_FACT_PLACEHOLDER_VALUES = TECHNICAL_PLACEHOLDER_VALUES


class CaseFact(StrictModel):
    key: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1, max_length=2000)
    certainty: FactCertainty
    source_message_ids: list[str] = Field(default_factory=list)
    from_trusted_crm_context: bool = False

    @field_validator("value")
    @classmethod
    def reject_known_placeholders(cls, value: str) -> str:
        if value.strip().lower() in _CASE_FACT_PLACEHOLDER_VALUES:
            raise PydanticCustomError(
                "case_fact_placeholder_value",
                "case_facts.value must be a concrete supported value, not a placeholder",
            )
        return value

    @model_validator(mode="after")
    def require_source_or_trusted(self) -> CaseFact:
        if not self.source_message_ids and not self.from_trusted_crm_context:
            raise ValueError("Fato sem source_message_ids exige from_trusted_crm_context=true")
        return self

    @model_validator(mode="after")
    def canonical_keys_respect_value_domain(self) -> CaseFact:
        """Chave canônica booleana exige literal do domínio documentado.

        Valor fora do domínio é rejeitado com código estável — não é renomeado,
        removido nem convertido para produzir sucesso.
        """
        if canonical_fact_value_out_of_domain(self.key, self.value):
            raise PydanticCustomError(
                "canonical_boolean_fact_value",
                "canonical boolean fact requires a documented boolean literal",
            )
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
            raise PydanticCustomError(
                "ambiguity_present_requires_reason",
                "ambiguity.present=true requires reason",
            )
        if not self.present and (
            self.alternative_area is not None or self.alternative_subject is not None
        ):
            raise PydanticCustomError(
                "ambiguity_alternatives_without_present",
                "without ambiguity, alternative_area/subject must be null",
            )
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
            raise PydanticCustomError(
                "secondary_area_equals_primary",
                "secondary_area must differ from primary_area",
            )

        taxonomy = get_taxonomy()
        if not taxonomy.is_valid_subject(self.primary_area, self.subject):
            raise PydanticCustomError(
                "subject_not_in_primary_area",
                "subject is not valid for primary_area",
            )

        allowed_subs = taxonomy.subsubjects_for(self.primary_area, self.subject)
        for sub in self.subsubjects:
            if allowed_subs:
                if sub not in allowed_subs:
                    raise PydanticCustomError(
                        "subsubject_not_in_catalog",
                        "subsubject is not in the catalog for area/subject",
                    )
            elif sub not in {"other", "undetermined"}:
                raise PydanticCustomError(
                    "subsubject_without_catalog",
                    "subsubject not allowed when catalog is empty",
                )

        return self
