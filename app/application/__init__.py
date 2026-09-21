"""Camada de aplicação — validação local sem I/O externo."""

from __future__ import annotations

from typing import Protocol

from pydantic import ValidationError

from app.config import Settings, get_settings
from app.observability import CONTRACT_VALIDATIONS, get_logger
from app.playbooks import get_playbooks, load_all_playbooks
from app.schemas.inbound import TriageAnalysisRequest
from app.schemas.lead_understanding import LeadUnderstanding
from app.schemas.triage_next_step import TriageNextStep
from app.taxonomy import get_taxonomy, load_taxonomy

logger = get_logger("app.application")


class AdvCrmAiClient(Protocol):
    """Integração futura com advcrm-ai — não implementar HTTP nesta fase."""

    async def classify(
        self,
        request: TriageAnalysisRequest,
        *,
        json_schema: dict[str, object],
    ) -> LeadUnderstanding: ...


class AdvCrmDecisionSink(Protocol):
    """Devolução futura da decisão ao AdvCRM — não implementar nesta fase."""

    async def submit_decision(
        self,
        *,
        tenant_id: str,
        lead_id: str,
        understanding: LeadUnderstanding,
        next_step: TriageNextStep,
    ) -> None: ...


class ReadinessError(Exception):
    """Falha de readiness (config/taxonomia/playbooks)."""


def check_readiness(settings: Settings | None = None) -> None:
    """Valida configuração, taxonomia e playbooks. Levanta ReadinessError se falhar."""
    cfg = settings or get_settings()
    try:
        if not (0.0 <= cfg.confidence_medium_threshold <= cfg.confidence_high_threshold <= 1.0):
            raise ReadinessError("Limiares de confiança inconsistentes")
        taxonomy = load_taxonomy()
        if taxonomy.taxonomy_version != cfg.taxonomy_version:
            raise ReadinessError(
                f"Versão de taxonomia esperada {cfg.taxonomy_version}, "
                f"encontrada {taxonomy.taxonomy_version}"
            )
        # Popula cache
        get_taxonomy()
        playbooks = load_all_playbooks()
        if len(playbooks) != 9:
            raise ReadinessError(f"Esperados 9 playbooks, obtidos {len(playbooks)}")
        get_playbooks()
    except ReadinessError:
        raise
    except Exception as exc:
        raise ReadinessError(str(exc)) from exc


def validate_lead_understanding(payload: dict[str, object]) -> LeadUnderstanding:
    try:
        model = LeadUnderstanding.model_validate(payload)
        CONTRACT_VALIDATIONS.labels(contract="lead_understanding", result="ok").inc()
        logger.info("contract_validated contract=lead_understanding result=ok")
        return model
    except ValidationError:
        CONTRACT_VALIDATIONS.labels(contract="lead_understanding", result="error").inc()
        logger.info("contract_validated contract=lead_understanding result=error")
        raise


def validate_triage_next_step(payload: dict[str, object]) -> TriageNextStep:
    try:
        model = TriageNextStep.model_validate(payload)
        CONTRACT_VALIDATIONS.labels(contract="triage_next_step", result="ok").inc()
        logger.info("contract_validated contract=triage_next_step result=ok")
        return model
    except ValidationError:
        CONTRACT_VALIDATIONS.labels(contract="triage_next_step", result="error").inc()
        logger.info("contract_validated contract=triage_next_step result=error")
        raise


def validate_triage_request(payload: dict[str, object]) -> TriageAnalysisRequest:
    try:
        model = TriageAnalysisRequest.model_validate(payload)
        CONTRACT_VALIDATIONS.labels(contract="triage_request", result="ok").inc()
        logger.info(
            "contract_validated contract=triage_request result=ok "
            "event_id=%s tenant_id=%s lead_id=%s",
            model.event_id,
            model.tenant_id,
            model.lead_id,
        )
        return model
    except ValidationError:
        CONTRACT_VALIDATIONS.labels(contract="triage_request", result="error").inc()
        logger.info("contract_validated contract=triage_request result=error")
        raise
