"""Domínio público."""

from app.domain.enums import (
    PRIORITY_AREAS,
    ContentType,
    FactCertainty,
    HandoffReason,
    Intent,
    Language,
    LegalArea,
    MessageDirection,
    MessageRole,
    Priority,
    RequestSource,
    RiskFlag,
    TriageAction,
    TriageState,
    UrgencyLevel,
)
from app.domain.state_machine import (
    ALLOWED_TRANSITIONS,
    InvalidTransitionError,
    assert_transition,
    can_transition,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "PRIORITY_AREAS",
    "ContentType",
    "FactCertainty",
    "HandoffReason",
    "Intent",
    "InvalidTransitionError",
    "Language",
    "LegalArea",
    "MessageDirection",
    "MessageRole",
    "Priority",
    "RequestSource",
    "RiskFlag",
    "TriageAction",
    "TriageState",
    "UrgencyLevel",
    "assert_transition",
    "can_transition",
]
