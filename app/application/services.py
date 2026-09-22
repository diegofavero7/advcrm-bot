"""Serviços de compreensão, próximo passo e pipeline de triagem."""

from __future__ import annotations

from typing import Protocol

from pydantic import ValidationError

from app.application.context_builder import (
    build_next_step_context,
    build_safety_signals_context,
    build_understanding_context,
)
from app.application.degraded_path import allows_safety_after_understanding_failure
from app.clients.errors import (
    AiRuntimeContractVersionError,
    AiRuntimeError,
    AiRuntimePolicyError,
    AiRuntimeSchemaValidationError,
    AiRuntimeSemanticValidationError,
)
from app.clients.schemas import (
    SCHEMA_NAME_LEAD,
    SCHEMA_NAME_NEXT_STEP,
    SCHEMA_NAME_SAFETY,
    SCHEMA_NAME_SAFETY_V2,
    get_lead_understanding_schema,
    get_safety_signals_schema,
    get_triage_next_step_schema,
)
from app.clients.types import StructuredCompletionResult
from app.clients.validation_diagnostics import (
    is_semantic_coherence_failure,
    sanitize_validation_error,
)
from app.config import Settings, get_settings
from app.domain.fact_trust import trusted_crm_origin_violations
from app.observability import FAIL_CLOSED, get_logger
from app.playbooks import Playbook, resolve_playbook
from app.policies import (
    check_area_subject_coherence,
    check_secondary_area_distinct,
    detect_silent_reclassification,
    must_block_question_when_handoff_required,
)
from app.policies.degraded_safety import (
    DEGRADED_POLICY_VERSION,
    DegradedSafetyDecision,
    ExtractorOnlySafety,
    decide_degraded_safety_action,
)
from app.policies.resolution import (
    POLICY_VERSION,
    PolicyResolution,
    primary_rule_id,
    reconcile_next_step,
    resolve_conservative_policy,
)
from app.prompts import (
    load_lead_understanding_prompt,
    load_safety_signals_prompt,
    load_triage_next_step_prompt,
)
from app.safety.compose import COMPOSITION_VERSION, compose_effective_safety_signals
from app.safety.evidence import validate_safety_message_ids
from app.safety.taxonomy_risks import derive_risks_from_validated_subject
from app.schemas.inbound import TriageAnalysisRequest
from app.schemas.lead_understanding import LeadUnderstanding
from app.schemas.proposal import (
    DecisionProvenance,
    ProposalError,
    ProposalMetadata,
    ProposalStatus,
    SafeFallback,
    TriageProposal,
)
from app.schemas.safety_signals import SafetySignals, SafetySignalsV2
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
        organization_id: str | None = None,
    ) -> StructuredCompletionResult: ...


def _safe_fallback(category: str, message: str) -> SafeFallback:
    return SafeFallback(
        source="deterministic_policy",
        recommended_internal_action="request_human_review",
        reason_category=category,
        message=message,
    )


def _apply_error_telemetry(meta: ProposalMetadata, exc: AiRuntimeError, *, stage: str) -> None:
    if exc.model is not None:
        meta.model = exc.model
    if stage == "understanding":
        if exc.prompt_version is not None:
            meta.prompt_version_understanding = exc.prompt_version
        if exc.prompt_hash is not None:
            meta.prompt_hash_understanding = exc.prompt_hash
        if exc.latency_ms is not None:
            meta.understanding_latency_ms = exc.latency_ms
        if exc.retry_count is not None:
            meta.understanding_retry_count = exc.retry_count
        if exc.request_id is not None:
            meta.request_id_understanding = exc.request_id
    elif stage == "next_step":
        if exc.prompt_version is not None:
            meta.prompt_version_next_step = exc.prompt_version
        if exc.prompt_hash is not None:
            meta.prompt_hash_next_step = exc.prompt_hash
        if exc.latency_ms is not None:
            meta.next_step_latency_ms = exc.latency_ms
        if exc.retry_count is not None:
            meta.next_step_retry_count = exc.retry_count
        if exc.request_id is not None:
            meta.request_id_next_step = exc.request_id
    elif stage == "safety_signals":
        if exc.prompt_version is not None:
            meta.prompt_version_safety_signals = exc.prompt_version
        if exc.prompt_hash is not None:
            meta.prompt_hash_safety_signals = exc.prompt_hash
        if exc.latency_ms is not None:
            meta.safety_signals_latency_ms = exc.latency_ms
        if exc.retry_count is not None:
            meta.safety_signals_retry_count = exc.retry_count
        if exc.request_id is not None:
            meta.request_id_safety_signals = exc.request_id


