"""Catálogo de taxonomia no contexto de compreensão do lead."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.application.context_builder import build_understanding_context
from app.clients.ai_runtime import AiRuntimeClient
from app.clients.errors import ContextLimitExceededError
from app.clients.schemas import get_lead_understanding_schema
from app.config import Settings
from app.domain.enums import ContentType, LegalArea, MessageDirection, MessageRole, TriageState
from app.prompts import clear_prompt_cache, load_lead_understanding_prompt
from app.schemas.inbound import ConversationMessage, TriageAnalysisRequest
from app.schemas.lead_understanding import LeadUnderstanding
from app.taxonomy import get_taxonomy
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]


def _request_from_example() -> TriageAnalysisRequest:
    example = json.loads(
        (ROOT / "examples/valid/01_prison_allowance_spouse.json").read_text(encoding="utf-8")
    )
    return TriageAnalysisRequest.model_validate(example["request"])


def _extract_context_json(context: str) -> dict[str, object]:
    match = re.search(r"<triage_context>\n(.*)\n</triage_context>", context, re.DOTALL)
    assert match is not None
    data = json.loads(match.group(1))
    assert isinstance(data, dict)
    return data


def test_understanding_context_includes_validator_taxonomy_catalog() -> None:
    taxonomy = get_taxonomy()
    context = build_understanding_context(_request_from_example())
    payload = _extract_context_json(context)
    catalog = payload["taxonomy_catalog"]
    assert isinstance(catalog, dict)
    assert catalog == taxonomy.to_prompt_catalog()
    assert catalog["taxonomy_version"] == taxonomy.taxonomy_version

    areas = catalog["areas"]
    assert isinstance(areas, dict)
    ss = areas["social_security"]["subjects"]
    assert "prison_allowance" in ss
    assert "spouse_relationship" in ss["prison_allowance"]
    # Mesma fonte do validador
    assert taxonomy.is_valid_subject(LegalArea.SOCIAL_SECURITY, "prison_allowance")
    assert set(ss["prison_allowance"]) == set(
        taxonomy.subsubjects_for(LegalArea.SOCIAL_SECURITY, "prison_allowance")
    )


def test_prompt_v4_requires_exact_catalog_ids() -> None:
    clear_prompt_cache()
    prompt = load_lead_understanding_prompt()
    assert prompt.version == "lead_understanding.v9"
    assert "taxonomy_catalog" in prompt.text
    assert "identificadores EXATOS" in prompt.text
    assert "rótulos descritivos" in prompt.text


def test_client_payload_carries_catalog_in_user_message() -> None:
    """Offline: mensagens montadas como o serviço faria incluem o catálogo."""
    clear_prompt_cache()
    prompt = load_lead_understanding_prompt()
    context = build_understanding_context(_request_from_example())
    schema = get_lead_understanding_schema()
    client = AiRuntimeClient(Settings(ai_runtime_enabled=False, ai_runtime_max_tokens=2048))
    payload = client.build_structured_payload(
        json_schema=schema,
        messages=[
            {"role": "system", "content": prompt.text},
            {"role": "user", "content": context},
        ],
    )
    user = payload["messages"][1]["content"]
    assert "taxonomy_catalog" in user
    assert "prison_allowance" in user
    assert "spouse_relationship" in user
    assert payload["schema"] is schema
    # Sem truncamento silencioso: conteúdo completo abaixo do limite por mensagem.
    assert 1 <= len(user) <= 32_000


def test_valid_and_invalid_pairs_still_enforced() -> None:
    example = json.loads(
        (ROOT / "examples/valid/01_prison_allowance_spouse.json").read_text(encoding="utf-8")
    )
    valid = LeadUnderstanding.model_validate(example["lead_understanding"])
    assert valid.subject == "prison_allowance"

    bad = dict(example["lead_understanding"])
    bad["subject"] = "auxilio-reclusao"  # rótulo descritivo, não id do catálogo
    with pytest.raises(ValidationError) as caught:
        LeadUnderstanding.model_validate(bad)
    types = {err["type"] for err in caught.value.errors()}
    assert "subject_not_in_primary_area" in types


def test_context_limit_still_fail_closed_with_catalog() -> None:
    request = TriageAnalysisRequest.model_validate(
        {
            "event_id": "e",
            "tenant_id": "t",
            "lead_id": "l",
            "conversation_id": "c",
            "triage_state": TriageState.PENDING_CLASSIFICATION,
            "source": "whatsapp",
            "messages": [
                ConversationMessage(
                    message_id="m1",
                    role=MessageRole.LEAD,
                    direction=MessageDirection.INBOUND,
                    content_type=ContentType.TEXT,
                    text="x",
                    created_at=datetime(2026, 9, 20, 14, 0, tzinfo=UTC),
                    reply_to_message_id=None,
                )
            ],
            "known_facts": [],
            "previous_decision": None,
        }
    )
    with pytest.raises(ContextLimitExceededError):
        build_understanding_context(request, Settings(ai_context_max_chars=1000))
