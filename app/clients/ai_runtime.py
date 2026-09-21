"""Cliente HTTP assíncrono para AdvCRM AI (Chat Completions + json_schema)."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

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


class AiRuntimeClient:
    """Chama o runtime; não aplica políticas jurídicas."""

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

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        key = self._settings.ai_runtime_api_key
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def build_chat_payload(
        self,
        *,
        schema_name: str,
        json_schema: dict[str, Any],
        messages: list[dict[str, str]],
        allow_missing_model: bool = False,
    ) -> dict[str, Any]:
        model = self._settings.ai_runtime_model
        if not model or not model.strip():
            if allow_missing_model:
                model = "offline-preview"
            else:
                raise AiRuntimeDisabledError("AI_RUNTIME_MODEL não configurado")
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": self._settings.ai_runtime_temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": json_schema,
                },
            },
        }
        if self._settings.ai_runtime_max_tokens is not None:
            payload["max_tokens"] = self._settings.ai_runtime_max_tokens
        return payload

    async def health_check(self) -> None:
        """GET curto no health path — sem inferência."""
        path = self._settings.ai_runtime_health_path
        if not path:
            raise AiRuntimeProtocolError("AI_RUNTIME_HEALTH_PATH não configurado")
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
            raise AiRuntimeTimeoutError("Timeout no health check do runtime") from exc
        except httpx.TransportError as exc:
            raise AiRuntimeConnectionError("Falha de conexão no health check") from exc
        if response.status_code >= 400:
            raise AiRuntimeServerError(f"Health check HTTP {response.status_code}")

    async def complete_structured(
        self,
        *,
        schema_name: str,
        json_schema: dict[str, Any],
        messages: list[dict[str, str]],
        contract_label: str,
    ) -> StructuredCompletionResult:
        if not self._settings.ai_runtime_enabled:
            raise AiRuntimeDisabledError("AI_RUNTIME_ENABLED=false")

        payload = self.build_chat_payload(
            schema_name=schema_name, json_schema=json_schema, messages=messages
        )
        path = self._settings.ai_runtime_chat_completions_path
        max_attempts = self._settings.ai_runtime_max_retries + 1
        retry_count = 0
        last_error: Exception | None = None
        started = time.perf_counter()

        for attempt in range(max_attempts):
            try:
                response = await self._client.post(path, headers=self._headers(), json=payload)
            except httpx.TimeoutException as exc:
                last_error = AiRuntimeTimeoutError("Timeout na chamada ao runtime")
                if attempt + 1 >= max_attempts:
                    self._record_failure(contract_label, "timeout", started, retry_count)
                    raise last_error from exc
                retry_count += 1
                AI_RETRIES.labels(contract=contract_label).inc()
                await asyncio.sleep(self._backoff_seconds(attempt, None))
                continue
            except httpx.TransportError as exc:
                last_error = AiRuntimeConnectionError("Falha de conexão com o runtime")
                if attempt + 1 >= max_attempts:
                    self._record_failure(contract_label, "connection", started, retry_count)
                    raise last_error from exc
                retry_count += 1
                AI_RETRIES.labels(contract=contract_label).inc()
                await asyncio.sleep(self._backoff_seconds(attempt, None))
                continue

            if response.status_code in _RETRYABLE_STATUS:
                retry_after = self._parse_retry_after(response)
                category = "rate_limit" if response.status_code == 429 else "server"
                if attempt + 1 >= max_attempts:
                    self._record_failure(contract_label, category, started, retry_count)
                    if response.status_code == 429:
                        raise AiRuntimeRateLimitError(
                            f"HTTP {response.status_code}",
                            retry_after_seconds=retry_after,
                        )
                    raise AiRuntimeServerError(f"HTTP {response.status_code}")
                retry_count += 1
                AI_RETRIES.labels(contract=contract_label).inc()
                await asyncio.sleep(self._backoff_seconds(attempt, retry_after))
                continue

            if response.status_code in {401, 403}:
                self._record_failure(contract_label, "authentication", started, retry_count)
                raise AiRuntimeAuthenticationError(f"HTTP {response.status_code}")
            if response.status_code == 400:
                self._record_failure(contract_label, "protocol", started, retry_count)
                raise AiRuntimeProtocolError("HTTP 400 do runtime")
            if response.status_code >= 400:
                self._record_failure(contract_label, "server", started, retry_count)
                raise AiRuntimeServerError(f"HTTP {response.status_code}")

            try:
                result = self._parse_success_response(response, started, retry_count)
            except AiRuntimeError:
                latency = (time.perf_counter() - started) * 1000
                AI_LATENCY.labels(contract=contract_label).observe(latency)
                raise

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
        raise last_error or AiRuntimeServerError("Falha desconhecida no runtime")

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

        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AiRuntimeProtocolError("choices ausente ou vazio")
        first = choices[0]
        if not isinstance(first, dict):
            raise AiRuntimeProtocolError("choice inválida")

        finish_reason = first.get("finish_reason")
        if finish_reason is None:
            raise AiRuntimeProtocolError("finish_reason ausente")
        if not isinstance(finish_reason, str):
            raise AiRuntimeProtocolError("finish_reason inválido")
        if finish_reason == "length":
            raise AiRuntimeTruncatedError("Resposta truncada (finish_reason=length)")
        if finish_reason == "refusal":
            raise AiRuntimeRefusalError("Modelo recusou a geração")
        if finish_reason not in _KNOWN_FINISH:
            raise AiRuntimeProtocolError(f"finish_reason desconhecido: {finish_reason}")

        message = first.get("message")
        if not isinstance(message, dict):
            raise AiRuntimeProtocolError("message ausente")
        if message.get("refusal"):
            raise AiRuntimeRefusalError("Campo refusal presente na message")
        content = message.get("content")
        if content is None or (isinstance(content, str) and not content.strip()):
            raise AiRuntimeProtocolError("message.content ausente ou vazio")
        if not isinstance(content, str):
            raise AiRuntimeProtocolError("message.content não é string")

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise AiRuntimeInvalidJsonError("content não é JSON válido") from exc
        if not isinstance(parsed, dict):
            raise AiRuntimeInvalidJsonError("JSON raiz deve ser objeto")

        usage_raw = body.get("usage")
        usage: TokenUsage | None = None
        if isinstance(usage_raw, dict):
            usage = TokenUsage(
                prompt_tokens=usage_raw.get("prompt_tokens"),
                completion_tokens=usage_raw.get("completion_tokens"),
                total_tokens=usage_raw.get("total_tokens"),
            )

        model = body.get("model") or self._settings.ai_runtime_model or "unknown"
        request_id = response.headers.get("x-request-id") or body.get("id")
        if request_id is not None:
            request_id = str(request_id)

        latency_ms = (time.perf_counter() - started) * 1000
        return StructuredCompletionResult(
            parsed_content=parsed,
            model=str(model),
            finish_reason=finish_reason,
            latency_ms=latency_ms,
            retry_count=retry_count,
            request_id=request_id,
            usage=usage,
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
