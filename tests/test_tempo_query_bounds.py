from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = ROOT / "src" / "services" / "target" / "cluster-agent"
sys.path.insert(0, str(AGENT_ROOT))

from providers.tempo_providers import tempo_search_params  # noqa: E402
from queries import OpenTelemetrySpanQuery, TelemetryQueryDefinition  # noqa: E402

from packages.contracts.evidence_policy import (  # noqa: E402
    TEMPO_RECENT_TRACE_QUERY_NAME,
    TEMPO_RECENT_TRACE_RANGE_SECONDS,
)


def test_tempo_policy_range_compiles_to_provider_query() -> None:
    definition = TelemetryQueryDefinition.from_mapping(
        {
            "source": "tempo",
            "name": "recent",
            "description": "recent traces",
            "query": "{}",
            "range_seconds": 900,
        }
    )

    query = definition.to_provider_query()

    assert isinstance(query, OpenTelemetrySpanQuery)
    assert query.range_seconds == 900


def test_tempo_search_uses_policy_range_as_start_and_end() -> None:
    query = OpenTelemetrySpanQuery(
        query_name="recent",
        description="recent traces",
        traceql="{}",
        range_seconds=900,
    )

    assert tempo_search_params(query, now_seconds=10_000.9) == {
        "q": "{}",
        "limit": 20,
        "start": 9_100,
        "end": 10_000,
    }


def test_legacy_tempo_query_without_range_keeps_compatible_request() -> None:
    query = OpenTelemetrySpanQuery(
        query_name="legacy",
        description="legacy policy",
        traceql="{}",
    )

    assert tempo_search_params(query, now_seconds=10_000) == {
        "q": "{}",
        "limit": 20,
    }


def test_upgrade_compatible_canonical_query_still_uses_recent_range() -> None:
    query = OpenTelemetrySpanQuery(
        query_name=TEMPO_RECENT_TRACE_QUERY_NAME,
        description="server-owned recent trace query",
        traceql="{}",
    )

    assert tempo_search_params(query, now_seconds=10_000.9) == {
        "q": "{}",
        "limit": 20,
        "start": 10_000 - TEMPO_RECENT_TRACE_RANGE_SECONDS,
        "end": 10_000,
    }
