"""Rotas de saúde e métricas."""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from fastapi.responses import JSONResponse

from app.application import ReadinessError, check_readiness
from app.config import get_settings
from app.observability import READY_CHECKS, metrics_payload

router = APIRouter(tags=["ops"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": get_settings().app_name}


@router.get("/ready")
def ready() -> JSONResponse:
    try:
        check_readiness()
        READY_CHECKS.labels(result="ok").inc()
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "ready"},
        )
    except ReadinessError as exc:
        READY_CHECKS.labels(result="error").inc()
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_ready", "detail": str(exc)},
        )


@router.get("/metrics")
def metrics() -> Response:
    payload, content_type = metrics_payload()
    return Response(content=payload, media_type=content_type)
