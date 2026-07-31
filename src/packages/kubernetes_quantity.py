"""Small, dependency-free Kubernetes quantity parsers shared by collectors and projections."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


def cpu_millicores(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    factors = {"n": Decimal("0.000001"), "u": Decimal("0.001"), "m": Decimal("1")}
    suffix = text[-1]
    try:
        if suffix in factors:
            parsed = Decimal(text[:-1]) * factors[suffix]
        else:
            parsed = Decimal(text) * Decimal(1000)
    except InvalidOperation:
        return None
    return float(parsed) if parsed.is_finite() and parsed >= 0 else None


def memory_mebibytes(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    units = {
        "Ki": Decimal(1) / Decimal(1024),
        "Mi": Decimal(1),
        "Gi": Decimal(1024),
        "Ti": Decimal(1024 * 1024),
        "K": Decimal(1000) / Decimal(1024 * 1024),
        "M": Decimal(1000 * 1000) / Decimal(1024 * 1024),
        "G": Decimal(1000 * 1000 * 1000) / Decimal(1024 * 1024),
    }
    try:
        for suffix, factor in units.items():
            if text.endswith(suffix):
                parsed = Decimal(text[: -len(suffix)]) * factor
                break
        else:
            parsed = Decimal(text) / Decimal(1024 * 1024)
    except InvalidOperation:
        return None
    return float(parsed) if parsed.is_finite() and parsed >= 0 else None
