from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

from conftest import load_service

from domains.rca.recovery_verification import (
    before_alert_snapshot,
    evaluate_recovery_evidence,
)

feedback_worker = load_service("ai/rca-feedback-worker")

START = datetime(2026, 7, 24, 1, 0, tzinfo=UTC)
TARGET = {
    "cluster_id": "game-server111-7224",
    "namespace": "sandbox",
    "resource_kind": "Deployment",
    "resource_name": "api-server",
}
SLI_IDENTITY = {
    "namespace": TARGET["namespace"],
    "resource_kind": TARGET["resource_kind"],
    "resource_name": TARGET["resource_name"],
    "service": "matchmaking",
    "sli": "admission",
    "symptom": "admission_failure",
}
ALERT_SERIES_IDENTITY = dict(SLI_IDENTITY)


def lifecycle() -> dict[str, object]:
    return {
        "verification": {
            "started_at": START.isoformat(),
            "deadline_at": (START + timedelta(minutes=10)).isoformat(),
            "minimum_seconds": 300,
            "maximum_seconds": 600,
            "target": TARGET,
            "expected": {
                "failure_ratio_max": 0.2,
                "request_rate_baseline": 40.0,
                "request_rate_tolerance_ratio": 0.2,
                "replicas": 2,
                "protected_workloads": 5,
                "failure_ratio_metric_identity": SLI_IDENTITY,
                "request_rate_metric_identity": SLI_IDENTITY,
                "evidence_cadence_seconds": 60,
            },
            "before": {
                "available": True,
                "alert_event_id": "alert-original",
                "rule_id": "opsia-sli",
                "rule_name": "OpsiaSliFailureRatioHigh",
                "source": "alertmanager",
                "subject_key": "sandbox:Deployment:api-server",
                "series_identity": ALERT_SERIES_IDENTITY,
            },
            "protected_baseline": [
                {
                    "kind": "Deployment",
                    "namespace": TARGET["namespace"],
                    "name": f"arena-{chr(ord('a') + index)}",
                    "uid": f"uid-room-{index}",
                    "pod_uids": [f"uid-room-{index}-pod"],
                    "pod_start_times": ["2026-07-24T00:00:00Z"],
                    "restart_count": 0,
                }
                for index in range(5)
            ],
            "protected_session_baseline": [
                {
                    "kind": "Deployment",
                    "namespace": TARGET["namespace"],
                    "name": f"arena-{chr(ord('a') + index)}",
                    "continuity_id": f"session-{index}",
                    "pod_uid": f"uid-room-{index}-pod",
                    "value": 1.0,
                    "sample_timestamp": (
                        START - timedelta(seconds=30)
                    ).timestamp(),
                }
                for index in range(5)
            ],
        }
    }


def alert(
    *,
    event_id: str = "alert-original",
    status: str = "resolved",
    fired_at: datetime = START - timedelta(minutes=2),
    resolved_at: datetime | None = START + timedelta(seconds=10),
) -> dict[str, object]:
    return {
        "event_id": event_id,
        "rule_id": "opsia-sli",
        "rule_name": "OpsiaSliFailureRatioHigh",
        "source": "alertmanager",
        "subject_key": "sandbox:Deployment:api-server",
        "series_identity": ALERT_SERIES_IDENTITY,
        "subject": {
            "cluster": TARGET["cluster_id"],
            "namespace": TARGET["namespace"],
            "kind": TARGET["resource_kind"],
            "name": TARGET["resource_name"],
        },
        "status": status,
        "fired_at": fired_at.isoformat(),
        "resolved_at": resolved_at.isoformat() if resolved_at else None,
    }


def alert_for_snapshot(
    *,
    event_id: str = "alert-original",
    status: str = "resolved",
    incident_id: str = "incident-1",
    fired_at: datetime | None = START - timedelta(minutes=2),
    resolved_at: datetime | None = START - timedelta(minutes=1),
) -> dict[str, object]:
    item = alert(
        event_id=event_id,
        status=status,
        fired_at=fired_at or START - timedelta(minutes=2),
        resolved_at=resolved_at,
    )
    item.update(
        {
            "incident_id": incident_id,
            "observed_value": 0.381,
            "threshold": 0.2,
        }
    )
    if fired_at is None:
        item["fired_at"] = None
    return item


