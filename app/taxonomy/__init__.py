"""Taxonomia jurídica versionada — catálogo controlado área → assunto → subassuntos."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, field_validator

from app.domain.enums import LegalArea
from app.domain.models import StrictModel

_DATA_DIR = Path(__file__).resolve().parent / "data"
_DEFAULT_TAXONOMY_FILE = _DATA_DIR / "legal_subjects.v1.yaml"


class AreaCatalog(StrictModel):
    subjects: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("subjects")
    @classmethod
    def subjects_must_include_other_undetermined(
        cls, value: dict[str, list[str]]
    ) -> dict[str, list[str]]:
        required = {"other", "undetermined"}
        missing = required - set(value)
        if missing:
            raise ValueError(f"Assuntos obrigatórios ausentes: {sorted(missing)}")
        return value


class TaxonomyCatalog(StrictModel):
    taxonomy_version: str
    description: str = ""
    areas: dict[str, AreaCatalog]

    @field_validator("areas")
    @classmethod
    def must_cover_all_legal_areas(cls, value: dict[str, AreaCatalog]) -> dict[str, AreaCatalog]:
        expected = {area.value for area in LegalArea}
        missing = expected - set(value)
        if missing:
            raise ValueError(f"Áreas ausentes na taxonomia: {sorted(missing)}")
        return value

    def subjects_for(self, area: LegalArea | str) -> frozenset[str]:
        key = area.value if isinstance(area, LegalArea) else area
        catalog = self.areas.get(key)
        if catalog is None:
            return frozenset()
        return frozenset(catalog.subjects.keys())

    def subsubjects_for(self, area: LegalArea | str, subject: str) -> frozenset[str]:
        key = area.value if isinstance(area, LegalArea) else area
        catalog = self.areas.get(key)
        if catalog is None:
            return frozenset()
        return frozenset(catalog.subjects.get(subject, []))

    def is_valid_subject(self, area: LegalArea | str, subject: str) -> bool:
        return subject in self.subjects_for(area)

    def is_valid_subsubject(self, area: LegalArea | str, subject: str, subsubject: str) -> bool:
        allowed = self.subsubjects_for(area, subject)
        if not allowed:
            # Sem catálogo detalhado: apenas other/undetermined (ou vazio = nenhum)
            return subsubject in {"other", "undetermined"}
        return subsubject in allowed

    def to_prompt_catalog(self) -> dict[str, Any]:
        """Representação compacta e determinística para o contexto do modelo.

        Derivada da mesma instância usada por ``is_valid_subject`` / subassuntos.
        Não inventa descrições ausentes na fonte YAML.
        """
        areas: dict[str, Any] = {}
        for area_id in sorted(self.areas.keys()):
            subjects_map = self.areas[area_id].subjects
            subjects: dict[str, list[str]] = {
                subject_id: sorted(subjects_map[subject_id])
                for subject_id in sorted(subjects_map.keys())
            }
            areas[area_id] = {"subjects": subjects}

        payload: dict[str, Any] = {
            "taxonomy_version": self.taxonomy_version,
            "areas": areas,
            "rules": {
                "use_exact_identifiers": True,
                "empty_subsubjects_list_allows_only": ["other", "undetermined"],
                "indeterminacy_subject_ids": ["other", "undetermined"],
                "indeterminacy_subsubject_ids": ["other", "undetermined"],
            },
        }
        description = (self.description or "").strip()
        if description:
            payload["description"] = description
        return payload


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Taxonomia inválida em {path}: esperado mapping")
    return data


def load_taxonomy(path: Path | None = None) -> TaxonomyCatalog:
    target = path or _DEFAULT_TAXONOMY_FILE
    raw = _load_yaml(target)
    return TaxonomyCatalog.model_validate(raw)


@lru_cache
def get_taxonomy() -> TaxonomyCatalog:
    return load_taxonomy()


def clear_taxonomy_cache() -> None:
    get_taxonomy.cache_clear()


def validate_area_subject(area: LegalArea | str, subject: str) -> bool:
    return get_taxonomy().is_valid_subject(area, subject)


def validate_subsubjects(
    area: LegalArea | str,
    subject: str,
    subsubjects: list[str],
) -> bool:
    taxonomy = get_taxonomy()
    allowed = taxonomy.subsubjects_for(area, subject)
    for item in subsubjects:
        if allowed:
            if item not in allowed:
                return False
        elif item not in {"other", "undetermined"}:
            return False
    return True
