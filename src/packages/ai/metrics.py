from __future__ import annotations

import inspect
import json
import math
import time
from dataclasses import dataclass
from typing import Any

from packages.ai.llm import LlmClient, describe_llm_client
from packages.config.settings import env

LLM_INPUT_COST_PER_1M_TOKENS_ENV = "LLM_INPUT_COST_PER_1M_TOKENS"
LLM_OUTPUT_COST_PER_1M_TOKENS_ENV = "LLM_OUTPUT_COST_PER_1M_TOKENS"


@dataclass(frozen=True, slots=True)
class LlmInvocationMetric:
    workspace_id: str
    provider: str
    model: str
    operation: str
    status: str
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_micros: int
    event_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    error_type: str | None = None


class MeteredLlmClient:
    def __init__(
        self,
        inner: LlmClient,
        recorder: Any,
        *,
        workspace_id: str,
        event_id: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> None:
        self.inner = inner
        self.recorder = recorder
        self.workspace_id = workspace_id
        self.event_id = event_id
        self.correlation_id = correlation_id
        self.causation_id = causation_id

    async def complete(self, prompt: str, **options: Any) -> str:
        started_at = time.perf_counter()
        try:
            output = await self.inner.complete(prompt, **options)
        except Exception as exc:
            await self._record(
                operation="complete",
                status="failed",
                latency_ms=elapsed_ms(started_at),
                prompt=prompt,
                output="",
                error_type=type(exc).__name__,
            )
            raise
        await self._record(
            operation="complete",
            status="succeeded",
            latency_ms=elapsed_ms(started_at),
            prompt=prompt,
            output=output,
        )
        return output

    async def complete_json(self, prompt: str, schema: dict[str, Any], **options: Any) -> Any:
        started_at = time.perf_counter()
        measured_prompt = (
            f"{prompt}\n\n"
            "Return only JSON that matches this JSON Schema:\n"
            f"{json.dumps(schema, ensure_ascii=False, sort_keys=True)}"
        )
        try:
            output = await self.inner.complete_json(prompt, schema, **options)
        except Exception as exc:
            await self._record(
                operation="complete_json",
                status="failed",
                latency_ms=elapsed_ms(started_at),
                prompt=measured_prompt,
                output="",
                error_type=type(exc).__name__,
            )
            raise
        await self._record(
            operation="complete_json",
            status="succeeded",
            latency_ms=elapsed_ms(started_at),
            prompt=measured_prompt,
            output=json.dumps(output, ensure_ascii=False, sort_keys=True),
        )
        return output

    def metadata(self, *, provider: str | None = None) -> dict[str, Any]:
        metadata = getattr(self.inner, "metadata", None)
        if callable(metadata):
            return metadata(provider=provider) if provider is not None else metadata()
        return describe_llm_client(self.inner)

    async def _record(
        self,
        *,
        operation: str,
        status: str,
        latency_ms: int,
        prompt: str,
        output: str,
        error_type: str | None = None,
    ) -> None:
        record = getattr(self.recorder, "record_llm_invocation_metric", None)
        if not callable(record):
            return
        metadata = self.metadata()
        prompt_tokens = estimate_tokens(prompt)
        completion_tokens = estimate_tokens(output)
        sample = LlmInvocationMetric(
            workspace_id=self.workspace_id,
            provider=str(metadata.get("provider") or "unknown"),
            model=str(metadata.get("model") or "unknown"),
            operation=operation,
            status=status,
            latency_ms=max(0, int(latency_ms)),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            estimated_cost_micros=estimated_cost_micros(prompt_tokens, completion_tokens),
            event_id=self.event_id,
            correlation_id=self.correlation_id,
            causation_id=self.causation_id,
            error_type=error_type,
        )
        try:
            result = record(sample)
            if inspect.isawaitable(result):
                await result
        except Exception:
            return


def metered_llm_client(
    inner: LlmClient,
    recorder: Any,
    *,
    workspace_id: str,
    event_id: str | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
) -> LlmClient:
    return MeteredLlmClient(
        inner,
        recorder,
        workspace_id=workspace_id,
        event_id=event_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
    )


def elapsed_ms(started_at: float) -> int:
    return max(0, int((time.perf_counter() - started_at) * 1000))


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def estimated_cost_micros(prompt_tokens: int, completion_tokens: int) -> int:
    input_price = float(env(LLM_INPUT_COST_PER_1M_TOKENS_ENV, "0") or 0)
    output_price = float(env(LLM_OUTPUT_COST_PER_1M_TOKENS_ENV, "0") or 0)
    return max(0, round(prompt_tokens * input_price + completion_tokens * output_price))
