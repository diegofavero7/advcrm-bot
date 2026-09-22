"""Geração determinística de JSON Schemas em duas execuções."""

from __future__ import annotations

import json
import runpy
from pathlib import Path


def test_schema_export_deterministic_two_runs(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[2] / "scripts" / "export_json_schemas.py"
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    for target in (dir_a, dir_b):
        # Executa o módulo do script com output-dir via namespace local
        namespace = runpy.run_path(str(script))
        export_schemas = namespace["export_schemas"]
        export_schemas(target)

    for name in (
        "lead_understanding.v1.json",
        "triage_next_step.v1.json",
        "safety_signals.v1.json",
        "safety_signals.v2.json",
        "triage_analysis_request.v1.json",
    ):
        content_a = (dir_a / name).read_bytes()
        content_b = (dir_b / name).read_bytes()
        assert content_a == content_b
        assert json.loads(content_a) == json.loads(content_b)


def test_all_properties_are_required() -> None:
    from app.schemas import build_strict_json_schema
    from app.schemas.inbound import TriageAnalysisRequest
    from app.schemas.lead_understanding import LeadUnderstanding
    from app.schemas.safety_signals import SafetySignals, SafetySignalsV2
    from app.schemas.triage_next_step import TriageNextStep

    for model in (
        LeadUnderstanding,
        TriageNextStep,
        SafetySignals,
        SafetySignalsV2,
        TriageAnalysisRequest,
    ):
        schema = build_strict_json_schema(model)
        props = schema.get("properties", {})
        required = set(schema.get("required", []))
        assert set(props.keys()) == required
        assert schema.get("additionalProperties") is False
