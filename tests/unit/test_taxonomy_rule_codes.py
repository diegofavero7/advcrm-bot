"""Códigos estáveis das regras de taxonomia em LeadUnderstanding."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from app.clients.validation_diagnostics import sanitize_validation_error
from app.schemas import build_strict_json_schema
from app.schemas.lead_understanding import LeadUnderstanding
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "artifacts" / "schemas" / "lead_understanding.v1.json"

_SECRET_SUBJECT = "assunto-secreto-do-modelo-XYZ"
_SECRET_SUB = "subassunto-secreto-ABC"


def _base_payload(**overrides: object) -> dict[str, Any]:
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
    base.update(overrides)
    return base


def _assert_code(payload: dict[str, Any], code: str, *forbidden: str) -> None:
    with pytest.raises(ValidationError) as caught:
        LeadUnderstanding.model_validate(payload)
    details = sanitize_validation_error(caught.value)
    assert details
    assert any(item["type"] == code for item in details)
    for item in details:
        assert set(item.keys()) == {"loc", "type"}
        assert "msg" not in item
        assert "input" not in item
        assert "ctx" not in item
    blob = str(details) + caught.value.__class__.__name__
    # Não vazar valores de entrada no diagnóstico sanitizado.
    for secret in forbidden:
        assert secret not in blob
        for item in details:
            assert secret not in str(item["loc"])
            assert secret not in str(item["type"])
    # str(exc) completo do Pydantic pode conter input — CLI usa só details.
    diag_only = str(details)
    for secret in forbidden:
        assert secret not in diag_only


def test_secondary_area_equals_primary_code() -> None:
    _assert_code(
        _base_payload(secondary_area="social_security"),
        "secondary_area_equals_primary",
    )


def test_subject_not_in_primary_area_code() -> None:
    _assert_code(
        _base_payload(subject=_SECRET_SUBJECT, subsubjects=[]),
        "subject_not_in_primary_area",
        _SECRET_SUBJECT,
    )


def test_subsubject_not_in_catalog_code() -> None:
    _assert_code(
        _base_payload(subsubjects=[_SECRET_SUB]),
        "subsubject_not_in_catalog",
        _SECRET_SUB,
    )


def test_subsubject_without_catalog_code() -> None:
    # civil/debt_collection tem catálogo de subassuntos vazio.
    _assert_code(
        _base_payload(
            primary_area="civil",
            subject="debt_collection",
            subsubjects=[_SECRET_SUB],
            reasoning_summary="Relata cobrança indevida",
        ),
        "subsubject_without_catalog",
        _SECRET_SUB,
    )


def test_subject_of_another_demand_is_not_accepted_as_subsubject() -> None:
    """Regressão `competing_areas`: subject previdenciário não vira subassunto trabalhista.

    labor/dismissal_without_just_cause não tem catálogo de subassuntos; o payload é
    rejeitado fail-closed, sem apagar o subassunto inválido nem fabricar catálogo.
    """
    _assert_code(
        _base_payload(
            primary_area="labor",
            subject="dismissal_without_just_cause",
            secondary_area="social_security",
            subsubjects=["benefit_review"],
            reasoning_summary="Relata demissão e revisão de benefício",
        ),
        "subsubject_without_catalog",
    )


def test_empty_subsubjects_is_valid_for_multiple_demands() -> None:
    understanding = LeadUnderstanding.model_validate(
        _base_payload(
            primary_area="labor",
            subject="other",
            secondary_area="social_security",
            subsubjects=[],
            reasoning_summary="Relata demissão sem modalidade e revisão de benefício",
        )
    )
    assert understanding.subsubjects == []
    assert understanding.secondary_area is not None


def test_exported_schema_unchanged_by_custom_errors() -> None:
    generated = build_strict_json_schema(LeadUnderstanding)
    artifact = ARTIFACT.read_bytes()
    gen_bytes = (
        __import__("json").dumps(generated, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    assert hashlib.sha256(gen_bytes).hexdigest() == hashlib.sha256(artifact).hexdigest()
    text = artifact.decode("utf-8")
    for code in (
        "secondary_area_equals_primary",
        "subject_not_in_primary_area",
        "subsubject_not_in_catalog",
        "subsubject_without_catalog",
    ):
        assert code not in text
