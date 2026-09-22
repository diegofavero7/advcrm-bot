"""Contrato triage_next_step.v1 — próxima ação estruturada proposta."""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator
from pydantic_core import PydanticCustomError

from app.domain.enums import HandoffReason, Priority, TriageAction
from app.domain.models import StrictModel

SCHEMA_VERSION: Literal["triage_next_step.v1"] = "triage_next_step.v1"


class TriageNextStep(StrictModel):
    """Próximo passo proposto — o AdvCRM decide e executa ações externas."""

    schema_version: Literal["triage_next_step.v1"] = SCHEMA_VERSION
    action: TriageAction
    priority: Priority
    missing_information: list[str]
    selected_missing_information: list[str]
    proposed_question: str | None
    requires_human_handoff: bool
    handoff_reason: HandoffReason | None
    policy_flags: list[str]

    @model_validator(mode="after")
    def semantic_rules(self) -> TriageNextStep:
        if self.action == TriageAction.ASK_QUESTION:
            if not self.missing_information:
                raise PydanticCustomError(
                    "ask_question_requires_missing_information",
                    "ask_question requires at least one missing_information item",
                )
            if len(self.selected_missing_information) != 1:
                raise PydanticCustomError(
                    "ask_question_requires_exactly_one_selected",
                    "ask_question requires exactly one selected_missing_information item",
                )
            selected = self.selected_missing_information[0]
            if selected not in self.missing_information:
                raise PydanticCustomError(
                    "ask_question_selected_not_in_missing",
                    "selected_missing_information must be in missing_information",
                )
            if self.proposed_question is None:
                raise PydanticCustomError(
                    "ask_question_requires_proposed_question",
                    "ask_question requires proposed_question non-null",
                )
            if not self.proposed_question.strip():
                raise PydanticCustomError(
                    "ask_question_requires_proposed_question",
                    "ask_question requires proposed_question non-empty",
                )
            # Limitação mecânica: conta '?' — não garante unicidade semântica da pergunta.
            # A orientação normativa (uma informação por turno) está no prompt next_step.
            if self.proposed_question.count("?") > 1:
                raise PydanticCustomError(
                    "ask_question_single_question_mark",
                    "proposed_question must contain at most one logical step (? count)",
                )
            if self.requires_human_handoff:
                raise PydanticCustomError(
                    "ask_question_forbids_handoff",
                    "ask_question requires requires_human_handoff=false",
                )
            if self.handoff_reason is not None:
                raise PydanticCustomError(
                    "ask_question_forbids_handoff_reason",
                    "ask_question requires handoff_reason=null",
                )
        elif self.proposed_question is not None:
            raise ValueError(f"Ação {self.action.value} exige proposed_question=null")

        if self.requires_human_handoff:
            if self.handoff_reason is None:
                raise ValueError("requires_human_handoff=true exige handoff_reason")
            if self.action == TriageAction.ASK_QUESTION:
                raise PydanticCustomError(
                    "ask_question_forbids_handoff",
                    "handoff obrigatório impede ask_question",
                )
        elif self.handoff_reason is not None:
            raise ValueError("requires_human_handoff=false exige handoff_reason=null")

        # Flags de política não podem indicar mérito jurídico
        banned = {
            "win_probability",
            "eligibility_confirmed",
            "recommend_lawsuit",
            "guarantee_right",
        }
        for flag in self.policy_flags:
            if flag.lower() in banned:
                raise ValueError(f"policy_flag '{flag}' indica mérito jurídico — proibido")

        return self
