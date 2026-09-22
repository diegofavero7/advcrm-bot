"""Cliente HTTP assíncrono para AdvCRM AI (POST /internal/v1/generate/structured)."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from uuid import UUID

import httpx

from app.clients.errors import (
    AiRuntimeAuthenticationError,
    AiRuntimeConnectionError,
    AiRuntimeDisabledError,
    AiRuntimeError,
    AiRuntimeInvalidJsonError,
    AiRuntimeProtocolError,
    AiRuntimeRateLimitError,
    AiRuntimeRefusalError,
    AiRuntimeServerError,
    AiRuntimeStructuredValidationError,
    AiRuntimeTimeoutError,
    AiRuntimeTruncatedError,
)
from app.clients.types import StructuredCompletionResult, TokenUsage
from app.config import Settings, get_settings
from app.observability import (
    AI_CALLS,
    AI_FAILURES,
    AI_LATENCY,
    AI_RETRIES,
    get_logger,
)

logger = get_logger("app.clients.ai_runtime")

_RETRYABLE_STATUS = frozenset({429, 502, 503, 504})
_KNOWN_FINISH = frozenset({"stop", "length", "refusal"})
_ALLOWED_ROLES = frozenset({"system", "user", "assistant"})
_MAX_MESSAGES = 32
_MAX_CONTENT_PER_MESSAGE = 32_000
_MAX_AGGREGATE_CONTENT = 24_000
_MAX_SCHEMA_BYTES = 16 * 1024
_ORGANIZATION_HEADER = "X-AdvCRM-Organization-Id"
_USER_HEADER = "X-AdvCRM-User-Id"
# Código estável documentado pelo AdvCRM AI (docs/internal-api.md).
STRUCTURED_VALIDATION_FAILED_CODE = "structured_validation_failed"
_STRUCTURED_DIAG_STRING_KEYS = frozenset(
    {
        "code",
        "request_id",
        "validator",
        "instance_path",
        "schema_path",
        "missing_properties",
    }
)


class AiRuntimeClient:
    """Adaptador do contrato AdvCRM AI structured generation.

    Não aplica políticas jurídicas. Não usa Chat Completions.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._owned_client = client is None
        if client is not None:
            self._client = client
        else:
            timeout = httpx.Timeout(
                timeout=self._settings.ai_runtime_timeout_seconds,
                connect=self._settings.ai_runtime_connect_timeout_seconds,
            )
            self._client = httpx.AsyncClient(
                base_url=self._settings.ai_runtime_base_url,
                timeout=timeout,
                verify=self._settings.ai_runtime_verify_tls,
                transport=transport,
            )

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def __aenter__(self) -> AiRuntimeClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    def resolve_organization_id(self, organization_id: str | None = None) -> str:
        """Organização confiável: argumento explícito ou config da CLI/runtime.

        Não deriva de tenant_id nem de mensagens do lead.
        """
        value = organization_id or self._settings.ai_runtime_organization_id
        if value is None or not str(value).strip():
            raise AiRuntimeAuthenticationError(
                "organization_id obrigatório (parâmetro ou AI_RUNTIME_ORGANIZATION_ID)"
            )
        try:
            return str(UUID(str(value).strip()))
        except ValueError as exc:
            raise AiRuntimeAuthenticationError(
                "organization_id deve ser UUID (X-AdvCRM-Organization-Id)"
            ) from exc

    def _headers(self, *, organization_id: str | None = None) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        key = self._settings.ai_runtime_api_key
        if key:
            headers["Authorization"] = f"Bearer {key}"
        if organization_id:
            headers[_ORGANIZATION_HEADER] = organization_id
        user_id = self._settings.ai_runtime_user_id
        if user_id:
            headers[_USER_HEADER] = user_id
        return headers

    def build_structured_payload(
        self,
        *,
        json_schema: dict[str, Any],
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Monta o envelope AdvCRM AI — sem model/response_format/strict/schema_name."""
        self._validate_messages(messages)
        self._validate_schema(json_schema)
        max_tokens = self._settings.ai_runtime_max_tokens
        if max_tokens is None:
            raise AiRuntimeProtocolError(
                "AI_RUNTIME_MAX_TOKENS obrigatório para generate/structured "
                "(proposta inicial de teste: 2048; limite do servidor: 1..2048)"
            )
        if not (1 <= max_tokens <= 2048):
            raise AiRuntimeProtocolError("max_tokens deve estar entre 1 e 2048")
        temperature = self._settings.ai_runtime_temperature
        if not (0.0 <= temperature <= 1.0):
            raise AiRuntimeProtocolError("temperature deve estar entre 0 e 1")
        return {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "schema": json_schema,
        }

    def _validate_messages(self, messages: list[dict[str, str]]) -> None:
        if not isinstance(messages, list) or not (1 <= len(messages) <= _MAX_MESSAGES):
            raise AiRuntimeProtocolError(f"messages deve ter entre 1 e {_MAX_MESSAGES} itens")
        total = 0
        for message in messages:
            if not isinstance(message, dict):
                raise AiRuntimeProtocolError("message inválida")
            role = message.get("role")
            content = message.get("content")
            if role not in _ALLOWED_ROLES:
                raise AiRuntimeProtocolError("role deve ser system, user ou assistant")
            if not isinstance(content, str) or not (1 <= len(content) <= _MAX_CONTENT_PER_MESSAGE):
                raise AiRuntimeProtocolError(
                    f"content deve ter entre 1 e {_MAX_CONTENT_PER_MESSAGE} caracteres"
                )
            total += len(content)
        if total > _MAX_AGGREGATE_CONTENT:
            raise AiRuntimeProtocolError(
                f"conteúdo agregado das mensagens excede {_MAX_AGGREGATE_CONTENT} caracteres"
            )

    def _validate_schema(self, json_schema: dict[str, Any]) -> None:
        if not isinstance(json_schema, dict) or not json_schema:
            raise AiRuntimeProtocolError("schema deve ser objeto JSON não vazio")
        encoded = json.dumps(json_schema, ensure_ascii=False).encode("utf-8")
        if len(encoded) > _MAX_SCHEMA_BYTES:
            raise AiRuntimeProtocolError(
                f"schema JSON deve ter no máximo {_MAX_SCHEMA_BYTES} bytes"
            )

    async def ready_check(self) -> None:
        """GET curto em AI_RUNTIME_READY_PATH — sem inferência.

        Em AdvCRM AI, /ready não exige headers S2S do cliente; verifica token S2S
        configurado no próprio runtime e, se INFERENCE_ENABLED, a saúde da inferência.
        """
        path = self._settings.ai_runtime_ready_path
        if not path:
            raise AiRuntimeProtocolError("AI_RUNTIME_READY_PATH não configurado")
        try:
            response = await self._client.get(
                path,
                headers=self._headers(),
                timeout=httpx.Timeout(
                    timeout=self._settings.ai_runtime_connect_timeout_seconds,
                    connect=self._settings.ai_runtime_connect_timeout_seconds,
                ),
            )
        except httpx.TimeoutException as exc:
            raise AiRuntimeTimeoutError("Timeout no ready check do runtime") from exc
        except httpx.TransportError as exc:
            raise AiRuntimeConnectionError("Falha de conexão no ready check") from exc
        if response.status_code >= 400:
            raise AiRuntimeServerError(f"Ready check HTTP {response.status_code}")

    # Compatibilidade com chamadas anteriores de readiness.
    async def health_check(self) -> None:
        await self.ready_check()

    async def complete_structured(
        self,
        *,
        schema_name: str,
        json_schema: dict[str, Any],
        messages: list[dict[str, str]],
        contract_label: str,
        organization_id: str | None = None,
    ) -> StructuredCompletionResult:
        if not self._settings.ai_runtime_enabled:
            raise AiRuntimeDisabledError("AI_RUNTIME_ENABLED=false")

        org = self.resolve_organization_id(organization_id)
        payload = self.build_structured_payload(json_schema=json_schema, messages=messages)
        path = self._settings.ai_runtime_structured_generation_path
        max_attempts = self._settings.ai_runtime_max_retries + 1
        retry_count = 0
        last_error: Exception | None = None
        started = time.perf_counter()
        headers = self._headers(organization_id=org)
        # schema_name é só identificador interno (métricas/logs); não vai no envelope.
        _ = schema_name

        for attempt in range(max_attempts):
            try:
                response = await self._client.post(path, headers=headers, json=payload)
            except httpx.TimeoutException as exc:
                last_error = self._error_with_telemetry(
                    AiRuntimeTimeoutError("Timeout na chamada ao runtime"),
                    started=started,
                    retry_count=retry_count,
                )
                if attempt + 1 >= max_attempts:
                    self._record_failure(contract_label, "timeout", started, retry_count)
                    raise last_error from exc
                retry_count += 1
                AI_RETRIES.labels(contract=contract_label).inc()
                await asyncio.sleep(self._backoff_seconds(attempt, None))
                continue
            except httpx.TransportError as exc:
                last_error = self._error_with_telemetry(
                    AiRuntimeConnectionError("Falha de conexão com o runtime"),
                    started=started,
                    retry_count=retry_count,
                )
                if attempt + 1 >= max_attempts:
                    self._record_failure(contract_label, "connection", started, retry_count)
                    raise last_error from exc
                retry_count += 1
                AI_RETRIES.labels(contract=contract_label).inc()
                await asyncio.sleep(self._backoff_seconds(attempt, None))
                continue

            # Política de não-retry: código estável OU retryable é o booleano False.
            # Nunca usar substring de detail. Campo ausente/inválido/"false" ≠ False.
            error_body = self._parse_error_body_object(response)
            if response.status_code == 502 and self._is_structured_validation_failed(error_body):
                structured_exc = self._structured_validation_error_from_body(
                    error_body, response=response
                )
                self._record_failure(
                    contract_label,
                    structured_exc.category,
                    started,
                    retry_count,
                )
                raise self._error_with_telemetry(
                    structured_exc,
                    started=started,
                    retry_count=retry_count,
                    response=response,
                )

            if response.status_code in _RETRYABLE_STATUS:
                retry_after = self._parse_retry_after(response)
                category = "rate_limit" if response.status_code == 429 else "server"
                explicit_no_retry = self._is_explicit_non_retryable(error_body)
                if attempt + 1 >= max_attempts or explicit_no_retry:
                    self._record_failure(contract_label, category, started, retry_count)
                    if response.status_code == 429:
                        raise self._error_with_telemetry(
                            AiRuntimeRateLimitError(
                                f"HTTP {response.status_code}",
                                retry_after_seconds=retry_after,
                            ),
                            started=started,
                            retry_count=retry_count,
                            response=response,
                        )
                    raise self._error_with_telemetry(
                        AiRuntimeServerError(f"HTTP {response.status_code}"),
                        started=started,
                        retry_count=retry_count,
                        response=response,
                    )
                retry_count += 1
                AI_RETRIES.labels(contract=contract_label).inc()
                await asyncio.sleep(self._backoff_seconds(attempt, retry_after))
                continue

            if response.status_code in {401, 403}:
                self._record_failure(contract_label, "authentication", started, retry_count)
                raise self._error_with_telemetry(
                    AiRuntimeAuthenticationError(f"HTTP {response.status_code}"),
                    started=started,
                    retry_count=retry_count,
                    response=response,
                )
            if response.status_code in {400, 422}:
                # Não registrar body: 422 pode ecoar o input (mensagens/schema).
                self._record_failure(contract_label, "protocol", started, retry_count)
                raise self._error_with_telemetry(
                    AiRuntimeProtocolError(f"HTTP {response.status_code} do runtime"),
                    started=started,
                    retry_count=retry_count,
                    response=response,
                )
            if response.status_code >= 400:
                self._record_failure(contract_label, "server", started, retry_count)
                raise self._error_with_telemetry(
                    AiRuntimeServerError(f"HTTP {response.status_code}"),
                    started=started,
                    retry_count=retry_count,
                    response=response,
                )

            try:
                result = self._parse_success_response(response, started, retry_count)
            except AiRuntimeError as exc:
                latency = (time.perf_counter() - started) * 1000
                AI_LATENCY.labels(contract=contract_label).observe(latency)
                raise self._error_with_telemetry(
                    exc,
                    started=started,
                    retry_count=retry_count,
                    response=response,
                ) from exc

            latency = (time.perf_counter() - started) * 1000
            AI_CALLS.labels(contract=contract_label, result="ok").inc()
            AI_LATENCY.labels(contract=contract_label).observe(latency)
            logger.info(
                "ai_runtime_ok contract=%s latency_ms=%.1f retries=%d finish_reason=%s",
                contract_label,
                result.latency_ms,
                result.retry_count,
                result.finish_reason,
            )
            return result

        self._record_failure(contract_label, "unknown", started, retry_count)
        if last_error is not None:
            raise last_error
        raise self._error_with_telemetry(
            AiRuntimeServerError("Falha desconhecida no runtime"),
            started=started,
            retry_count=retry_count,
        )

    def _parse_success_response(
        self,
        response: httpx.Response,
        started: float,
        retry_count: int,
    ) -> StructuredCompletionResult:
        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise AiRuntimeInvalidJsonError("Body HTTP não é JSON") from exc
        if not isinstance(body, dict):
            raise AiRuntimeProtocolError("Body HTTP não é objeto")

        # Contrato AdvCRM AI: data já é objeto — não usar choices/message.content.
        if "choices" in body:
            raise AiRuntimeProtocolError(
                "Resposta no formato Chat Completions rejeitada; "
                "esperado envelope AdvCRM AI (data/model/finish_reason/usage)"
            )

        data = body.get("data", _MISSING)
        if data is _MISSING:
            raise AiRuntimeProtocolError("data ausente")
        if data is None:
            raise AiRuntimeProtocolError("data é null")
        if isinstance(data, str):
            raise AiRuntimeProtocolError("data não deve ser string")
        if isinstance(data, list):
            raise AiRuntimeProtocolError("data não deve ser array")
        if not isinstance(data, dict):
            raise AiRuntimeProtocolError("data deve ser objeto")

        model = body.get("model")
        if model is None or not isinstance(model, str) or not model.strip():
            raise AiRuntimeProtocolError("model ausente ou inválido")

        finish_reason = body.get("finish_reason")
        if finish_reason is None:
            raise AiRuntimeProtocolError("finish_reason ausente")
        if not isinstance(finish_reason, str):
            raise AiRuntimeProtocolError("finish_reason inválido")
        if finish_reason == "length":
            raise AiRuntimeTruncatedError("Resposta truncada (finish_reason=length)")
        if finish_reason == "refusal":
            # OpenAPI AdvCRM AI não define envelope de refusal; tratar se aparecer.
            raise AiRuntimeRefusalError("Modelo recusou a geração")
        if finish_reason not in _KNOWN_FINISH:
            raise AiRuntimeProtocolError(f"finish_reason desconhecido: {finish_reason}")

        usage = self._parse_usage(body.get("usage", _MISSING))

        request_id = response.headers.get("x-request-id")
        if request_id is not None:
            request_id = str(request_id)

        latency_ms = (time.perf_counter() - started) * 1000
        return StructuredCompletionResult(
            parsed_content=data,
            model=model.strip(),
            finish_reason=finish_reason,
            latency_ms=latency_ms,
            retry_count=retry_count,
            request_id=request_id,
            usage=usage,
        )

    def _parse_usage(self, usage_raw: object) -> TokenUsage:
        if usage_raw is _MISSING:
            raise AiRuntimeProtocolError("usage ausente")
        if not isinstance(usage_raw, dict):
            raise AiRuntimeProtocolError("usage inválido")
        required = ("prompt_tokens", "completion_tokens", "total_tokens")
        values: dict[str, int] = {}
        for key in required:
            value = usage_raw.get(key)
            if not isinstance(value, int) or isinstance(value, bool):
                raise AiRuntimeProtocolError(f"usage.{key} deve ser inteiro")
            values[key] = value
        return TokenUsage(
            prompt_tokens=values["prompt_tokens"],
            completion_tokens=values["completion_tokens"],
            total_tokens=values["total_tokens"],
        )

    def _parse_error_body_object(self, response: httpx.Response) -> dict[str, Any] | None:
        try:
            body = response.json()
        except json.JSONDecodeError:
            return None
        return body if isinstance(body, dict) else None

    @staticmethod
    def _is_structured_validation_failed(body: dict[str, Any] | None) -> bool:
        return body is not None and body.get("code") == STRUCTURED_VALIDATION_FAILED_CODE

    @staticmethod
    def _is_explicit_non_retryable(body: dict[str, Any] | None) -> bool:
        """Só o booleano False conta; ausente, inválido ou string 'false' não."""
        return body is not None and body.get("retryable") is False

    def _structured_validation_error_from_body(
        self,
        body: dict[str, Any] | None,
        *,
        response: httpx.Response,
    ) -> AiRuntimeStructuredValidationError:
        """Monta erro tipado a partir do envelope documentado (sem detail livre)."""
        diag: dict[str, Any] = {"code": STRUCTURED_VALIDATION_FAILED_CODE}
        if isinstance(body, dict):
            retryable = body.get("retryable")
            if isinstance(retryable, bool):
                diag["retryable"] = retryable
            for key in _STRUCTURED_DIAG_STRING_KEYS - {"code"}:
                value = body.get(key)
                if isinstance(value, str) and value:
                    diag[key] = value

        request_id = diag.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            header_id = response.headers.get("x-request-id")
            request_id = str(header_id) if header_id is not None else None

        return AiRuntimeStructuredValidationError(
            details=[diag],
            http_status=502,
            request_id=request_id,
        )

    def _parse_retry_after(self, response: httpx.Response) -> float | None:
        raw = response.headers.get("Retry-After")
        if raw is None:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    def _backoff_seconds(self, attempt: int, retry_after: float | None) -> float:
        exponential = 0.05 * (2**attempt)
        base = 0.5 if exponential > 0.5 else exponential
        if retry_after is None:
            return base
        cap = self._settings.ai_runtime_retry_after_cap_seconds
        capped = cap if retry_after > cap else retry_after
        return capped if capped > base else base

    def _error_with_telemetry(
        self,
        exc: AiRuntimeError,
        *,
        started: float,
        retry_count: int,
        response: httpx.Response | None = None,
    ) -> AiRuntimeError:
        """Anexa latência/retries/status/request_id sem bodies ou credenciais."""
        if exc.latency_ms is None:
            exc.latency_ms = (time.perf_counter() - started) * 1000
        if exc.retry_count is None:
            exc.retry_count = retry_count
        if response is not None:
            if exc.http_status is None:
                exc.http_status = response.status_code
            if exc.request_id is None:
                raw_id = response.headers.get("x-request-id")
                if raw_id is not None:
                    exc.request_id = str(raw_id)
        return exc

    def _record_failure(
        self,
        contract: str,
        category: str,
        started: float,
        retry_count: int,
    ) -> None:
        AI_CALLS.labels(contract=contract, result="error").inc()
        AI_FAILURES.labels(contract=contract, category=category).inc()
        AI_LATENCY.labels(contract=contract).observe((time.perf_counter() - started) * 1000)
        logger.info(
            "ai_runtime_error contract=%s category=%s retries=%d",
            contract,
            category,
            retry_count,
        )


class _MissingType:
    pass


_MISSING = _MissingType()