def test_before_alert_snapshot_accepts_exact_terminal_resolved_occurrence() -> None:
    snapshot = before_alert_snapshot(
        [alert_for_snapshot()],
        target=TARGET,
        correlation_id="correlation-1",
        incident_id="incident-1",
    )

    assert snapshot["available"] is True
    assert snapshot["alert_event_id"] == "alert-original"
    assert snapshot["observed_value"] == 0.381
    assert snapshot["threshold"] == 0.2


def test_before_alert_snapshot_rejects_incomplete_resolved_chronology() -> None:
    for candidate in (
        alert_for_snapshot(fired_at=None),
        alert_for_snapshot(resolved_at=None),
        alert_for_snapshot(
            fired_at=START,
            resolved_at=START - timedelta(seconds=1),
        ),
    ):
        snapshot = before_alert_snapshot(
            [candidate],
            target=TARGET,
            correlation_id="correlation-1",
            incident_id="incident-1",
        )

        assert snapshot == {
            "available": False,
            "reason_code": "pre_recovery_alert_missing",
        }


def test_before_alert_snapshot_keeps_exact_resolved_duplicates_ambiguous() -> None:
    snapshot = before_alert_snapshot(
        [
            alert_for_snapshot(event_id="alert-original"),
            alert_for_snapshot(event_id="alert-duplicate"),
        ],
        target=TARGET,
        correlation_id="correlation-1",
        incident_id="incident-1",
    )

    assert snapshot == {
        "available": False,
        "reason_code": "pre_recovery_alert_ambiguous",
    }


def test_before_alert_snapshot_rejects_other_incident_and_series() -> None:
    other_series = alert_for_snapshot()
    other_series["series_identity"] = {
        **ALERT_SERIES_IDENTITY,
        "resource_name": "another-api",
    }

    snapshot = before_alert_snapshot(
        [
            alert_for_snapshot(incident_id="another-incident"),
            other_series,
        ],
        target=TARGET,
        correlation_id="correlation-1",
        incident_id="incident-1",
    )

    assert snapshot == {
        "available": False,
        "reason_code": "pre_recovery_alert_missing",
    }


def test_before_alert_snapshot_requires_alertmanager_rule_and_metric_identity() -> None:
    wrong_source = alert_for_snapshot(event_id="wrong-source")
    wrong_source["source"] = "synthetic"
    wrong_rule = alert_for_snapshot(event_id="wrong-rule")
    wrong_rule["rule_name"] = "AnotherFailureRatioRule"
    wrong_sli = alert_for_snapshot(event_id="wrong-sli")
    wrong_sli["series_identity"] = {
        **ALERT_SERIES_IDENTITY,
        "symptom": "latency_failure",
    }

    snapshot = before_alert_snapshot(
        [wrong_source, wrong_rule, wrong_sli],
        target=TARGET,
        correlation_id="correlation-1",
        incident_id="incident-1",
        expected_series_identity=ALERT_SERIES_IDENTITY,
    )

    assert snapshot == {
        "available": False,
        "reason_code": "pre_recovery_alert_missing",
    }