def _proposal_error(exc: AiRuntimeError, *, stage: str) -> ProposalError:
    details = getattr(exc, "details", None)
    rejected = getattr(exc, "rejected_payload", None)
    return ProposalError(
        category=exc.category,
        message=exc.message,
        stage=stage,
        details=details if isinstance(details, list) else None,
        http_status=exc.http_status,
        request_id=exc.request_id,
        retry_count=exc.retry_count,
        latency_ms=exc.latency_ms,
        rejected_payload=rejected if isinstance(rejected, dict) else None,
    )


def _empty_metadata(settings: Settings) -> ProposalMetadata:
    return ProposalMetadata(
        model=settings.ai_runtime_model,
        prompt_version_understanding=None,
        prompt_version_next_step=None,
        prompt_version_safety_signals=None,
        prompt_hash_understanding=None,
        prompt_hash_next_step=None,
        prompt_hash_safety_signals=None,
        schema_version_understanding="lead_understanding.v1",
        schema_version_next_step="triage_next_step.v1",
        schema_version_safety_signals=settings.ai_safety_signals_schema_version,
        taxonomy_version=settings.taxonomy_version,
        playbook_id=None,
        playbook_version=None,
        understanding_latency_ms=None,
        next_step_latency_ms=None,
        safety_signals_latency_ms=None,
        understanding_retry_count=None,
        next_step_retry_count=None,
        safety_signals_retry_count=None,
        request_id_understanding=None,
        request_id_next_step=None,
        request_id_safety_signals=None,
        policy_version=None,
        policy_rule_id=None,
        composition_version=None,
    )


