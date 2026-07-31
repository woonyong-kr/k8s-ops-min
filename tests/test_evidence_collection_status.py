from __future__ import annotations

import asyncio
import importlib
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from domains.rca.events import ClusterEvidenceReceivedBody, IncidentRecord
from domains.release_flow.projection import evidence_details
from domains.target.evidence_jobs import (
    aggregate_evidence_payload,
    normalize_evidence_provider_result,
)
from services.ai.agent.pipeline.evidence import EvidenceBuilder
from services.ai.agent.pipeline.evidence_bundle import (
    build_incident_evidence_bundle,
    collect_evidence_items,
)

AGENT_ROOT = (
    Path(__file__).resolve().parents[1] / "src" / "services" / "target" / "cluster-agent"
)
sys.path.insert(0, str(AGENT_ROOT))
try:
    EvidenceCollector = importlib.import_module("evidence.collector").EvidenceCollector
    EvidenceJobScheduler = importlib.import_module("evidence.jobs").EvidenceJobScheduler
finally:
    sys.path.remove(str(AGENT_ROOT))


@dataclass(frozen=True)
class StubQuery:
    name: str
    fails: bool = False


@dataclass(frozen=True)
class StubDefinition:
    name: str
    fails: bool = False

    def to_provider_query(self) -> StubQuery:
        return StubQuery(self.name, self.fails)


class StubMetricsProvider:
    evidence_key = "metrics"
    source = "prometheus"
    span_name = "stub.metrics.collect"
    query_count_attribute = "stub.query_count"
    result_count_attribute = "stub.result_count"
    timeout_seconds = 1
    failure_message = "stub metrics collection failed"
    queries: tuple[StubQuery, ...] = ()

    async def query(self, _client: object, query: StubQuery) -> dict:
        if query.fails:
            raise OSError("prometheus.target.svc does not resolve")
        return {"value": 1}

    def empty_results(self) -> dict:
        return {}

    def append_result(self, results: dict, query: StubQuery, payload: dict) -> None:
        results[query.name] = payload

    def build_response(self, results: dict) -> dict:
        return {"source": self.source, "results": results}


class StubEvidenceSource:
    async def collect(self, *_evidence_keys: str) -> dict:
        return {}


class RecordingScheduleClient:
    def __init__(self, scheduler: EvidenceJobScheduler | None = None) -> None:
        self.scheduler = scheduler
        self.calls: list[tuple[str, str, list[str]]] = []

    async def schedule_evidence_jobs(
        self,
        source_id: str,
        window_start: str,
        provider_keys: list[str],
    ) -> dict:
        self.calls.append((source_id, window_start, list(provider_keys)))
        if self.scheduler is not None:
            self.scheduler.configure_schedule(
                provider_intervals={"kubernetes": 30, "traces": 30},
                enabled_provider_keys={"kubernetes", "traces"},
            )
        return {"evidence_key": "window-1"}


def evidence_scheduler(
    *provider_keys: str,
) -> EvidenceJobScheduler:
    return EvidenceJobScheduler(
        cluster_id="cluster-1",
        workspace_id="workspace-1",
        agent_id="agent-1",
        source_id="cluster-snapshot",
        collector=StubEvidenceSource(),
        provider_keys=provider_keys,
        provider_worker_counts={provider_key: 1 for provider_key in provider_keys},
        interval_seconds=30,
    )


def test_policy_provider_change_aligns_the_next_evidence_window() -> None:
    scheduler = evidence_scheduler("kubernetes", "logs", "traces")
    scheduler.enabled_provider_keys = {"kubernetes", "logs"}
    scheduler.next_provider_runs.update(
        {"kubernetes": 100.0, "logs": 101.0, "traces": 0.0}
    )

    scheduler.configure_schedule(
        provider_intervals={"kubernetes": 30, "logs": 30, "traces": 30},
        enabled_provider_keys={"kubernetes", "logs", "traces"},
    )

    assert scheduler.due_provider_keys(1.0) == ("kubernetes", "logs", "traces")


