"""Regressões eval_expected.v5: associação de cues, preflight, críticas, dual-fail."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.application.services import TriagePipelineService
from app.clients.errors import (
    AiRuntimeSchemaValidationError,
    AiRuntimeSemanticValidationError,
)
from app.config import Settings
from app.policies.degraded_safety import DEGRADED_POLICY_VERSION
from evaluations.expectations import (
    ExpectationConfigError,
    resolve_active_expectation_slice,
    score_extractor_cues,
    score_risk_temporal,
    validate_expectation_config,
)
from evaluations.metrics import (
    NOT_APPLICABLE,
    NOT_EVALUATED,
    NOT_OBSERVED,
    compute_metrics,
    evaluate_targets,
)
from evaluations.runner import score_case
from evaluations.stages import stages_for_degraded_safety, stages_for_failure, stages_for_success
from scripts.generate_eval_cases import EXPECTATIONS_VERSION

from tests.helpers.safety import cue_present, occurrence, safety_v2
from tests.unit.test_degraded_safety import FakeClient, _compose_extractor, _request

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_DIR = ROOT / "evaluations" / "expected"


def test_suite_is_v5() -> None:
    assert EXPECTATIONS_VERSION == "eval_expected.v5"
    versions = {
        json.loads(p.read_text(encoding="utf-8")).get("expectations_version")
        for p in EXPECTED_DIR.glob("*.json")
    }
    assert versions == {"eval_expected.v5"}


def test_empty_related_list_is_not_absent_annotation() -> None:
    signals = safety_v2(
        occurrences=[
            occurrence(
                occurrence_id="dv1",
                risk="domestic_violence",
                quote="briga",
                temporal="historical",
            )
        ],
        urgent_help_request=cue_present(
            quote="ajuda urgente",
            summary="pedido",
            related=["dv1"],
        ),
    )
    match, details = score_extractor_cues(
        cue_spec={
            "urgent_help_request": "present",
            "urgent_help_related_risks": [],
            "urgent_help_forbidden_related_risks": ["domestic_violence"],
        },
        safety_signals=signals,
    )
    assert match is False
    assert details["urgent_help_related_risks"]["explicit_empty"] is True
    assert "urgent_help_related_risks" in details["failures"]
    assert "urgent_help_forbidden_related_risks" in details["failures"]


def test_correct_cue_wrong_risk_association_fails() -> None:
    signals = safety_v2(
        occurrences=[
            occurrence(
                occurrence_id="dv1",
                risk="domestic_violence",
                quote="briga",
                temporal="historical",
            )
        ],
        urgent_help_request=cue_present(
            quote="ajuda urgente com contrato",
            summary="pedido",
            related=["dv1"],
        ),
    )
    expected = json.loads((EXPECTED_DIR / "sf_urgent_other_topic.json").read_text())
    row = score_case(
        expected=expected,
        understanding=None,
        next_step={
            "action": "human_handoff",
            "requires_human_handoff": True,
            "handoff_reason": "sensitive_situation",
        },
        fail_closed=False,
        latency_ms=1.0,
        retry_count=0,
        stages=stages_for_degraded_safety("semantic_validation"),
        pipeline_status="degraded_safety",
        proposal={
            "safety_signals": signals,
            "effective_safety_signals": _compose_extractor(
                __import__(
                    "app.schemas.safety_signals", fromlist=["SafetySignalsV2"]
                ).SafetySignalsV2.model_validate(signals)
            ).model_dump(mode="json"),
        },
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
    assert row["extractor_cues_match"] is False
    assert row["handoff_match"] is False
    assert row["policy_rule_match"] is False
    assert "extractor_cues" in row["critical_failures"]
    assert "handoff" in row["critical_failures"]


def test_preflight_rejects_critical_without_annotation() -> None:
    with pytest.raises(ExpectationConfigError, match="extractor_cues"):
        validate_expectation_config(
            {
                "case_id": "bad",
                "critical_expectations": ["extractor_cues"],
                "acceptable_actions": ["human_handoff"],
            }
        )


def test_path_shared_fields_survive_resolution() -> None:
    expected = {
        "case_id": "x",
        "extractor_cues": {"urgent_help_request": "present"},
        "required_extractor_risks": ["domestic_violence"],
        "path_expectations": {
            "success": {"acceptable_policy_rules": ["imminent_deadline_requires_handoff"]},
            "degraded_safety": {
                "acceptable_policy_rules": ["degraded_safety_imminent_deadline_requires_handoff"],
            },
        },
    }
    success = resolve_active_expectation_slice(expected, "success")
    degraded = resolve_active_expectation_slice(expected, "degraded_safety")
    assert success["extractor_cues"] == {"urgent_help_request": "present"}
    assert degraded["extractor_cues"] == {"urgent_help_request": "present"}
    assert "extractor_cues" in success["shared_from_top"]
    assert success["required_extractor_risks"] == ["domestic_violence"]
    # Serialização do expected_active no score
    row = score_case(
        expected=expected,
        understanding={
            "intent": "new_legal_lead",
            "primary_area": "labor",
            "subject": "other",
            "case_facts": [],
            "safety": {"detected_risks": []},
        },
        next_step={"action": "human_handoff", "requires_human_handoff": True},
        fail_closed=False,
        latency_ms=1.0,
        retry_count=0,
        stages=stages_for_success(),
        pipeline_status="success",
        decision_provenance={
            "source": "deterministic_policy",
            "policy_version": "conservative_action.v7",
            "policy_rule_id": "imminent_deadline_requires_handoff",
            "policy_flags": [],
            "advisory_flags": [],
            "policy_status": "applied_mandatory",
            "model_inference_skipped": True,
        },
        proposal={"safety_signals": safety_v2()},
    )
    assert row["expected"]["extractor_cues"]["urgent_help_request"] == "present"
    assert row["expected_active"]["extractor_cues"]["urgent_help_request"] == "present"
    assert row["expected_active"]["path_branch"] == "success"


def test_live_undelivered_critical_blocks_approval() -> None:
    row = score_case(
        expected={
            "case_id": "ud",
            "acceptable_actions": ["human_handoff"],
            "handoff_required": True,
            "acceptable_policy_rules": ["imminent_deadline_requires_handoff"],
            "critical_expectations": ["handoff", "policy_rule"],
        },
        understanding=None,
        next_step=None,
        fail_closed=True,
        latency_ms=1.0,
        retry_count=0,
        stages=stages_for_failure("schema_validation", failure_stage="safety_signals"),
        pipeline_status="failed_closed",
        decision_provenance={
            "source": "deterministic_policy",
            "policy_version": DEGRADED_POLICY_VERSION,
            "policy_rule_id": None,
            "policy_flags": [],
            "advisory_flags": [],
            "policy_status": "not_reached",
            "model_inference_skipped": True,
        },
    )
    assert row["handoff_match"] == NOT_OBSERVED
    assert row["policy_rule_match"] == NOT_OBSERVED
    assert set(row["critical_undelivered"]) == {"handoff", "policy_rule"}
    metrics = compute_metrics([row], mode="live")
    checks = evaluate_targets(metrics, mode="live")
    assert checks["critical_expectations"] is False


def test_offline_critical_remains_not_evaluated() -> None:
    row = score_case(
        expected={
            "case_id": "off",
            "acceptable_actions": ["human_handoff"],
            "handoff_required": True,
            "acceptable_policy_rules": ["imminent_deadline_requires_handoff"],
            "critical_expectations": ["handoff", "policy_rule"],
        },
        understanding=None,
        next_step=None,
        fail_closed=False,
        latency_ms=None,
        retry_count=0,
        stages=stages_for_success(),
        pipeline_status="success",
    )
    metrics = compute_metrics([row], mode="offline")
    checks = evaluate_targets(metrics, mode="offline")
    assert checks.get("critical_expectations", NOT_EVALUATED) == NOT_EVALUATED


def test_missing_occurrence_not_labeled_wrong_temporal() -> None:
    match, details = score_risk_temporal(
        temporal_spec={
            "domestic_violence": ["unknown"],
            "violence_or_threat": ["unknown"],
        },
        safety_signals=safety_v2(
            occurrences=[
                occurrence(
                    occurrence_id="dv1",
                    risk="domestic_violence",
                    quote="violência",
                    temporal="unknown",
                )
            ]
        ),
    )
    assert match is False
    assert details["violence_or_threat"]["failure_kind"] == "missing_occurrence"
    assert details["domestic_violence"]["failure_kind"] is None
    assert "violence_or_threat" in details["missing_occurrences"]
    assert details["wrong_temporal"] == []


@pytest.mark.asyncio
async def test_dual_failure_preserves_separate_diagnostics() -> None:
    client = FakeClient(
        [
            AiRuntimeSemanticValidationError(
                "understanding falhou",
                details=[{"type": "taxonomy"}],
                rejected_payload={"intent": "x"},
            ),
            AiRuntimeSchemaValidationError(
                "safety inválido",
                details=[{"type": "explicit_cue_not_informed_must_be_empty"}],
                rejected_payload={
                    "schema_version": "safety_signals.v2",
                    "urgent_help_request": {
                        "state": "not_informed",
                        "related_occurrence_ids": ["x"],
                    },
                },
            ),
        ]
    )
    proposal = await TriagePipelineService(client, Settings()).run(_request())
    assert proposal.error is not None
    assert proposal.error.stage == "understanding"
    assert proposal.error.details is not None
    assert proposal.error.rejected_payload == {"intent": "x"}
    secondary = next(
        d["secondary_failure"] for d in proposal.error.details if "secondary_failure" in d
    )
    assert secondary["stage"] == "safety_signals"
    assert secondary["category"] == "schema_validation"
    assert secondary["details"] == [{"type": "explicit_cue_not_informed_must_be_empty"}]
    assert secondary["rejected_payload"]["schema_version"] == "safety_signals.v2"
    assert secondary["data_availability"]["details"] == "present"
    assert secondary["data_availability"]["rejected_payload"] == "present"


def test_internal_review_satisfies_handoff_required_false() -> None:
    row = score_case(
        expected={
            "case_id": "ir",
            "handoff_required": False,
            "acceptable_policy_rules": ["degraded_safety_internal_review"],
            "critical_expectations": ["handoff", "policy_rule"],
        },
        understanding=None,
        next_step=None,
        fail_closed=False,
        latency_ms=1.0,
        retry_count=0,
        stages=stages_for_degraded_safety("semantic_validation"),
        pipeline_status="degraded_safety",
        decision_provenance={
            "source": "deterministic_policy",
            "policy_version": DEGRADED_POLICY_VERSION,
            "policy_rule_id": "degraded_safety_internal_review",
            "policy_flags": [],
            "advisory_flags": [],
            "policy_status": "applied_mandatory",
            "model_inference_skipped": True,
        },
    )
    assert row["handoff_match"] is True
    assert row["policy_rule_match"] is True


def test_deadline_cues_apply_on_success_path() -> None:
    expected = json.loads((EXPECTED_DIR / "sf_deadline_plus_urgent_help.json").read_text())
    signals = safety_v2(
        occurrences=[
            occurrence(
                occurrence_id="d1",
                risk="imminent_deadline",
                quote="depois de amanhã",
                temporal="near_future",
            )
        ],
        urgent_help_request=cue_present(quote="ajuda urgente", summary="pedido"),
    )
    from app.schemas.safety_signals import SafetySignalsV2

    row = score_case(
        expected=expected,
        understanding={
            "intent": "new_legal_lead",
            "primary_area": "labor",
            "subject": "other",
            "case_facts": [],
            "safety": {"detected_risks": ["imminent_deadline"]},
        },
        next_step={"action": "human_handoff", "requires_human_handoff": True},
        fail_closed=False,
        latency_ms=1.0,
        retry_count=0,
        stages=stages_for_success(),
        pipeline_status="success",
        proposal={
            "safety_signals": signals,
            "effective_safety_signals": _compose_extractor(
                SafetySignalsV2.model_validate(signals)
            ).model_dump(mode="json"),
        },
        decision_provenance={
            "source": "deterministic_policy",
            "policy_version": "conservative_action.v7",
            "policy_rule_id": "imminent_deadline_requires_handoff",
            "policy_flags": [],
            "advisory_flags": [],
            "policy_status": "applied_mandatory",
            "model_inference_skipped": True,
        },
    )
    assert row["path_expectation_branch"] == "success"
    assert row["extractor_cues_match"] is True
    assert row["extractor_cues_match"] is not NOT_APPLICABLE
