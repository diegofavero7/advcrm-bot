"""Testes da máquina de estados."""

from __future__ import annotations

import pytest
from app.domain.enums import TriageState
from app.domain.state_machine import (
    ALLOWED_TRANSITIONS,
    InvalidTransitionError,
    assert_transition,
    can_transition,
    is_terminal,
)


def test_all_allowed_transitions() -> None:
    for current, targets in ALLOWED_TRANSITIONS.items():
        for target in targets:
            assert can_transition(current, target)
            assert_transition(current, target)


def test_forbidden_transitions_sample() -> None:
    forbidden = [
        (TriageState.HUMAN_ASSIGNED, TriageState.COLLECTING_INFORMATION),
        (TriageState.COMPLETED, TriageState.PENDING_CLASSIFICATION),
        (TriageState.CANCELLED, TriageState.CLASSIFIED),
        (TriageState.FAILED, TriageState.COLLECTING_INFORMATION),
        (TriageState.FAILED, TriageState.COMPLETED),
        (TriageState.CLASSIFIED, TriageState.WAITING_LEAD_REPLY),
    ]
    for current, target in forbidden:
        assert not can_transition(current, target)
        with pytest.raises(InvalidTransitionError):
            assert_transition(current, target)


def test_failed_recovery_only_to_classification_or_review() -> None:
    assert can_transition(TriageState.FAILED, TriageState.PENDING_CLASSIFICATION)
    assert can_transition(TriageState.FAILED, TriageState.PENDING_HUMAN_REVIEW)
    for state in TriageState:
        if state in {
            TriageState.PENDING_CLASSIFICATION,
            TriageState.PENDING_HUMAN_REVIEW,
        }:
            continue
        assert not can_transition(TriageState.FAILED, state)


def test_new_states_present() -> None:
    assert TriageState.WAITING_LEAD_REPLY in ALLOWED_TRANSITIONS
    assert TriageState.PENDING_ACTION_APPROVAL in ALLOWED_TRANSITIONS


def test_terminal_states() -> None:
    assert is_terminal(TriageState.COMPLETED)
    assert is_terminal(TriageState.CANCELLED)
    assert not is_terminal(TriageState.FAILED)


def test_exhaustive_forbidden_matrix() -> None:
    """Todas as combinações não listadas devem ser rejeitadas."""
    for current in TriageState:
        allowed = ALLOWED_TRANSITIONS[current]
        for target in TriageState:
            if target in allowed:
                assert can_transition(current, target)
            else:
                assert not can_transition(current, target)
