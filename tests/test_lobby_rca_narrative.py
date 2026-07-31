from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

from conftest import load_service, make_context, run_handler

from domains.rca.events import (
    IncidentRecord,
    RcaAnalysisBlockedBody,
    RcaCandidatesEvaluatedBody,
    RcaCompletedBody,
    rca_enriched_evidence_key,
)
from domains.rca.query_router import get_evidence_window_payload
from domains.rca.report_projection import rca_report_projection
from services.ai.agent.causes.engine import analyze_root_cause, evaluate_causes, plan_causes
from services.ai.agent.pipeline.evidence_bundle import build_incident_evidence_bundle
from services.ai.agent.pipeline.pipeline import RcaCompletionPipeline
from services.ai.agent.pipeline.rca_narrative import (
    deterministic_rca_narrative,
    evidence_anchored_narrative,
    sanitized_rca_narrative_input,
)

WORKSPACE = "workspace-demo"
CLUSTER = "battlegrounds"
ALERT_STARTED = "2026-07-24T01:01:00Z"


def metric_labels(*, namespace: str = "sandbox", resource_name: str = "api-server"):
    return {
        "namespace": namespace,
        "resource_kind": "Deployment",
        "resource_name": resource_name,
        "service": "matchmaking",
        "sli": "admission",
        "symptom": "admission_failure",
    }


def aligned_payload(
    *,
    namespace: str = "sandbox",
    resource_name: str = "api-server",
    reason: str = "rate_limited",
) -> dict[str, object]:
    return {
        "workspace_id": WORKSPACE,
        "cluster_id": CLUSTER,
        "source_id": "cluster-agent-source",
        "window_start": "2026-07-24T01:00:30Z",
        "kubernetes": {},
        "metrics": {
            "source": "prometheus",
            "results": {
                "opsia_sli_failure_ratio": {
                    "samples": [
                        {
                            "metric": metric_labels(
                                namespace=namespace,
                                resource_name=resource_name,
                            ),
                            "value": 0.42,
                        }
                    ]
                }
            },
        },
        "logs": [
            {
                "streams": [
                    {
                        "stream": {
                            "k8s_namespace_name": namespace,
                            "k8s_pod_name": f"{resource_name}-7bbd8",
                        },
                        "values": [
                            {
                                "line": (
                                    '{"timestamp":"2026-07-24T01:00:50Z",'
                                    '"level":"warn","event":"find_game_rejected",'
                                    '"outcome":"rejected",'
                                    f'"reason":"{reason}",'
                                    f'"namespace":"{namespace}",'
                                    '"resource_kind":"Deployment",'
                                    f'"resource_name":"{resource_name}",'
                                    '"service":"matchmaking","sli":"admission",'
                                    '"symptom":"admission_failure"}'
                                )
                            },
                            {
                                "line": (
                                    '{"event":"debug","token":"must-not-reach-narrative",'
                                    '"message":"Bearer super-secret"}'
                                )
                            },
                        ],
                    }
                ]
            }
        ],
        "traces": {},
        "metadata": {},
    }


