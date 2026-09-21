"""Endpoints de validação de contratos (somente validação local)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.application import (
    validate_lead_understanding,
    validate_triage_next_step,
    validate_triage_request,
)

router = APIRouter(prefix="/v1/contracts", tags=["contracts"])

_UNPROCESSABLE = 422


def _sanitize_validation_error(exc: ValidationError) -> dict[str, Any]:
    """Erros sem texto de conversa nem segredos — apenas loc/type/msg estruturado."""
    errors: list[dict[str, Any]] = []
    for err in exc.errors():
        errors.append(
            {
                "loc": list(err.get("loc", ())),
                "type": err.get("type", "validation_error"),
                "msg": err.get("msg", "invalid"),
            }
        )
    return {"valid": False, "errors": errors}


@router.post("/lead-understanding/validate")
async def validate_lead_understanding_endpoint(request: Request) -> JSONResponse:
    payload = await request.json()
    if not isinstance(payload, dict):
        return JSONResponse(
            status_code=_UNPROCESSABLE,
            content={"valid": False, "errors": [{"msg": "body must be object"}]},
        )
    try:
        model = validate_lead_understanding(payload)
        return JSONResponse(
            status_code=200,
            content={"valid": True, "schema_version": model.schema_version},
        )
    except ValidationError as exc:
        return JSONResponse(
            status_code=_UNPROCESSABLE,
            content=_sanitize_validation_error(exc),
        )


@router.post("/triage-next-step/validate")
async def validate_triage_next_step_endpoint(request: Request) -> JSONResponse:
    payload = await request.json()
    if not isinstance(payload, dict):
        return JSONResponse(
            status_code=_UNPROCESSABLE,
            content={"valid": False, "errors": [{"msg": "body must be object"}]},
        )
    try:
        model = validate_triage_next_step(payload)
        return JSONResponse(
            status_code=200,
            content={"valid": True, "schema_version": model.schema_version},
        )
    except ValidationError as exc:
        return JSONResponse(
            status_code=_UNPROCESSABLE,
            content=_sanitize_validation_error(exc),
        )


@router.post("/triage-request/validate")
async def validate_triage_request_endpoint(request: Request) -> JSONResponse:
    payload = await request.json()
    if not isinstance(payload, dict):
        return JSONResponse(
            status_code=_UNPROCESSABLE,
            content={"valid": False, "errors": [{"msg": "body must be object"}]},
        )
    try:
        model = validate_triage_request(payload)
        return JSONResponse(
            status_code=200,
            content={
                "valid": True,
                "event_id": model.event_id,
                "message_count": len(model.messages),
            },
        )
    except ValidationError as exc:
        return JSONResponse(
            status_code=_UNPROCESSABLE,
            content=_sanitize_validation_error(exc),
        )