def _provenance_from_policy(
    policy: PolicyResolution,
    *,
    source: str,
    policy_status: str,
    model_inference_skipped: bool,
) -> DecisionProvenance:
    return DecisionProvenance(
        source=source,  # type: ignore[arg-type]
        policy_version=policy.policy_version,
        policy_rule_id=policy.policy_rule_id,
        policy_flags=list(policy.policy_flags),
        advisory_flags=list(policy.advisory_flags),
        policy_status=policy_status,  # type: ignore[arg-type]
        model_inference_skipped=model_inference_skipped,
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
            organization_id=self._settings.ai_runtime_organization_id,
        )
        try:
            understanding = LeadUnderstanding.model_validate(completion.parsed_content)
        except ValidationError as exc:
            details = sanitize_validation_error(exc)
            rejected = (
                completion.parsed_content if isinstance(completion.parsed_content, dict) else None
            )
            if is_semantic_coherence_failure(details):
                logger.info(
                    "semantic_validation_failed contract=lead_understanding issues=%d",
                    len(details),
                )
                raise AiRuntimeSemanticValidationError(
                    "lead_understanding incoerente (validação semântica)",
                    details=details,
                    rejected_payload=rejected,
                    latency_ms=completion.latency_ms,
                    retry_count=completion.retry_count,
                    request_id=completion.request_id,
                    model=completion.model,
                    prompt_version=prompt.version,
                    prompt_hash=prompt.sha256,
                ) from exc
            logger.info(
                "schema_validation_failed contract=lead_understanding issues=%d",
                len(details),
            )
            raise AiRuntimeSchemaValidationError(
                "lead_understanding fora do schema",
                details=details,
                rejected_payload=rejected,
                latency_ms=completion.latency_ms,
                retry_count=completion.retry_count,
                request_id=completion.request_id,
                model=completion.model,
                prompt_version=prompt.version,
                prompt_hash=prompt.sha256,
            ) from exc

        rejected = (
            completion.parsed_content if isinstance(completion.parsed_content, dict) else None
        )
        crm_trust_details = trusted_crm_origin_violations(
            understanding.case_facts,
            request.known_facts,
        )
        if crm_trust_details:
            logger.info(
                "semantic_validation_failed contract=lead_understanding "
                "reason=unverified_trusted_crm_context issues=%d",
                len(crm_trust_details),
            )
            raise AiRuntimeSemanticValidationError(
                "case_facts declara confiança CRM sem suporte em known_facts",
                details=crm_trust_details,
                rejected_payload=rejected,
                latency_ms=completion.latency_ms,
                retry_count=completion.retry_count,
                request_id=completion.request_id,
                model=completion.model,
                prompt_version=prompt.version,
                prompt_hash=prompt.sha256,
            )

        if understanding.schema_version != "lead_understanding.v1":
            raise AiRuntimeContractVersionError(
                "schema_version inválida",
                latency_ms=completion.latency_ms,
                retry_count=completion.retry_count,
                request_id=completion.request_id,
                model=completion.model,
                prompt_version=prompt.version,
                prompt_hash=prompt.sha256,
            )

        if not check_area_subject_coherence(understanding.primary_area, understanding.subject):
            raise AiRuntimeSemanticValidationError(
                "área/assunto incoerentes",
                latency_ms=completion.latency_ms,
                retry_count=completion.retry_count,
                request_id=completion.request_id,
                model=completion.model,
                prompt_version=prompt.version,
                prompt_hash=prompt.sha256,
            )
        if not check_secondary_area_distinct(
            understanding.primary_area, understanding.secondary_area
        ):
            raise AiRuntimeSemanticValidationError(
                "secondary_area igual à primary",
                latency_ms=completion.latency_ms,
                retry_count=completion.retry_count,
                request_id=completion.request_id,
                model=completion.model,
                prompt_version=prompt.version,
                prompt_hash=prompt.sha256,
            )

        if request.previous_decision is not None and detect_silent_reclassification(
            request.previous_decision.primary_area,
            request.previous_decision.subject,
            understanding.primary_area,
            understanding.subject,
            explicitly_acknowledged=understanding.ambiguity.present,
        ):
            raise AiRuntimeSemanticValidationError(
                "reclassificação silenciosa proibida",
                latency_ms=completion.latency_ms,
                retry_count=completion.retry_count,
                request_id=completion.request_id,
                model=completion.model,
                prompt_version=prompt.version,
                prompt_hash=prompt.sha256,
            )

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
            organization_id=self._settings.ai_runtime_organization_id,
        )
        try:
            next_step = TriageNextStep.model_validate(completion.parsed_content)
        except ValidationError as exc:
            details = sanitize_validation_error(exc)
            rejected = (
                completion.parsed_content if isinstance(completion.parsed_content, dict) else None
            )
            if is_semantic_coherence_failure(details):
                logger.info(
                    "semantic_validation_failed contract=triage_next_step issues=%d",
                    len(details),
                )
                raise AiRuntimeSemanticValidationError(
                    "triage_next_step incoerente (validação semântica)",
                    details=details,
                    rejected_payload=rejected,
                    latency_ms=completion.latency_ms,
                    retry_count=completion.retry_count,
                    request_id=completion.request_id,
                    model=completion.model,
                    prompt_version=prompt.version,
                    prompt_hash=prompt.sha256,
                ) from exc
            logger.info(
                "schema_validation_failed contract=triage_next_step issues=%d",
                len(details),
            )
            raise AiRuntimeSchemaValidationError(
                "triage_next_step fora do schema",
                details=details,
                rejected_payload=rejected,
                latency_ms=completion.latency_ms,
                retry_count=completion.retry_count,
                request_id=completion.request_id,
                model=completion.model,
                prompt_version=prompt.version,
                prompt_hash=prompt.sha256,
            ) from exc

        if next_step.schema_version != "triage_next_step.v1":
            raise AiRuntimeContractVersionError(
                "schema_version inválida",
                latency_ms=completion.latency_ms,
                retry_count=completion.retry_count,
                request_id=completion.request_id,
                model=completion.model,
                prompt_version=prompt.version,
                prompt_hash=prompt.sha256,
            )

        if must_block_question_when_handoff_required(
            next_step.requires_human_handoff, next_step.action
        ):
            raise AiRuntimeSemanticValidationError(
                "handoff obrigatório impede ask_question",
                latency_ms=completion.latency_ms,
                retry_count=completion.retry_count,
                request_id=completion.request_id,
                model=completion.model,
                prompt_version=prompt.version,
                prompt_hash=prompt.sha256,
            )

        if (
            last_bot_question
            and next_step.proposed_question
            and next_step.proposed_question.strip() == last_bot_question.strip()
        ):
            raise AiRuntimeSemanticValidationError(
                "pergunta repetida",
                latency_ms=completion.latency_ms,
                retry_count=completion.retry_count,
                request_id=completion.request_id,
                model=completion.model,
                prompt_version=prompt.version,
                prompt_hash=prompt.sha256,
            )

        return next_step, completion, playbook, prompt.version, prompt.sha256


