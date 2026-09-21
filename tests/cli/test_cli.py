"""Testes da CLI offline e mutual exclusion."""

from __future__ import annotations

import json
from pathlib import Path

from app.cli.triage import EXIT_CONFIG, EXIT_OK, main

ROOT = Path(__file__).resolve().parents[2]


def test_cli_mutual_exclusion() -> None:
    code = main(
        [
            "--input",
            str(ROOT / "examples/valid/01_prison_allowance_spouse.json"),
            "--understanding-only",
            "--next-step-only",
        ]
    )
    assert code == EXIT_CONFIG


def test_cli_offline_understanding(tmp_path: Path) -> None:
    out = tmp_path / "out.json"
    code = main(
        [
            "--input",
            str(ROOT / "examples/valid/01_prison_allowance_spouse.json"),
            "--output",
            str(out),
            "--understanding-only",
        ]
    )
    assert code == EXIT_OK
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["mode"] == "offline"
    assert data["schema_name"] == "advcrm_lead_understanding_v1"
    assert data["response_format"]["json_schema"]["strict"] is True


def test_cli_offline_next_step_envelope(tmp_path: Path) -> None:
    example = json.loads(
        (ROOT / "examples/valid/01_prison_allowance_spouse.json").read_text(encoding="utf-8")
    )
    envelope_path = tmp_path / "envelope.json"
    envelope_path.write_text(
        json.dumps(
            {
                "request": example["request"],
                "lead_understanding": example["lead_understanding"],
                "questions_asked": 1,
                "last_bot_question": "Qual a data?",
                "missing_information": ["approximate_prison_date"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    out = tmp_path / "out.json"
    code = main(
        [
            "--input",
            str(envelope_path),
            "--output",
            str(out),
            "--next-step-only",
        ]
    )
    assert code == EXIT_OK
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["stage"] == "next_step"
    assert data["schema_name"] == "advcrm_triage_next_step_v1"
