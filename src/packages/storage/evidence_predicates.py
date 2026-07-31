"""Shared SQL predicates for persisted agent evidence."""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_
from sqlalchemy.sql.elements import ColumnElement

from packages.contracts.cost.observations import (
    COST_EVIDENCE_METRICS,
    COST_NAMESPACE_HOURLY_METRIC,
)


def cost_evidence_predicate(
    payload: ColumnElement[dict[str, Any]],
) -> ColumnElement[bool]:
    """Match the Cost metric envelope used by both reads and the partial index."""

    metric_results = payload["metrics"]["results"]
    return or_(
        *(
            metric_results.has_key(metric)  # noqa: W601
            for metric in COST_EVIDENCE_METRICS
        )
    )


def cost_overview_evidence_predicate(
    payload: ColumnElement[dict[str, Any]],
) -> ColumnElement[bool]:
    """Match rows that can contribute namespace rates to the Cost overview."""

    return payload["metrics"]["results"].has_key(  # noqa: W601
        COST_NAMESPACE_HOURLY_METRIC
    )