def test_unchanged_policy_keeps_existing_provider_deadlines() -> None:
    scheduler = evidence_scheduler("kubernetes", "logs")
    scheduler.next_provider_runs.update({"kubernetes": 100.0, "logs": 101.0})

    scheduler.configure_schedule(
        provider_intervals={"kubernetes": 30, "logs": 30},
        enabled_provider_keys={"kubernetes", "logs"},
    )

    assert scheduler.next_provider_runs == {"kubernetes": 100.0, "logs": 101.0}


def test_runtime_provider_registration_aligns_existing_providers() -> None:
    scheduler = evidence_scheduler("kubernetes", "logs")
    scheduler.next_provider_runs.update({"kubernetes": 100.0, "logs": 101.0})

    scheduler.register_provider(
        "metrics",
        worker_count=1,
        interval_seconds=30,
        enabled=True,
    )

    assert scheduler.due_provider_keys(1.0) == ("kubernetes", "logs", "metrics")


def test_schedule_once_queues_the_aligned_provider_set_together() -> None:
    scheduler = evidence_scheduler("kubernetes", "logs", "traces")
    scheduler.enabled_provider_keys = {"kubernetes", "logs"}
    scheduler.next_provider_runs.update(
        {"kubernetes": 100.0, "logs": 101.0, "traces": 0.0}
    )
    scheduler.configure_schedule(
        provider_intervals={"kubernetes": 30, "logs": 30, "traces": 30},
        enabled_provider_keys={"kubernetes", "logs", "traces"},
    )
    client = RecordingScheduleClient()

    evidence_key = asyncio.run(scheduler.schedule_once(client, now=1.0))

    assert evidence_key == "window-1"
    assert client.calls == [
        (
            "cluster-snapshot",
            "1970-01-01T00:00:00+00:00",
            ["kubernetes", "logs", "traces"],
        )
    ]
    assert scheduler.next_provider_runs == {
        "kubernetes": 31.0,
        "logs": 31.0,
        "traces": 31.0,
    }


def test_policy_change_during_schedule_does_not_restore_stale_deadline() -> None:
    scheduler = evidence_scheduler("kubernetes", "traces")
    scheduler.enabled_provider_keys = {"traces"}
    scheduler.next_provider_runs.update({"kubernetes": 100.0, "traces": 0.0})
    client = RecordingScheduleClient(scheduler)

    asyncio.run(scheduler.schedule_once(client, now=1.0))

    assert client.calls[0][2] == ["traces"]
    assert scheduler.next_provider_runs == {"kubernetes": 0.0, "traces": 0.0}


def collect_stub(
    *definitions: StubDefinition,
    failure_policy: str = "allow_partial",
) -> dict:
    collector = EvidenceCollector((StubMetricsProvider(),))
    return asyncio.run(
        collector.collect_query_policy(
            "metrics",
            definitions,
            failure_policy=failure_policy,
        )
    )


def test_allow_partial_marks_all_failed_queries_unavailable() -> None:
    result = collect_stub(StubDefinition("up", fails=True))

    assert result["metrics"] == {"source": "prometheus", "results": {}}
    status = result["collection_status"]["providers"]["metrics"]
    assert status == {
        "status": "unavailable",
        "source": "prometheus",
        "query_count": 1,
        "completed_query_count": 0,
        "failed_query_count": 1,
        "reason_codes": ["provider_query_failed"],
    }
    assert "prometheus.target.svc" not in str(result)


def test_allow_partial_marks_mixed_queries_partial() -> None:
    result = collect_stub(
        StubDefinition("up"),
        StubDefinition("missing", fails=True),
    )

    assert result["metrics"]["results"] == {"up": {"value": 1}}
    status = result["collection_status"]["providers"]["metrics"]
    assert status["status"] == "partial"
    assert status["completed_query_count"] == 1
    assert status["failed_query_count"] == 1


def test_zero_queries_are_explicitly_not_queried() -> None:
    result = collect_stub()

    status = result["collection_status"]["providers"]["metrics"]
    assert status["status"] == "not_queried"
    assert status["reason_codes"] == ["no_queries_configured"]


def test_strict_policy_still_propagates_provider_failure() -> None:
    with pytest.raises(OSError, match="does not resolve"):
        collect_stub(
            StubDefinition("up", fails=True),
            failure_policy="strict",
        )


