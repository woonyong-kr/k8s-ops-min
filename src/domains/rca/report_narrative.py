"""LLM-authored RCA narrative payload contract.

The narrative lives inside the existing ``rca_reports.payload`` JSON document so
deployments do not need a schema migration.  Both the writer and the read
projection pass values through this module; malformed or oversized model output
therefore never becomes part of the public response contract.
"""

from __future__ import annotations

from typing import Any

from packages.contracts.event_bus.interfaces import JsonObject
from packages.security.log_lines import redact_log_line

RCA_NARRATIVE_PAYLOAD_KEY = "narrative"
RCA_NARRATIVE_STATUS_KEY = "narrative_status"
RCA_NARRATIVE_GENERATED = "generated"
RCA_NARRATIVE_UNAVAILABLE = "unavailable"
RCA_NARRATIVE_LOCALE = "ko"

RCA_NARRATIVE_TEXT_FIELDS = (
    "executive_summary",
    "impact",
    "reasoning",
    "recommended_action",
)
RCA_NARRATIVE_LIST_FIELDS = ("recurrence_prevention", "limitations")
MAX_NARRATIVE_TEXT_LENGTH = 3000
MAX_NARRATIVE_LIST_ITEMS = 8
MAX_NARRATIVE_LIST_ITEM_LENGTH = 1000


RCA_NARRATIVE_SCHEMA: JsonObject = {
    "type": "object",
    "properties": {
        "locale": {"type": "string", "enum": [RCA_NARRATIVE_LOCALE]},
        **{
            field: {"type": "string", "minLength": 1, "maxLength": MAX_NARRATIVE_TEXT_LENGTH}
            for field in RCA_NARRATIVE_TEXT_FIELDS
        },
        **{
            field: {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_NARRATIVE_LIST_ITEMS,
                "items": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": MAX_NARRATIVE_LIST_ITEM_LENGTH,
                },
            }
            for field in RCA_NARRATIVE_LIST_FIELDS
        },
    },
    "required": [
        "locale",
        *RCA_NARRATIVE_TEXT_FIELDS,
        *RCA_NARRATIVE_LIST_FIELDS,
    ],
    "additionalProperties": False,
}


def normalize_rca_narrative(value: Any) -> JsonObject | None:
    """Return a bounded, redacted narrative or ``None`` for contract drift."""
    if not isinstance(value, dict) or value.get("locale") != RCA_NARRATIVE_LOCALE:
        return None
    if set(value) != {
        "locale",
        *RCA_NARRATIVE_TEXT_FIELDS,
        *RCA_NARRATIVE_LIST_FIELDS,
    }:
        return None

    normalized: JsonObject = {"locale": RCA_NARRATIVE_LOCALE}
    for field in RCA_NARRATIVE_TEXT_FIELDS:
        text = _bounded_text(value.get(field), MAX_NARRATIVE_TEXT_LENGTH)
        if text is None:
            return None
        normalized[field] = text
    for field in RCA_NARRATIVE_LIST_FIELDS:
        items = value.get(field)
        if not isinstance(items, list) or not items or len(items) > MAX_NARRATIVE_LIST_ITEMS:
            return None
        normalized_items: list[str] = []
        for item in items:
            text = _bounded_text(item, MAX_NARRATIVE_LIST_ITEM_LENGTH)
            if text is None:
                return None
            normalized_items.append(text)
        normalized[field] = normalized_items
    return normalized


def _bounded_text(value: Any, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = redact_log_line(value).strip()
    if not text or len(text) > maximum:
        return None
    return text
