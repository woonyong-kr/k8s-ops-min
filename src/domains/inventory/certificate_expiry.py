"""Safe certificate-expiry projection from observed TLS Secret ownership."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from domains.inventory.provider_detail import provider_detail_projection
from packages.contracts.gateway.responses import (
    HomeCertificateExpiryItem,
    HomeCertificateExpirySummary,
    HomeInsightCoverage,
)
from packages.contracts.inventory_provider import CertificateProviderDetail
from packages.contracts.parity import ResourceRef

CERTIFICATE_ITEM_LIMIT = 8
TLS_SECRET_OBSERVATION_UNAVAILABLE = "tls_secret_observation_unavailable"
CERTIFICATE_EXPIRY_UNAVAILABLE = "certificate_expiry_unavailable"
TLS_SECRET_SCAN_TRUNCATED = "tls_secret_scan_truncated"
SOURCE_RESOURCES_INCOMPLETE = "source_resources_incomplete"


def certificate_expiry_summary(
    observations: Sequence[Mapping[str, Any]],
    *,
    cluster_id: str,
    context: Mapping[str, Any] | None,
    scan_truncated: bool,
    warning_before_seconds: int,
    now: datetime | None = None,
) -> HomeCertificateExpirySummary:
    """Project only expiry facts tied to an observed exact TLS Secret identity."""

    revision = int((context or {}).get("snapshot_revision") or 0)
    if revision <= 0:
        return _unavailable(
            cluster_id=cluster_id,
            context=context,
            reason=f"inventory_snapshot_unavailable:{cluster_id}",
            warning_before_seconds=warning_before_seconds,
        )
    if not observations:
        return _unavailable(
            cluster_id=cluster_id,
            context=context,
            reason=TLS_SECRET_OBSERVATION_UNAVAILABLE,
            warning_before_seconds=warning_before_seconds,
        )

    instant = (now or datetime.now(UTC)).astimezone(UTC)
    reasons = {
        str(reason)
        for reason in (context or {}).get("partial_reason_codes", ())
        if str(reason).strip()
    }
    if not bool((context or {}).get("resources_complete")):
        reasons.add(SOURCE_RESOURCES_INCOMPLETE)
    if scan_truncated:
        reasons.add(TLS_SECRET_SCAN_TRUNCATED)

    projected: list[HomeCertificateExpiryItem] = []
    for observation in observations:
        item = _expiry_item(observation, now=instant, warning_before_seconds=warning_before_seconds)
        if item is None:
            reasons.add(CERTIFICATE_EXPIRY_UNAVAILABLE)
            continue
        projected.append(item)
    if not projected:
        return _unavailable(
            cluster_id=cluster_id,
            context=context,
            reason=CERTIFICATE_EXPIRY_UNAVAILABLE,
            warning_before_seconds=warning_before_seconds,
        )

    projected.sort(
        key=lambda item: (
            item.not_after,
            item.secret.namespace or "",
            item.secret.name,
            item.secret.uid,
        )
    )
    expired_count = sum(item.status == "expired" for item in projected)
    expiring_count = sum(item.status == "expiring" for item in projected)
    return HomeCertificateExpirySummary(
        coverage=HomeInsightCoverage(
            availability="partial" if reasons else "available",
            observed_at=_iso((context or {}).get("observed_at")),
            reason_codes=tuple(sorted(reasons)),
        ),
        items=tuple(projected[:CERTIFICATE_ITEM_LIMIT]),
        tls_secret_count=len(observations),
        observed_expiry_count=len(projected),
        expiring_count=expiring_count,
        expired_count=expired_count,
        earliest_expiry=projected[0].not_after,
        warning_before_seconds=warning_before_seconds,
        has_more=len(projected) > CERTIFICATE_ITEM_LIMIT,
    )


def _expiry_item(
    observation: Mapping[str, Any],
    *,
    now: datetime,
    warning_before_seconds: int,
) -> HomeCertificateExpiryItem | None:
    secret = _mapping(observation.get("secret"))
    certificate = _mapping(observation.get("certificate"))
    if not secret or not certificate:
        return None
    detail = provider_detail_projection(certificate)
    if not isinstance(detail, CertificateProviderDetail):
        return None
    secret_name = _text(secret.get("name"))
    if not secret_name or detail.secret_name != secret_name or detail.not_after is None:
        return None
    not_after = _timestamp(detail.not_after)
    if not_after is None:
        return None
    seconds_remaining = int((not_after - now).total_seconds())
    status = (
        "expired"
        if seconds_remaining <= 0
        else "expiring"
        if seconds_remaining <= warning_before_seconds
        else "valid"
    )
    return HomeCertificateExpiryItem(
        secret=_resource_ref(secret),
        source_certificate=_resource_ref(certificate),
        not_after=not_after.isoformat(),
        status=status,
        seconds_remaining=seconds_remaining,
        observed_at=_iso(secret.get("observed_at")),
    )


def _unavailable(
    *,
    cluster_id: str,
    context: Mapping[str, Any] | None,
    reason: str,
    warning_before_seconds: int,
) -> HomeCertificateExpirySummary:
    return HomeCertificateExpirySummary(
        coverage=HomeInsightCoverage(
            availability="unavailable",
            observed_at=_iso((context or {}).get("observed_at")),
            reason_codes=(reason,),
        ),
        warning_before_seconds=warning_before_seconds,
    )


def _resource_ref(value: Mapping[str, Any]) -> ResourceRef:
    api_group, version = _api_group_and_version(_text(value.get("api_version")) or "")
    inventory_key = _text(value.get("inventory_key"))
    uid = _text(value.get("uid")) or inventory_key
    if not uid:
        raise ValueError("certificate observation resource identity is incomplete")
    return ResourceRef(
        api_group=api_group,
        version=version,
        kind=_text(value.get("kind")) or "",
        namespace=_text(value.get("namespace")),
        name=_text(value.get("name")) or "",
        uid=uid,
    )


def _timestamp(value: object) -> datetime | None:
    text = _text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _iso(value: object) -> str | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return None
        return value.astimezone(UTC).isoformat()
    parsed = _timestamp(value)
    return parsed.isoformat() if parsed is not None else None


def _api_group_and_version(value: str) -> tuple[str, str]:
    api_group, separator, version = value.partition("/")
    return ("", api_group) if not separator else (api_group, version)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str | None:
    normalized = str(value).strip() if value is not None else ""
    return normalized or None
