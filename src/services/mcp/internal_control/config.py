from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from ipaddress import ip_address
from urllib.parse import urlsplit, urlunsplit

from packages.config.constants import Auth
from packages.config.settings import env
from services.mcp.internal_control import limits as mcp_limits

OPSIA_MCP_API_BASE_URL_ENV = "OPSIA_MCP_API_BASE_URL"
OPSIA_MCP_BEARER_TOKEN_ENV = "OPSIA_MCP_BEARER_TOKEN"
OPSIA_MCP_COOKIE_ENV = "OPSIA_MCP_COOKIE"
OPSIA_MCP_SESSION_COOKIE_ENV = "OPSIA_MCP_SESSION_COOKIE"
OPSIA_MCP_SESSION_COOKIE_NAME_ENV = "OPSIA_MCP_SESSION_COOKIE_NAME"
OPSIA_MCP_TRUSTED_PROXY_SECRET_ENV = "OPSIA_MCP_TRUSTED_PROXY_SECRET"
OPSIA_MCP_ENABLE_WRITES_ENV = "OPSIA_MCP_ENABLE_WRITES"
OPSIA_MCP_ALLOW_INSECURE_HTTP_ENV = "OPSIA_MCP_ALLOW_INSECURE_HTTP"
OPSIA_MCP_TIMEOUT_SECONDS_ENV = "OPSIA_MCP_REQUEST_TIMEOUT_SECONDS"
OPSIA_MCP_MAX_RESPONSE_BYTES_ENV = "OPSIA_MCP_MAX_RESPONSE_BYTES"
MANAGEMENT_BASE_URL_ENV = "MANAGEMENT_BASE_URL"
DEFAULT_TIMEOUT_SECONDS = mcp_limits.DEFAULT_TIMEOUT_SECONDS
MAX_TIMEOUT_SECONDS = mcp_limits.MAX_TIMEOUT_SECONDS
DEFAULT_MAX_RESPONSE_BYTES = mcp_limits.DEFAULT_MAX_RESPONSE_BYTES
MAX_RESPONSE_BYTES_CAP = mcp_limits.MAX_RESPONSE_BYTES_CAP
SUPPORTED_API_BASE_URL_SCHEMES = frozenset({"http", "https"})
COOKIE_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


class McpConfigurationError(RuntimeError):
    """The MCP service cannot safely start with the supplied configuration."""


