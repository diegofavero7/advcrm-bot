"""Loader de prompts versionados com hash identificável."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources


@dataclass(frozen=True, slots=True)
class PromptResource:
    name: str
    version: str
    text: str
    sha256: str


def _load_text(filename: str) -> str:
    return (
        resources.files("app.prompts").joinpath(filename).read_text(encoding="utf-8").strip() + "\n"
    )


@lru_cache
def load_lead_understanding_prompt() -> PromptResource:
    text = _load_text("lead_understanding.v9.txt")
    return PromptResource(
        name="lead_understanding",
        version="lead_understanding.v9",
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


@lru_cache
def load_triage_next_step_prompt() -> PromptResource:
    text = _load_text("triage_next_step.v3.txt")
    return PromptResource(
        name="triage_next_step",
        version="triage_next_step.v3",
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


@lru_cache
def load_safety_signals_prompt(schema_version: str = "safety_signals.v2") -> PromptResource:
    """Prompt alinhado ao contrato ativo.

    Contrato v1 → prompt ``safety_signals.v2`` (legado).
    Contrato v2 → prompt ``safety_signals.v4`` (asserção/temporalidade/cues + anti-FP).
    Prompt ``safety_signals.v3`` permanece no repositório (não carregado por padrão).
    """
    if schema_version == "safety_signals.v1":
        text = _load_text("safety_signals.v2.txt")
        version = "safety_signals.v2"
    elif schema_version == "safety_signals.v2":
        text = _load_text("safety_signals.v4.txt")
        version = "safety_signals.v4"
    else:
        raise ValueError(f"schema_version de safety desconhecida: {schema_version}")
    return PromptResource(
        name="safety_signals",
        version=version,
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def clear_prompt_cache() -> None:
    load_lead_understanding_prompt.cache_clear()
    load_triage_next_step_prompt.cache_clear()
    load_safety_signals_prompt.cache_clear()
