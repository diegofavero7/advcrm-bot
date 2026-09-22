"""Fidelidade de fatos: placeholders técnicos e vocabulário canônico."""

from __future__ import annotations

import pytest
from app.domain.enums import FactCertainty
from app.domain.fact_vocabulary import (
    EXPLICIT_HUMAN_REQUEST,
    LEGAL_REQUEST_SUBJECT,
    RELATIONSHIP_TO_DETAINEE,
    TECHNICAL_PLACEHOLDER_VALUES,
)
from app.prompts.loader import (
    clear_prompt_cache,
    load_lead_understanding_prompt,
    load_safety_signals_prompt,
)
from app.schemas.lead_understanding import CaseFact
from pydantic import ValidationError


@pytest.mark.parametrize("placeholder", sorted(TECHNICAL_PLACEHOLDER_VALUES))
def test_technical_placeholders_rejected(placeholder: str) -> None:
    with pytest.raises(ValidationError) as exc:
        CaseFact(
            key="any_key",
            value=placeholder,
            certainty=FactCertainty.EXPLICIT,
            source_message_ids=["m1"],
        )
    assert "case_fact_placeholder_value" in str(exc.value)


def test_unknown_substring_in_real_value_allowed() -> None:
    fact = CaseFact(
        key="note",
        value="unknown relative living abroad",
        certainty=FactCertainty.EXPLICIT,
        source_message_ids=["m1"],
    )
    assert "unknown" in fact.value


def test_lead_untrusted_data_rejected() -> None:
    with pytest.raises(ValidationError):
        CaseFact(
            key="debt_recognition",
            value="lead_untrusted_data",
            certainty=FactCertainty.EXPLICIT,
            source_message_ids=["m1"],
        )


def test_canonical_keys_documented() -> None:
    assert EXPLICIT_HUMAN_REQUEST == "explicit_human_request"
    assert RELATIONSHIP_TO_DETAINEE == "relationship_to_detainee"
    assert LEGAL_REQUEST_SUBJECT == "legal_request_subject"


def test_prompt_versions_bumped() -> None:
    clear_prompt_cache()
    u = load_lead_understanding_prompt()
    s = load_safety_signals_prompt()
    assert u.version == "lead_understanding.v9"
    assert s.version == "safety_signals.v4"
    assert "explicit_human_request" in u.text
    assert "legal_request_subject" in u.text
    assert "lead_untrusted_data" in u.text
    assert "occurrences" in s.text or "urgent_help_request" in s.text
    # Contrato ativo v2 — prompt v4.
    assert "safety_signals.v2" in s.text
    legacy = load_safety_signals_prompt("safety_signals.v1")
    assert legacy.version == "safety_signals.v2"
    assert "safety_signals.v1" in legacy.text
