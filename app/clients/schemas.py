"""Carregamento de JSON Schemas estritos com verificação de hash vs artefato."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.clients.errors import AiRuntimeProtocolError
from app.schemas import build_strict_json_schema
from app.schemas.lead_understanding import LeadUnderstanding
from app.schemas.triage_next_step import TriageNextStep

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACTS_DIR = _REPO_ROOT / "artifacts" / "schemas"

SCHEMA_NAME_LEAD = "advcrm_lead_understanding_v1"
SCHEMA_NAME_NEXT_STEP = "advcrm_triage_next_step_v1"

_MODEL_BY_ARTIFACT: dict[str, type[Any]] = {
    "lead_understanding.v1.json": LeadUnderstanding,
    "triage_next_step.v1.json": TriageNextStep,
}


def canonical_schema_json(schema: dict[str, Any]) -> str:
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def schema_hash(schema: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_schema_json(schema).encode("utf-8")).hexdigest()


def load_exported_artifact(filename: str) -> dict[str, Any]:
    path = _ARTIFACTS_DIR / filename
    if not path.is_file():
        raise AiRuntimeProtocolError(f"Artefato de schema ausente: {filename}")
    data: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise AiRuntimeProtocolError(f"Artefato inválido: {filename}")
    return data


def assert_schema_matches_artifact(filename: str, model: type[Any]) -> dict[str, Any]:
    """Gera schema do modelo e exige igualdade canônica com o artefato exportado."""
    generated = build_strict_json_schema(model)
    artifact = load_exported_artifact(filename)
    gen_canon = canonical_schema_json(generated)
    art_canon = canonical_schema_json(artifact)
    if gen_canon != art_canon:
        raise AiRuntimeProtocolError(
            f"Divergência de schema entre modelo e artefato: {filename} "
            f"(generated={schema_hash(generated)[:12]} artifact={schema_hash(artifact)[:12]})"
        )
    return generated


@lru_cache
def get_lead_understanding_schema() -> dict[str, Any]:
    return assert_schema_matches_artifact("lead_understanding.v1.json", LeadUnderstanding)


@lru_cache
def get_triage_next_step_schema() -> dict[str, Any]:
    return assert_schema_matches_artifact("triage_next_step.v1.json", TriageNextStep)


def clear_schema_cache() -> None:
    get_lead_understanding_schema.cache_clear()
    get_triage_next_step_schema.cache_clear()


def verify_all_ai_schemas() -> None:
    for filename, model in _MODEL_BY_ARTIFACT.items():
        assert_schema_matches_artifact(filename, model)
