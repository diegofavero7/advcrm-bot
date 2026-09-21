"""Observabilidade — métricas Prometheus; logs sem texto de conversa."""

from __future__ import annotations

import logging
from typing import Final

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)

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

AI_CALLS = Counter(
    "advcrm_bot_ai_calls_total",
    "Chamadas ao runtime AI",
    labelnames=("contract", "result"),
    registry=REGISTRY,
)

AI_FAILURES = Counter(
    "advcrm_bot_ai_failures_total",
    "Falhas do runtime por categoria",
    labelnames=("contract", "category"),
    registry=REGISTRY,
)

AI_RETRIES = Counter(
    "advcrm_bot_ai_retries_total",
    "Retries adicionais ao runtime",
    labelnames=("contract",),
    registry=REGISTRY,
)

AI_LATENCY = Histogram(
    "advcrm_bot_ai_latency_ms",
    "Latência das chamadas ao runtime em ms",
    labelnames=("contract",),
    buckets=(50, 100, 250, 500, 1000, 2500, 5000, 15000, 60000),
    registry=REGISTRY,
)

FAIL_CLOSED = Counter(
    "advcrm_bot_fail_closed_total",
    "Resultados fail-closed do pipeline",
    labelnames=("stage", "category"),
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