@dataclass(frozen=True, repr=False)
class McpSettings:
    api_base_url: str
    bearer_token: str = ""
    cookie_header: str = ""
    session_cookie: str = ""
    session_cookie_name: str = Auth.SESSION_COOKIE_NAME
    trusted_proxy_secret: str = ""
    writes_enabled: bool = False
    allow_insecure_http: bool = False
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES

    def __repr__(self) -> str:
        return (
            "McpSettings("
            f"api_base_url={self.api_base_url!r}, "
            f"bearer_token={_redacted(self.bearer_token)!r}, "
            f"cookie_header={_redacted(self.cookie_header)!r}, "
            f"session_cookie={_redacted(self.session_cookie)!r}, "
            f"session_cookie_name={self.session_cookie_name!r}, "
            f"trusted_proxy_secret={_redacted(self.trusted_proxy_secret)!r}, "
            f"writes_enabled={self.writes_enabled!r}, "
            f"allow_insecure_http={self.allow_insecure_http!r}, "
            f"timeout_seconds={self.timeout_seconds!r}, "
            f"max_response_bytes={self.max_response_bytes!r})"
        )

    def validate(self) -> McpSettings:
        api_base_url = _normalize_api_base_url(
            self.api_base_url,
            allow_insecure_http=self.allow_insecure_http,
        )
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise McpConfigurationError(f"{OPSIA_MCP_TIMEOUT_SECONDS_ENV} must be positive")
        if self.timeout_seconds > MAX_TIMEOUT_SECONDS:
            raise McpConfigurationError(
                f"{OPSIA_MCP_TIMEOUT_SECONDS_ENV} must be at most {MAX_TIMEOUT_SECONDS:g}"
            )
        if self.max_response_bytes <= 0:
            raise McpConfigurationError(f"{OPSIA_MCP_MAX_RESPONSE_BYTES_ENV} must be positive")
        if self.max_response_bytes > MAX_RESPONSE_BYTES_CAP:
            raise McpConfigurationError(
                f"{OPSIA_MCP_MAX_RESPONSE_BYTES_ENV} must be at most {MAX_RESPONSE_BYTES_CAP}"
            )
        for name, value in (
            (OPSIA_MCP_BEARER_TOKEN_ENV, self.bearer_token),
            (OPSIA_MCP_COOKIE_ENV, self.cookie_header),
            (OPSIA_MCP_SESSION_COOKIE_ENV, self.session_cookie),
        ):
            _validate_header_value(name, value)
        if self.trusted_proxy_secret.strip():
            raise McpConfigurationError(
                f"{OPSIA_MCP_TRUSTED_PROXY_SECRET_ENV} is not supported for MCP; "
                "use a user-scoped bearer token, cookie header, or session cookie"
            )
        session_cookie_name = self.session_cookie_name.strip()
        if not COOKIE_NAME_RE.fullmatch(session_cookie_name):
            raise McpConfigurationError(
                f"{OPSIA_MCP_SESSION_COOKIE_NAME_ENV} is not a valid cookie name"
            )
        auth_mechanisms = self.auth_mechanisms()
        if not auth_mechanisms:
            raise McpConfigurationError(
                "exactly one of OPSIA_MCP_BEARER_TOKEN, OPSIA_MCP_COOKIE, "
                "or OPSIA_MCP_SESSION_COOKIE is required"
            )
        if len(auth_mechanisms) > 1:
            raise McpConfigurationError(
                "only one MCP authentication mechanism may be configured at a time"
            )
        return replace(
            self,
            api_base_url=api_base_url,
            bearer_token=self.bearer_token.strip(),
            cookie_header=self.cookie_header.strip(),
            session_cookie=self.session_cookie.strip(),
            session_cookie_name=session_cookie_name,
            trusted_proxy_secret=self.trusted_proxy_secret.strip(),
        )

    def has_authentication(self) -> bool:
        return bool(self.auth_mechanisms())

    def auth_mechanisms(self) -> tuple[str, ...]:
        configured: list[str] = []
        if self.bearer_token.strip():
            configured.append("bearer")
        if self.cookie_header.strip():
            configured.append("cookie")
        if self.session_cookie.strip():
            configured.append("session_cookie")
        return tuple(configured)

    def auth_headers(self) -> dict[str, str]:
        if self.trusted_proxy_secret.strip():
            raise McpConfigurationError(
                f"{OPSIA_MCP_TRUSTED_PROXY_SECRET_ENV} is not supported for MCP"
            )
        auth_mechanisms = self.auth_mechanisms()
        if len(auth_mechanisms) != 1:
            raise McpConfigurationError(
                "exactly one MCP authentication mechanism must be configured"
            )
        for name, value in (
            (OPSIA_MCP_BEARER_TOKEN_ENV, self.bearer_token),
            (OPSIA_MCP_COOKIE_ENV, self.cookie_header),
            (OPSIA_MCP_SESSION_COOKIE_ENV, self.session_cookie),
        ):
            _validate_header_value(name, value)
        session_cookie_name = self.session_cookie_name.strip()
        if not COOKIE_NAME_RE.fullmatch(session_cookie_name):
            raise McpConfigurationError(
                f"{OPSIA_MCP_SESSION_COOKIE_NAME_ENV} is not a valid cookie name"
            )
        headers: dict[str, str] = {"accept": "application/json"}
        if self.bearer_token.strip():
            headers["authorization"] = f"Bearer {self.bearer_token.strip()}"
        if self.cookie_header.strip():
            headers["cookie"] = self.cookie_header.strip()
        elif self.session_cookie.strip():
            headers["cookie"] = f"{session_cookie_name}={self.session_cookie.strip()}"
        return headers


def load_settings() -> McpSettings:
    return load_settings_with_auth(
        bearer_token=env(OPSIA_MCP_BEARER_TOKEN_ENV, ""),
        cookie_header=env(OPSIA_MCP_COOKIE_ENV, ""),
        session_cookie=env(OPSIA_MCP_SESSION_COOKIE_ENV, ""),
    )