class AlignedEvidenceDb:
    def __init__(
        self,
        rows: list[dict[str, object]],
        *,
        alert_rows: list[dict[str, object]] | None = None,
    ) -> None:
        self.rows = rows
        self.alert_rows = alert_rows or []
        self.alignment_calls: list[tuple[object, ...]] = []
        self.saved_window: dict[str, object] | None = None
        self.saved_evidence: dict[str, object] | None = None

    async def list_aligned_evidence_window_payloads(
        self, *args: object, **kwargs: object
    ) -> list[dict[str, object]]:
        self.alignment_calls.append((*args, kwargs))
        return self.rows

    async def list_aligned_alertmanager_window_payloads(
        self, *args: object, **kwargs: object
    ) -> list[dict[str, object]]:
        self.alignment_calls.append((*args, kwargs))
        return self.alert_rows

    async def list_recent_workload_changes_for_evidence(
        self, *args: object, **kwargs: object
    ) -> list[dict[str, object]]:
        return [
            {
                "workspace_id": WORKSPACE,
                "cluster_id": CLUSTER,
                "namespace": "sandbox",
                "resource_kind": "deployment",
                "resource_name": "api-server",
                "repository_id": "repo-demo",
                "binding_id": "binding-demo",
                "repo_ref": "example/game",
                "manifest_path": "deploy/k8s/overlays/game-server",
                "commit_sha": "a" * 40,
                "workflow_run_id": "run-demo",
                "changed_at": "2026-07-24T01:00:00Z",
                "diff_details": {
                    "basis": {"old_desired_source": "last_approved_snapshot"},
                    "changes": [
                        {
                            "field_path": "spec.replicas",
                            "old_desired": 2,
                            "new_desired": 1,
                        }
                    ],
                },
            }
        ]

    async def upsert_rca_enriched_evidence_window(
        self,
        **kwargs: object,
    ) -> bool:
        self.saved_window = dict(kwargs)
        return True

    async def save_evidence(
        self,
        correlation_id: str,
        workspace_id: str,
        kind: str,
        body: dict[str, object],
    ) -> None:
        self.saved_evidence = {
            "correlation_id": correlation_id,
            "workspace_id": workspace_id,
            "kind": kind,
            "body": body,
        }


class CapturingRcaReportDb:
    def __init__(self) -> None:
        self.saved: list[dict[str, object]] = []

    async def save_rca_report(
        self,
        correlation_id: str,
        workspace_id: str,
        root_cause: str,
        action: str,
        body: dict[str, object],
    ) -> None:
        self.saved.append(
            {
                "correlation_id": correlation_id,
                "workspace_id": workspace_id,
                "root_cause": root_cause,
                "action": action,
                "body": body,
            }
        )


def alert_event(worker):
    return worker.ClusterEvidenceReceivedBody(
        workspace_id=WORKSPACE,
        cluster_id=CLUSTER,
        kubernetes={
            "resource": {
                "namespace": "sandbox",
                "kind": "Deployment",
                "name": "api-server",
            },
            "symptom": "admission_failure",
            "severity": "warning",
        },
        metrics={
            "alertmanager": {
                "alerts": [
                    {
                        "status": "firing",
                        "startsAt": ALERT_STARTED,
                        "labels": {
                            "alertname": "OpsiaSliFailureRatioHigh",
                            "opsia_namespace": "sandbox",
                            "opsia_resource_kind": "Deployment",
                            "opsia_resource_name": "api-server",
                            "opsia_service": "matchmaking",
                            "opsia_sli": "admission",
                            "opsia_symptom": "admission_failure",
                        },
                        "annotations": {
                            "opsia_observed_value": "0.42",
                            "opsia_threshold": "0.2",
                        },
                    }
                ]
            }
        },
        logs=[],
        traces={},
        source_id=worker.ALERTMANAGER_SOURCE_ID,
        window_start=ALERT_STARTED,
        evidence_key="alert-window",
    )


def incident() -> IncidentRecord:
    return IncidentRecord(
        incident_id="incident-admission",
        cluster_id=CLUSTER,
        resource_kind="Deployment",
        resource_name="api-server",
        namespace="sandbox",
        symptom="admission_failure",
        severity="warning",
        first_seen_at=ALERT_STARTED,
        summary="신규 입장 실패율 임계치 초과",
        workspace_id=WORKSPACE,
    )


def completed_report(bundle) -> RcaCompletedBody:
    cause_plan = plan_causes(incident(), bundle, "object://evidence/alert-window.json")
    evaluations = evaluate_causes(cause_plan.candidates, bundle)
    detail = analyze_root_cause(evaluations)
    return RcaCompletedBody(
        root_cause=detail.root_cause,
        action="safe_pr",
        evidence_ref="object://evidence/alert-window.json",
        workspace_id=WORKSPACE,
        incident=incident(),
        evidence_bundle=bundle,
        candidates=cause_plan.candidates,
        evaluations=evaluations,
        rca_detail=detail,
    )


