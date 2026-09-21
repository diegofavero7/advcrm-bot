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
    text = _load_text("lead_understanding.v1.txt")
    return PromptResource(
        name="lead_understanding",
        version="lead_understanding.v1",
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


@lru_cache
def load_triage_next_step_prompt() -> PromptResource:
    text = _load_text("triage_next_step.v1.txt")
    return PromptResource(
        name="triage_next_step",
        version="triage_next_step.v1",
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def clear_prompt_cache() -> None:
    load_lead_understanding_prompt.cache_clear()
    load_triage_next_step_prompt.cache_clear()
