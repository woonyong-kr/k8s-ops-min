"""Bounded, cached release-manifest check with explicit unavailable states."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from typing import Any
from urllib.parse import urlparse

import httpx

from packages.contracts.bootstrap import VersionCheckResponse

RELEASE_MANIFEST_URL_ENV = "RELEASE_MANIFEST_URL"
CURRENT_VERSION_ENV = "OPSIA_VERSION"
RELEASE_MANIFEST_MAX_BYTES = 65_536
RELEASE_NOTES_MAX_LENGTH = 2_000
RELEASE_CHECK_TIMEOUT_SECONDS = 4.0
RELEASE_CHECK_SUCCESS_TTL_SECONDS = 3_600
RELEASE_CHECK_FAILURE_TTL_SECONDS = 300

SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\."
    r"(?P<minor>0|[1-9]\d*)\."
    r"(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


@dataclass(frozen=True)
class ReleaseManifestFetch:
    status_code: int
    content: bytes


ManifestFetcher = Callable[[str], Awaitable[ReleaseManifestFetch]]
VersionProvider = Callable[[], str]


class VersionCheckService:
    """Coalesce release checks so browser bootstrap cannot stampede the release source."""

    def __init__(
        self,
        *,
        manifest_url: str | None = None,
        current_version: VersionProvider | None = None,
        fetch_manifest: ManifestFetcher | None = None,
    ) -> None:
        self._manifest_url = (
            manifest_url
            if manifest_url is not None
            else os.getenv(RELEASE_MANIFEST_URL_ENV, "").strip()
        )
        self._current_version = current_version or installed_version
        self._fetch_manifest = fetch_manifest or fetch_release_manifest
        self._lock = asyncio.Lock()
        self._cached: VersionCheckResponse | None = None
        self._cached_until = 0.0

    async def check(self) -> VersionCheckResponse:
        now = time.monotonic()
        if self._cached is not None and now < self._cached_until:
            return self._cached
        async with self._lock:
            now = time.monotonic()
            if self._cached is not None and now < self._cached_until:
                return self._cached
            response = await self._check_uncached()
            ttl = (
                RELEASE_CHECK_SUCCESS_TTL_SECONDS
                if response.availability in {"available", "partial"}
                else RELEASE_CHECK_FAILURE_TTL_SECONDS
            )
            self._cached = response
            self._cached_until = now + ttl
            return response

    async def _check_uncached(self) -> VersionCheckResponse:
        observed_at = datetime.now(UTC)
        current_version = self._safe_current_version()
        if not _is_valid_semver(current_version):
            return _unavailable(
                current_version,
                observed_at,
                "current_version_unavailable",
            )
        if not self._manifest_url:
            return _unavailable(
                current_version,
                observed_at,
                "release_manifest_not_configured",
            )
        if not _is_https_url(self._manifest_url):
            return _unavailable(current_version, observed_at, "release_manifest_url_invalid")
        try:
            fetched = await self._fetch_manifest(self._manifest_url)
        except Exception:
            return _unavailable(current_version, observed_at, "release_manifest_unavailable")
        if fetched.status_code != 200:
            return _unavailable(current_version, observed_at, "release_manifest_unavailable")
        if len(fetched.content) > RELEASE_MANIFEST_MAX_BYTES:
            return _unavailable(current_version, observed_at, "release_manifest_too_large")
        try:
            decoded = json.loads(fetched.content)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _unavailable(current_version, observed_at, "release_manifest_invalid")
        if not isinstance(decoded, Mapping):
            return _unavailable(current_version, observed_at, "release_manifest_invalid")
        latest_version = str(decoded.get("version") or "").strip()
        if not _is_valid_semver(latest_version):
            return _unavailable(current_version, observed_at, "release_manifest_version_invalid")
        release_url = _optional_text(decoded.get("release_url"))
        if release_url is not None and (len(release_url) > 2_048 or not _is_https_url(release_url)):
            return _unavailable(current_version, observed_at, "release_manifest_url_invalid")
        raw_notes = _optional_text(decoded.get("release_notes"))
        reason_codes: list[str] = []
        release_notes = raw_notes
        if raw_notes is not None and len(raw_notes) > RELEASE_NOTES_MAX_LENGTH:
            release_notes = raw_notes[:RELEASE_NOTES_MAX_LENGTH]
            reason_codes.append("release_notes_truncated")
        return VersionCheckResponse(
            availability="partial" if reason_codes else "available",
            current_version=current_version,
            latest_version=latest_version,
            update_available=compare_semver(latest_version, current_version) > 0,
            release_url=release_url,
            release_notes=release_notes,
            observed_at=observed_at,
            reason_codes=reason_codes,
        )

    def _safe_current_version(self) -> str:
        try:
            value = self._current_version()
        except metadata.PackageNotFoundError:
            return "unknown"
        return value.strip() if isinstance(value, str) and value.strip() else "unknown"


def installed_version() -> str:
    configured = os.getenv(CURRENT_VERSION_ENV, "").strip()
    if configured:
        return configured
    return metadata.version("services")


async def fetch_release_manifest(url: str) -> ReleaseManifestFetch:
    timeout = httpx.Timeout(RELEASE_CHECK_TIMEOUT_SECONDS)
    content = bytearray()
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        headers={"accept": "application/json"},
    ) as client:
        async with client.stream("GET", url) as response:
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > RELEASE_MANIFEST_MAX_BYTES:
                    break
            return ReleaseManifestFetch(
                status_code=response.status_code,
                content=bytes(content),
            )


def compare_semver(left: str, right: str) -> int:
    left_value = _semver_key(left)
    right_value = _semver_key(right)
    if left_value[:3] != right_value[:3]:
        return 1 if left_value[:3] > right_value[:3] else -1
    return _compare_prerelease(left_value[3], right_value[3])


def _semver_key(version: str) -> tuple[int, int, int, tuple[str, ...] | None]:
    match = SEMVER_RE.fullmatch(version)
    if match is None:
        raise ValueError("invalid semantic version")
    prerelease = match.group("prerelease")
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
        tuple(prerelease.split(".")) if prerelease else None,
    )


def _compare_prerelease(
    left: tuple[str, ...] | None,
    right: tuple[str, ...] | None,
) -> int:
    if left is None or right is None:
        if left == right:
            return 0
        return 1 if left is None else -1
    for left_part, right_part in zip(left, right, strict=False):
        if left_part == right_part:
            continue
        left_numeric = left_part.isdigit()
        right_numeric = right_part.isdigit()
        if left_numeric and right_numeric:
            return 1 if int(left_part) > int(right_part) else -1
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return 1 if left_part > right_part else -1
    if len(left) == len(right):
        return 0
    return 1 if len(left) > len(right) else -1


def _is_valid_semver(value: str) -> bool:
    return len(value) <= 64 and SEMVER_RE.fullmatch(value) is not None


def _is_https_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc) and not parsed.username


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) and value else None


def _unavailable(
    current_version: str,
    observed_at: datetime,
    reason_code: str,
) -> VersionCheckResponse:
    return VersionCheckResponse(
        availability="unavailable",
        current_version=current_version,
        observed_at=observed_at,
        reason_codes=[reason_code],
    )