def persisted_bundle(worker, event):
    persisted = replace(
        event,
        evidence_key=rca_enriched_evidence_key(
            event.workspace_id,
            event.cluster_id,
            "corr-1",
        ),
    )
    evidence = worker.pipeline.build_evidence(persisted, "corr-1")
    return build_incident_evidence_bundle(evidence, incident())


def test_alert_joins_only_exact_agent_identity_and_preserves_clickable_log_ref() -> None:
    worker = load_service("ai/evidence-worker")
    wrong = aligned_payload(namespace="target", resource_name="cluster-agent")
    incomplete = aligned_payload()
    incomplete["logs"] = []
    db = AlignedEvidenceDb(
        [
            {
                "evidence_key": "wrong-window",
                "window_start": "2026-07-24T01:00:30Z",
                "payload": wrong,
            },
            {
                "evidence_key": "newer-metrics-only-window",
                "window_start": "2026-07-24T01:00:00Z",
                "payload": incomplete,
            },
            {
                "evidence_key": "agent-window",
                "window_start": "2026-07-24T01:00:30Z",
                "payload": aligned_payload(),
            },
        ]
    )
    event = alert_event(worker)

    joined = asyncio.run(worker.attach_aligned_cluster_evidence(event, make_context(db=db)))
    enriched = asyncio.run(worker.attach_gitops_change_context(joined, make_context(db=db)))
    bundle = persisted_bundle(worker, enriched)
    report = completed_report(bundle)

    assert db.alignment_calls[0][0:2] == (WORKSPACE, CLUSTER)
    assert report.root_cause == "lobby_capacity_saturation"
    assert report.rca_detail is not None
    assert report.rca_detail.missing_evidence == []
    refs = {
        (ref.source, ref.name): ref.evidence_ref
        for ref in report.rca_detail.supporting_evidence_refs
    }
    assert refs[("logs", "related_logs")].startswith(
        "object://evidence/corr-1.json#logs:"
    )
    assert refs[("metadata", "change_context")].startswith(
        "object://evidence/corr-1.json#metadata:"
    )
    projected = rca_report_projection(report.to_body())
    evidence_key = rca_enriched_evidence_key(WORKSPACE, CLUSTER, "corr-1")
    assert {
        ref["evidence_key"]
        for ref in projected["supporting_evidence_refs"]
    } == {evidence_key}


def test_alert_selects_nearest_identity_window_without_inspecting_outcome() -> None:
    worker = load_service("ai/evidence-worker")
    nearest = aligned_payload()
    nearest["window_start"] = "2026-07-24T01:00:55Z"
    nearest["logs"] = []
    farther_with_desired_outcome = aligned_payload()
    farther_with_desired_outcome["window_start"] = "2026-07-24T01:00:30Z"
    db = AlignedEvidenceDb(
        [
            {
                "evidence_key": "farther-capacity-window",
                "window_start": "2026-07-24T01:00:30Z",
                "payload": farther_with_desired_outcome,
            },
            {
                "evidence_key": "nearest-metrics-window",
                "window_start": "2026-07-24T01:00:55Z",
                "payload": nearest,
            },
        ]
    )

    joined = asyncio.run(
        worker.attach_aligned_cluster_evidence(alert_event(worker), make_context(db=db))
    )

    assert joined.logs == []
    assert joined.metadata["aligned_evidence"]["evidence_key"] == (
        "nearest-metrics-window"
    )


def test_replica_change_uses_applied_before_after_when_snapshot_is_stale() -> None:
    worker = load_service("ai/evidence-worker")

    assert worker.replica_field_change(
        {
            "basis": {"old_desired_source": "last_approved_snapshot"},
            "changes": [
                {
                    "field_path": "spec.replicas",
                    "classification": "drift",
                    "old_desired": 1,
                    "live": 2,
                    "new_desired": 1,
                    "before": 2,
                    "after": 1,
                }
            ],
        }
    ) == (2, 1)


