"""Validadores de invariantes de domínio."""

from __future__ import annotations

from app.domain.enums import LegalArea


def secondary_differs_from_primary(
    primary: LegalArea,
    secondary: LegalArea | None,
) -> bool:
    """Área secundária, se presente, deve diferir da principal."""
    if secondary is None:
        return True
    return secondary != primary