def evidence_row(
    provider_key: str,
    result: object,
    *,
    status: str = "completed",
) -> dict:
    return {
        "evidence_key": "workspace-1:cluster-1:cluster-snapshot:window-1",
        "workspace_id": "workspace-1",
        "cluster_id": "cluster-1",
        "source_id": "cluster-snapshot",
        "provider_key": provider_key,
        "provider_policy": {},
        "window_start": "window-1",
        "status": status,
        "failure_policy": "allow_partial",
        "agent_id": "agent-1",
        "result": result,
    }


def test_provider_result_preserves_only_leased_provider_status() -> None:
    result = normalize_evidence_provider_result(
        "metrics",
        {
            "metrics": {"source": "prometheus", "results": {}},
            "logs": [{"line": "must not escape"}],
            "collection_status": {
                "providers": {
                    "metrics": {
                        "status": "unavailable",
                        "query_count": 1,
                        "reason_codes": ["provider_query_failed"],
                    },
                    "logs": {"status": "completed"},
                }
            },
        },
    )

    assert "logs" not in result
    assert result["collection_status"]["providers"] == {
        "metrics": {
            "status": "unavailable",
            "query_count": 1,
            "reason_codes": ["provider_query_failed"],
        }
    }


def test_legacy_raw_kubernetes_bucket_keeps_coverage_collection_status() -> None:
    result = normalize_evidence_provider_result(
        "kubernetes",
        {
            "pods": [],
            "collection_status": {
                "pods": {
                    "status": "partial",
                    "reason_codes": ["rbac_forbidden"],
                }
            },
        },
    )

    assert result["kubernetes"]["collection_status"]["pods"]["status"] == "partial"


def test_aggregate_infers_legacy_empty_prometheus_envelope_unavailable() -> None:
    payload = aggregate_evidence_payload(
        [
            evidence_row(
                "metrics",
                {"metrics": {"source": "prometheus", "results": {}}},
            )
        ]
    )

    assert payload is not None
    assert payload["metrics"] == {"source": "prometheus", "results": {}}
    status = payload["collection_status"]
    assert status["complete"] is False
    assert status["failed_providers"] == ["metrics"]
    assert status["providers"]["metrics"] == {
        "status": "unavailable",
        "reason_codes": ["no_provider_results"],
    }


def test_aggregate_keeps_successful_empty_vector_query_available() -> None:
    payload = aggregate_evidence_payload(
        [
            evidence_row(
                "metrics",
                {
                    "metrics": {
                        "source": "prometheus",
                        "results": {"up": {"samples": []}},
                    }
                },
            )
        ]
    )

    assert payload is not None
    assert payload["collection_status"]["complete"] is True
    assert payload["collection_status"]["completed_providers"] == ["metrics"]


def cluster_evidence(
    *,
    metrics: dict | None = None,
    traces: dict | None = None,
    collection_status: dict | None = None,
) -> ClusterEvidenceReceivedBody:
    return ClusterEvidenceReceivedBody(
        cluster_id="cluster-1",
        kubernetes={
            "resource": {
                "kind": "ReplicaSet",
                "name": "management-server",
                "namespace": "sandbox",
            },
            "symptom": "Matchmaking join failure",
            "severity": "medium",
        },
        metrics=metrics or {},
        logs=[],
        traces=traces or {},
        collection_status=collection_status or {},
    )


def incident() -> IncidentRecord:
    return IncidentRecord(
        incident_id="incident-1",
        cluster_id="cluster-1",
        resource_kind="ReplicaSet",
        resource_name="management-server",
        namespace="sandbox",
        symptom="Matchmaking join failure",
        severity="medium",
        first_seen_at=None,
        summary="readiness failed",
    )


def test_evidence_builder_preserves_collection_status_in_metadata() -> None:
    status = {
        "complete": False,
        "providers": {"metrics": {"status": "unavailable"}},
    }

    evidence = EvidenceBuilder().build_evidence(
        cluster_evidence(collection_status=status),
        "correlation-1",
    )

    assert evidence.metadata["collection_status"] == status


