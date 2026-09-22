"""Sinais de segurança efetivos — taxonomia, extrator e composição com proveniência."""

from app.safety.compose import (
    EffectiveSafetySignals,
    RiskProvenanceEntry,
    SafetySignalSource,
    compose_effective_safety_signals,
)
from app.safety.promotion import TAXONOMY_PROMOTION_RULES, PromotionReason
from app.safety.taxonomy_risks import derive_risks_from_validated_subject

__all__ = [
    "TAXONOMY_PROMOTION_RULES",
    "EffectiveSafetySignals",
    "PromotionReason",
    "RiskProvenanceEntry",
    "SafetySignalSource",
    "compose_effective_safety_signals",
    "derive_risks_from_validated_subject",
]
