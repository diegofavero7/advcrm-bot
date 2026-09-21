"""Testes de enums e taxonomia."""

from __future__ import annotations

import pytest
from app.domain.enums import (
    NON_PRIORITY_AREAS,
    PRIORITY_AREAS,
    Intent,
    LegalArea,
)
from app.taxonomy import get_taxonomy


def test_all_legal_areas_present_in_taxonomy() -> None:
    taxonomy = get_taxonomy()
    for area in LegalArea:
        assert area.value in taxonomy.areas
        subjects = taxonomy.subjects_for(area)
        assert "other" in subjects
        assert "undetermined" in subjects


def test_priority_and_non_priority_partition() -> None:
    assert PRIORITY_AREAS.isdisjoint(NON_PRIORITY_AREAS)
    assert set(LegalArea) == PRIORITY_AREAS | NON_PRIORITY_AREAS


def test_disambiguated_subject_names() -> None:
    tax = get_taxonomy()
    assert "permanent_disability_benefit" in tax.subjects_for(LegalArea.SOCIAL_SECURITY)
    assert "disability_benefit" not in tax.subjects_for(LegalArea.SOCIAL_SECURITY)
    assert "dismissal_without_just_cause" in tax.subjects_for(LegalArea.LABOR)
    assert "unfair_dismissal" not in tax.subjects_for(LegalArea.LABOR)
    assert "child_support" in tax.subjects_for(LegalArea.FAMILY)
    assert "spousal_support" in tax.subjects_for(LegalArea.FAMILY)
    assert "alimony" not in tax.subjects_for(LegalArea.FAMILY)


def test_subsubjects_only_for_two_detailed_subjects() -> None:
    tax = get_taxonomy()
    prison = tax.subsubjects_for(LegalArea.SOCIAL_SECURITY, "prison_allowance")
    vehicle = tax.subsubjects_for(LegalArea.CONSUMER, "vehicle_purchase_irregularities")
    assert "spouse_relationship" in prison
    assert "auction_history" in vehicle
    # Outros assuntos sem catálogo detalhado
    assert tax.subsubjects_for(LegalArea.LABOR, "unpaid_severance") == frozenset()


@pytest.mark.parametrize("intent", list(Intent))
def test_intents_are_closed(intent: Intent) -> None:
    assert intent.value


def test_validate_subsubjects_helpers() -> None:
    from app.taxonomy import clear_taxonomy_cache, validate_subsubjects

    clear_taxonomy_cache()
    assert validate_subsubjects(
        LegalArea.SOCIAL_SECURITY, "prison_allowance", ["spouse_relationship"]
    )
    assert not validate_subsubjects(
        LegalArea.SOCIAL_SECURITY, "prison_allowance", ["not_a_real_sub"]
    )
    assert validate_subsubjects(LegalArea.LABOR, "unpaid_severance", ["other"])
    assert not validate_subsubjects(LegalArea.LABOR, "unpaid_severance", ["weird"])
