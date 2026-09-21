"""Observabilidade — métricas Prometheus; logs sem texto de conversa."""

from __future__ import annotations

import logging
from typing import Final

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, generate_latest

REGISTRY: Final[CollectorRegistry] = CollectorRegistry()

CONTRACT_VALIDATIONS = Counter(
    "advcrm_bot_contract_validations_total",
    "Validações de contrato por nome e resultado",
    labelnames=("contract", "result"),
    registry=REGISTRY,
)

READY_CHECKS = Counter(
    "advcrm_bot_ready_checks_total",
    "Checagens de readiness",
    labelnames=("result",),
    registry=REGISTRY,
)


def metrics_payload() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


def get_logger(name: str) -> logging.Logger:
    """Logger que deve correlacionar apenas por IDs técnicos."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
