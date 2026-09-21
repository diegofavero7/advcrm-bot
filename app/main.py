"""Aplicação FastAPI mínima — health, ready, metrics e validação de contratos."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.routes import contracts, health
from app.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "Orquestrador de triagem jurídica do AdvCRM. "
            "Fase 1: contratos, taxonomia, políticas e validação local. "
            "Não envia mensagens nem acessa o banco do CRM."
        ),
    )
    application.include_router(health.router)
    application.include_router(contracts.router)

    @application.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "valid": False,
                "errors": [
                    {"loc": list(e.get("loc", ())), "msg": e.get("msg", "invalid")}
                    for e in exc.errors()
                ],
            },
        )

    return application


app = create_app()
