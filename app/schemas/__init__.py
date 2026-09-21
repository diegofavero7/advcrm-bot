"""Schemas públicos e utilitários de JSON Schema estrito."""

from __future__ import annotations

from typing import Any

from app.schemas.inbound import TriageAnalysisRequest
from app.schemas.lead_understanding import LeadUnderstanding
from app.schemas.triage_next_step import TriageNextStep

__all__ = [
    "LeadUnderstanding",
    "TriageAnalysisRequest",
    "TriageNextStep",
    "build_strict_json_schema",
]


def _force_additional_properties_false(node: dict[str, Any]) -> None:
    """Garante additionalProperties=false em todos os objetos do schema."""
    if node.get("type") == "object" or "properties" in node:
        node["additionalProperties"] = False
        props = node.get("properties")
        if isinstance(props, dict):
            # Todos os campos devem estar em required (incluindo anuláveis)
            node["required"] = sorted(props.keys())
            for child in props.values():
                if isinstance(child, dict):
                    _force_additional_properties_false(child)
    if "$defs" in node and isinstance(node["$defs"], dict):
        for defn in node["$defs"].values():
            if isinstance(defn, dict):
                _force_additional_properties_false(defn)
    for key in ("anyOf", "oneOf", "allOf", "items", "prefixItems"):
        child = node.get(key)
        if isinstance(child, dict):
            _force_additional_properties_false(child)
        elif isinstance(child, list):
            for item in child:
                if isinstance(item, dict):
                    _force_additional_properties_false(item)


def build_strict_json_schema(model: type[Any]) -> dict[str, Any]:
    """Gera JSON Schema determinístico e fechado a partir do modelo Pydantic."""
    schema: dict[str, Any] = model.model_json_schema(mode="validation")
    _force_additional_properties_false(schema)
    return schema