def test_alert_fails_closed_when_identity_windows_are_equally_near() -> None:
    worker = load_service("ai/evidence-worker")
    before = aligned_payload()
    before["window_start"] = "2026-07-24T01:00:50Z"
    after = aligned_payload()
    after["window_start"] = "2026-07-24T01:01:10Z"
    db = AlignedEvidenceDb(
        [
            {
                "evidence_key": "before-window",
                "window_start": before["window_start"],
                "payload": before,
            },
            {
                "evidence_key": "after-window",
                "window_start": after["window_start"],
                "payload": after,
            },
        ]
    )

    joined = asyncio.run(
        worker.attach_aligned_cluster_evidence(alert_event(worker), make_context(db=db))
    )

    assert joined.logs == []
    assert joined.metadata == {}


def test_alert_rejects_row_and_payload_with_different_window_times() -> None:
    worker = load_service("ai/evidence-worker")
    payload = aligned_payload()
    db = AlignedEvidenceDb(
        [
            {
                "evidence_key": "tampered-window-time",
                "window_start": "2026-07-24T01:00:59Z",
                "payload": payload,
            },
        ]
    )

    joined = asyncio.run(
        worker.attach_aligned_cluster_evidence(alert_event(worker), make_context(db=db))
    )

    assert joined.logs == []
    assert joined.metadata == {}


def test_alert_does_not_join_same_workload_with_different_service_or_sli() -> None:
    worker = load_service("ai/evidence-worker")
    wrong_series = aligned_payload()
    metric = wrong_series["metrics"]["results"]["opsia_sli_failure_ratio"]["samples"][0][
        "metric"
    ]
    metric["service"] = "room-directory"
    db = AlignedEvidenceDb(
        [
            {
                "evidence_key": "wrong-sli-window",
                "window_start": "2026-07-24T01:00:30Z",
                "payload": wrong_series,
            }
        ]
    )

    joined = asyncio.run(
        worker.attach_aligned_cluster_evidence(alert_event(worker), make_context(db=db))
    )

    assert joined.logs == []
    assert joined.metadata == {}


def test_alert_sli_identity_must_match_the_incident_resource() -> None:
    worker = load_service("ai/evidence-worker")
    body = alert_event(worker).to_body()
    labels = body["metrics"]["alertmanager"]["alerts"][0]["labels"]
    labels["opsia_resource_name"] = "other-api"
    mismatched_alert = worker.ClusterEvidenceReceivedBody.from_body(body)
    payload = aligned_payload(resource_name="other-api")
    db = AlignedEvidenceDb(
        [
            {
                "evidence_key": "other-api-window",
                "window_start": payload["window_start"],
                "payload": payload,
            }
        ]
    )

    joined = asyncio.run(
        worker.attach_aligned_cluster_evidence(mismatched_alert, make_context(db=db))
    )

    assert joined.logs == []
    assert joined.metadata == {}
    assert db.alignment_calls == []


def test_alert_group_with_multiple_sli_identities_fails_closed() -> None:
    worker = load_service("ai/evidence-worker")
    body = alert_event(worker).to_body()
    alerts = body["metrics"]["alertmanager"]["alerts"]
    alerts.append(
        {
            **alerts[0],
            "labels": {
                **alerts[0]["labels"],
                "opsia_service": "room-directory",
                "opsia_sli": "availability",
            },
        }
    )
    ambiguous = worker.ClusterEvidenceReceivedBody.from_body(body)
    db = AlignedEvidenceDb(
        [
            {
                "evidence_key": "agent-window",
                "window_start": "2026-07-24T01:00:30Z",
                "payload": aligned_payload(),
            }
        ]
    )

    joined = asyncio.run(
        worker.attach_aligned_cluster_evidence(ambiguous, make_context(db=db))
    )

    assert joined.logs == []
    assert db.alignment_calls == []