def evidence(
    offset_seconds: int,
    *,
    failure_ratio: float = 0.05,
    request_rate: float = 40.0,
    ready_replicas: int = 2,
    desired_replicas: int = 2,
    evidence_key: str | None = None,
) -> dict[str, object]:
    labels = dict(SLI_IDENTITY)
    workloads: list[dict[str, object]] = [
        {
            "workload": {
                "kind": "Deployment",
                "namespace": TARGET["namespace"],
                "name": TARGET["resource_name"],
                "uid": "uid-api-server",
                "generation": 9,
            },
            "deployment_status": {
                "observed_generation": 9,
                "desired_replicas": desired_replicas,
                "ready_replicas": ready_replicas,
                "updated_replicas": ready_replicas,
                "available_replicas": ready_replicas,
                "unavailable_replicas": desired_replicas - ready_replicas,
            },
            "pod_statuses": [],
        }
    ]
    for index in range(5):
        workloads.append(
            {
                "workload": {
                    "kind": "Deployment",
                    "namespace": TARGET["namespace"],
                    "name": f"arena-{chr(ord('a') + index)}",
                    "uid": f"uid-room-{index}",
                    "generation": 1,
                },
                "deployment_status": {
                    "observed_generation": 1,
                    "desired_replicas": 1,
                    "ready_replicas": 1,
                    "updated_replicas": 1,
                    "available_replicas": 1,
                    "unavailable_replicas": 0,
                },
                "deployment_labels": {
                    "opsia.dev/recovery-continuity": "protected",
                },
                "pod_template_labels": {
                    "opsia.dev/recovery-continuity": "protected",
                },
                "pod_statuses": [
                    {
                        "uid": f"uid-room-{index}-pod",
                        "ready": True,
                        "restart_count": 0,
                        "start_time": "2026-07-24T00:00:00Z",
                    }
                ],
            }
        )
    observed_at = START + timedelta(seconds=offset_seconds)
    return {
        "evidence_key": evidence_key or f"window-{offset_seconds}",
        "window_start": observed_at.isoformat(),
        "metrics": {
            "results": {
                "opsia_sli_failure_ratio": {
                    "samples": [{"metric": labels, "value": failure_ratio}]
                },
                "opsia_sli_request_rate": {
                    "samples": [{"metric": labels, "value": request_rate}]
                },
                "opsia_continuity_active_sessions": {
                    "samples": [
                        {
                            "metric": {
                                "namespace": TARGET["namespace"],
                                "resource_kind": "Deployment",
                                "resource_name": f"arena-{chr(ord('a') + index)}",
                                "continuity_id": f"session-{index}",
                                "pod_uid": f"uid-room-{index}-pod",
                            },
                            "value": 1,
                            "timestamp": observed_at.timestamp(),
                        }
                        for index in range(5)
                    ]
                },
            }
        },
        "metadata": {"current_workload_snapshots": workloads},
    }


def apply_decision(
    durable: dict[str, object],
    *,
    offset_seconds: int,
    sample: dict[str, object] | None = None,
    alerts: list[dict[str, object]] | None = None,
):
    current = sample or evidence(offset_seconds)
    decision = evaluate_recovery_evidence(
        plan_payload={"target": TARGET},
        lifecycle=durable,
        evidence=current,
        alerts=alerts or [alert()],
        now=START + timedelta(seconds=offset_seconds),
    )
    verification = durable["verification"]
    assert isinstance(verification, dict)
    verification.update(
        {
            "healthy_since": decision.healthy_since,
            "last_healthy_observed_at": decision.last_healthy_observed_at,
            "distinct_evidence_count": decision.distinct_evidence_count,
            "last_evidence_key": decision.last_evidence_key,
        }
    )
    current_sessions = decision.after.get("protected_active_sessions")
    if isinstance(current_sessions, list):
        verification["last_session_samples"] = deepcopy(current_sessions)
    protected = decision.after.get("protected_workloads")
    if verification.get("protected_baseline") is None and isinstance(protected, list):
        verification["protected_baseline"] = [
            {
                "name": room["name"],
                "kind": room["kind"],
                "namespace": room["namespace"],
                "uid": room["uid"],
                "pod_uids": room["pod_uids"],
                "pod_start_times": room["pod_start_times"],
                "restart_count": room["restart_count"],
            }
            for room in protected
        ]
    return decision


def test_completes_only_after_distinct_continuous_five_minute_windows() -> None:
    durable = lifecycle()

    for offset in (0, 60, 120, 180, 240):
        decision = apply_decision(durable, offset_seconds=offset)
        assert decision.status == "pending"
        assert decision.reason_code == "stabilization_window_in_progress"

    completed = apply_decision(durable, offset_seconds=300)

    assert completed.status == "completed"
    assert completed.reason_code == "recovery_verified"
    assert completed.after["failure_ratio"] == 0.05
    assert completed.after["request_rate"] == 40.0
    assert completed.after["deployment"]["ready_replicas"] == 2
    assert len(completed.after["protected_workloads"]) == 5
    assert len(completed.after["protected_active_sessions"]) == 5
    assert completed.after["checks"]["protected_active_sessions_maintained"] is True


def test_restores_approved_three_replica_baseline_without_two_replica_constant() -> None:
    durable = lifecycle()
    verification = durable["verification"]
    assert isinstance(verification, dict)
    expected = verification["expected"]
    assert isinstance(expected, dict)
    expected["replicas"] = 3

    decision = apply_decision(
        durable,
        offset_seconds=0,
        sample=evidence(0, ready_replicas=3, desired_replicas=3),
    )

    assert decision.after["deployment"]["desired_replicas"] == 3
    assert decision.after["checks"]["desired_replicas_restored"] is True
    assert decision.after["checks"]["ready_replicas_restored"] is True


