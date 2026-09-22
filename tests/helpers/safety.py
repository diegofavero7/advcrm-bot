"""Payloads sintéticos do extrator para testes (v1 legado / v2 ativo)."""

from __future__ import annotations

from typing import Any


def cue_not_informed() -> dict[str, Any]:
    return {
        "state": "not_informed",
        "source_message_ids": [],
        "evidence_quote": None,
        "evidence_summary": None,
        "related_occurrence_ids": [],
    }


def cue_present(
    *,
    quote: str,
    summary: str,
    msg_ids: list[str] | None = None,
    related: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "state": "present",
        "source_message_ids": msg_ids or ["m1"],
        "evidence_quote": quote,
        "evidence_summary": summary,
        "related_occurrence_ids": related or [],
    }


def safety_v2(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": "safety_signals.v2",
        "occurrences": [],
        "urgent_help_request": cue_not_informed(),
        "immediate_danger": cue_not_informed(),
    }
    base.update(overrides)
    return base


def occurrence(
    *,
    occurrence_id: str,
    risk: str,
    quote: str,
    summary: str = "sinal",
    assertion: str = "affirmed",
    temporal: str = "unknown",
    msg_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "occurrence_id": occurrence_id,
        "risk": risk,
        "assertion": assertion,
        "temporal_context": temporal,
        "source_message_ids": msg_ids or ["m1"],
        "evidence_quote": quote,
        "evidence_summary": summary,
    }


def safety_v1(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"schema_version": "safety_signals.v1", "detected_risks": []}
    base.update(overrides)
    return base