def test_reference_hydration_is_workspace_scoped_and_rejects_spoofed_identity() -> None:
    worker = load_service("ai/evidence-worker")
    full = alert_event(worker)
    reference = replace(full, kubernetes={}, metrics={}, logs=[], traces={})

    class HydrateDb:
        def __init__(self, payload):
            self.payload = payload
            self.calls = []

        async def get_evidence_window_payload_for_workspace(
            self,
            workspace_id: str,
            evidence_key: str,
        ):
            self.calls.append((workspace_id, evidence_key))
            return self.payload

    valid_db = HydrateDb(full.to_body())
    hydrated = asyncio.run(worker.hydrate_evidence(reference, make_context(db=valid_db)))
    assert valid_db.calls == [(WORKSPACE, "alert-window")]
    assert hydrated.metrics == full.metrics

    for field, spoofed in (
        ("workspace_id", "workspace-other"),
        ("cluster_id", "cluster-other"),
        ("source_id", "other-source"),
        ("evidence_key", "other-window"),
    ):
        payload = full.to_body()
        payload[field] = spoofed
        rejected = asyncio.run(
            worker.hydrate_evidence(reference, make_context(db=HydrateDb(payload)))
        )
        assert rejected == reference


def test_later_agent_window_can_join_existing_alert_without_repeat_webhook() -> None:
    worker = load_service("ai/evidence-worker")
    alert = alert_event(worker).to_body()
    db = AlignedEvidenceDb(
        [],
        alert_rows=[
            {
                "evidence_key": "alert-window",
                "window_start": ALERT_STARTED,
                "payload": alert,
            }
        ],
    )
    payload = aligned_payload()
    agent_event = worker.ClusterEvidenceReceivedBody.from_body(payload)

    joined = asyncio.run(
        worker.attach_aligned_cluster_evidence(agent_event, make_context(db=db))
    )
    enriched = asyncio.run(worker.attach_gitops_change_context(joined, make_context(db=db)))
    report = completed_report(persisted_bundle(worker, enriched))

    assert "alertmanager" in joined.metrics
    assert joined.kubernetes["resource"] == {
        "namespace": "sandbox",
        "kind": "Deployment",
        "name": "api-server",
    }
    assert report.rca_detail is not None
    assert report.rca_detail.missing_evidence == []
    metric_ref = next(
        ref.evidence_ref
        for ref in report.rca_detail.supporting_evidence_refs
        if ref.source == "metrics"
    )
    assert metric_ref.startswith("object://evidence/corr-1.json#metrics:")


def test_later_agent_window_fails_closed_for_equally_near_alerts() -> None:
    worker = load_service("ai/evidence-worker")
    before = alert_event(worker).to_body()
    before["window_start"] = "2026-07-24T01:00:20Z"
    after = alert_event(worker).to_body()
    after["window_start"] = "2026-07-24T01:00:40Z"
    payload = aligned_payload()
    payload["window_start"] = "2026-07-24T01:00:30Z"
    db = AlignedEvidenceDb(
        [],
        alert_rows=[
            {
                "evidence_key": "alert-before",
                "window_start": before["window_start"],
                "payload": before,
            },
            {
                "evidence_key": "alert-after",
                "window_start": after["window_start"],
                "payload": after,
            },
        ],
    )
    agent_event = worker.ClusterEvidenceReceivedBody.from_body(payload)

    joined = asyncio.run(
        worker.attach_aligned_cluster_evidence(agent_event, make_context(db=db))
    )

    assert "alertmanager" not in joined.metrics
    assert joined.metadata == {}


