"""Validated server policy for certificate-expiry health buckets."""

from __future__ import annotations

from functools import lru_cache

from packages.config.settings import env

CERTIFICATE_EXPIRY_WARNING_DAYS_ENV = "OPSIA_CERTIFICATE_EXPIRY_WARNING_DAYS"
DEFAULT_CERTIFICATE_EXPIRY_WARNING_DAYS = 30
MIN_CERTIFICATE_EXPIRY_WARNING_DAYS = 1
MAX_CERTIFICATE_EXPIRY_WARNING_DAYS = 3650
SECONDS_PER_DAY = 24 * 60 * 60


@lru_cache(maxsize=1)
def certificate_expiry_warning_seconds() -> int:
    raw = env(
        CERTIFICATE_EXPIRY_WARNING_DAYS_ENV,
        str(DEFAULT_CERTIFICATE_EXPIRY_WARNING_DAYS),
    ).strip()
    try:
        days = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{CERTIFICATE_EXPIRY_WARNING_DAYS_ENV} must be an integer") from exc
    if not MIN_CERTIFICATE_EXPIRY_WARNING_DAYS <= days <= MAX_CERTIFICATE_EXPIRY_WARNING_DAYS:
        raise RuntimeError(
            f"{CERTIFICATE_EXPIRY_WARNING_DAYS_ENV} must be between "
            f"{MIN_CERTIFICATE_EXPIRY_WARNING_DAYS} and "
            f"{MAX_CERTIFICATE_EXPIRY_WARNING_DAYS}"
        )
    return days * SECONDS_PER_DAY
