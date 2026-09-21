"""Serviços de compreensão, próximo passo e pipeline de triagem."""

from __future__ import annotations

from typing import Protocol

from pydantic import ValidationError

from app.application.context_builder import (
    build_next_step_context,
    build_understanding_context,
)
from app.clients.errors import (
    AiRuntimeContractVersionError,
    AiRuntimeError,
    AiRuntimeSchemaValidationError,
    AiRuntimeSemanticValidationError,
)
from app.clients.schemas import (
    SCHEMA_NAME_LEAD,
    SCHEMA_NAME_NEXT_STEP,
    get_lead_understanding_schema,
    get_triage_next_step_schema,
)
from app.clients.types import StructuredCompletionResult
from app.config import Settings, get_settings
from app.observability import FAIL_CLOSED, get_logger
from app.playbooks import Playbook, resolve_playbook
from app.policies import (
    check_area_subject_coherence,
    check_secondary_area_distinct,
    detect_silent_reclassification,
    must_block_question_when_handoff_required,
)
from app.prompts import (
    load_lead_understanding_prompt,
    load_triage_next_step_prompt,
)
from app.schemas.inbound import TriageAnalysisRequest
from app.schemas.lead_understanding import LeadUnderstanding
from app.schemas.proposal import (
    ProposalError,
    ProposalMetadata,
    ProposalStatus,
    SafeFallback,
    TriageProposal,
)
from app.schemas.triage_next_step import TriageNextStep

logger = get_logger("app.application.services")


class StructuredAiClient(Protocol):
    async def complete_structured(
        self,
        *,
        schema_name: str,
        json_schema: dict[str, object],
        messages: list[dict[str, str]],
        contract_label: str,
    ) -> StructuredCompletionResult: ...


def _safe_fallback(category: str, message: str) -> SafeFallback:
    return SafeFallback(
        source="deterministic_policy",
        recommended_internal_action="request_human_review",
        reason_category=category,
        message=message,
    )


def _empty_metadata(settings: Settings) -> ProposalMetadata:
    return ProposalMetadata(
        model=settings.ai_runtime_model,
        prompt_version_understanding=None,
        prompt_version_next_step=None,
        prompt_hash_understanding=None,
        prompt_hash_next_step=None,
        schema_version_understanding="lead_understanding.v1",
        schema_version_next_step="triage_next_step.v1",
        taxonomy_version=settings.taxonomy_version,
        playbook_id=None,
        playbook_version=None,
        understanding_latency_ms=None,
        next_step_latency_ms=None,
        understanding_retry_count=None,
        next_step_retry_count=None,
        request_id_understanding=None,
        request_id_next_step=None,
    )


class LeadUnderstandingService:
    def __init__(
        self,
        client: StructuredAiClient,
        settings: Settings | None = None,
    ) -> None:
        self._client = client
        self._settings = settings or get_settings()

    async def run(
        self, request: TriageAnalysisRequest
    ) -> tuple[LeadUnderstanding, StructuredCompletionResult, str, str]:
        prompt = load_lead_understanding_prompt()
        schema = get_lead_understanding_schema()
        context = build_understanding_context(request, self._settings)
        messages = [
            {"role": "system", "content": prompt.text},
            {"role": "user", "content": context},
        ]
        completion = await self._client.complete_structured(
            schema_name=SCHEMA_NAME_LEAD,
            json_schema=schema,
            messages=messages,
            contract_label="lead_understanding",
        )
        try:
            understanding = LeadUnderstanding.model_validate(completion.parsed_content)
        except ValidationError as exc:
            raise AiRuntimeSchemaValidationError("lead_understanding fora do schema") from exc

        if understanding.schema_version != "lead_understanding.v1":
            raise AiRuntimeContractVersionError("schema_version inválida")

        if not check_area_subject_coherence(understanding.primary_area, understanding.subject):
            raise AiRuntimeSemanticValidationError("área/assunto incoerentes")
        if not check_secondary_area_distinct(
            understanding.primary_area, understanding.secondary_area
        ):
            raise AiRuntimeSemanticValidationError("secondary_area igual à primary")

        if request.previous_decision is not None and detect_silent_reclassification(
            request.previous_decision.primary_area,
            request.previous_decision.subject,
            understanding.primary_area,
            understanding.subject,
            explicitly_acknowledged=understanding.ambiguity.present,
        ):
            raise AiRuntimeSemanticValidationError("reclassificação silenciosa proibida")

        return understanding, completion, prompt.version, prompt.sha256


