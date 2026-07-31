"""LLM Gateway port 와 provider adapter.

에이전트는 `LlmClient` port 하나만 호출 — gateway 가 env 설정으로 provider adapter 를
선택하므로 에이전트 코드 수정 없이 OpenAI/Anthropic/Gemini/OpenAI 호환 교체 가능
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from packages.config.settings import env

LLM_PROVIDER_ENV = "LLM_PROVIDER"
LLM_BASE_URL_ENV = "LLM_BASE_URL"
LLM_API_KEY_ENV = "LLM_API_KEY"
LLM_MODEL_ENV = "LLM_MODEL"
LLM_TIMEOUT_SECONDS_ENV = "LLM_TIMEOUT_SECONDS"
LLM_MAX_RETRIES_ENV = "LLM_MAX_RETRIES"
LLM_MAX_TOKENS_ENV = "LLM_MAX_TOKENS"

OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
OPENAI_BASE_URL_ENV = "OPENAI_BASE_URL"
OPENAI_MODEL_ENV = "OPENAI_MODEL"
OPENAI_COMPATIBLE_API_KEY_ENV = "OPENAI_COMPATIBLE_API_KEY"
OPENAI_COMPATIBLE_BASE_URL_ENV = "OPENAI_COMPATIBLE_BASE_URL"
OPENAI_COMPATIBLE_MODEL_ENV = "OPENAI_COMPATIBLE_MODEL"
ANTHROPIC_API_KEY_ENV = "ANTHROPIC_API_KEY"
ANTHROPIC_BASE_URL_ENV = "ANTHROPIC_BASE_URL"
ANTHROPIC_MODEL_ENV = "ANTHROPIC_MODEL"
ANTHROPIC_VERSION_ENV = "ANTHROPIC_VERSION"
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
GOOGLE_API_KEY_ENV = "GOOGLE_API_KEY"
GEMINI_BASE_URL_ENV = "GEMINI_BASE_URL"
GEMINI_MODEL_ENV = "GEMINI_MODEL"

DEFAULT_LLM_PROVIDER = "unconfigured"
DEFAULT_LLM_TIMEOUT_SECONDS = "30"
DEFAULT_LLM_MAX_RETRIES = "2"
DEFAULT_LLM_MAX_TOKENS = "1024"
DEFAULT_TEMPERATURE = 0.2

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
DEFAULT_ANTHROPIC_MODEL = "claude-3-5-haiku-latest"
DEFAULT_ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

RETRYABLE_HTTP_STATUS = {408, 429, 500, 502, 503, 504}
MAX_RETRY_AFTER_SECONDS = 30.0
JSON_PARSE_ATTEMPTS = 2

PROVIDER_OPENAI = "openai"
PROVIDER_OPENAI_COMPATIBLE = "openai-compatible"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_GEMINI = "gemini"
PROVIDER_UNCONFIGURED = "unconfigured"

_PROVIDER_ALIASES = {
    "": PROVIDER_UNCONFIGURED,
    "unconfigured": PROVIDER_UNCONFIGURED,
    "http": PROVIDER_OPENAI_COMPATIBLE,
    "openai_compatible": PROVIDER_OPENAI_COMPATIBLE,
    "openai-compatible": PROVIDER_OPENAI_COMPATIBLE,
    "compatible": PROVIDER_OPENAI_COMPATIBLE,
    "claude": PROVIDER_ANTHROPIC,
    "anthropic": PROVIDER_ANTHROPIC,
    "google": PROVIDER_GEMINI,
    "gemini": PROVIDER_GEMINI,
    "openai": PROVIDER_OPENAI,
}


class LlmClient(Protocol):
    """에이전트가 사용하는 추상 LLM port"""

    async def complete(self, prompt: str, **options: Any) -> str: ...

    async def complete_json(self, prompt: str, schema: dict[str, Any], **options: Any) -> Any: ...


class LlmProviderAdapter(Protocol):
    """LLM Gateway 뒤의 provider 별 adapter"""

    async def complete(self, request: LlmRequest) -> str: ...


@dataclass(frozen=True, slots=True)
class LlmRequest:
    prompt: str
    model: str
    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int | None = None
    response_format: dict[str, Any] | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LlmProviderSettings:
    provider: str
    base_url: str
    api_key: str
    api_key_env: str | None
    model: str
    timeout_seconds: float
    max_retries: int
    default_max_tokens: int
    anthropic_version: str = DEFAULT_ANTHROPIC_VERSION


class LlmGateway:
    """모든 에이전트 LLM 호출의 단일 진입점"""

    def __init__(
        self,
        *,
        default_provider: str,
        settings_loader: Callable[[str], LlmProviderSettings] = lambda provider: (
            load_provider_settings(provider)
        ),
        adapters: Mapping[str, LlmProviderAdapter] | None = None,
    ) -> None:
        self.default_provider = normalize_provider(default_provider)
        self._settings_loader = settings_loader
        self._adapters: dict[str, LlmProviderAdapter] = dict(adapters or {})
        self._settings: dict[str, LlmProviderSettings] = {}

    async def complete(self, prompt: str, **options: Any) -> str:
        request_options = dict(options)
        provider = normalize_provider(str(request_options.pop("provider", self.default_provider)))
        settings = self._provider_settings(provider)
        request = LlmRequest(
            prompt=prompt,
            model=str(request_options.pop("model", settings.model)),
            temperature=float(request_options.pop("temperature", DEFAULT_TEMPERATURE)),
            max_tokens=_optional_int(request_options.pop("max_tokens", None)),
            response_format=request_options.pop("response_format", None),
            extra=request_options,
        )
        adapter = self._provider_adapter(provider)
        return await self._call_with_retry(
            lambda: adapter.complete(request),
            max_retries=settings.max_retries,
        )

    async def complete_json(self, prompt: str, schema: dict[str, Any], **options: Any) -> Any:
        json_prompt = (
            f"{prompt}\n\n"
            "Return only JSON that matches this JSON Schema:\n"
            f"{json.dumps(schema, ensure_ascii=False, sort_keys=True)}"
        )
        # LLM이 코드펜스로 감싸거나 잘린 JSON을 내는 경우가 있어 파싱 실패는 1회 재요청
        last_error: json.JSONDecodeError | None = None
        for _ in range(JSON_PARSE_ATTEMPTS):
            raw = await self.complete(
                json_prompt,
                response_format={"type": "json_object"},
                **options,
            )
            try:
                return json.loads(_strip_json_fences(raw))
            except json.JSONDecodeError as exc:
                last_error = exc
        raise ValueError("LLM did not return valid JSON") from last_error

    def metadata(self, *, provider: str | None = None) -> dict[str, Any]:
        selected_provider = normalize_provider(provider or self.default_provider)
        settings = self._provider_settings(selected_provider)
        return {
            "provider": settings.provider,
            "model": settings.model,
            "base_url": settings.base_url,
            "timeout_seconds": settings.timeout_seconds,
            "max_retries": settings.max_retries,
        }

    def _provider_settings(self, provider: str) -> LlmProviderSettings:
        if provider not in self._settings:
            self._settings[provider] = self._settings_loader(provider)
        return self._settings[provider]

    def _provider_adapter(self, provider: str) -> LlmProviderAdapter:
        if provider not in self._adapters:
            settings = self._provider_settings(provider)
            self._adapters[provider] = build_provider_adapter(settings)
        return self._adapters[provider]

    async def _call_with_retry(
        self,
        action: Callable[[], Awaitable[str]],
        *,
        max_retries: int,
    ) -> str:
        attempt = 0
        while True:
            server_delay: float | None = None
            try:
                return await action()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in RETRYABLE_HTTP_STATUS or attempt >= max_retries:
                    raise
                server_delay = _retry_after_seconds(exc.response)
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt >= max_retries:
                    raise
            attempt += 1
            # 429/503의 Retry-After가 있으면 서버 지시를 따르고, 없으면 지수 백오프.
            backoff = min(2 ** (attempt - 1), 8)
            await asyncio.sleep(server_delay if server_delay is not None else backoff)


@dataclass(slots=True)
class OpenAiChatCompletionsAdapter:
    settings: LlmProviderSettings
    transport: httpx.AsyncBaseTransport | None = None

    async def complete(self, request: LlmRequest) -> str:
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [{"role": "user", "content": request.prompt}],
            "temperature": request.temperature,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.response_format is not None:
            payload["response_format"] = request.response_format
        payload.update(request.extra)

        data = await self._post_json(
            f"{self.settings.base_url}/chat/completions",
            headers={
                "authorization": f"Bearer {self.settings.api_key}",
                "content-type": "application/json",
            },
            payload=payload,
        )
        return _extract_openai_text(data)

    async def _post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(
            timeout=self.settings.timeout_seconds,
            transport=self.transport,
        ) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()


@dataclass(slots=True)
class AnthropicMessagesAdapter:
    settings: LlmProviderSettings
    transport: httpx.AsyncBaseTransport | None = None

    async def complete(self, request: LlmRequest) -> str:
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [{"role": "user", "content": request.prompt}],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens or self.settings.default_max_tokens,
        }
        payload.update(request.extra)

        async with httpx.AsyncClient(
            timeout=self.settings.timeout_seconds,
            transport=self.transport,
        ) as client:
            response = await client.post(
                f"{self.settings.base_url}/v1/messages",
                headers={
                    "x-api-key": self.settings.api_key,
                    "anthropic-version": self.settings.anthropic_version,
                    "content-type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            return _extract_anthropic_text(response.json())


@dataclass(slots=True)
class GeminiGenerateContentAdapter:
    settings: LlmProviderSettings
    transport: httpx.AsyncBaseTransport | None = None

    async def complete(self, request: LlmRequest) -> str:
        generation_config: dict[str, Any] = {"temperature": request.temperature}
        if request.max_tokens is not None:
            generation_config["maxOutputTokens"] = request.max_tokens
        if (request.response_format or {}).get("type") == "json_object":
            generation_config["responseMimeType"] = "application/json"
        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": request.prompt}]}],
            "generationConfig": generation_config,
        }
        payload.update(request.extra)

        async with httpx.AsyncClient(
            timeout=self.settings.timeout_seconds,
            transport=self.transport,
        ) as client:
            response = await client.post(
                f"{self.settings.base_url}/models/{request.model}:generateContent",
                headers={
                    "x-goog-api-key": self.settings.api_key,
                    "content-type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            return _extract_gemini_text(response.json())


@dataclass(slots=True)
class UnconfiguredLlmAdapter:
    settings: LlmProviderSettings

    async def complete(self, request: LlmRequest) -> str:
        raise ValueError(f"{LLM_PROVIDER_ENV} is required")


def build_llm_client() -> LlmClient:
    # provider 이름 오설정은 여기(부팅)에서 즉시 실패, 미설정/API 키 부재는 요청 시점에
    # ValueError 로 실패해 워커의 실패 이벤트(ai.message.failed 등) 경로로 수렴함.
    return LlmGateway(default_provider=env(LLM_PROVIDER_ENV, DEFAULT_LLM_PROVIDER))


def build_provider_adapter(settings: LlmProviderSettings) -> LlmProviderAdapter:
    if settings.provider == PROVIDER_UNCONFIGURED:
        return UnconfiguredLlmAdapter(settings)
    if settings.provider in {PROVIDER_OPENAI, PROVIDER_OPENAI_COMPATIBLE}:
        return OpenAiChatCompletionsAdapter(settings)
    if settings.provider == PROVIDER_ANTHROPIC:
        return AnthropicMessagesAdapter(settings)
    if settings.provider == PROVIDER_GEMINI:
        return GeminiGenerateContentAdapter(settings)
    raise ValueError(f"unsupported LLM provider: {settings.provider}")


def load_provider_settings(provider: str) -> LlmProviderSettings:
    normalized = normalize_provider(provider)
    timeout_seconds = float(env(LLM_TIMEOUT_SECONDS_ENV, DEFAULT_LLM_TIMEOUT_SECONDS))
    max_retries = int(env(LLM_MAX_RETRIES_ENV, DEFAULT_LLM_MAX_RETRIES))
    default_max_tokens = int(env(LLM_MAX_TOKENS_ENV, DEFAULT_LLM_MAX_TOKENS))

    if normalized == PROVIDER_UNCONFIGURED:
        return LlmProviderSettings(
            provider=normalized,
            base_url="",
            api_key="",
            api_key_env=None,
            model="",
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            default_max_tokens=default_max_tokens,
        )
    if normalized == PROVIDER_OPENAI:
        return _http_settings(
            provider=normalized,
            base_url=_env_first((OPENAI_BASE_URL_ENV, LLM_BASE_URL_ENV), DEFAULT_OPENAI_BASE_URL),
            api_key_names=(OPENAI_API_KEY_ENV, LLM_API_KEY_ENV),
            model=_env_first((OPENAI_MODEL_ENV, LLM_MODEL_ENV), DEFAULT_OPENAI_MODEL),
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            default_max_tokens=default_max_tokens,
        )
    if normalized == PROVIDER_OPENAI_COMPATIBLE:
        return _http_settings(
            provider=normalized,
            base_url=_env_first(
                (OPENAI_COMPATIBLE_BASE_URL_ENV, LLM_BASE_URL_ENV, OPENAI_BASE_URL_ENV),
                DEFAULT_OPENAI_BASE_URL,
            ),
            api_key_names=(
                OPENAI_COMPATIBLE_API_KEY_ENV,
                LLM_API_KEY_ENV,
                OPENAI_API_KEY_ENV,
            ),
            model=_env_first(
                (OPENAI_COMPATIBLE_MODEL_ENV, LLM_MODEL_ENV, OPENAI_MODEL_ENV),
                DEFAULT_OPENAI_MODEL,
            ),
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            default_max_tokens=default_max_tokens,
        )
    if normalized == PROVIDER_ANTHROPIC:
        return _http_settings(
            provider=normalized,
            base_url=env(ANTHROPIC_BASE_URL_ENV, DEFAULT_ANTHROPIC_BASE_URL),
            api_key_names=(ANTHROPIC_API_KEY_ENV,),
            model=_env_first((ANTHROPIC_MODEL_ENV, LLM_MODEL_ENV), DEFAULT_ANTHROPIC_MODEL),
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            default_max_tokens=default_max_tokens,
            anthropic_version=env(ANTHROPIC_VERSION_ENV, DEFAULT_ANTHROPIC_VERSION),
        )
    if normalized == PROVIDER_GEMINI:
        return _http_settings(
            provider=normalized,
            base_url=env(GEMINI_BASE_URL_ENV, DEFAULT_GEMINI_BASE_URL),
            api_key_names=(GEMINI_API_KEY_ENV, GOOGLE_API_KEY_ENV),
            model=_env_first((GEMINI_MODEL_ENV, LLM_MODEL_ENV), DEFAULT_GEMINI_MODEL),
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            default_max_tokens=default_max_tokens,
        )
    raise ValueError(f"unsupported LLM provider: {provider}")


def normalize_provider(provider: str) -> str:
    key = provider.strip().lower().replace(" ", "-")
    if key not in _PROVIDER_ALIASES:
        raise ValueError(f"unsupported LLM provider: {provider}")
    return _PROVIDER_ALIASES[key]


def describe_llm_client(client: LlmClient) -> dict[str, Any]:
    metadata = getattr(client, "metadata", None)
    if callable(metadata):
        return metadata()
    return {"provider": client.__class__.__name__, "model": "unknown"}


def _http_settings(
    *,
    provider: str,
    base_url: str,
    api_key_names: tuple[str, ...],
    model: str,
    timeout_seconds: float,
    max_retries: int,
    default_max_tokens: int,
    anthropic_version: str = DEFAULT_ANTHROPIC_VERSION,
) -> LlmProviderSettings:
    api_key, api_key_env = _first_env_value(api_key_names)
    if not api_key:
        expected = " or ".join(api_key_names)
        raise ValueError(f"{expected} is required for LLM provider {provider}")
    return LlmProviderSettings(
        provider=provider,
        base_url=base_url.rstrip("/"),
        api_key=api_key,
        api_key_env=api_key_env,
        model=model,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        default_max_tokens=default_max_tokens,
        anthropic_version=anthropic_version,
    )


def _env_first(names: tuple[str, ...], default: str) -> str:
    for name in names:
        value = env(name, "")
        if value:
            return value
    return default


def _first_env_value(names: tuple[str, ...]) -> tuple[str, str | None]:
    for name in names:
        value = env(name, "")
        if value:
            return value, name
    return "", None


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after", "")
    try:
        seconds = float(raw)
    except ValueError:
        return None
    if seconds <= 0:
        return None
    return min(seconds, MAX_RETRY_AFTER_SECONDS)


def _strip_json_fences(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```") and text.endswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 : -3]
    return text.strip()


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _extract_openai_text(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        raise ValueError("LLM response did not include choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(item.get("text") or item.get("content"))
            for item in content
            if isinstance(item, dict) and (item.get("text") or item.get("content"))
        ]
        if parts:
            return "\n".join(parts)
    raise ValueError("LLM response message content is missing")


def _extract_anthropic_text(data: dict[str, Any]) -> str:
    content = data.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(item.get("text"))
            for item in content
            if isinstance(item, dict) and item.get("type") == "text" and item.get("text")
        ]
        if parts:
            return "\n".join(parts)
    raise ValueError("Anthropic response text content is missing")


def _extract_gemini_text(data: dict[str, Any]) -> str:
    candidates = data.get("candidates") or []
    if not candidates:
        raise ValueError("Gemini response did not include candidates")
    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []
    text_parts = [
        str(part.get("text")) for part in parts if isinstance(part, dict) and part.get("text")
    ]
    if text_parts:
        return "\n".join(text_parts)
    raise ValueError("Gemini response text content is missing")