def test_omitted_unavailable_replicas_is_zero_for_a_complete_rollout() -> None:
    sample = evidence(0)
    metadata = sample["metadata"]
    assert isinstance(metadata, dict)
    snapshots = metadata["current_workload_snapshots"]
    assert isinstance(snapshots, list)
    target_snapshot = snapshots[0]
    assert isinstance(target_snapshot, dict)
    status = target_snapshot["deployment_status"]
    assert isinstance(status, dict)
    status.pop("unavailable_replicas")

    decision = apply_decision(lifecycle(), offset_seconds=0, sample=sample)

    assert decision.reason_code == "stabilization_window_in_progress"
    assert decision.after["deployment"]["unavailable_replicas"] is None
    assert decision.after["checks"]["unavailable_replicas_zero"] is True


def test_omitted_unavailable_replicas_fails_closed_for_incomplete_rollout() -> None:
    variants = (
        ("desired_replicas", 1),
        ("ready_replicas", 1),
        ("updated_replicas", 1),
        ("available_replicas", 1),
        ("observed_generation", 8),
    )

    for field, value in variants:
        sample = evidence(0)
        snapshots = sample["metadata"]["current_workload_snapshots"]  # type: ignore[index]
        assert isinstance(snapshots, list)
        target_snapshot = snapshots[0]
        assert isinstance(target_snapshot, dict)
        status = target_snapshot["deployment_status"]
        assert isinstance(status, dict)
        status.pop("unavailable_replicas")
        status[field] = value

        decision = apply_decision(lifecycle(), offset_seconds=0, sample=sample)

        assert decision.after["checks"]["unavailable_replicas_zero"] is None


def test_omitted_unavailable_replicas_accepts_newer_observed_generation() -> None:
    sample = evidence(0)
    snapshots = sample["metadata"]["current_workload_snapshots"]  # type: ignore[index]
    assert isinstance(snapshots, list)
    target_snapshot = snapshots[0]
    assert isinstance(target_snapshot, dict)
    status = target_snapshot["deployment_status"]
    assert isinstance(status, dict)
    status.pop("unavailable_replicas")
    status["observed_generation"] = 10

    decision = apply_decision(lifecycle(), offset_seconds=0, sample=sample)

    assert decision.after["checks"]["unavailable_replicas_zero"] is True


def test_invalid_present_unavailable_replicas_is_not_treated_as_omitted() -> None:
    sample = evidence(0)
    snapshots = sample["metadata"]["current_workload_snapshots"]  # type: ignore[index]
    assert isinstance(snapshots, list)
    target_snapshot = snapshots[0]
    assert isinstance(target_snapshot, dict)
    status = target_snapshot["deployment_status"]
    assert isinstance(status, dict)
    status["unavailable_replicas"] = -1

    decision = apply_decision(lifecycle(), offset_seconds=0, sample=sample)

    assert decision.after["checks"]["unavailable_replicas_zero"] is None


def test_duplicate_and_stale_windows_do_not_advance_stability_clock() -> None:
    durable = lifecycle()
    first = apply_decision(durable, offset_seconds=0)
    assert first.distinct_evidence_count == 1

    duplicate = apply_decision(
        durable,
        offset_seconds=60,
        sample=evidence(60, evidence_key="window-0"),
    )
    stale = apply_decision(durable, offset_seconds=-1)
    future = apply_decision(
        durable,
        offset_seconds=60,
        sample=evidence(61, evidence_key="future-window"),
    )

    assert duplicate.reason_code == "duplicate_evidence_window"
    assert duplicate.distinct_evidence_count == 1
    assert stale.reason_code == "stale_evidence_window"
    assert stale.distinct_evidence_count == 1
    assert future.reason_code == "stale_evidence_window"
    assert future.distinct_evidence_count == 1


def test_collection_gap_resets_continuous_window() -> None:
    durable = lifecycle()
    apply_decision(durable, offset_seconds=0)
    apply_decision(durable, offset_seconds=60)

    after_gap = apply_decision(durable, offset_seconds=180)

    assert after_gap.status == "pending"
    assert after_gap.healthy_since == (START + timedelta(seconds=180)).isoformat()
    assert after_gap.distinct_evidence_count == 1


