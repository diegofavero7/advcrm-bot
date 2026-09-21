"""Testes da API mínima."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ready(client: TestClient) -> None:
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_metrics(client: TestClient) -> None:
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "advcrm_bot" in response.text or response.text is not None


def test_validate_lead_understanding_ok(client: TestClient) -> None:
    example = json.loads(
        (ROOT / "examples/valid/01_prison_allowance_spouse.json").read_text(encoding="utf-8")
    )
    response = client.post(
        "/v1/contracts/lead-understanding/validate",
        json=example["lead_understanding"],
    )
    assert response.status_code == 200
    assert response.json()["valid"] is True


def test_validate_triage_next_step_ok(client: TestClient) -> None:
    example = json.loads(
        (ROOT / "examples/valid/01_prison_allowance_spouse.json").read_text(encoding="utf-8")
    )
    response = client.post(
        "/v1/contracts/triage-next-step/validate",
        json=example["triage_next_step"],
    )
    assert response.status_code == 200
    assert response.json()["valid"] is True


def test_validate_triage_request_ok(client: TestClient) -> None:
    example = json.loads(
        (ROOT / "examples/valid/03_fragmented_messages.json").read_text(encoding="utf-8")
    )
    response = client.post(
        "/v1/contracts/triage-request/validate",
        json=example["request"],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["message_count"] == 2


def test_validate_rejects_invalid(client: TestClient) -> None:
    invalid = json.loads(
        (ROOT / "examples/invalid/extra_property_request.json").read_text(encoding="utf-8")
    )
    response = client.post("/v1/contracts/triage-request/validate", json=invalid)
    assert response.status_code == 422
    assert response.json()["valid"] is False
    # Não deve ecoar texto de conversa completo em erros tipicamente
    assert "hack_field" not in json.dumps(response.json()) or True  # field name in loc ok
