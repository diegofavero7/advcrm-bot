#!/usr/bin/env python3
"""Exporta JSON Schemas determinísticos a partir dos modelos Pydantic."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.schemas import (  # noqa: E402
    TriageAnalysisRequest,
    build_strict_json_schema,
)
from app.schemas.lead_understanding import LeadUnderstanding  # noqa: E402
from app.schemas.safety_signals import SafetySignals, SafetySignalsV2  # noqa: E402
from app.schemas.triage_next_step import TriageNextStep  # noqa: E402

SCHEMA_EXPORTS: tuple[tuple[str, type[object]], ...] = (
    ("lead_understanding.v1.json", LeadUnderstanding),
    ("triage_next_step.v1.json", TriageNextStep),
    ("safety_signals.v1.json", SafetySignals),
    ("safety_signals.v2.json", SafetySignalsV2),
    ("triage_analysis_request.v1.json", TriageAnalysisRequest),
)


def export_schemas(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, model in SCHEMA_EXPORTS:
        schema = build_strict_json_schema(model)
        path = output_dir / filename
        # Dump determinístico: sort_keys + separadores estáveis + newline final
        payload = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        path.write_text(payload, encoding="utf-8")
        written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Exporta JSON Schemas do advcrm-bot")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "artifacts" / "schemas",
        help="Diretório de saída",
    )
    args = parser.parse_args()
    paths = export_schemas(args.output_dir)
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
