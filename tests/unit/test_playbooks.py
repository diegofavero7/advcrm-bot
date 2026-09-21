"""Testes de carregamento de playbooks."""

from __future__ import annotations

from app.domain.enums import LegalArea
from app.playbooks import (
    EXPECTED_PLAYBOOK_COUNT,
    get_playbooks,
    resolve_playbook,
)


def test_loads_exactly_nine_playbooks() -> None:
    playbooks = get_playbooks()
    assert len(playbooks) == EXPECTED_PLAYBOOK_COUNT
    assert len(playbooks) == 9


def test_specific_playbooks_precede_generic() -> None:
    specific = resolve_playbook(LegalArea.SOCIAL_SECURITY, "prison_allowance")
    assert specific is not None
    assert specific.id == "social_security_prison_allowance"

    generic = resolve_playbook(LegalArea.SOCIAL_SECURITY, "retirement")
    assert generic is not None
    assert generic.id == "generic_social_security"


def test_non_priority_playbook() -> None:
    pb = resolve_playbook(LegalArea.TAX, "tax_assessment")
    assert pb is not None
    assert pb.id == "generic_non_priority"
    assert LegalArea.TAX in pb.applies_to_areas


def test_vehicle_playbook() -> None:
    pb = resolve_playbook(LegalArea.CONSUMER, "vehicle_purchase_irregularities")
    assert pb is not None
    assert pb.subject == "vehicle_purchase_irregularities"
    assert pb.max_questions <= 5
