"""Contrato triage_next_step.v1 — próxima ação estruturada proposta."""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator

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
            if self.proposed_question is None or not self.proposed_question.strip():
                raise ValueError("ask_question exige proposed_question não vazia")
            # Uma etapa lógica — rejeitar múltiplas perguntas concatenadas de forma óbvia
            if self.proposed_question.count("?") > 1:
                raise ValueError("proposed_question deve conter apenas uma etapa lógica")
        elif self.proposed_question is not None:
            raise ValueError(f"Ação {self.action.value} exige proposed_question=null")

        if self.requires_human_handoff:
            if self.handoff_reason is None:
                raise ValueError("requires_human_handoff=true exige handoff_reason")
            if self.action == TriageAction.ASK_QUESTION:
                raise ValueError(
                    "handoff obrigatório impede ask_question; use human_handoff "
                    "ou request_human_review"
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
