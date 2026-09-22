"""Diagnóstico sanitizado de ValidationError — sem input, ctx ou mensagens."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

# Códigos de coerência/taxonomia: semânticos mesmo quando emitidos em model_validate.
SEMANTIC_COHERENCE_ERROR_TYPES: frozenset[str] = frozenset(
    {
        "secondary_area_equals_primary",
        "subject_not_in_primary_area",
        "subsubject_not_in_catalog",
        "subsubject_without_catalog",
        "ambiguity_present_requires_reason",
        "ambiguity_alternatives_without_present",
        "case_fact_placeholder_value",
        "canonical_boolean_fact_value",
        "unverified_trusted_crm_context",
        "ask_question_requires_missing_information",
        "ask_question_requires_exactly_one_selected",
        "ask_question_selected_not_in_missing",
        "ask_question_requires_proposed_question",
        "ask_question_single_question_mark",
        "ask_question_forbids_handoff",
        "ask_question_forbids_handoff_reason",
    }
)


def sanitize_validation_error(exc: ValidationError) -> list[dict[str, Any]]:
    """Extrai apenas loc e type de cada erro Pydantic.

    Não inclui input, ctx, msg, url nem str(exc) — esses campos podem
    conter valores do payload (mensagens do lead, fatos, etc.).
    """
    details: list[dict[str, Any]] = []
    for err in exc.errors():
        loc_raw = err.get("loc", ())
        loc: list[str | int] = []
        if isinstance(loc_raw, (list, tuple)):
            for part in loc_raw:
                if isinstance(part, (str, int)) and not isinstance(part, bool):
                    loc.append(part)
                else:
                    loc.append(str(part))
        details.append(
            {
                "loc": loc,
                "type": str(err.get("type", "validation_error")),
            }
        )
    return details


def is_semantic_coherence_failure(details: list[dict[str, Any]]) -> bool:
    """True quando todos os erros sanitizados são de coerência semântica."""
    if not details:
        return False
    types = {str(item.get("type", "")) for item in details}
    return bool(types) and types <= SEMANTIC_COHERENCE_ERROR_TYPES
