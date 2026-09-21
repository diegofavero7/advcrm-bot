"""Playbooks versionados — load via yaml.safe_load + validação Pydantic."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, field_validator

from app.domain.enums import LegalArea
from app.domain.models import StrictModel

_DATA_DIR = Path(__file__).resolve().parent / "data"
EXPECTED_PLAYBOOK_COUNT = 9


class PlaybookFact(StrictModel):
    key: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=500)


class PlaybookQuestion(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=500)
    maps_to_fact: str = Field(min_length=1, max_length=128)


class Playbook(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=32)
    area: LegalArea
    subject: str | None
    title: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1, max_length=2000)
    priority_area: bool
    applies_to_areas: list[LegalArea] = Field(default_factory=list)
    max_questions: int = Field(ge=0, le=20)
    minimum_facts: list[PlaybookFact]
    optional_facts: list[PlaybookFact]
    allowed_questions: list[PlaybookQuestion]
    sufficiency_criteria: list[str]
    ambiguity_signals: list[str]
    risk_signals: list[str]
    handoff_conditions: list[str]
    mentionable_documents: list[str]
    policy_warnings: list[str]

    @field_validator("policy_warnings")
    @classmethod
    def must_warn_no_legal_opinion(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("Playbook deve declarar policy_warnings")
        return value


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Playbook inválido em {path}: esperado mapping")
    return data


def load_playbook(path: Path) -> Playbook:
    return Playbook.model_validate(_load_yaml(path))


def load_all_playbooks(directory: Path | None = None) -> list[Playbook]:
    data_dir = directory or _DATA_DIR
    paths = sorted(data_dir.glob("*.yaml"))
    if not paths:
        raise ValueError(f"Nenhum playbook encontrado em {data_dir}")
    playbooks = [load_playbook(path) for path in paths]
    if len(playbooks) != EXPECTED_PLAYBOOK_COUNT:
        raise ValueError(
            f"Esperados {EXPECTED_PLAYBOOK_COUNT} playbooks, encontrados {len(playbooks)}"
        )
    ids = [p.id for p in playbooks]
    if len(ids) != len(set(ids)):
        raise ValueError("IDs de playbook duplicados")
    return playbooks


@lru_cache
def get_playbooks() -> tuple[Playbook, ...]:
    return tuple(load_all_playbooks())


def clear_playbooks_cache() -> None:
    get_playbooks.cache_clear()


def resolve_playbook(area: LegalArea, subject: str | None) -> Playbook | None:
    """Específico tem precedência sobre genérico da mesma área."""
    playbooks = get_playbooks()
    if subject is not None:
        for pb in playbooks:
            if pb.area == area and pb.subject == subject:
                return pb
    for pb in playbooks:
        if pb.area == area and pb.subject is None and pb.priority_area:
            return pb
    for pb in playbooks:
        if area in pb.applies_to_areas:
            return pb
    for pb in playbooks:
        if pb.id == "generic_non_priority":
            return pb
    return None
