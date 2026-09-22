"""Proteção spam_inconsistency_guard.v1 e classificação semântica pós-validate."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from app.application.services import LeadUnderstandingService, TriagePipelineService
from app.clients.errors import AiRuntimeSemanticValidationError
from app.clients.types import StructuredCompletionResult
from app.clients.validation_diagnostics import (
    SEMANTIC_COHERENCE_ERROR_TYPES,
    is_semantic_coherence_failure,
    sanitize_validation_error,
)
from app.config import Settings
from app.domain.enums import (
    ContentType,
    MessageDirection,
    MessageRole,
    TriageAction,
    TriageState,
)
from app.policies.resolution import (
    SPAM_INCONSISTENCY_GUARD_FLAG,
    SPAM_INCONSISTENCY_GUARD_VERSION,
    apply_spam_inconsistency_guard,
    resolve_conservative_policy,
    spam_conflicts_with_legal_signals,
)
from app.prompts import (
    clear_prompt_cache,
    load_lead_understanding_prompt,
    load_triage_next_step_prompt,
)
from app.schemas.inbound import ConversationMessage, TriageAnalysisRequest
from app.schemas.lead_understanding import Ambiguity, LeadUnderstanding
from app.schemas.proposal import ProposalStatus
from app.schemas.triage_next_step import TriageNextStep
from pydantic import ValidationError


def _request() -> TriageAnalysisRequest:
    return TriageAnalysisRequest(
        event_id="e-guard",
        tenant_id="t",
        lead_id="l",
        conversation_id="c",
        triage_state=TriageState.PENDING_CLASSIFICATION,
        source="whatsapp",  # type: ignore[arg-type]
        messages=[
            ConversationMessage(
                message_id="m1",
                role=MessageRole.LEAD,
                direction=MessageDirection.INBOUND,
                content_type=ContentType.TEXT,
                text="Meu cônjuge foi preso e quero auxílio-reclusão",
                created_at=datetime(2026, 9, 20, 14, 0, tzinfo=UTC),
                reply_to_message_id=None,
            )
        ],
        known_facts=[],
        previous_decision=None,
    )


def _understanding_payload(**overrides: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": "lead_understanding.v1",
        "intent": "new_legal_lead",
        "language": "pt-BR",
        "primary_area": "social_security",
        "secondary_area": None,
        "subject": "prison_allowance",
        "subsubjects": ["spouse_relationship"],
        "confidence": 0.9,
        "reasoning_summary": "Relata prisão do cônjuge",
        "participants": [],
        "case_facts": [
            {
                "key": "relationship_to_detainee",
                "value": "cônjuge",
                "certainty": "explicit",
                "source_message_ids": ["m1"],
                "from_trusted_crm_context": False,
            }
        ],
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
            "reason": "prisão relatada",
            "detected_risks": ["arrest_or_detention"],
            "recommend_handoff": False,
        },
    }
    base.update(overrides)
    return base


def _safety_payload(**overrides):
    from tests.helpers.safety import safety_v2

    return safety_v2(**overrides)


class FakeClient:
    def __init__(self, responses: list[StructuredCompletionResult | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    async def complete_structured(
        self,
        *,
        schema_name: str,
        json_schema: dict[str, object],
        messages: list[dict[str, str]],
        contract_label: str,
        organization_id: str | None = None,
    ) -> StructuredCompletionResult:
        self.calls.append(contract_label)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _completion(payload: dict[str, Any]) -> StructuredCompletionResult:
    return StructuredCompletionResult(
        parsed_content=payload,
        model="fake-model",
        finish_reason="stop",
        latency_ms=42.0,
        retry_count=0,
        request_id="rid-42",
        usage=None,
    )


def test_prompt_versions_v5_and_next_step_v3() -> None:
    clear_prompt_cache()
    u = load_lead_understanding_prompt()
    n = load_triage_next_step_prompt()
    assert u.version == "lead_understanding.v9"
    assert n.version == "triage_next_step.v3"
    assert "alternative_area e alternative_subject DEVEM ser null" in u.text
    assert "secondary_area deve ser null" in u.text
    assert "NÃO significa classificar automaticamente como spam" in u.text
    assert "arrest_or_detention" in u.text
    assert "entre 0 e 1" in u.text
    assert "0.95" in u.text
    assert "percentual" in u.text.lower() or "NÃO use percentual" in u.text
    assert "uma informação" in n.text.lower() or "UMA informação" in n.text
    assert "Nunca use action=ask_question quando missing_information estiver vazio" in n.text
    assert "Meu cônjuge foi preso e quero auxílio-reclusão" not in u.text


def test_ambiguity_coherence_uses_stable_semantic_code() -> None:
    with pytest.raises(ValidationError) as caught:
        Ambiguity.model_validate(
            {
                "present": False,
                "reason": None,
                "needs_confirmation": False,
                "alternative_area": "undetermined",
                "alternative_subject": "undetermined",
            }
        )
    details = sanitize_validation_error(caught.value)
    assert any(d["type"] == "ambiguity_alternatives_without_present" for d in details)
    assert "ambiguity_alternatives_without_present" in SEMANTIC_COHERENCE_ERROR_TYPES
    assert is_semantic_coherence_failure(details)


def test_spam_conflict_with_specific_legal_classification() -> None:
    understanding = LeadUnderstanding.model_validate(_understanding_payload(intent="spam"))
    assert spam_conflicts_with_legal_signals(understanding) is True
    ignore = TriageNextStep.model_validate(
        {
            "schema_version": "triage_next_step.v1",
            "action": "ignore",
            "priority": "low",
            "missing_information": [],
            "selected_missing_information": [],
            "proposed_question": None,
            "requires_human_handoff": False,
            "handoff_reason": None,
            "policy_flags": ["non_legal_or_spam"],
        }
    )
    guarded, version = apply_spam_inconsistency_guard(understanding, ignore)
    assert version == SPAM_INCONSISTENCY_GUARD_VERSION
    assert guarded.action == TriageAction.REQUEST_HUMAN_REVIEW
    assert guarded.requires_human_handoff is True
    assert SPAM_INCONSISTENCY_GUARD_FLAG in guarded.policy_flags


def test_coherent_spam_still_ignores() -> None:
    understanding = LeadUnderstanding.model_validate(
        _understanding_payload(
            intent="spam",
            primary_area="other",
            subject="other",
            subsubjects=[],
            case_facts=[],
            safety={
                "level": "normal",
                "reason": "spam",
                "detected_risks": [],
                "recommend_handoff": False,
            },
        )
    )
    assert spam_conflicts_with_legal_signals(understanding) is False
    resolution = resolve_conservative_policy(understanding, request=_request())
    assert resolution.decision.action == TriageAction.IGNORE
    assert resolution.policy_rule_id == "non_legal_or_spam"


def test_spam_mentioning_legal_theme_without_specific_classification_ignores() -> None:
    """Menção temática com other/undetermined não dispara o guard."""
    understanding = LeadUnderstanding.model_validate(
        _understanding_payload(
            intent="spam",
            primary_area="other",
            subject="other",
            subsubjects=[],
            case_facts=[
                {
                    "key": "mentioned_topic",
                    "value": "auxílio-reclusão",
                    "certainty": "uncertain",
                    "source_message_ids": ["m1"],
                    "from_trusted_crm_context": False,
                }
            ],
            safety={
                "level": "normal",
                "reason": "sem risco",
                "detected_risks": [],
                "recommend_handoff": False,
            },
        )
    )
    assert spam_conflicts_with_legal_signals(understanding) is False
    resolution = resolve_conservative_policy(understanding, request=_request())
    assert resolution.decision.action == TriageAction.IGNORE


@pytest.mark.asyncio
async def test_pipeline_spam_conflict_gets_internal_review_not_ignore() -> None:
    client = FakeClient(
        [_completion(_understanding_payload(intent="spam")), _completion(_safety_payload())]
    )
    proposal = await TriagePipelineService(client, Settings()).run(_request())
    assert proposal.status == ProposalStatus.SUCCESS
    assert client.calls == ["lead_understanding", "safety_signals"]
    assert proposal.triage_next_step is not None
    assert proposal.triage_next_step.action == TriageAction.REQUEST_HUMAN_REVIEW
    assert proposal.triage_next_step.action != TriageAction.IGNORE
    assert proposal.decision_provenance is not None
    assert proposal.decision_provenance.policy_version == SPAM_INCONSISTENCY_GUARD_VERSION
    assert proposal.decision_provenance.policy_rule_id == SPAM_INCONSISTENCY_GUARD_FLAG


@pytest.mark.asyncio
async def test_coherence_failure_is_semantic_and_preserves_prompt_metadata() -> None:
    bad = _understanding_payload(
        ambiguity={
            "present": False,
            "reason": None,
            "needs_confirmation": False,
            "alternative_area": "undetermined",
            "alternative_subject": "undetermined",
        }
    )
    client = FakeClient([_completion(bad), _completion(_safety_payload())])
    proposal = await TriagePipelineService(client, Settings()).run(_request())
    assert proposal.status == ProposalStatus.DEGRADED_SAFETY
    assert proposal.error is not None
    assert proposal.error.category == "semantic_validation"
    assert proposal.error.details
    assert any(
        d.get("type") == "ambiguity_alternatives_without_present" for d in proposal.error.details
    )
    assert proposal.metadata.model == "fake-model"
    assert proposal.metadata.prompt_version_understanding == "lead_understanding.v9"
    assert proposal.metadata.prompt_hash_understanding
    assert proposal.metadata.request_id_understanding == "rid-42"
    assert proposal.metadata.understanding_latency_ms == 42.0
    assert proposal.lead_understanding is None  # rejeitado não vaza como understanding
    assert proposal.safety_signals is not None
    assert client.calls == ["lead_understanding", "safety_signals"]


@pytest.mark.asyncio
async def test_secondary_equals_undetermined_is_semantic_not_schema() -> None:
    bad = _understanding_payload(
        primary_area="undetermined",
        secondary_area="undetermined",
        subject="undetermined",
        subsubjects=[],
    )
    client = FakeClient([_completion(bad), _completion(_safety_payload())])
    with pytest.raises(AiRuntimeSemanticValidationError) as caught:
        await LeadUnderstandingService(client, Settings()).run(_request())
    assert caught.value.category == "semantic_validation"
    assert any(d["type"] == "secondary_area_equals_primary" for d in caught.value.details)
    assert caught.value.prompt_version == "lead_understanding.v9"
    assert caught.value.model == "fake-model"


def test_question_mark_check_is_mechanical_only() -> None:
    with pytest.raises(ValidationError):
        TriageNextStep.model_validate(
            {
                "schema_version": "triage_next_step.v1",
                "action": "ask_question",
                "priority": "normal",
                "missing_information": ["a", "b"],
                "selected_missing_information": ["a", "b"],
                "proposed_question": "Qual a data? E o regime?",
                "requires_human_handoff": False,
                "handoff_reason": None,
                "policy_flags": [],
            }
        )