class TriageNextStepService:
    def __init__(
        self,
        client: StructuredAiClient,
        settings: Settings | None = None,
    ) -> None:
        self._client = client
        self._settings = settings or get_settings()

    def resolve_playbook_for(self, understanding: LeadUnderstanding) -> Playbook:
        playbook = resolve_playbook(understanding.primary_area, understanding.subject)
        if playbook is None:
            raise AiRuntimeSemanticValidationError("nenhum playbook resolvido")
        return playbook

    async def run(
        self,
        *,
        request: TriageAnalysisRequest,
        understanding: LeadUnderstanding,
        questions_asked: int = 0,
        last_bot_question: str | None = None,
        missing_information: list[str] | None = None,
    ) -> tuple[TriageNextStep, StructuredCompletionResult, Playbook, str, str]:
        playbook = self.resolve_playbook_for(understanding)
        prompt = load_triage_next_step_prompt()
        schema = get_triage_next_step_schema()
        context = build_next_step_context(
            request=request,
            understanding=understanding,
            playbook_id=playbook.id,
            playbook_version=playbook.version,
            questions_asked=questions_asked,
            last_bot_question=last_bot_question,
            missing_information=missing_information or [],
            settings=self._settings,
        )
        messages = [
            {"role": "system", "content": prompt.text},
            {"role": "user", "content": context},
        ]
        completion = await self._client.complete_structured(
            schema_name=SCHEMA_NAME_NEXT_STEP,
            json_schema=schema,
            messages=messages,
            contract_label="triage_next_step",
        )
        try:
            next_step = TriageNextStep.model_validate(completion.parsed_content)
        except ValidationError as exc:
            raise AiRuntimeSchemaValidationError("triage_next_step fora do schema") from exc

        if next_step.schema_version != "triage_next_step.v1":
            raise AiRuntimeContractVersionError("schema_version inválida")

        if must_block_question_when_handoff_required(
            next_step.requires_human_handoff, next_step.action
        ):
            raise AiRuntimeSemanticValidationError("handoff obrigatório impede ask_question")

        if (
            last_bot_question
            and next_step.proposed_question
            and next_step.proposed_question.strip() == last_bot_question.strip()
        ):
            raise AiRuntimeSemanticValidationError("pergunta repetida")

        return next_step, completion, playbook, prompt.version, prompt.sha256


class TriagePipelineService:
    def __init__(
        self,
        client: StructuredAiClient,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._understanding = LeadUnderstandingService(client, self._settings)
        self._next_step = TriageNextStepService(client, self._settings)

    async def run(
        self,
        request: TriageAnalysisRequest,
        *,
        questions_asked: int = 0,
        last_bot_question: str | None = None,
        missing_information: list[str] | None = None,
    ) -> TriageProposal:
        meta = _empty_metadata(self._settings)
        understanding: LeadUnderstanding | None = None

        try:
            understanding, u_comp, u_ver, u_hash = await self._understanding.run(request)
            meta.model = u_comp.model
            meta.prompt_version_understanding = u_ver
            meta.prompt_hash_understanding = u_hash
            meta.understanding_latency_ms = u_comp.latency_ms
            meta.understanding_retry_count = u_comp.retry_count
            meta.request_id_understanding = u_comp.request_id
        except AiRuntimeError as exc:
            FAIL_CLOSED.labels(stage="understanding", category=exc.category).inc()
            logger.info(
                "pipeline_fail_closed stage=understanding category=%s event_id=%s",
                exc.category,
                request.event_id,
            )
            return TriageProposal(
                event_id=request.event_id,
                status=ProposalStatus.FAILED_CLOSED,
                lead_understanding=None,
                triage_next_step=None,
                safe_fallback=_safe_fallback(exc.category, exc.message),
                error=ProposalError(
                    category=exc.category,
                    message=exc.message,
                    stage="understanding",
                ),
                metadata=meta,
            )

        try:
            next_step, n_comp, playbook, n_ver, n_hash = await self._next_step.run(
                request=request,
                understanding=understanding,
                questions_asked=questions_asked,
                last_bot_question=last_bot_question,
                missing_information=missing_information,
            )
            meta.model = n_comp.model
            meta.prompt_version_next_step = n_ver
            meta.prompt_hash_next_step = n_hash
            meta.playbook_id = playbook.id
            meta.playbook_version = playbook.version
            meta.next_step_latency_ms = n_comp.latency_ms
            meta.next_step_retry_count = n_comp.retry_count
            meta.request_id_next_step = n_comp.request_id
        except AiRuntimeError as exc:
            FAIL_CLOSED.labels(stage="next_step", category=exc.category).inc()
            logger.info(
                "pipeline_fail_closed stage=next_step category=%s event_id=%s",
                exc.category,
                request.event_id,
            )
            return TriageProposal(
                event_id=request.event_id,
                status=ProposalStatus.FAILED_CLOSED,
                lead_understanding=understanding,
                triage_next_step=None,
                safe_fallback=_safe_fallback(exc.category, exc.message),
                error=ProposalError(
                    category=exc.category,
                    message=exc.message,
                    stage="next_step",
                ),
                metadata=meta,
            )

        return TriageProposal(
            event_id=request.event_id,
            status=ProposalStatus.SUCCESS,
            lead_understanding=understanding,
            triage_next_step=next_step,
            safe_fallback=None,
            error=None,
            metadata=meta,
        )


# Re-export client type for DI
AiRuntimeClientProtocol = StructuredAiClient
