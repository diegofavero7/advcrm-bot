"""Experimento SetFit — classificação de sinais de atendimento (avaliação apenas).

Não substitui understanding, não altera política e não autoriza handoff.
"""

from __future__ import annotations

EXPERIMENT_ID = "attendance_signals_setfit.v1"
DATASET_PILOT_ID = "attendance_signals_pilot.v1"

# Sinais avaliados (podem coexistir). Conteúdo conversacional — sem confiança CRM.
SIGNAL_EXPLICIT_HUMAN_REQUEST = "explicit_human_request"
SIGNAL_EXISTING_CLIENT_DECLARATION = "existing_client_declaration"
SIGNAL_CASE_STATUS_REQUEST = "case_status_request"

SIGNAL_IDS: tuple[str, ...] = (
    SIGNAL_EXPLICIT_HUMAN_REQUEST,
    SIGNAL_EXISTING_CLIENT_DECLARATION,
    SIGNAL_CASE_STATUS_REQUEST,
)

# Checkpoint base proposto (não baixado nesta etapa).
PROPOSED_CHECKPOINT = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
PROPOSED_CHECKPOINT_LICENSE = "apache-2.0"
PROPOSED_CHECKPOINT_SOURCES = (
    "https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "https://huggingface.co/docs/setfit/en/index",
    "https://huggingface.co/docs/setfit/en/how_to/multilabel",
)

__all__ = [
    "DATASET_PILOT_ID",
    "EXPERIMENT_ID",
    "PROPOSED_CHECKPOINT",
    "PROPOSED_CHECKPOINT_LICENSE",
    "PROPOSED_CHECKPOINT_SOURCES",
    "SIGNAL_CASE_STATUS_REQUEST",
    "SIGNAL_EXISTING_CLIENT_DECLARATION",
    "SIGNAL_EXPLICIT_HUMAN_REQUEST",
    "SIGNAL_IDS",
]