def test_missing_metric_or_unready_rollout_resets_health() -> None:
    durable = lifecycle()
    apply_decision(durable, offset_seconds=0)

    without_rate = evidence(60)
    metrics = without_rate["metrics"]
    assert isinstance(metrics, dict)
    results = metrics["results"]
    assert isinstance(results, dict)
    results.pop("opsia_sli_request_rate")
    missing = apply_decision(durable, offset_seconds=60, sample=without_rate)
    unready = apply_decision(
        durable,
        offset_seconds=120,
        sample=evidence(120, ready_replicas=1),
    )

    assert missing.reason_code == "verification_evidence_missing"
    assert missing.healthy_since is None
    assert unready.reason_code == "verification_regressed"
    assert unready.after["checks"]["ready_replicas_restored"] is False


def test_same_workload_but_different_sli_series_cannot_verify_recovery() -> None:
    durable = lifecycle()
    wrong_series = evidence(0)
    metrics = wrong_series["metrics"]
    assert isinstance(metrics, dict)
    results = metrics["results"]
    assert isinstance(results, dict)
    for result in results.values():
        assert isinstance(result, dict)
        samples = result["samples"]
        assert isinstance(samples, list)
        sample = samples[0]
        assert isinstance(sample, dict)
        labels = sample["metric"]
        assert isinstance(labels, dict)
        labels["symptom"] = "latency"

    decision = apply_decision(durable, offset_seconds=0, sample=wrong_series)

    assert decision.reason_code == "verification_evidence_missing"
    assert decision.after["checks"]["failure_ratio_below_threshold"] is None
    assert decision.after["checks"]["request_rate_near_baseline"] is None


def test_standard_sli_ignores_raw_pod_series_and_uses_recorded_aggregate() -> None:
    durable = lifecycle()
    sample = evidence(0)
    failure_samples = sample["metrics"]["results"]["opsia_sli_failure_ratio"][  # type: ignore[index]
        "samples"
    ]
    assert isinstance(failure_samples, list)
    raw = deepcopy(failure_samples[0])
    assert isinstance(raw, dict)
    labels = raw["metric"]
    assert isinstance(labels, dict)
    labels.update({"pod": "legacy-lobby-a", "instance": "10.0.0.7:8080"})
    raw["value"] = 0.95
    failure_samples.insert(0, raw)

    decision = apply_decision(durable, offset_seconds=0, sample=sample)

    assert decision.after["failure_ratio"] == 0.05
    assert decision.after["checks"]["failure_ratio_below_threshold"] is True


def test_standard_sli_accepts_exact_record_name_label() -> None:
    durable = lifecycle()
    sample = evidence(0)
    for metric_name in ("opsia_sli_failure_ratio", "opsia_sli_request_rate"):
        samples = sample["metrics"]["results"][metric_name]["samples"]  # type: ignore[index]
        assert isinstance(samples, list)
        assert isinstance(samples[0], dict)
        labels = dict(samples[0]["metric"])
        assert isinstance(labels, dict)
        labels["__name__"] = metric_name
        samples[0]["metric"] = labels

    decision = apply_decision(durable, offset_seconds=0, sample=sample)

    assert decision.after["failure_ratio"] == 0.05
    assert decision.after["request_rate"] == 40.0


def test_exact_alert_can_resolve_during_rollout_but_must_not_refire_after_start() -> None:
    durable = lifecycle()
    resolved_too_early = alert(resolved_at=START - timedelta(seconds=1))
    early = apply_decision(durable, offset_seconds=0, alerts=[resolved_too_early])
    assert early.after["checks"]["alertmanager_resolved"] is True

    refire = alert(
        event_id="alert-refire",
        status="firing",
        fired_at=START + timedelta(seconds=30),
        resolved_at=None,
    )
    fired_again = apply_decision(
        durable,
        offset_seconds=60,
        alerts=[alert(), refire],
    )
    assert fired_again.after["checks"]["alertmanager_no_refire"] is False
    assert fired_again.status == "pending"

    quickly_resolved_refire = alert(
        event_id="alert-refire-resolved",
        status="resolved",
        fired_at=START + timedelta(seconds=40),
        resolved_at=START + timedelta(seconds=50),
    )
    resolved_again = apply_decision(
        durable,
        offset_seconds=60,
        alerts=[alert(), quickly_resolved_refire],
    )
    assert resolved_again.after["checks"]["alertmanager_no_refire"] is False


