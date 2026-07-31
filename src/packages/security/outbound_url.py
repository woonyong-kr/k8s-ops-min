"""외부 HTTP 목적지의 공용 SSRF 방어 경계."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable, Iterable
from urllib.parse import urlsplit

from packages.config.settings import env

ALERT_WEBHOOK_ALLOWED_HOSTS_ENV = "ALERT_WEBHOOK_ALLOWED_HOSTS"
MAX_OUTBOUND_URL_LENGTH = 2048
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")

HostResolver = Callable[[str], Awaitable[Iterable[str]]]


class UnsafeOutboundUrlError(ValueError):
    """URL이 공용 외부 목적지 정책을 통과하지 못했음을 나타낸다."""


def _unsafe_url() -> UnsafeOutboundUrlError:
    return UnsafeOutboundUrlError("unsafe outbound URL")


def _is_public_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        address.is_global
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_private
        and not address.is_reserved
        and not address.is_unspecified
    )


def _normalize_hostname(hostname: str) -> str:
    value = hostname.rstrip(".").lower()
    if not value or "%" in value:
        raise _unsafe_url()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    try:
        value = value.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise _unsafe_url() from exc
    if len(value) > 253:
        raise _unsafe_url()
    labels = value.split(".")
    if any(not _HOST_LABEL.fullmatch(label) for label in labels):
        raise _unsafe_url()
    return value


def _is_allowed_host(hostname: str, configured: str) -> bool:
    for raw_rule in configured.split(","):
        rule = raw_rule.strip().lower().rstrip(".")
        if not rule:
            continue
        wildcard = rule.startswith("*.")
        candidate = rule[2:] if wildcard else rule
        try:
            candidate = _normalize_hostname(candidate)
        except UnsafeOutboundUrlError:
            continue
        if wildcard and hostname.endswith(f".{candidate}"):
            return True
        if not wildcard and hostname == candidate:
            return True
    return False


def validate_outbound_url_syntax(
    url: str,
    *,
    allowed_hosts: str | None = None,
) -> str:
    """네트워크 조회 없이 URL 형태, literal IP, 선택 allowlist를 검사한다."""
    if (
        not url
        or len(url) > MAX_OUTBOUND_URL_LENGTH
        or url != url.strip()
        or "#" in url
        or "\\" in url
        or any(character.isspace() or ord(character) < 32 for character in url)
    ):
        raise _unsafe_url()
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise _unsafe_url() from exc
    if (
        parsed.scheme.lower() != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise _unsafe_url()

    hostname = _normalize_hostname(hostname)
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise _unsafe_url()
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None
    if literal_ip is not None and not _is_public_ip(literal_ip):
        raise _unsafe_url()

    configured_hosts = (
        env(ALERT_WEBHOOK_ALLOWED_HOSTS_ENV, "") if allowed_hosts is None else allowed_hosts
    ).strip()
    if configured_hosts and not _is_allowed_host(hostname, configured_hosts):
        raise _unsafe_url()
    return hostname


async def resolve_host_addresses(hostname: str) -> tuple[str, ...]:
    """시스템 resolver로 hostname의 A/AAAA 주소를 조회한다."""
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(
        hostname,
        443,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
    )
    return tuple(
        str(sockaddr[0])
        for family, _type, _proto, _canonname, sockaddr in records
        if family in {socket.AF_INET, socket.AF_INET6}
    )


async def validate_outbound_url(
    url: str,
    *,
    resolver: HostResolver | None = None,
    allowed_hosts: str | None = None,
) -> None:
    """URL 정적 정책과 현재 DNS 결과의 모든 A/AAAA 주소를 검사한다."""
    hostname = validate_outbound_url_syntax(url, allowed_hosts=allowed_hosts)
    try:
        ipaddress.ip_address(hostname)
        return
    except ValueError:
        pass

    try:
        addresses = tuple(await (resolver or resolve_host_addresses)(hostname))
    except OSError as exc:
        raise _unsafe_url() from exc
    if not addresses:
        raise _unsafe_url()
    try:
        resolved_ips = tuple(ipaddress.ip_address(address) for address in addresses)
    except ValueError as exc:
        raise _unsafe_url() from exc
    if any(not _is_public_ip(address) for address in resolved_ips):
        raise _unsafe_url()