def test_inverse_join_uses_firing_alert_threshold_instead_of_fixed_ratio() -> None:
    worker = load_service("ai/evidence-worker")
    alert = alert_event(worker).to_body()
    alert["metrics"]["alertmanager"]["alerts"][0]["annotations"] = {
        "opsia_observed_value": "0.40",
        "opsia_threshold": "0.35",
    }
    db = AlignedEvidenceDb(
        [],
        alert_rows=[
            {
                "evidence_key": "alert-window",
                "window_start": ALERT_STARTED,
                "payload": alert,
            }
        ],
    )
    payload = aligned_payload()
    payload["metrics"]["results"]["opsia_sli_failure_ratio"]["samples"][0]["value"] = 0.4
    agent_event = worker.ClusterEvidenceReceivedBody.from_body(payload)

    joined = asyncio.run(
        worker.attach_aligned_cluster_evidence(agent_event, make_context(db=db))
    )

    assert "alertmanager" in joined.metrics


def test_unrelated_cluster_agent_upstream_log_does_not_complete_capacity_rca() -> None:
    worker = load_service("ai/evidence-worker")
    db = AlignedEvidenceDb(
        [
            {
                "evidence_key": "target-agent-window",
                "window_start": "2026-07-24T01:00:30Z",
                "payload": aligned_payload(
                    namespace="target",
                    resource_name="cluster-agent",
                    reason="upstream_unavailable",
                ),
            }
        ]
    )
    event = alert_event(worker)

    joined = asyncio.run(worker.attach_aligned_cluster_evidence(event, make_context(db=db)))
    enriched = asyncio.run(worker.attach_gitops_change_context(joined, make_context(db=db)))
    report = completed_report(persisted_bundle(worker, enriched))

    assert joined.logs == []
    assert report.rca_detail is not None
    assert report.rca_detail.missing_evidence
    assert deterministic_rca_narrative(report) is None


def test_unscoped_no_room_text_never_finalizes_or_drives_fallback_narrative() -> None:
    worker = load_service("ai/evidence-worker")
    payload = aligned_payload(reason="no_room")
    payload["logs"][0]["streams"][0]["values"][0]["line"] = (
        "health probe annotation mentions no_room but is not an admission event"
    )
    db = AlignedEvidenceDb(
        [
            {
                "evidence_key": "unscoped-no-room-window",
                "window_start": payload["window_start"],
                "payload": payload,
            },
        ]
    )
    joined = asyncio.run(
        worker.attach_aligned_cluster_evidence(alert_event(worker), make_context(db=db))
    )
    enriched = asyncio.run(worker.attach_gitops_change_context(joined, make_context(db=db)))
    persisted = replace(
        enriched,
        evidence_key=rca_enriched_evidence_key(WORKSPACE, CLUSTER, "corr-unscoped"),
    )
    evidence = worker.pipeline.build_evidence(persisted, "corr-unscoped")
    bundle = build_incident_evidence_bundle(evidence, incident())
    plan = plan_causes(incident(), bundle, evidence.object_ref)
    evaluations = evaluate_causes(plan.candidates, bundle)
    evaluated = RcaCandidatesEvaluatedBody(
        candidate_count=plan.candidate_count,
        evidence_ref=evidence.object_ref,
        candidates=plan.candidates,
        evaluations=evaluations,
        workspace_id=WORKSPACE,
        evidence=evidence,
        incident=incident(),
        evidence_bundle=bundle,
    )
    result = RcaCompletionPipeline().complete_body(evaluated)

    assert isinstance(result, RcaAnalysisBlockedBody)
    assert "signal:no_room_identity_verified" in result.missing_evidence

    report_db = CapturingRcaReportDb()
    rca_worker = load_service("ai/rca-worker")
    emitted = run_handler(
        rca_worker.on_candidates_evaluated,
        evaluated,
        report_db,
        correlation_id="corr-unscoped",
    )

    assert isinstance(emitted[0], RcaAnalysisBlockedBody)
    assert report_db.saved[0]["root_cause"] == "insufficient_evidence"
    saved_body = report_db.saved[0]["body"]
    assert isinstance(saved_body, dict)
    assert saved_body["analysis_status"] == "blocked"
    assert saved_body["candidates"]
    assert saved_body["evaluations"]


