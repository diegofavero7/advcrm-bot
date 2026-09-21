"""Testes de schema hash, prompts e context builder."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.application.context_builder import build_understanding_context
from app.clients.errors import AiRuntimeProtocolError, ContextLimitExceededError
from app.clients.schemas import (
    clear_schema_cache,
    get_lead_understanding_schema,
    schema_hash,
    verify_all_ai_schemas,
)
from app.config import Settings
from app.domain.enums import ContentType, MessageDirection, MessageRole, TriageState
from app.prompts import load_lead_understanding_prompt, load_triage_next_step_prompt
from app.schemas.inbound import ConversationMessage, TriageAnalysisRequest


def test_schema_hash_matches_artifact() -> None:
    clear_schema_cache()
    verify_all_ai_schemas()
    schema = get_lead_understanding_schema()
    assert len(schema_hash(schema)) == 64


def test_schema_divergence_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.clients import schemas as schema_mod

    def fake_artifact(filename: str) -> dict:
        return {"type": "string", "title": "divergent"}

    monkeypatch.setattr(schema_mod, "load_exported_artifact", fake_artifact)
    clear_schema_cache()
    with pytest.raises(AiRuntimeProtocolError, match="Divergência"):
        get_lead_understanding_schema()
    clear_schema_cache()


def test_prompts_have_version_and_hash() -> None:
    p1 = load_lead_understanding_prompt()
    p2 = load_triage_next_step_prompt()
    assert p1.version.startswith("lead_understanding")
    assert p2.version.startswith("triage_next_step")
    assert len(p1.sha256) == 64
    assert (
        "NÃO CONFIÁVEIS" in p1.text
        or "NÃO CONFIÁVEIS" in p1.text.upper()
        or "não confi" in p1.text.lower()
    )


def test_context_builder_preserves_fragments_and_limit() -> None:
    request = TriageAnalysisRequest(
        event_id="e",
        tenant_id="t",
        lead_id="l",
        conversation_id="c",
        triage_state=TriageState.PENDING_CLASSIFICATION,
        source="whatsapp",  # type: ignore[arg-type]
        messages=[
            ConversationMessage(
                message_id="m1",
                role=MessageRole.LEAD,
                direction=MessageDirection.INBOUND,
                content_type=ContentType.TEXT,
                text="acho que 2 anos",
                created_at=datetime(2026, 9, 20, 14, 0, tzinfo=UTC),
                reply_to_message_id=None,
            ),
            ConversationMessage(
                message_id="m2",
                role=MessageRole.LEAD,
                direction=MessageDirection.INBOUND,
                content_type=ContentType.TEXT,
                text="a 3",
                created_at=datetime(2026, 9, 20, 14, 0, 12, tzinfo=UTC),
                reply_to_message_id="m1",
            ),
            ConversationMessage(
                message_id="s1",
                role=MessageRole.SYSTEM,
                direction=MessageDirection.OUTBOUND,
                content_type=ContentType.TEXT,
                text="auto",
                created_at=datetime(2026, 9, 20, 14, 0, 1, tzinfo=UTC),
                reply_to_message_id=None,
            ),
        ],
        known_facts=[],
        previous_decision=None,
    )
    ctx = build_understanding_context(request)
    assert '"message_id": "m1"' in ctx
    assert '"message_id": "m2"' in ctx
    assert "untrusted_user_data" in ctx
    assert 'is_lead_declaration": false' in ctx.replace(" ", "") or ('"role": "system"' in ctx)

    with pytest.raises(ContextLimitExceededError):
        build_understanding_context(request, Settings(ai_context_max_chars=1000))