def load_settings_with_auth(
    *,
    bearer_token: str = "",
    cookie_header: str = "",
    session_cookie: str = "",
    writes_enabled: bool | None = None,
) -> McpSettings:
    timeout = _float_env(OPSIA_MCP_TIMEOUT_SECONDS_ENV, DEFAULT_TIMEOUT_SECONDS)
    max_response_bytes = _int_env(
        OPSIA_MCP_MAX_RESPONSE_BYTES_ENV,
        DEFAULT_MAX_RESPONSE_BYTES,
    )
    return McpSettings(
        api_base_url=_first_env(OPSIA_MCP_API_BASE_URL_ENV, MANAGEMENT_BASE_URL_ENV),
        bearer_token=bearer_token,
        cookie_header=cookie_header,
        session_cookie=session_cookie,
        session_cookie_name=env(OPSIA_MCP_SESSION_COOKIE_NAME_ENV, Auth.SESSION_COOKIE_NAME),
        trusted_proxy_secret=env(OPSIA_MCP_TRUSTED_PROXY_SECRET_ENV, ""),
        writes_enabled=(
            _bool_env(OPSIA_MCP_ENABLE_WRITES_ENV, False)
            if writes_enabled is None
            else bool(writes_enabled)
        ),
        allow_insecure_http=_bool_env(OPSIA_MCP_ALLOW_INSECURE_HTTP_ENV, False),
        timeout_seconds=timeout,
        max_response_bytes=max_response_bytes,
    ).validate()


def configured_session_cookie_name() -> str:
    return env(OPSIA_MCP_SESSION_COOKIE_NAME_ENV, Auth.SESSION_COOKIE_NAME).strip()


def _first_env(*names: str) -> str:
    for name in names:
        value = env(name, "").strip()
        if value:
            return value
    return ""


def _float_env(name: str, default: float) -> float:
    raw = env(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise McpConfigurationError(f"{name} must be a number") from exc


def _int_env(name: str, default: int) -> int:
    raw = env(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise McpConfigurationError(f"{name} must be an integer") from exc


def _bool_env(name: str, default: bool) -> bool:
    raw = env(name, "").strip().casefold()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise McpConfigurationError(f"{name} must be a boolean")


def _normalize_api_base_url(value: str, *, allow_insecure_http: bool = False) -> str:
    raw = value.strip().rstrip("/")
    if not raw:
        raise McpConfigurationError(
            f"{OPSIA_MCP_API_BASE_URL_ENV} or {MANAGEMENT_BASE_URL_ENV} is required"
        )
    if "\\" in raw or any(character.isspace() or ord(character) < 32 for character in raw):
        raise McpConfigurationError("MCP API base URL contains unsafe characters")
    try:
        parsed = urlsplit(raw)
        _ = parsed.port
    except ValueError as exc:
        raise McpConfigurationError("MCP API base URL is invalid") from exc
    if parsed.scheme.lower() not in SUPPORTED_API_BASE_URL_SCHEMES:
        raise McpConfigurationError("MCP API base URL must use http or https")
    if not parsed.hostname:
        raise McpConfigurationError("MCP API base URL must include a host")
    if (
        parsed.scheme.lower() == "http"
        and not allow_insecure_http
        and not _is_loopback_hostname(parsed.hostname)
    ):
        raise McpConfigurationError(
            "MCP API base URL must use https unless it targets loopback; "
            f"set {OPSIA_MCP_ALLOW_INSECURE_HTTP_ENV}=true only for a trusted private network"
        )
    if parsed.username is not None or parsed.password is not None:
        raise McpConfigurationError("MCP API base URL must not include credentials")
    if parsed.query or parsed.fragment:
        raise McpConfigurationError("MCP API base URL must not include query or fragment")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, path, "", ""))


def _is_loopback_hostname(hostname: str) -> bool:
    normalized = hostname.strip().casefold().rstrip(".")
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


def _validate_header_value(name: str, value: str) -> None:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise McpConfigurationError(f"{name} contains unsafe header characters")


def _redacted(value: str) -> str:
    return "<configured>" if value else ""
