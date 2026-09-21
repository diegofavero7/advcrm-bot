"""Testes de contratos: extra=forbid, invariantes, exemplos."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.schemas.inbound import TriageAnalysisRequest
from app.schemas.lead_understanding import LeadUnderstanding
from app.schemas.triage_next_step import TriageNextStep
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
VALID = ROOT / "examples" / "valid"
INVALID = ROOT / "examples" / "invalid"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_extra_property_forbidden_on_request() -> None:
    raw = _load(INVALID / "extra_property_request.json")
    with pytest.raises(ValidationError):
        TriageAnalysisRequest.model_validate(raw)


def test_empty_messages_rejected() -> None:
    with pytest.raises(ValidationError):
        TriageAnalysisRequest.model_validate(_load(INVALID / "empty_messages.json"))


def test_duplicate_message_ids() -> None:
    with pytest.raises(ValidationError):
        TriageAnalysisRequest.model_validate(_load(INVALID / "duplicate_message_ids.json"))


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValidationError):
        TriageAnalysisRequest.model_validate(_load(INVALID / "naive_datetime.json"))


def test_text_requires_body() -> None:
    with pytest.raises(ValidationError):
        TriageAnalysisRequest.model_validate(_load(INVALID / "text_without_body.json"))


def test_invalid_reply_to() -> None:
    with pytest.raises(ValidationError):
        TriageAnalysisRequest.model_validate(_load(INVALID / "invalid_reply_to.json"))


def test_secondary_equals_primary_rejected() -> None:
    with pytest.raises(ValidationError):
        LeadUnderstanding.model_validate(_load(INVALID / "secondary_equals_primary.json"))


def test_ask_question_requires_text() -> None:
    with pytest.raises(ValidationError):
        TriageNextStep.model_validate(_load(INVALID / "ask_question_without_text.json"))


def test_handoff_requires_reason() -> None:
    with pytest.raises(ValidationError):
        TriageNextStep.model_validate(_load(INVALID / "handoff_without_reason.json"))


def test_subject_area_mismatch() -> None:
    with pytest.raises(ValidationError):
        LeadUnderstanding.model_validate(_load(INVALID / "subject_area_mismatch.json"))


def test_merit_policy_flag_rejected() -> None:
    with pytest.raises(ValidationError):
        TriageNextStep.model_validate(_load(INVALID / "merit_policy_flag.json"))


def test_valid_examples_load() -> None:
    for path in sorted(VALID.glob("*.json")):
        data = _load(path)
        if "request" in data:
            req = TriageAnalysisRequest.model_validate(data["request"])
            assert len(req.messages) >= 1
            # Fragments keep separate IDs
            if path.name.startswith("03_"):
                assert [m.message_id for m in req.messages] == ["m1", "m2"]
        if "lead_understanding" in data:
            LeadUnderstanding.model_validate(data["lead_understanding"])
        if "triage_next_step" in data:
            TriageNextStep.model_validate(data["triage_next_step"])


def test_system_messages_not_lead_declarations() -> None:
    req = TriageAnalysisRequest.model_validate(
        {
            "event_id": "e",
            "tenant_id": "t",
            "lead_id": "l",
            "conversation_id": "c",
            "triage_state": "collecting_messages",
            "source": "system",
            "messages": [
                {
                    "message_id": "s1",
                    "role": "system",
                    "direction": "outbound",
                    "content_type": "text",
                    "text": "auto-ack",
                    "created_at": "2026-09-20T14:00:00-03:00",
                    "reply_to_message_id": None,
                },
                {
                    "message_id": "m1",
                    "role": "lead",
                    "direction": "inbound",
                    "content_type": "text",
                    "text": "preciso de ajuda com INSS",
                    "created_at": "2026-09-20T14:01:00-03:00",
                    "reply_to_message_id": None,
                },
            ],
            "known_facts": [],
            "previous_decision": None,
        }
    )
    assert req.lead_declaration_message_ids() == ["m1"]
