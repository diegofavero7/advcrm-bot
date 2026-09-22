"""Derivação determinística de riscos a partir de subjects validados pelo catálogo.

Não altera urgency, recommend_handoff nem lead_understanding.safety.detected_risks.
Não usa matching textual no lead.
"""

from __future__ import annotations

from app.domain.enums import LegalArea, RiskFlag
from app.taxonomy import validate_area_subject

# Mapa subject → riscos categóricos. Só aplicável se o subject for válido na área.
SUBJECT_DERIVED_RISKS: dict[str, frozenset[RiskFlag]] = {
    "banking_fraud": frozenset({RiskFlag.FRAUD_OR_SCAM}),
    "flagrant_arrest": frozenset({RiskFlag.ARREST_OR_DETENTION}),
    "child_custody": frozenset({RiskFlag.CHILD_OR_VULNERABLE_PERSON}),
    "domestic_violence": frozenset({RiskFlag.DOMESTIC_VIOLENCE, RiskFlag.VIOLENCE_OR_THREAT}),
    "prison_allowance": frozenset({RiskFlag.ARREST_OR_DETENTION}),
}

# Ordem canônica do enum para saída determinística.
_RISK_ORDER: tuple[RiskFlag, ...] = tuple(RiskFlag)


def derive_risks_from_validated_subject(
    area: LegalArea | str,
    subject: str,
) -> tuple[RiskFlag, ...]:
    """Retorna riscos derivados se e somente se area/subject passam no catálogo.

    Subject inválido ou ausente do mapa → ().
    """
    if not validate_area_subject(area, subject):
        return ()
    mapped = SUBJECT_DERIVED_RISKS.get(subject)
    if not mapped:
        return ()
    return tuple(r for r in _RISK_ORDER if r in mapped)
