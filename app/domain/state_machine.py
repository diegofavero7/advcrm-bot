"""Máquina de estados determinística da triagem.

O estado oficial pertence ao AdvCRM. Este módulo apenas valida
transições propostas — nunca altera estado silenciosamente.
"""

from __future__ import annotations

from app.domain.enums import TriageState

ALLOWED_TRANSITIONS: dict[TriageState, frozenset[TriageState]] = {
    TriageState.COLLECTING_MESSAGES: frozenset(
        {
            TriageState.PENDING_CLASSIFICATION,
            TriageState.PENDING_HUMAN_REVIEW,
            TriageState.CANCELLED,
            TriageState.FAILED,
        }
    ),
    TriageState.PENDING_CLASSIFICATION: frozenset(
        {
            TriageState.CLASSIFIED,
            TriageState.COLLECTING_MESSAGES,
            TriageState.PENDING_HUMAN_REVIEW,
            TriageState.FAILED,
        }
    ),
    TriageState.CLASSIFIED: frozenset(
        {
            TriageState.COLLECTING_INFORMATION,
            TriageState.PENDING_ACTION_APPROVAL,
            TriageState.PENDING_HUMAN_REVIEW,
            TriageState.CANCELLED,
        }
    ),
    TriageState.COLLECTING_INFORMATION: frozenset(
        {
            TriageState.WAITING_LEAD_REPLY,
            TriageState.PENDING_ACTION_APPROVAL,
            TriageState.CLASSIFIED,
            TriageState.PENDING_HUMAN_REVIEW,
            TriageState.CANCELLED,
            TriageState.FAILED,
        }
    ),
    TriageState.WAITING_LEAD_REPLY: frozenset(
        {
            TriageState.COLLECTING_INFORMATION,
            TriageState.PENDING_CLASSIFICATION,
            TriageState.PENDING_HUMAN_REVIEW,
            TriageState.CANCELLED,
            TriageState.FAILED,
        }
    ),
    TriageState.PENDING_ACTION_APPROVAL: frozenset(
        {
            TriageState.WAITING_LEAD_REPLY,
            TriageState.READY_FOR_HANDOFF,
            TriageState.PENDING_HUMAN_REVIEW,
            TriageState.CANCELLED,
            TriageState.FAILED,
        }
    ),
    TriageState.PENDING_HUMAN_REVIEW: frozenset(
        {
            TriageState.HUMAN_ASSIGNED,
            TriageState.COLLECTING_INFORMATION,
            TriageState.READY_FOR_HANDOFF,
            TriageState.CANCELLED,
            TriageState.FAILED,
        }
    ),
    TriageState.READY_FOR_HANDOFF: frozenset(
        {
            TriageState.HUMAN_ASSIGNED,
            TriageState.CANCELLED,
            TriageState.FAILED,
        }
    ),
    TriageState.HUMAN_ASSIGNED: frozenset(
        {
            TriageState.COMPLETED,
            TriageState.CANCELLED,
        }
    ),
    TriageState.COMPLETED: frozenset(),
    TriageState.CANCELLED: frozenset(),
    TriageState.FAILED: frozenset(
        {
            TriageState.PENDING_CLASSIFICATION,
            TriageState.PENDING_HUMAN_REVIEW,
        }
    ),
}

TERMINAL_STATES: frozenset[TriageState] = frozenset(
    {
        TriageState.COMPLETED,
        TriageState.CANCELLED,
    }
)


class InvalidTransitionError(ValueError):
    """Transição de estado não permitida."""

    def __init__(self, current: TriageState, target: TriageState) -> None:
        self.current = current
        self.target = target
        super().__init__(
            f"Transição inválida: {current.value} -> {target.value}. "
            "O estado oficial pertence ao AdvCRM; o bot não corrige silenciosamente."
        )


def can_transition(current: TriageState, target: TriageState) -> bool:
    """Retorna True se a transição for explicitamente permitida."""
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def assert_transition(current: TriageState, target: TriageState) -> None:
    """Valida transição; rejeita com erro — sem correção silenciosa."""
    if not can_transition(current, target):
        raise InvalidTransitionError(current, target)


def allowed_targets(current: TriageState) -> frozenset[TriageState]:
    return ALLOWED_TRANSITIONS.get(current, frozenset())


def is_terminal(state: TriageState) -> bool:
    return state in TERMINAL_STATES