def test_other_sli_alert_does_not_block_admission_recovery_completion() -> None:
    durable = lifecycle()
    other_sli = alert(
        event_id="alert-other-sli",
        status="resolved",
        fired_at=START + timedelta(seconds=10),
        resolved_at=START + timedelta(seconds=20),
    )
    other_sli["series_identity"] = {
        **ALERT_SERIES_IDENTITY,
        "sli": "latency",
        "symptom": "request_latency",
    }

    decision = None
    for offset in (0, 60, 120, 180, 240, 300):
        decision = apply_decision(
            durable,
            offset_seconds=offset,
            alerts=[alert(), other_sli],
        )

    assert decision is not None
    assert decision.status == "completed"
    assert decision.after["checks"]["alertmanager_no_refire"] is True


def test_game_room_uid_or_restart_change_prevents_false_resolution() -> None:
    durable = lifecycle()
    apply_decision(durable, offset_seconds=0)
    changed = deepcopy(evidence(60))
    metadata = changed["metadata"]
    assert isinstance(metadata, dict)
    rooms = metadata["current_workload_snapshots"]
    assert isinstance(rooms, list)
    room = rooms[1]
    assert isinstance(room, dict)
    pods = room["pod_statuses"]
    assert isinstance(pods, list)
    pod = pods[0]
    assert isinstance(pod, dict)
    pod["restart_count"] = 1

    decision = apply_decision(durable, offset_seconds=60, sample=changed)

    assert decision.status == "pending"
    assert decision.after["checks"]["protected_workloads_uninterrupted"] is False


def test_missing_pre_recovery_protected_baseline_fails_closed() -> None:
    durable = lifecycle()
    verification = durable["verification"]
    assert isinstance(verification, dict)
    verification["protected_baseline"] = []

    decision = apply_decision(durable, offset_seconds=0)

    assert decision.status == "pending"
    assert decision.reason_code == "verification_regressed"
    assert decision.after["checks"]["protected_workloads_present"] is False
    assert decision.after["checks"]["protected_workloads_uninterrupted"] is None
    assert decision.distinct_evidence_count == 0


def test_missing_duplicate_lower_or_pod_mismatched_session_series_fail_closed() -> None:
    variants: list[dict[str, object]] = []
    missing = deepcopy(evidence(0))
    missing_results = missing["metrics"]
    assert isinstance(missing_results, dict)
    missing_metric_results = missing_results["results"]
    assert isinstance(missing_metric_results, dict)
    missing_metric_results.pop("opsia_continuity_active_sessions")
    variants.append(missing)

    duplicate = deepcopy(evidence(0))
    duplicate_samples = duplicate["metrics"]["results"][  # type: ignore[index]
        "opsia_continuity_active_sessions"
    ]["samples"]
    assert isinstance(duplicate_samples, list)
    duplicate_samples.append(deepcopy(duplicate_samples[0]))
    variants.append(duplicate)

    lower = deepcopy(evidence(0))
    lower_samples = lower["metrics"]["results"][  # type: ignore[index]
        "opsia_continuity_active_sessions"
    ]["samples"]
    assert isinstance(lower_samples, list)
    assert isinstance(lower_samples[0], dict)
    lower_samples[0]["value"] = 0
    variants.append(lower)

    mismatched = deepcopy(evidence(0))
    mismatched_samples = mismatched["metrics"]["results"][  # type: ignore[index]
        "opsia_continuity_active_sessions"
    ]["samples"]
    assert isinstance(mismatched_samples, list)
    assert isinstance(mismatched_samples[0], dict)
    mismatched_labels = mismatched_samples[0]["metric"]
    assert isinstance(mismatched_labels, dict)
    mismatched_labels["pod_uid"] = "replacement-pod"
    variants.append(mismatched)

    duplicate_continuity_id = deepcopy(evidence(0))
    duplicate_continuity_samples = duplicate_continuity_id["metrics"]["results"][  # type: ignore[index]
        "opsia_continuity_active_sessions"
    ]["samples"]
    assert isinstance(duplicate_continuity_samples, list)
    assert isinstance(duplicate_continuity_samples[0], dict)
    assert isinstance(duplicate_continuity_samples[1], dict)
    first_identity = duplicate_continuity_samples[0]["metric"]
    second_identity = duplicate_continuity_samples[1]["metric"]
    assert isinstance(first_identity, dict)
    assert isinstance(second_identity, dict)
    second_identity["continuity_id"] = first_identity["continuity_id"]
    variants.append(duplicate_continuity_id)

    stale = deepcopy(evidence(0))
    stale_samples = stale["metrics"]["results"][  # type: ignore[index]
        "opsia_continuity_active_sessions"
    ]["samples"]
    assert isinstance(stale_samples, list)
    assert isinstance(stale_samples[0], dict)
    stale_samples[0]["timestamp"] = (START - timedelta(seconds=31)).timestamp()
    variants.append(stale)

    overflow = deepcopy(evidence(0))
    overflow_samples = overflow["metrics"]["results"][  # type: ignore[index]
        "opsia_continuity_active_sessions"
    ]["samples"]
    assert isinstance(overflow_samples, list)
    assert isinstance(overflow_samples[0], dict)
    overflow_samples[0]["timestamp"] = 1e300
    variants.append(overflow)

    for variant in variants:
        decision = apply_decision(
            lifecycle(),
            offset_seconds=0,
            sample=variant,
        )
        assert decision.status == "pending"
        assert decision.after["checks"]["protected_active_session_series_present"] is None
        assert decision.after["checks"]["protected_active_sessions_maintained"] is None