class SafetySignalsService:
    def __init__(
        self,
        client: StructuredAiClient,
        settings: Settings | None = None,
    ) -> None:
        self._client = client
        self._settings = settings or get_settings()

    async def run(
        self, request: TriageAnalysisRequest
    ) -> tuple[SafetySignals | SafetySignalsV2, StructuredCompletionResult, str, str]:
        schema_version = self._settings.ai_safety_signals_schema_version
        prompt = load_safety_signals_prompt(schema_version)
        schema = get_safety_signals_schema(schema_version)
        context = build_safety_signals_context(request, self._settings)
        messages = [
            {"role": "system", "content": prompt.text},
            {"role": "user", "content": context},
        ]
        schema_name = (
            SCHEMA_NAME_SAFETY_V2 if schema_version == "safety_signals.v2" else SCHEMA_NAME_SAFETY
        )
        completion = await self._client.complete_structured(
            schema_name=schema_name,
            json_schema=schema,
            messages=messages,
            contract_label="safety_signals",
            organization_id=self._settings.ai_runtime_organization_id,
        )
        rejected = (
            completion.parsed_content if isinstance(completion.parsed_content, dict) else None
        )
        try:
            if schema_version == "safety_signals.v2":
                signals: SafetySignals | SafetySignalsV2 = SafetySignalsV2.model_validate(
                    completion.parsed_content
                )
            else:
                signals = SafetySignals.model_validate(completion.parsed_content)
        except ValidationError as exc:
            details = sanitize_validation_error(exc)
            if is_semantic_coherence_failure(details):
                logger.info(
                    "semantic_validation_failed contract=safety_signals issues=%d",
                    len(details),
                )
                raise AiRuntimeSemanticValidationError(
                    "safety_signals incoerente (validação semântica)",
                    details=details,
                    rejected_payload=rejected,
                    latency_ms=completion.latency_ms,
                    retry_count=completion.retry_count,
                    request_id=completion.request_id,
                    model=completion.model,
                    prompt_version=prompt.version,
                    prompt_hash=prompt.sha256,
                ) from exc
            logger.info(
                "schema_validation_failed contract=safety_signals issues=%d",
                len(details),
            )
            raise AiRuntimeSchemaValidationError(
                "safety_signals fora do schema",
                details=details,
                rejected_payload=rejected,
                latency_ms=completion.latency_ms,
                retry_count=completion.retry_count,
                request_id=completion.request_id,
                model=completion.model,
                prompt_version=prompt.version,
                prompt_hash=prompt.sha256,
            ) from exc

        expected_version = schema_version
        if signals.schema_version != expected_version:
            raise AiRuntimeContractVersionError(
                "schema_version inválida",
                latency_ms=completion.latency_ms,
                retry_count=completion.retry_count,
                request_id=completion.request_id,
                model=completion.model,
                prompt_version=prompt.version,
                prompt_hash=prompt.sha256,
            )

        validate_safety_message_ids(
            signals,
            request,
            latency_ms=completion.latency_ms,
            retry_count=completion.retry_count,
            request_id=completion.request_id,
            model=completion.model,
            prompt_version=prompt.version,
            prompt_hash=prompt.sha256,
            rejected_payload=rejected if isinstance(rejected, dict) else None,
        )

        return signals, completion, prompt.version, prompt.sha256