def test_fallback_requires_attestation_for_the_selected_candidate() -> None:
    worker = load_service("ai/evidence-worker")
    valid = aligned_payload(reason="no_room")
    db = AlignedEvidenceDb(
        [
            {
                "evidence_key": "structured-no-room-window",
                "window_start": valid["window_start"],
                "payload": valid,
            },
        ]
    )
    joined = asyncio.run(
        worker.attach_aligned_cluster_evidence(alert_event(worker), make_context(db=db))
    )
    valid_report = completed_report(persisted_bundle(worker, joined))
    assert valid_report.root_cause == "no_room_capacity"

    unscoped = aligned_payload(reason="no_room")
    unscoped["logs"][0]["streams"][0]["values"][0]["line"] = "unscoped no_room text"
    untrusted_bundle = persisted_bundle(
        worker,
        worker.ClusterEvidenceReceivedBody.from_body(
            {
                **joined.to_body(),
                "logs": unscoped["logs"],
            }
        ),
    )
    forged_completed = replace(valid_report, evidence_bundle=untrusted_bundle)

    assert deterministic_rca_narrative(forged_completed) is None


def test_deterministic_narrative_renders_only_correlated_values_without_raw_leakage() -> None:
    worker = load_service("ai/evidence-worker")
    db = AlignedEvidenceDb(
        [
            {
                "evidence_key": "agent-window",
                "window_start": "2026-07-24T01:00:30Z",
                "payload": aligned_payload(),
            }
        ]
    )
    event = alert_event(worker)
    joined = asyncio.run(worker.attach_aligned_cluster_evidence(event, make_context(db=db)))
    enriched = asyncio.run(worker.attach_gitops_change_context(joined, make_context(db=db)))
    report = completed_report(persisted_bundle(worker, enriched))

    narrative = deterministic_rca_narrative(report)
    safe_input = sanitized_rca_narrative_input(report)

    assert narrative is not None
    assert "spec.replicas를 2에서 1로" in narrative["executive_summary"]
    assert "reason=rate_limited" in narrative["reasoning"]
    assert "42.0%" in narrative["reasoning"]
    assert "20.0%" in narrative["reasoning"]
    assert "로비 처리 용량 부족" in narrative["executive_summary"]
    assert "must-not-reach-narrative" not in str(narrative)
    assert "super-secret" not in str(narrative)
    assert "must-not-reach-narrative" not in str(safe_input)
    assert safe_input["structured_findings"]["replica_change"] == {
        "field_path": "spec.replicas",
        "before": 2,
        "after": 1,
    }
    assert safe_input["structured_findings"]["failure_ratio"] == {
        "observed": 0.42,
        "threshold": 0.2,
    }

    projected = rca_report_projection(
        {
            **report.to_body(),
            "narrative": narrative,
            "narrative_status": "generated",
        }
    )
    assert projected["narrative"]["executive_summary"] == narrative["executive_summary"]
    assert projected["supporting_evidence_refs"]


def test_attested_narrative_keeps_recovery_pr_direction_over_model_contradiction() -> None:
    fallback = {
        "locale": "ko",
        "executive_summary": "replica 감소와 실패 시점이 일치합니다.",
        "impact": "실패율이 임계치를 초과했습니다.",
        "reasoning": "배포·지표·로그가 같은 워크로드에서 일치합니다.",
        "recommended_action": "이전 replica 값으로 복구 PR을 생성합니다.",
        "recurrence_prevention": ["축소 전 부하 검증"],
        "limitations": ["배포 후 재검증 필요"],
    }
    generated = {
        **fallback,
        "executive_summary": "모델 요약",
        "reasoning": "모델 추론",
        "recommended_action": "현재 상태를 유지합니다.",
        "impact": "사람이 읽기 쉬운 영향 설명",
    }

    narrative = evidence_anchored_narrative(generated, fallback)

    assert narrative["executive_summary"] == fallback["executive_summary"]
    assert narrative["reasoning"] == fallback["reasoning"]
    assert narrative["recommended_action"] == fallback["recommended_action"]
    assert narrative["impact"] == "사람이 읽기 쉬운 영향 설명"


