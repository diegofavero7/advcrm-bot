"""Validação de evidência literal do extrator contra mensagens autorizadas."""

from __future__ import annotations

from app.clients.errors import AiRuntimeSemanticValidationError
from app.domain.enums import ContentType, MessageRole
from app.schemas.inbound import TriageAnalysisRequest
from app.schemas.safety_signals import (
    ExplicitCueState,
    ExplicitSafetyCue,
    SafetySignals,
    SafetySignalsV2,
)


def _lead_text_by_id(request: TriageAnalysisRequest) -> dict[str, str]:
    out: dict[str, str] = {}
    for message in request.messages:
        if message.role != MessageRole.LEAD:
            continue
        if message.content_type != ContentType.TEXT or not message.text:
            out[message.message_id] = ""
            continue
        out[message.message_id] = message.text
    return out


def _quote_in_sources(
    quote: str,
    source_ids: list[str],
    texts: dict[str, str],
) -> bool:
    for mid in source_ids:
        body = texts.get(mid)
        if body is not None and quote in body:
            return True
    return False


def validate_safety_message_ids(
    signals: SafetySignals | SafetySignalsV2,
    request: TriageAnalysisRequest,
    *,
    latency_ms: float | None = None,
    retry_count: int | None = None,
    request_id: str | None = None,
    model: str | None = None,
    prompt_version: str | None = None,
    prompt_hash: str | None = None,
    rejected_payload: dict[str, object] | None = None,
) -> None:
    allowed_ids = {m.message_id for m in request.messages}

    def _raise(message: str, *, detail_type: str, loc: list[object]) -> None:
        raise AiRuntimeSemanticValidationError(
            message,
            latency_ms=latency_ms,
            retry_count=retry_count,
            request_id=request_id,
            model=model,
            prompt_version=prompt_version,
            prompt_hash=prompt_hash,
            details=[{"type": detail_type, "loc": loc}],
            rejected_payload=rejected_payload,
        )

    if isinstance(signals, SafetySignals):
        for idx, item in enumerate(signals.detected_risks):
            for mid in item.source_message_ids:
                if mid not in allowed_ids:
                    _raise(
                        f"source_message_ids referencia id inexistente: {mid}",
                        detail_type="invalid_source_message_id",
                        loc=["detected_risks", idx, "source_message_ids"],
                    )
        return

    texts = _lead_text_by_id(request)
    for idx, occ in enumerate(signals.occurrences):
        for mid in occ.source_message_ids:
            if mid not in allowed_ids:
                _raise(
                    f"source_message_ids referencia id inexistente: {mid}",
                    detail_type="invalid_source_message_id",
                    loc=["occurrences", idx, "source_message_ids"],
                )
        if not _quote_in_sources(occ.evidence_quote, occ.source_message_ids, texts):
            _raise(
                "evidence_quote não encontrado literalmente nas mensagens de origem",
                detail_type="evidence_quote_not_in_source",
                loc=["occurrences", idx, "evidence_quote"],
            )

    for field_name, cue in (
        ("urgent_help_request", signals.urgent_help_request),
        ("immediate_danger", signals.immediate_danger),
    ):
        _validate_cue(
            cue,
            field_name=field_name,
            allowed_ids=allowed_ids,
            texts=texts,
            raise_fn=_raise,
        )


def _validate_cue(
    cue: ExplicitSafetyCue,
    *,
    field_name: str,
    allowed_ids: set[str],
    texts: dict[str, str],
    raise_fn: object,
) -> None:
    if cue.state != ExplicitCueState.PRESENT:
        return
    assert callable(raise_fn)
    for mid in cue.source_message_ids:
        if mid not in allowed_ids:
            raise_fn(
                f"source_message_ids referencia id inexistente: {mid}",
                detail_type="invalid_source_message_id",
                loc=[field_name, "source_message_ids"],
            )
    assert cue.evidence_quote is not None
    if not _quote_in_sources(cue.evidence_quote, cue.source_message_ids, texts):
        raise_fn(
            "evidence_quote não encontrado literalmente nas mensagens de origem",
            detail_type="evidence_quote_not_in_source",
            loc=[field_name, "evidence_quote"],
        )


def occurrence_audit_snapshot(signals: SafetySignalsV2) -> list[dict[str, str]]:
    """Campos não sensíveis para auditoria (sem colar quotes longos em logs)."""
    return [
        {
            "occurrence_id": o.occurrence_id,
            "risk": o.risk.value,
            "assertion": o.assertion.value,
            "temporal_context": o.temporal_context.value,
        }
        for o in signals.occurrences
    ]