class TriagePipelineService:
    def __init__(
        self,
        client: StructuredAiClient,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._understanding = LeadUnderstandingService(client, self._settings)
        self._safety = SafetySignalsService(client, self._settings)
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
            _apply_error_telemetry(meta, exc, stage="understanding")
            if allows_safety_after_understanding_failure(exc.category):
                return await self._run_degraded_safety_path(
                    request,
                    meta=meta,
                    understanding_error=exc,
                )
            return TriageProposal(
                event_id=request.event_id,
                status=ProposalStatus.FAILED_CLOSED,
                lead_understanding=None,
                triage_next_step=None,
                model_next_step_proposal=None,
                safety_signals=None,
                effective_safety_signals=None,
                decision_provenance=DecisionProvenance(
                    source="deterministic_policy",
                    policy_version=POLICY_VERSION,
                    policy_rule_id=None,
                    policy_flags=[],
                    advisory_flags=[],
                    policy_status="not_reached",
                    model_inference_skipped=True,
                ),
                safe_fallback=_safe_fallback(exc.category, exc.message),
                error=_proposal_error(exc, stage="understanding"),
                metadata=meta,
            )

        # Derivação por taxonomia (determinística; subject já validado).
        taxonomy_derived = derive_risks_from_validated_subject(
            understanding.primary_area,
            understanding.subject,
        )

        # Extrator estreito sequencial (mensagens originais; não muta understanding).
        safety_signals: SafetySignals | SafetySignalsV2 | None = None
        try:
            safety_signals, s_comp, s_ver, s_hash = await self._safety.run(request)
            meta.model = s_comp.model or meta.model
            meta.prompt_version_safety_signals = s_ver
            meta.prompt_hash_safety_signals = s_hash
            meta.safety_signals_latency_ms = s_comp.latency_ms
            meta.safety_signals_retry_count = s_comp.retry_count
            meta.request_id_safety_signals = s_comp.request_id
        except AiRuntimeError as exc:
            FAIL_CLOSED.labels(stage="safety_signals", category=exc.category).inc()
            logger.info(
                "pipeline_fail_closed stage=safety_signals category=%s event_id=%s",
                exc.category,
                request.event_id,
            )
            _apply_error_telemetry(meta, exc, stage="safety_signals")
            return TriageProposal(
                event_id=request.event_id,
                status=ProposalStatus.FAILED_CLOSED,
                lead_understanding=understanding,
                triage_next_step=None,
                model_next_step_proposal=None,
                safety_signals=None,
                effective_safety_signals=None,
                decision_provenance=DecisionProvenance(
                    source="deterministic_policy",
                    policy_version=POLICY_VERSION,
                    policy_rule_id=None,
                    policy_flags=[],
                    advisory_flags=[],
                    policy_status="not_reached",
                    model_inference_skipped=True,
                ),
                safe_fallback=_safe_fallback(exc.category, exc.message),
                error=_proposal_error(exc, stage="safety_signals"),
                metadata=meta,
            )

        effective_safety = compose_effective_safety_signals(
            model_detected=list(understanding.safety.detected_risks),
            taxonomy_derived=taxonomy_derived,
            extractor=safety_signals,
            subject=understanding.subject,
            case_facts=list(understanding.case_facts),
            allowed_message_ids=frozenset(m.message_id for m in request.messages),
        )

        # Política só sobre entendimento validado + sinais efetivos (nunca payload rejeitado).
        try:
            policy = resolve_conservative_policy(
                understanding,
                request=request,
                questions_asked=questions_asked,
                settings=self._settings,
                effective_safety=effective_safety,
            )
        except Exception:
            FAIL_CLOSED.labels(stage="policy", category="policy_error").inc()
            logger.info(
                "pipeline_fail_closed stage=policy category=policy_error event_id=%s",
                request.event_id,
            )
            policy_exc = AiRuntimePolicyError("falha ao aplicar política determinística")
            return TriageProposal(
                event_id=request.event_id,
                status=ProposalStatus.FAILED_CLOSED,
                lead_understanding=understanding,
                triage_next_step=None,
                model_next_step_proposal=None,
                safety_signals=safety_signals,
                effective_safety_signals=effective_safety,
                decision_provenance=DecisionProvenance(
                    source="deterministic_policy",
                    policy_version=POLICY_VERSION,
                    policy_rule_id=None,
                    policy_flags=[],
                    advisory_flags=[],
                    policy_status="failed",
                    model_inference_skipped=True,
                ),
                safe_fallback=_safe_fallback(policy_exc.category, policy_exc.message),
                error=_proposal_error(policy_exc, stage="policy"),
                metadata=meta,
            )

        meta.policy_version = policy.policy_version
        meta.policy_rule_id = policy.policy_rule_id
        meta.composition_version = COMPOSITION_VERSION
        playbook = resolve_playbook(understanding.primary_area, understanding.subject)
        if playbook is not None:
            meta.playbook_id = playbook.id
            meta.playbook_version = playbook.version

        if policy.skips_model_inference:
            logger.info(
                "policy_resolved_without_model rule=%s event_id=%s",
                policy.policy_rule_id,
                request.event_id,
            )
            return TriageProposal(
                event_id=request.event_id,
                status=ProposalStatus.SUCCESS,
                lead_understanding=understanding,
                triage_next_step=policy.decision,
                model_next_step_proposal=None,
                safety_signals=safety_signals,
                effective_safety_signals=effective_safety,
                decision_provenance=_provenance_from_policy(
                    policy,
                    source="deterministic_policy",
                    policy_status="applied_mandatory",
                    model_inference_skipped=True,
                ),
                safe_fallback=None,
                error=None,
                metadata=meta,
            )

        try:
            next_step, n_comp, playbook_used, n_ver, n_hash = await self._next_step.run(
                request=request,
                understanding=understanding,
                questions_asked=questions_asked,
                last_bot_question=last_bot_question,
                missing_information=missing_information,
            )
            meta.model = n_comp.model
            meta.prompt_version_next_step = n_ver
            meta.prompt_hash_next_step = n_hash
            if playbook_used is not None:
                meta.playbook_id = playbook_used.id
                meta.playbook_version = playbook_used.version
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
            _apply_error_telemetry(meta, exc, stage="next_step")
            return TriageProposal(
                event_id=request.event_id,
                status=ProposalStatus.FAILED_CLOSED,
                lead_understanding=understanding,
                triage_next_step=None,
                model_next_step_proposal=None,
                safety_signals=safety_signals,
                effective_safety_signals=effective_safety,
                decision_provenance=_provenance_from_policy(
                    policy,
                    source="deterministic_policy",
                    policy_status="advisory_recorded",
                    model_inference_skipped=False,
                ),
                safe_fallback=_safe_fallback(exc.category, exc.message),
                error=_proposal_error(exc, stage="next_step"),
                metadata=meta,
            )

        try:
            effective, source = reconcile_next_step(
                policy=policy,
                model_proposal=next_step,
                understanding=understanding,
            )
        except ValueError as exc:
            FAIL_CLOSED.labels(stage="policy", category="policy_error").inc()
            policy_exc = AiRuntimePolicyError(str(exc))
            return TriageProposal(
                event_id=request.event_id,
                status=ProposalStatus.FAILED_CLOSED,
                lead_understanding=understanding,
                triage_next_step=None,
                model_next_step_proposal=next_step,
                safety_signals=safety_signals,
                effective_safety_signals=effective_safety,
                decision_provenance=_provenance_from_policy(
                    policy,
                    source="deterministic_policy",
                    policy_status="failed",
                    model_inference_skipped=False,
                ),
                safe_fallback=_safe_fallback(policy_exc.category, policy_exc.message),
                error=_proposal_error(policy_exc, stage="policy"),
                metadata=meta,
            )

        # Reconciliação determinística sobre política recomendativa: a proveniência
        # aponta a regra que substituiu a proposta, não um advisory genérico.
        reconciliation_override = source == "deterministic_policy" and not policy.mandatory
        if reconciliation_override:
            provenance = DecisionProvenance(
                source="deterministic_policy",
                policy_version=policy.policy_version,
                policy_rule_id=primary_rule_id(list(effective.policy_flags)),
                policy_flags=list(effective.policy_flags),
                advisory_flags=list(policy.advisory_flags),
                policy_status="applied_mandatory",
                model_inference_skipped=False,
            )
            meta.policy_rule_id = provenance.policy_rule_id
        else:
            provenance = _provenance_from_policy(
                policy,
                source=source,
                policy_status="advisory_recorded",
                model_inference_skipped=False,
            )

        return TriageProposal(
            event_id=request.event_id,
            status=ProposalStatus.SUCCESS,
            lead_understanding=understanding,
            triage_next_step=effective,
            model_next_step_proposal=next_step,
            safety_signals=safety_signals,
            effective_safety_signals=effective_safety,
            decision_provenance=provenance,
            safe_fallback=None,
            error=None,
            metadata=meta,
        )

    async def _run_degraded_safety_path(
        self,
        request: TriageAnalysisRequest,
        *,
        meta: ProposalMetadata,
        understanding_error: AiRuntimeError,
    ) -> TriageProposal:
        """Understanding rejeitado: analisa safety uma vez só com o request autorizado.

        Não promove riscos do payload rejeitado. Não chama next-step.
        """
        understanding_diag = _proposal_error(understanding_error, stage="understanding")
        allowed_ids = frozenset(m.message_id for m in request.messages)

        try:
            safety_signals, s_comp, s_ver, s_hash = await self._safety.run(request)
            meta.model = s_comp.model or meta.model
            meta.prompt_version_safety_signals = s_ver
            meta.prompt_hash_safety_signals = s_hash
            meta.safety_signals_latency_ms = s_comp.latency_ms
            meta.safety_signals_retry_count = s_comp.retry_count
            meta.request_id_safety_signals = s_comp.request_id
        except AiRuntimeError as safety_exc:
            FAIL_CLOSED.labels(stage="safety_signals", category=safety_exc.category).inc()
            logger.info(
                "pipeline_degraded_both_failed understanding=%s safety=%s event_id=%s",
                understanding_error.category,
                safety_exc.category,
                request.event_id,
            )
            _apply_error_telemetry(meta, safety_exc, stage="safety_signals")
            details = list(understanding_diag.details or [])
            safety_details = getattr(safety_exc, "details", None)
            safety_rejected = getattr(safety_exc, "rejected_payload", None)
            secondary: dict[str, object] = {
                "stage": "safety_signals",
                "category": safety_exc.category,
                "message": safety_exc.message,
                "http_status": safety_exc.http_status,
                "request_id": safety_exc.request_id,
                "retry_count": safety_exc.retry_count,
                "latency_ms": safety_exc.latency_ms,
                "data_availability": {
                    "details": ("present" if isinstance(safety_details, list) else "unavailable"),
                    "rejected_payload": (
                        "present" if isinstance(safety_rejected, dict) else "unavailable"
                    ),
                },
            }
            if isinstance(safety_details, list):
                secondary["details"] = safety_details
            if isinstance(safety_rejected, dict):
                # Artefato por caso apenas — não logar payload sensível.
                secondary["rejected_payload"] = safety_rejected
            details.append({"secondary_failure": secondary})
            return TriageProposal(
                event_id=request.event_id,
                status=ProposalStatus.FAILED_CLOSED,
                lead_understanding=None,
                triage_next_step=None,
                model_next_step_proposal=None,
                safety_signals=None,
                effective_safety_signals=None,
                decision_provenance=DecisionProvenance(
                    source="deterministic_policy",
                    policy_version=DEGRADED_POLICY_VERSION,
                    policy_rule_id=None,
                    policy_flags=[],
                    advisory_flags=[],
                    policy_status="not_reached",
                    model_inference_skipped=True,
                ),
                safe_fallback=_safe_fallback(
                    understanding_error.category,
                    (
                        f"{understanding_error.message}; "
                        f"safety também falhou ({safety_exc.category})"
                    ),
                ),
                error=understanding_diag.model_copy(update={"details": details}),
                metadata=meta,
            )

        # Só extrator: zero model_detected / taxonomy / case_facts do understanding rejeitado.
        effective_safety = compose_effective_safety_signals(
            model_detected=[],
            taxonomy_derived=[],
            extractor=safety_signals,
            subject=None,
            case_facts=[],
            allowed_message_ids=allowed_ids,
        )

        try:
            if isinstance(safety_signals, SafetySignalsV2):
                extractor_input = ExtractorOnlySafety.from_validated(
                    signals=safety_signals,
                    effective=effective_safety,
                )
                decision = decide_degraded_safety_action(extractor_input)
            else:
                # v1: sem temporalidade/cues — não inventar; só revisão interna.
                decision = DegradedSafetyDecision(
                    next_step=None,
                    policy_rule_id="degraded_safety_v1_insufficient_representation",
                    policy_flags=("degraded_safety_v1_insufficient_representation",),
                    requires_internal_review=True,
                    policy_version=DEGRADED_POLICY_VERSION,
                )
        except ValueError as exc:
            FAIL_CLOSED.labels(stage="policy", category="policy_error").inc()
            logger.info(
                "pipeline_degraded_composition_rejected event_id=%s reason=%s",
                request.event_id,
                str(exc),
            )
            policy_exc = AiRuntimePolicyError(str(exc))
            return TriageProposal(
                event_id=request.event_id,
                status=ProposalStatus.FAILED_CLOSED,
                lead_understanding=None,
                triage_next_step=None,
                model_next_step_proposal=None,
                safety_signals=safety_signals,
                effective_safety_signals=effective_safety,
                decision_provenance=DecisionProvenance(
                    source="deterministic_policy",
                    policy_version=DEGRADED_POLICY_VERSION,
                    policy_rule_id=None,
                    policy_flags=[],
                    advisory_flags=[],
                    policy_status="failed",
                    model_inference_skipped=True,
                ),
                safe_fallback=_safe_fallback(policy_exc.category, policy_exc.message),
                error=understanding_diag.model_copy(
                    update={
                        "details": [
                            *(understanding_diag.details or []),
                            {
                                "secondary_failure": {
                                    "stage": "policy",
                                    "category": "policy_error",
                                    "message": policy_exc.message,
                                }
                            },
                        ]
                    }
                ),
                metadata=meta,
            )

        meta.policy_version = decision.policy_version
        meta.policy_rule_id = decision.policy_rule_id
        meta.composition_version = COMPOSITION_VERSION

        supporting_refs = list(decision.supporting_occurrence_ids)
        if decision.supporting_cue:
            supporting_refs.append(decision.supporting_cue)

        provenance = DecisionProvenance(
            source="deterministic_policy",
            policy_version=decision.policy_version,
            policy_rule_id=decision.policy_rule_id,
            policy_flags=list(decision.policy_flags),
            advisory_flags=[],
            policy_status="applied_mandatory",
            model_inference_skipped=True,
            supporting_signal_refs=supporting_refs,
        )

        if decision.requires_internal_review:
            logger.info(
                "pipeline_degraded_internal_review rule=%s event_id=%s",
                decision.policy_rule_id,
                request.event_id,
            )
            return TriageProposal(
                event_id=request.event_id,
                status=ProposalStatus.DEGRADED_SAFETY,
                lead_understanding=None,
                triage_next_step=None,
                model_next_step_proposal=None,
                safety_signals=safety_signals,
                effective_safety_signals=effective_safety,
                decision_provenance=provenance,
                safe_fallback=_safe_fallback(
                    understanding_error.category,
                    understanding_error.message,
                ),
                error=understanding_diag,
                metadata=meta,
            )

        logger.info(
            "pipeline_degraded_attendance_handoff rule=%s event_id=%s",
            decision.policy_rule_id,
            request.event_id,
        )
        return TriageProposal(
            event_id=request.event_id,
            status=ProposalStatus.DEGRADED_SAFETY,
            lead_understanding=None,
            triage_next_step=decision.next_step,
            model_next_step_proposal=None,
            safety_signals=safety_signals,
            effective_safety_signals=effective_safety,
            decision_provenance=provenance,
            safe_fallback=None,
            error=understanding_diag,
            metadata=meta,
        )


# Re-export client type for DI
AiRuntimeClientProtocol = StructuredAiClient