def test_fallback_and_llm_input_render_observed_values_not_demo_constants() -> None:
    worker = load_service("ai/evidence-worker")
    db = AlignedEvidenceDb(
        [
            {
                "evidence_key": "agent-window",
                "window_start": "2026-07-24T01:00:30Z",
                "payload": aligned_payload(reason="capacity_exhausted"),
            }
        ]
    )
    joined = asyncio.run(
        worker.attach_aligned_cluster_evidence(alert_event(worker), make_context(db=db))
    )
    enriched = asyncio.run(worker.attach_gitops_change_context(joined, make_context(db=db)))
    change = enriched.metadata["change_context"]["recent_changes"][0]
    change["before"] = 3
    change["after"] = 2
    report = completed_report(persisted_bundle(worker, enriched))

    narrative = deterministic_rca_narrative(report)
    safe_input = sanitized_rca_narrative_input(report)

    assert narrative is not None
    assert "spec.replicas를 3에서 2로" in narrative["executive_summary"]
    assert "reason=capacity_exhausted" in narrative["reasoning"]
    assert "3→2" in narrative["reasoning"]
    assert safe_input["structured_findings"]["replica_change"]["before"] == 3
    assert safe_input["structured_findings"]["replica_change"]["after"] == 2
    assert safe_input["structured_findings"]["structured_log"]["reason"] == (
        "capacity_exhausted"
    )


def test_server_enriched_metadata_reference_opens_exact_authoritative_diff() -> None:
    worker = load_service("ai/evidence-worker")
    db = AlignedEvidenceDb(
        [
            {
                "evidence_key": "agent-window",
                "window_start": "2026-07-24T01:00:30Z",
                "payload": aligned_payload(),
            }
        ]
    )

    async def run_worker():
        return [
            item
            async for item in worker.on_cluster_evidence(
                alert_event(worker),
                make_context(db=db, correlation_id="corr-click"),
            )
        ]

    outputs = asyncio.run(run_worker())

    assert outputs
    assert db.saved_window is not None
    stored_payload = db.saved_window["payload"]
    assert isinstance(stored_payload, dict)
    metadata = stored_payload["metadata"]
    assert metadata["change_context"]["recent_changes"][0] == {
        "change_type": "replicas",
        "changed_at": "2026-07-24T01:00:00Z",
        "target_resource": "deployment/api-server",
        "field": "spec.replicas",
        "source": "gitops",
        "repository_id": "repo-demo",
        "repo_ref": "example/game",
        "manifest_path": "deploy/k8s/overlays/game-server",
        "commit_sha": "a" * 40,
        "workflow_run_id": "run-demo",
        "field_path": "spec.replicas",
        "before": 2,
        "after": 1,
    }

    class QueryDb:
        def get_evidence_window_payload_for_workspace(
            self,
            workspace_id: str,
            evidence_key: str,
        ):
            assert workspace_id == WORKSPACE
            assert evidence_key == db.saved_window["evidence_key"]
            return stored_payload

    response = asyncio.run(
        get_evidence_window_payload(
            str(db.saved_window["evidence_key"]),
            source="metadata",
            current=SimpleNamespace(workspace_id=WORKSPACE),
            db=QueryDb(),
        )
    )

    assert response.payload["metadata"]["change_context"]["recent_changes"][0][
        "field_path"
    ] == "spec.replicas"
    assert response.payload["metadata"]["change_context"]["recent_changes"][0]["before"] == 2
    assert response.payload["metadata"]["change_context"]["recent_changes"][0]["after"] == 1


def test_missing_diff_or_structured_log_never_invents_presentation_facts() -> None:
    bundle = build_incident_evidence_bundle(
        alert_event(load_service("ai/evidence-worker")),
        incident(),
    )
    report = completed_report(bundle)

    assert deterministic_rca_narrative(report) is None
    assert "structured_findings" not in sanitized_rca_narrative_input(report)
