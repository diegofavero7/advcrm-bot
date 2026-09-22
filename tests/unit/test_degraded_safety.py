"""Caminho degradado v2: understanding inválido + safety_signals.v2 tipado."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from app.application.degraded_path import (
    UNDERSTANDING_FAILURES_ALLOWING_SAFETY,
    UNDERSTANDING_FAILURES_BLOCKING_SAFETY,
    allows_safety_after_understanding_failure,
)
from app.application.services import TriagePipelineService
from app.clients.errors import (
    AiRuntimeContractVersionError,
    AiRuntimeSchemaValidationError,
    AiRuntimeSemanticValidationError,
    AiRuntimeServerError,
    AiRuntimeTimeoutError,
)
from app.clients.types import StructuredCompletionResult
from app.config import Settings
from app.domain.enums import (
    ContentType,
    HandoffReason,
    MessageDirection,
    MessageRole,
    Priority,
    RiskFlag,
    TriageAction,
    TriageState,
)
from app.policies.degraded_safety import (
    DEGRADED_POLICY_VERSION,
    ExtractorOnlySafety,
    decide_degraded_safety_action,
)
from app.safety.compose import compose_effective_safety_signals
from app.schemas.inbound import ConversationMessage, TriageAnalysisRequest
from app.schemas.proposal import ProposalStatus
from app.schemas.safety_signals import SafetySignals, SafetySignalsV2
from evaluations.runner import score_case
from evaluations.stages import stages_for_degraded_safety

from tests.helpers.safety import cue_present, occurrence, safety_v1, safety_v2


def _request(*, text: str = "Relato do lead com agressão e pedido") -> TriageAnalysisRequest:
    return TriageAnalysisRequest(
        event_id="e-degraded",
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
                text=text,
                created_at=datetime(2026, 9, 20, 14, 0, tzinfo=UTC),
                reply_to_message_id=None,
            )
        ],
        known_facts=[],
        previous_decision=None,
    )


def _understanding_ok() -> dict[str, Any]:
    return {
        "schema_version": "lead_understanding.v1",
        "intent": "new_legal_lead",
        "language": "pt-BR",
        "primary_area": "social_security",
        "secondary_area": None,
        "subject": "prison_allowance",
        "subsubjects": ["spouse_relationship"],
        "confidence": 0.9,
        "reasoning_summary": "Relata prisão do cônjuge",
        "participants": [{"role": "contact_person", "relationship": "spouse"}],
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
            "existing_case": False,
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
            "reason": "prisão sem risco adicional",
            "detected_risks": ["arrest_or_detention"],
            "recommend_handoff": False,
        },
    }


def _understanding_area_subject_mismatch() -> dict[str, Any]:
    payload = _understanding_ok()
    payload["primary_area"] = "criminal"
    payload["subject"] = "domestic_violence"
    payload["subsubjects"] = []
    payload["urgency"] = "high"
    payload["safety"] = {
        "level": "high",
        "reason": "violência doméstica (payload rejeitado — não usar)",
        "detected_risks": ["domestic_violence", "violence_or_threat"],
        "recommend_handoff": True,
    }
    payload["case_facts"] = []
    payload["participants"] = []
    return payload


def _next_step() -> dict[str, Any]:
    return {
        "schema_version": "triage_next_step.v1",
        "action": "ask_question",
        "priority": "normal",
        "missing_information": ["approximate_prison_date"],
        "selected_missing_information": ["approximate_prison_date"],
        "proposed_question": "Em que período aproximado ocorreu a prisão?",
        "requires_human_handoff": False,
        "handoff_reason": None,
        "policy_flags": [],
    }


def _completion(payload: dict[str, Any], *, request_id: str = "r1") -> StructuredCompletionResult:
    return StructuredCompletionResult(
        parsed_content=payload,
        model="fake",
        finish_reason="stop",
        latency_ms=10.0,
        retry_count=0,
        request_id=request_id,
        usage=None,
    )


class FakeClient:
    def __init__(self, responses: list[StructuredCompletionResult | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []
        self.contexts: list[str] = []

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
        for msg in messages:
            if msg.get("role") == "user":
                self.contexts.append(msg.get("content") or "")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _compose_extractor(signals: SafetySignals | SafetySignalsV2):
    return compose_effective_safety_signals(
        model_detected=[],
        taxonomy_derived=[],
        extractor=signals,
        allowed_message_ids=frozenset({"m1"}),
    )


def test_failure_categories_partition() -> None:
    assert UNDERSTANDING_FAILURES_ALLOWING_SAFETY.isdisjoint(UNDERSTANDING_FAILURES_BLOCKING_SAFETY)
    for cat in ("schema_validation", "structured_validation", "semantic_validation"):
        assert allows_safety_after_understanding_failure(cat)
    for cat in (
        "connection",
        "timeout",
        "authentication",
        "server",
        "protocol",
        "context_limit",
        "contract_version",
    ):
        assert not allows_safety_after_understanding_failure(cat)


@pytest.mark.asyncio
async def test_context_limit_and_contract_version_do_not_call_safety() -> None:
    for err in (
        AiRuntimeContractVersionError("versão"),
        # context_limit via category attribute on base-like path: use Semantic? use Protocol?
    ):
        client = FakeClient([err, _completion(safety_v2())])
        proposal = await TriagePipelineService(client, Settings()).run(_request())
        assert proposal.status == ProposalStatus.FAILED_CLOSED
        assert client.calls == ["lead_understanding"]
        assert len(client.responses) == 1

    from app.clients.errors import ContextLimitExceededError

    client = FakeClient([ContextLimitExceededError("limite"), _completion(safety_v2())])
    proposal = await TriagePipelineService(client, Settings()).run(_request())
    assert proposal.status == ProposalStatus.FAILED_CLOSED
    assert "safety_signals" not in client.calls


@pytest.mark.asyncio
async def test_valid_understanding_runs_safety_exactly_once() -> None:
    client = FakeClient(
        [_completion(_understanding_ok()), _completion(safety_v2()), _completion(_next_step())]
    )
    proposal = await TriagePipelineService(client, Settings()).run(_request())
    assert proposal.status == ProposalStatus.SUCCESS
    assert client.calls.count("safety_signals") == 1


@pytest.mark.asyncio
async def test_area_subject_mismatch_still_runs_safety_once() -> None:
    text = "Sofri agressões do companheiro em casa"
    client = FakeClient(
        [
            _completion(_understanding_area_subject_mismatch()),
            _completion(
                safety_v2(
                    occurrences=[
                        occurrence(
                            occurrence_id="dv1",
                            risk="domestic_violence",
                            quote="agressões",
                            temporal="ongoing",
                        ),
                        occurrence(
                            occurrence_id="vt1",
                            risk="violence_or_threat",
                            quote="agressões",
                            temporal="ongoing",
                        ),
                    ]
                )
            ),
        ]
    )
    proposal = await TriagePipelineService(client, Settings()).run(_request(text=text))
    assert proposal.status == ProposalStatus.DEGRADED_SAFETY
    assert client.calls == ["lead_understanding", "safety_signals"]
    assert proposal.triage_next_step is None
    assert proposal.safe_fallback is not None
    assert proposal.effective_safety_signals is not None
    assert proposal.effective_safety_signals.model_detected == []


@pytest.mark.asyncio
async def test_rejected_payload_never_feeds_safety_context() -> None:
    err = AiRuntimeSchemaValidationError(
        "fora do schema",
        details=[{"loc": ["intent"], "type": "enum"}],
        rejected_payload={
            "primary_area": "criminal",
            "subject": "domestic_violence",
            "urgency": "immediate",
            "safety": {"recommend_handoff": True, "detected_risks": ["self_harm"]},
        },
    )
    client = FakeClient([err, _completion(safety_v2())])
    proposal = await TriagePipelineService(client, Settings()).run(
        _request(text="texto autorizado do lead")
    )
    assert proposal.status == ProposalStatus.DEGRADED_SAFETY
    safety_ctx = client.contexts[1]
    assert "texto autorizado do lead" in safety_ctx
    assert "recommend_handoff" not in safety_ctx
    assert "primary_area" not in safety_ctx


@pytest.mark.asyncio
async def test_taxonomy_model_only_composition_rejected_for_degraded_policy() -> None:
    signals = SafetySignalsV2.model_validate(safety_v2())
    bad = compose_effective_safety_signals(
        model_detected=[RiskFlag.SELF_HARM],
        taxonomy_derived=[RiskFlag.DOMESTIC_VIOLENCE],
        extractor=signals,
        allowed_message_ids=frozenset({"m1"}),
    )
    with pytest.raises(ValueError, match=r"model_detected|taxonomy"):
        ExtractorOnlySafety.from_validated(signals=signals, effective=bad)


@pytest.mark.asyncio
async def test_dv_plus_urgent_help_sensitive_situation_high() -> None:
    text = "Sofri agressões do companheiro e preciso de ajuda urgente com isso"
    client = FakeClient(
        [
            AiRuntimeSemanticValidationError(
                "assunto incoerente",
                details=[{"type": "subject_not_in_primary_area"}],
            ),
            _completion(
                safety_v2(
                    occurrences=[
                        occurrence(
                            occurrence_id="dv1",
                            risk="domestic_violence",
                            quote="agressões",
                            temporal="ongoing",
                        )
                    ],
                    urgent_help_request=cue_present(
                        quote="ajuda urgente",
                        summary="pedido urgente de ajuda",
                        related=["dv1"],
                    ),
                )
            ),
        ]
    )
    proposal = await TriagePipelineService(client, Settings()).run(_request(text=text))
    assert proposal.status == ProposalStatus.DEGRADED_SAFETY
    assert proposal.triage_next_step is not None
    assert proposal.triage_next_step.action == TriageAction.HUMAN_HANDOFF
    assert proposal.triage_next_step.handoff_reason == HandoffReason.SENSITIVE_SITUATION
    assert proposal.triage_next_step.priority == Priority.HIGH
    assert proposal.decision_provenance is not None
    assert proposal.decision_provenance.policy_version == DEGRADED_POLICY_VERSION
    assert "dv1" in proposal.decision_provenance.supporting_signal_refs


@pytest.mark.asyncio
async def test_dv_plus_violence_without_urgent_help_is_internal_review() -> None:
    text = "Sofri agressões do companheiro em casa"
    client = FakeClient(
        [
            _completion(_understanding_area_subject_mismatch()),
            _completion(
                safety_v2(
                    occurrences=[
                        occurrence(
                            occurrence_id="dv1",
                            risk="domestic_violence",
                            quote="agressões",
                            temporal="ongoing",
                        ),
                        occurrence(
                            occurrence_id="vt1",
                            risk="violence_or_threat",
                            quote="agressões",
                            temporal="ongoing",
                        ),
                    ]
                )
            ),
        ]
    )
    proposal = await TriagePipelineService(client, Settings()).run(_request(text=text))
    assert proposal.triage_next_step is None
    assert proposal.safe_fallback is not None


@pytest.mark.asyncio
async def test_historical_self_harm_denied_hypothetical_no_immediate_risk() -> None:
    text = "Já pensei em me machucar no passado, mas não agora. E se eu me machucasse?"
    for assertion, temporal, oid in (
        ("affirmed", "historical", "sh1"),
        ("denied", "unknown", "sh2"),
        ("hypothetical", "unknown", "sh3"),
    ):
        client = FakeClient(
            [
                AiRuntimeSemanticValidationError("x", details=[{"type": "y"}]),
                _completion(
                    safety_v2(
                        occurrences=[
                            occurrence(
                                occurrence_id=oid,
                                risk="self_harm",
                                quote="machucar",
                                assertion=assertion,
                                temporal=temporal,
                            )
                        ]
                    )
                ),
            ]
        )
        # Adjust quote for denied case — use substring present in text
        if assertion == "denied":
            # Still need a quote in source even for denied reports
            pass
        proposal = await TriagePipelineService(client, Settings()).run(_request(text=text))
        assert proposal.triage_next_step is None, assertion
        assert proposal.safe_fallback is not None


@pytest.mark.asyncio
async def test_immediate_danger_sustained_is_critical() -> None:
    text = "Ele me agride e estou em perigo agora"
    client = FakeClient(
        [
            AiRuntimeSemanticValidationError("x", details=[{"type": "y"}]),
            _completion(
                safety_v2(
                    occurrences=[
                        occurrence(
                            occurrence_id="vt1",
                            risk="violence_or_threat",
                            quote="agride",
                            temporal="ongoing",
                        )
                    ],
                    immediate_danger=cue_present(
                        quote="perigo agora",
                        summary="perigo imediato",
                        related=["vt1"],
                    ),
                )
            ),
        ]
    )
    proposal = await TriagePipelineService(client, Settings()).run(_request(text=text))
    assert proposal.triage_next_step is not None
    assert proposal.triage_next_step.handoff_reason == HandoffReason.IMMEDIATE_RISK
    assert proposal.triage_next_step.priority == Priority.CRITICAL


@pytest.mark.asyncio
async def test_deadline_and_immediate_danger_precedence() -> None:
    text = "Tenho audiência amanhã e estou em perigo agora por agressão"
    client = FakeClient(
        [
            AiRuntimeSemanticValidationError("x", details=[{"type": "y"}]),
            _completion(
                safety_v2(
                    occurrences=[
                        occurrence(
                            occurrence_id="dl1",
                            risk="imminent_deadline",
                            quote="audiência amanhã",
                            temporal="near_future",
                        ),
                        occurrence(
                            occurrence_id="vt1",
                            risk="violence_or_threat",
                            quote="agressão",
                            temporal="ongoing",
                        ),
                    ],
                    immediate_danger=cue_present(
                        quote="perigo agora",
                        summary="perigo",
                        related=["vt1"],
                    ),
                )
            ),
        ]
    )
    proposal = await TriagePipelineService(client, Settings()).run(_request(text=text))
    assert proposal.triage_next_step is not None
    assert proposal.triage_next_step.handoff_reason == HandoffReason.IMMEDIATE_RISK
    assert "audit_immediate_danger_present" in (
        proposal.decision_provenance.policy_flags if proposal.decision_provenance else []
    )


@pytest.mark.asyncio
async def test_urgent_help_on_other_topic_does_not_promote_dv() -> None:
    text = "Já sofri violência no passado. Preciso de ajuda urgente com uma dívida no banco"
    client = FakeClient(
        [
            AiRuntimeSemanticValidationError("x", details=[{"type": "y"}]),
            _completion(
                safety_v2(
                    occurrences=[
                        occurrence(
                            occurrence_id="dv1",
                            risk="domestic_violence",
                            quote="violência",
                            temporal="historical",
                        ),
                        occurrence(
                            occurrence_id="fr1",
                            risk="fraud_or_scam",
                            quote="dívida",
                            temporal="ongoing",
                        ),
                    ],
                    urgent_help_request=cue_present(
                        quote="ajuda urgente",
                        summary="ajuda com dívida",
                        related=["fr1"],  # não relaciona DV
                    ),
                )
            ),
        ]
    )
    proposal = await TriagePipelineService(client, Settings()).run(_request(text=text))
    assert proposal.triage_next_step is None
    assert proposal.safe_fallback is not None


@pytest.mark.asyncio
async def test_evidence_quote_missing_rejected() -> None:
    text = "Sofri agressões em casa"
    client = FakeClient(
        [
            AiRuntimeSemanticValidationError("x", details=[{"type": "y"}]),
            _completion(
                safety_v2(
                    occurrences=[
                        occurrence(
                            occurrence_id="dv1",
                            risk="domestic_violence",
                            quote="trecho inexistente xyz",
                            temporal="ongoing",
                        )
                    ]
                )
            ),
        ]
    )
    proposal = await TriagePipelineService(client, Settings()).run(_request(text=text))
    assert proposal.status == ProposalStatus.FAILED_CLOSED
    assert proposal.safety_signals is None
    assert any("secondary_failure" in d for d in (proposal.error.details or []))


@pytest.mark.asyncio
async def test_invalid_message_id_no_promotion() -> None:
    client = FakeClient(
        [
            AiRuntimeSemanticValidationError("x", details=[{"type": "y"}]),
            _completion(
                safety_v2(
                    occurrences=[
                        occurrence(
                            occurrence_id="o1",
                            risk="imminent_deadline",
                            quote="Relato",
                            temporal="near_future",
                            msg_ids=["ghost"],
                        )
                    ]
                )
            ),
        ]
    )
    proposal = await TriagePipelineService(client, Settings()).run(_request())
    assert proposal.status == ProposalStatus.FAILED_CLOSED
    assert proposal.triage_next_step is None


@pytest.mark.asyncio
async def test_safety_transport_preserves_both_diagnostics() -> None:
    client = FakeClient(
        [
            AiRuntimeSemanticValidationError("understanding falhou", details=[{"type": "x"}]),
            AiRuntimeTimeoutError("timeout no extrator"),
        ]
    )
    proposal = await TriagePipelineService(client, Settings()).run(_request())
    assert proposal.status == ProposalStatus.FAILED_CLOSED
    secondary = next(
        d["secondary_failure"] for d in (proposal.error.details or []) if "secondary_failure" in d
    )
    assert secondary["category"] == "timeout"


@pytest.mark.asyncio
async def test_no_next_step_when_understanding_invalid() -> None:
    text = "Estou em perigo agora por agressão"
    client = FakeClient(
        [
            AiRuntimeSemanticValidationError("x", details=[{"type": "y"}]),
            _completion(
                safety_v2(
                    occurrences=[
                        occurrence(
                            occurrence_id="vt1",
                            risk="violence_or_threat",
                            quote="agressão",
                            temporal="ongoing",
                        )
                    ],
                    immediate_danger=cue_present(
                        quote="perigo agora",
                        summary="perigo",
                        related=["vt1"],
                    ),
                )
            ),
            _completion(_next_step()),
        ]
    )
    await TriagePipelineService(client, Settings()).run(_request(text=text))
    assert "triage_next_step" not in client.calls
    assert len(client.responses) == 1


@pytest.mark.asyncio
async def test_transport_failure_does_not_call_safety() -> None:
    client = FakeClient([AiRuntimeServerError("down"), _completion(safety_v2())])
    proposal = await TriagePipelineService(client, Settings()).run(_request())
    assert client.calls == ["lead_understanding"]
    assert proposal.safety_signals is None


@pytest.mark.asyncio
async def test_v1_contract_does_not_get_synthetic_temporality() -> None:
    """Contrato v1 ativo: sem inventar cues; revisão interna mesmo com self_harm."""
    settings = Settings(ai_safety_signals_schema_version="safety_signals.v1")
    client = FakeClient(
        [
            AiRuntimeSemanticValidationError("x", details=[{"type": "y"}]),
            _completion(
                safety_v1(
                    detected_risks=[
                        {
                            "risk": "self_harm",
                            "source_message_ids": ["m1"],
                            "evidence_summary": "autoagressão",
                        }
                    ]
                )
            ),
        ]
    )
    proposal = await TriagePipelineService(client, settings).run(_request())
    assert proposal.status == ProposalStatus.DEGRADED_SAFETY
    assert proposal.triage_next_step is None
    assert proposal.decision_provenance is not None
    assert (
        proposal.decision_provenance.policy_rule_id
        == "degraded_safety_v1_insufficient_representation"
    )


@pytest.mark.asyncio
async def test_understanding_only_comparator_stays_isolated() -> None:
    from evaluations.compare.runner import (
        RecordingClient,
        SyntheticResponse,
        run_understanding_variant,
    )

    request = _request().model_dump(mode="json")
    pairs = [
        (
            {"case_id": "iso", "request": request},
            {
                "case_id": "iso",
                "expectations_version": "eval_expected.v3",
                "acceptable_intents": ["new_legal_lead"],
            },
        )
    ]
    client = RecordingClient(
        [
            SyntheticResponse(parsed_content=_understanding_ok()),
            SyntheticResponse(parsed_content=safety_v2()),
        ]
    )
    results, _manifest, recorder = await run_understanding_variant(
        variant_id="baseline",
        pairs=pairs,
        client=client,
        settings=Settings(),
        mode="offline",
        requested_model=None,
        runtime_base_url=None,
    )
    assert recorder is not None
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["contract_label"] == "lead_understanding"
    assert results[0]["pipeline_status"] == "understanding_ok"


def test_metrics_do_not_treat_degraded_as_full_success() -> None:
    proposal = {
        "effective_safety_signals": {
            "model_detected": [],
            "taxonomy_derived": [],
            "extractor_detected": ["domestic_violence"],
            "candidate": [],
            "effective": [
                {
                    "risk": "domestic_violence",
                    "sources": ["extractor_detected"],
                    "evidence_summary": "agressão",
                    "source_message_ids": ["m1"],
                    "promotion_reason": "extractor_confirmed",
                }
            ],
        }
    }
    row = score_case(
        expected={
            "case_id": "deg",
            "acceptable_intents": ["new_legal_lead"],
            "required_risks": ["domestic_violence"],
            "handoff_required": True,
            "acceptable_actions": ["human_handoff"],
        },
        understanding=None,
        next_step={
            "action": "human_handoff",
            "requires_human_handoff": True,
            "handoff_reason": "sensitive_situation",
        },
        fail_closed=False,
        latency_ms=100.0,
        retry_count=0,
        stages=stages_for_degraded_safety("semantic_validation"),
        pipeline_status="degraded_safety",
        error={"category": "semantic_validation", "stage": "understanding", "message": "x"},
        safe_fallback=None,
        metadata={"policy_version": DEGRADED_POLICY_VERSION},
        proposal=proposal,
        decision_provenance={
            "source": "deterministic_policy",
            "policy_version": DEGRADED_POLICY_VERSION,
            "policy_rule_id": "degraded_safety_domestic_violence_urgent_help_requires_handoff",
            "policy_flags": [],
            "advisory_flags": [],
            "policy_status": "applied_mandatory",
            "model_inference_skipped": True,
        },
    )
    assert row["pipeline_status"] == "degraded_safety"
    assert row["fail_closed"] is False
    assert row["intent_match"] == "not_observed"
    assert row["model_risks_match"] == "not_observed"
    assert row["effective_risks_match"] is True
    assert row["handoff_match"] is True


def test_degraded_policy_unit_table() -> None:
    empty = SafetySignalsV2.model_validate(safety_v2())
    decision = decide_degraded_safety_action(
        ExtractorOnlySafety.from_validated(signals=empty, effective=_compose_extractor(empty))
    )
    assert decision.requires_internal_review is True

    # v1 payload rejected by ExtractorOnlySafety
    v1 = SafetySignals.model_validate(safety_v1())
    with pytest.raises(ValueError, match=r"safety_signals\.v2"):
        ExtractorOnlySafety.from_validated(signals=v1, effective=_compose_extractor(v1))