def test_bundle_rejects_legacy_empty_metrics_and_reports_unavailable() -> None:
    bundle = build_incident_evidence_bundle(
        cluster_evidence(metrics={"source": "prometheus", "results": {}}),
        incident(),
    )

    assert "metrics" not in {item.source for item in bundle.items}
    assert bundle.missing_evidence == ["metrics"]
    assert bundle.missing_evidence_checks[0].status == "unavailable"
    assert "no_provider_results" in bundle.missing_evidence_checks[0].reason


def test_bundle_rejects_explicit_unavailable_metrics_even_with_stale_results() -> None:
    bundle = build_incident_evidence_bundle(
        cluster_evidence(
            metrics={
                "source": "prometheus",
                "results": {"up": {"samples": [{"value": 1}]}},
            },
            collection_status={
                "providers": {
                    "metrics": {
                        "status": "unavailable",
                        "reason_codes": ["provider_query_failed"],
                    }
                }
            },
        ),
        incident(),
    )

    assert bundle.missing_evidence == ["metrics"]
    assert bundle.missing_evidence_checks[0].status == "unavailable"
    assert "provider_query_failed" in bundle.missing_evidence_checks[0].reason


def test_persisted_status_metadata_still_blocks_stale_metrics() -> None:
    evidence = EvidenceBuilder().build_evidence(
        cluster_evidence(
            metrics={
                "source": "prometheus",
                "results": {"up": {"samples": [{"value": 1}]}},
            },
            collection_status={
                "providers": {
                    "metrics": {
                        "status": "unavailable",
                        "reason_codes": ["provider_query_failed"],
                    }
                }
            },
        ),
        "correlation-1",
    )

    bundle = build_incident_evidence_bundle(evidence, incident())

    assert bundle.missing_evidence == ["metrics"]
    assert bundle.missing_evidence_checks[0].status == "unavailable"


def test_partial_metrics_with_a_successful_query_remain_usable() -> None:
    bundle = build_incident_evidence_bundle(
        cluster_evidence(
            metrics={
                "source": "prometheus",
                "results": {"up": {"samples": []}},
            },
            collection_status={
                "providers": {
                    "metrics": {
                        "status": "partial",
                        "completed_query_count": 1,
                        "failed_query_count": 1,
                    }
                }
            },
        ),
        incident(),
    )

    assert bundle.complete is True
    assert "metrics" in {item.source for item in bundle.items}


def test_bundle_accepts_successful_empty_vector_and_alertmanager_metrics() -> None:
    successful_empty = build_incident_evidence_bundle(
        cluster_evidence(
            metrics={
                "source": "prometheus",
                "results": {"up": {"samples": []}},
            }
        ),
        incident(),
    )
    alertmanager = build_incident_evidence_bundle(
        cluster_evidence(
            metrics={
                "alertmanager": {
                    "alerts": [
                        {
                            "status": "firing",
                            "labels": {"alertname": "ReadinessFailure"},
                        }
                    ]
                }
            }
        ),
        incident(),
    )

    assert successful_empty.complete is True
    assert alertmanager.complete is True
    assert "metrics" in {item.source for item in alertmanager.items}


def test_evidence_items_reject_legacy_empty_tempo_envelope() -> None:
    items = collect_evidence_items(
        cluster_evidence(traces={"source": "tempo", "results": {}})
    )

    assert "traces" not in {item.source for item in items}


def test_release_projection_does_not_claim_unavailable_metrics() -> None:
    unavailable = evidence_details(
        {
            "metrics": {"source": "prometheus", "results": {}},
            "collection_status": {
                "providers": {"metrics": {"status": "unavailable"}}
            },
        }
    )
    legacy_empty = evidence_details(
        {"metrics": {"source": "prometheus", "results": {}}}
    )
    persisted_unavailable = evidence_details(
        {
            "evidence": {
                "metrics": {
                    "source": "prometheus",
                    "results": {"up": {"samples": []}},
                },
                "metadata": {
                    "collection_status": {
                        "providers": {"metrics": {"status": "unavailable"}}
                    }
                },
            }
        }
    )
    alertmanager = evidence_details(
        {"metrics": {"alertmanager": {"alerts": [{"status": "firing"}]}}}
    )

    assert unavailable["has_metrics"] is False
    assert legacy_empty["has_metrics"] is False
    assert persisted_unavailable["has_metrics"] is False
    assert alertmanager["has_metrics"] is True
