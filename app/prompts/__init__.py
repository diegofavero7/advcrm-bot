"""Prompts versionados do AdvCRM Bot."""

from app.prompts.loader import (
    PromptResource,
    clear_prompt_cache,
    load_lead_understanding_prompt,
    load_triage_next_step_prompt,
)

__all__ = [
    "PromptResource",
    "clear_prompt_cache",
    "load_lead_understanding_prompt",
    "load_triage_next_step_prompt",
]
