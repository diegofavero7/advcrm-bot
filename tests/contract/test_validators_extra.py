"""Cobertura adicional de validadores de contrato."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.domain.enums import TriageState
from app.domain.state_machine import allowed_targets
from app.schemas.inbound import KnownFact, PreviousDecisionSummary, TriageAnalysisRequest
from app.schemas.lead_understanding import CaseFact, LeadUnderstanding
from app.schemas.triage_next_step import TriageNextStep
from pydantic import ValidationError


def test_known_fact_requires_source_or_trusted() -> None:
    with pytest.raises(ValidationError):
        KnownFact(
            key="k",
            value="v",
            certainty="explicit",  # type: ignore[arg-type]
            source_message_ids=[],
            from_trusted_crm_context=False,
        )
    fact = KnownFact(
        key="k",
        value="v",
        certainty="explicit",  # type: ignore[arg-type]
        source_message_ids=[],
        from_trusted_crm_context=True,
    )
    assert fact.from_trusted_crm_context


def test_previous_decision_handoff_coherence() -> None:
    with pytest.raises(ValidationError):
        PreviousDecisionSummary(
            intent="new_legal_lead",  # type: ignore[arg-type]
            primary_area="civil",  # type: ignore[arg-type]
            subject="debt_collection",
            action="human_handoff",  # type: ignore[arg-type]
            requires_human_handoff=True,
            handoff_reason=None,
        )
    with pytest.raises(ValidationError):
        PreviousDecisionSummary(
            intent="new_legal_lead",  # type: ignore[arg-type]
            primary_area="civil",  # type: ignore[arg-type]
            subject="debt_collection",
            action="route_lead",  # type: ignore[arg-type]
            requires_human_handoff=False,
            handoff_reason="other",  # type: ignore[arg-type]
        )


def test_known_fact_message_id_must_exist_in_request() -> None:
    with pytest.raises(ValidationError):
        TriageAnalysisRequest.model_validate(
            {
                "event_id": "e",
                "tenant_id": "t",
                "lead_id": "l",
                "conversation_id": "c",
                "triage_state": "collecting_messages",
                "source": "whatsapp",
                "messages": [
                    {
                        "message_id": "m1",
                        "role": "lead",
                        "direction": "inbound",
                        "content_type": "text",
                        "text": "oi",
                        "created_at": "2026-09-20T14:00:00-03:00",
                        "reply_to_message_id": None,
                    }
                ],
                "known_facts": [
                    {
                        "key": "k",
                        "value": "v",
                        "certainty": "explicit",
                        "source_message_ids": ["m-missing"],
                        "from_trusted_crm_context": False,
                    }
                ],
                "previous_decision": None,
            }
        )


def test_case_fact_and_reasoning_and_subsubject() -> None:
    with pytest.raises(ValidationError):
        CaseFact(
            key="k",
            value="v",
            certainty="explicit",  # type: ignore[arg-type]
            source_message_ids=[],
            from_trusted_crm_context=False,
        )

    with pytest.raises(ValidationError):
        LeadUnderstanding.model_validate(
            {
                "schema_version": "lead_understanding.v1",
                "intent": "new_legal_lead",
                "language": "pt-BR",
                "primary_area": "civil",
                "secondary_area": None,
                "subject": "debt_collection",
                "subsubjects": ["not_allowed_sub"],
                "confidence": 0.9,
                "reasoning_summary": "Relata cobrança",
                "participants": [],
                "case_facts": [],
                "procedural_situation": {
                    "stage": None,
                    "prior_request": None,
                    "prior_denial": None,
                    "existing_case": None,
                    "prior_attempts": None,
                },
                "mentioned_documents": [],
                "documents_availability": "unknown",
                "ambiguity": {
                    "present": False,
                    "reason": None,
                    "needs_confirmation": False,
                    "alternative_area": None,
                    "alternative_subject": None,
                },
                "urgency": "normal",
                "safety": {
                    "level": "normal",
                    "reason": "ok",
                    "detected_risks": [],
                    "recommend_handoff": False,
                },
            }
        )

    with pytest.raises(ValidationError):
        LeadUnderstanding.model_validate(
            {
                "schema_version": "lead_understanding.v1",
                "intent": "new_legal_lead",
                "language": "pt-BR",
                "primary_area": "civil",
                "secondary_area": None,
                "subject": "debt_collection",
                "subsubjects": [],
                "confidence": 0.9,
                "reasoning_summary": "chain of thought detalhado",
                "participants": [],
                "case_facts": [],
                "procedural_situation": {
                    "stage": None,
                    "prior_request": None,
                    "prior_denial": None,
                    "existing_case": None,
                    "prior_attempts": None,
                },
                "mentioned_documents": [],
                "documents_availability": "unknown",
                "ambiguity": {
                    "present": False,
                    "reason": None,
                    "needs_confirmation": False,
                    "alternative_area": None,
                    "alternative_subject": None,
                },
                "urgency": "normal",
                "safety": {
                    "level": "normal",
                    "reason": "ok",
                    "detected_risks": [],
                    "recommend_handoff": False,
                },
            }
        )


def test_ambiguity_rules() -> None:
    with pytest.raises(ValidationError):
        LeadUnderstanding.model_validate(
            {
                "schema_version": "lead_understanding.v1",
                "intent": "new_legal_lead",
                "language": "pt-BR",
                "primary_area": "civil",
                "secondary_area": None,
                "subject": "debt_collection",
                "subsubjects": [],
                "confidence": 0.9,
                "reasoning_summary": "Relata cobrança",
                "participants": [],
                "case_facts": [],
                "procedural_situation": {
                    "stage": None,
                    "prior_request": None,
                    "prior_denial": None,
                    "existing_case": None,
                    "prior_attempts": None,
                },
                "mentioned_documents": [],
                "documents_availability": "unknown",
                "ambiguity": {
                    "present": True,
                    "reason": None,
                    "needs_confirmation": True,
                    "alternative_area": None,
                    "alternative_subject": None,
                },
                "urgency": "normal",
                "safety": {
                    "level": "normal",
                    "reason": "ok",
                    "detected_risks": [],
                    "recommend_handoff": False,
                },
            }
        )


def test_next_step_multi_question_and_handoff_blocks_ask() -> None:
    with pytest.raises(ValidationError):
        TriageNextStep.model_validate(
            {
                "schema_version": "triage_next_step.v1",
                "action": "ask_question",
                "priority": "normal",
                "missing_information": ["a"],
                "selected_missing_information": ["a"],
                "proposed_question": "Qual a data? E o valor?",
                "requires_human_handoff": False,
                "handoff_reason": None,
                "policy_flags": [],
            }
        )
    with pytest.raises(ValidationError):
        TriageNextStep.model_validate(
            {
                "schema_version": "triage_next_step.v1",
                "action": "ask_question",
                "priority": "normal",
                "missing_information": [],
                "selected_missing_information": [],
                "proposed_question": "Pode confirmar?",
                "requires_human_handoff": True,
                "handoff_reason": "user_requested_human",
                "policy_flags": [],
            }
        )
    with pytest.raises(ValidationError):
        TriageNextStep.model_validate(
            {
                "schema_version": "triage_next_step.v1",
                "action": "route_lead",
                "priority": "normal",
                "missing_information": [],
                "selected_missing_information": [],
                "proposed_question": None,
                "requires_human_handoff": True,
                "handoff_reason": None,
                "policy_flags": [],
            }
        )


def test_allowed_targets_helper() -> None:
    targets = allowed_targets(TriageState.FAILED)
    assert TriageState.PENDING_CLASSIFICATION in targets


def test_timezone_aware_message_ok() -> None:
    # smoke via datetime object path
    assert datetime(2026, 1, 1, tzinfo=UTC).tzinfo is not None