def test_equal_fresh_session_sample_is_accepted_between_prometheus_scrapes() -> None:
    durable = lifecycle()
    apply_decision(durable, offset_seconds=0)
    replay = evidence(30)
    samples = replay["metrics"]["results"][  # type: ignore[index]
        "opsia_continuity_active_sessions"
    ]["samples"]
    assert isinstance(samples, list)
    for sample in samples:
        assert isinstance(sample, dict)
        sample["timestamp"] = START.timestamp()

    decision = apply_decision(durable, offset_seconds=30, sample=replay)

    assert decision.status == "pending"
    assert decision.reason_code == "stabilization_window_in_progress"
    assert decision.after["checks"]["protected_active_session_series_present"] is True
    assert decision.distinct_evidence_count == 2


def test_eight_second_windows_complete_with_fifteen_second_scrapes() -> None:
    durable = lifecycle()
    verification = durable["verification"]
    assert isinstance(verification, dict)
    expected = verification["expected"]
    assert isinstance(expected, dict)
    expected["evidence_cadence_seconds"] = 8

    decision = None
    for offset in range(0, 305, 8):
        sample = evidence(offset)
        samples = sample["metrics"]["results"][  # type: ignore[index]
            "opsia_continuity_active_sessions"
        ]["samples"]
        assert isinstance(samples, list)
        scraped_at = START + timedelta(seconds=(offset // 15) * 15)
        for item in samples:
            assert isinstance(item, dict)
            item["timestamp"] = scraped_at.timestamp()
        decision = apply_decision(
            durable,
            offset_seconds=offset,
            sample=sample,
        )

    assert decision is not None
    assert decision.status == "completed"
    assert decision.reason_code == "recovery_verified"
    assert decision.after["stable_seconds"] == 304


def test_active_session_count_decrease_resets_verification() -> None:
    durable = lifecycle()
    verification = durable["verification"]
    assert isinstance(verification, dict)
    baseline = verification["protected_session_baseline"]
    assert isinstance(baseline, list)
    assert isinstance(baseline[0], dict)
    baseline[0]["value"] = 2.0

    decision = apply_decision(durable, offset_seconds=0)

    assert decision.after["checks"]["protected_active_session_series_present"] is True
    assert decision.after["checks"]["protected_active_sessions_maintained"] is False
    assert decision.reason_code == "verification_regressed"


def test_maximum_window_fails_without_five_minutes_of_continuous_health() -> None:
    durable = lifecycle()
    apply_decision(durable, offset_seconds=540)

    expired = apply_decision(durable, offset_seconds=600)

    assert expired.status == "failed"
    assert expired.reason_code == "verification_window_expired"


def test_evidence_expiry_persists_retryable_failure_identity() -> None:
    durable = lifecycle()
    expired = apply_decision(durable, offset_seconds=600)

    feedback_worker.apply_verification_terminal_state(
        durable,
        expired,
        evidence_ref="window-expired",
    )

    assert durable["failure"] == {
        "reason_code": "verification_window_expired",
        "reason": expired.reason,
        "evidence_ref": "window-expired",
    }
