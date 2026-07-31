from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from packages.security.log_lines import redact_log_line
from services.mcp.internal_control import limits as mcp_limits
from services.mcp.internal_control.config import McpSettings

MAX_ERROR_DETAIL_LENGTH = mcp_limits.MAX_ERROR_DETAIL_LENGTH
SUPPORTED_MANAGEMENT_API_METHODS = frozenset({"GET", "POST", "PATCH"})
BODY_MANAGEMENT_API_METHODS = frozenset({"POST", "PATCH"})
ALLOWED_MANAGEMENT_API_EXTRA_HEADERS = frozenset({"idempotency-key"})


@dataclass
class ManagementApiError(RuntimeError):
    status_code: int
    detail: str

    def __str__(self) -> str:
        return f"management API returned {self.status_code}: {self.detail}"


class ManagementApiClient:
    def __init__(
        self,
        settings: McpSettings,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings.validate()
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=self.settings.timeout_seconds)

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request_json("GET", path, params=params)

    async def post_json(
        self,
        path: str,
        body: dict[str, Any],
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return await self._request_json(
            "POST",
            path,
            params=params,
            json_body=body,
            headers=headers,
        )

    async def patch_json(
        self,
        path: str,
        body: dict[str, Any],
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return await self._request_json(
            "PATCH",
            path,
            params=params,
            json_body=body,
            headers=headers,
        )

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        if method not in SUPPORTED_MANAGEMENT_API_METHODS:
            raise ManagementApiError(0, "unsupported management API method")
        if method not in BODY_MANAGEMENT_API_METHODS and json_body is not None:
            raise ManagementApiError(
                0,
                "management API request body is only allowed for POST or PATCH",
            )
        kwargs: dict[str, Any] = {
            "headers": _request_headers(self.settings.auth_headers(), headers or {}),
            "params": _query_params(params or {}),
        }
        if json_body is not None:
            kwargs["json"] = json_body
        async with self._client.stream(
            method,
            self._url(path),
            **kwargs,
        ) as response:
            content = await _read_limited_content(
                response,
                max_bytes=self.settings.max_response_bytes,
            )
        if not response.is_success:
            raise ManagementApiError(response.status_code, _error_detail(response, content))
        try:
            return json.loads(content.decode(response.encoding or "utf-8"))
        except ValueError as exc:
            raise ManagementApiError(response.status_code, "response was not valid JSON") from exc

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _url(self, path: str) -> str:
        safe_path = _validate_api_path(path)
        return f"{self.settings.api_base_url.rstrip('/')}{safe_path}"


def _query_params(params: dict[str, Any]) -> dict[str, str]:
    query: dict[str, str] = {}
    for key, value in params.items():
        if value is None:
            continue
        if not isinstance(key, str) or not key or _has_unsafe_url_character(key):
            raise ManagementApiError(0, "unsafe management API query parameter name")
        if isinstance(value, bool):
            query[key] = "true" if value else "false"
        else:
            text = str(value)
            if _has_unsafe_url_character(text):
                raise ManagementApiError(0, "unsafe management API query parameter value")
            query[key] = text
    return query


def _request_headers(base: dict[str, str], extra: dict[str, str]) -> dict[str, str]:
    headers = dict(base)
    existing = {key.casefold() for key in headers}
    for key, value in extra.items():
        if not isinstance(key, str) or not key or _has_unsafe_url_character(key) or ":" in key:
            raise ManagementApiError(0, "unsafe management API header name")
        normalized_key = key.casefold()
        if normalized_key not in ALLOWED_MANAGEMENT_API_EXTRA_HEADERS:
            raise ManagementApiError(0, "unsupported management API extra header")
        if normalized_key in existing:
            raise ManagementApiError(0, "management API header override is not allowed")
        if not isinstance(value, str) or _has_unsafe_url_character(value):
            raise ManagementApiError(0, "unsafe management API header value")
        headers[key] = value
    return headers


async def _read_limited_content(response: httpx.Response, *, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise ManagementApiError(
                response.status_code,
                f"management API response exceeded {max_bytes} bytes",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _validate_api_path(path: str) -> str:
    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or path.startswith("//")
        or "://" in path
        or "?" in path
        or "#" in path
        or _has_unsafe_url_character(path)
    ):
        raise ManagementApiError(0, "unsafe management API path")
    return path


def _has_unsafe_url_character(value: str) -> bool:
    return "\\" in value or any(character.isspace() or ord(character) < 32 for character in value)


def _error_detail(response: httpx.Response, content: bytes) -> str:
    text = _decode_response_content(response, content)
    try:
        payload = json.loads(text)
    except ValueError:
        return _safe_error_detail(text or response.reason_phrase)
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, str) and detail:
        return _safe_error_detail(detail)
    if detail is not None:
        return _safe_error_detail(json.dumps(detail, ensure_ascii=False, sort_keys=True))
    return _safe_error_detail(response.reason_phrase)


def _safe_error_detail(detail: str) -> str:
    compact = " ".join(detail.split())
    redacted = redact_log_line(compact)
    if len(redacted) <= MAX_ERROR_DETAIL_LENGTH:
        return redacted
    return f"{redacted[:MAX_ERROR_DETAIL_LENGTH]}..."


def _decode_response_content(response: httpx.Response, content: bytes) -> str:
    encoding = response.encoding or "utf-8"
    try:
        return content.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return content.decode("utf-8", errors="replace")
