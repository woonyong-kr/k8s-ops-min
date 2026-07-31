from domains.rca.events import EvidenceBundle, EvidenceItem
from services.ai.agent.causes.engine import analyze_root_cause, evaluate_causes
from services.ai.agent.causes.loader import load_catalog_profiles


def test_matchmaking_catalog_accepts_standardized_admission_failure() -> None:
    profile = next(
        item for item in load_catalog_profiles() if item.rule_id == "matchmaking_join_failure"
    )

    assert "admission_failure" in profile.symptoms
    lobby = next(
        item for item in profile.candidate_specs if item.candidate_id == "lobby_capacity_saturation"
    )
    facts = {
        matcher["fact"]
        for group in lobby.signals
        for matcher in group["any_of"]
        if "fact" in matcher
    }
    assert facts == {
        "standard_sli_alert_identity=verified",
        "structured_rejection_identity=verified:reason=rate_limited",
        "structured_rejection_identity=verified:reason=capacity_exhausted",
        "structured_rejection_identity=verified:reason=over_capacity",
        "structured_rejection_identity=verified:reason=lobby_capacity_exceeded",
    }
    assert lobby.title == "로비 처리 용량 부족"
    assert lobby.expected_evidence == (
        "metrics:telemetry_metrics",
        "logs:related_logs",
        "metadata:change_context",
    )
    assert all(
        "log_pattern" not in matcher
        for candidate in profile.candidate_specs
        for group in candidate.signals
        for matcher in group["any_of"]
    )


def admission_bundle(
    *,
    reason: str = "rate_limited",
    replicas_before: int = 2,
    replicas_after: int = 1,
    alert_started_at: str = "2026-07-24T01:01:00Z",
    changed_at: str = "2026-07-24T01:00:00Z",
) -> EvidenceBundle:
    return EvidenceBundle(
        incident_id="incident-admission",
        items=[
            EvidenceItem(
                source="metrics",
                name="telemetry_metrics",
                summary="standard SLI alert",
                value={
                    "alertmanager": {
                        "alerts": [
                            {
                                "status": "firing",
                                "startsAt": alert_started_at,
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
            ),
            EvidenceItem(
                source="logs",
                name="related_logs",
                summary="structured rejection logs",
                value={
                    "entries": [
                        {
                            "streams": [
                                {
                                    "stream": {
                                        "k8s_namespace_name": "sandbox",
                                        "k8s_pod_name": "api-server-7bbd8",
                                    },
                                    "values": [
                                        {
                                            "line": (
                                                '{"event":"find_game_rejected",'
                                                '"timestamp":"2026-07-24T01:00:50Z",'
                                                '"outcome":"rejected",'
                                                '"namespace":"sandbox",'
                                                '"resource_kind":"Deployment",'
                                                '"resource_name":"api-server",'
                                                '"service":"matchmaking",'
                                                '"sli":"admission",'
                                                '"symptom":"admission_failure",'
                                                f'"reason":"{reason}"'
                                                "}"
                                            )
                                        }
                                    ],
                                }
                            ]
                        }
                    ]
                },
            ),
            EvidenceItem(
                source="metadata",
                name="change_context",
                summary="exact GitOps replica diff",
                value={
                    "resource": {
                        "namespace": "sandbox",
                        "workload_kind": "Deployment",
                        "workload_name": "api-server",
                    },
                    "recent_changes": [
                        {
                            "field_path": "spec.replicas",
                            "target_resource": "Deployment/api-server",
                            "before": replicas_before,
                            "after": replicas_after,
                            "changed_at": changed_at,
                        }
                    ],
                },
            ),
        ],
        missing_evidence=[],
        complete=True,
    )


def lobby_evaluation(bundle: EvidenceBundle):
    profile = next(
        item for item in load_catalog_profiles() if item.rule_id == "matchmaking_join_failure"
    )
    candidate = next(
        item
        for item in profile.candidate_specs
        if item.candidate_id == "lobby_capacity_saturation"
    )
    return next(
        item
        for item in evaluate_causes([candidate.to_candidate()], bundle)
        if item.candidate_id == "lobby_capacity_saturation"
    )


def candidate_evaluation(bundle: EvidenceBundle, candidate_id: str):
    profile = next(
        item for item in load_catalog_profiles() if item.rule_id == "matchmaking_join_failure"
    )
    candidate = next(
        item for item in profile.candidate_specs if item.candidate_id == candidate_id
    )
    return next(
        item
        for item in evaluate_causes([candidate.to_candidate()], bundle)
        if item.candidate_id == candidate_id
    )


def test_lobby_capacity_requires_alert_log_and_time_aligned_replica_reduction() -> None:
    evaluation = lobby_evaluation(admission_bundle())
    generic_reduction = lobby_evaluation(
        admission_bundle(replicas_before=3, replicas_after=2)
    )

    assert evaluation.score == 1.0
    assert evaluation.missing_evidence == []
    assert generic_reduction.score == 1.0
    assert generic_reduction.missing_evidence == []


def test_lobby_capacity_can_finalize_when_already_scaled_to_one() -> None:
    evaluation = lobby_evaluation(
        admission_bundle(
            replicas_before=1,
            replicas_after=1,
        )
    )

    assert evaluation.score == 1.0
    assert evaluation.missing_evidence == []


def test_zero_signal_room_candidates_never_beat_verified_lobby_capacity() -> None:
    profile = next(
        item for item in load_catalog_profiles() if item.rule_id == "matchmaking_join_failure"
    )
    bundle = admission_bundle(replicas_before=1, replicas_after=1)

    evaluations = evaluate_causes(
        [item.to_candidate() for item in profile.candidate_specs],
        bundle,
    )
    detail = analyze_root_cause(evaluations)

    assert detail.root_cause == "lobby_capacity_saturation"
    assert detail.selected_candidate_id == "lobby_capacity_saturation"


def test_generic_sources_without_a_verified_reason_do_not_select_a_room_cause() -> None:
    profile = next(
        item for item in load_catalog_profiles() if item.rule_id == "matchmaking_join_failure"
    )
    bundle = admission_bundle(
        reason="unclassified",
        replicas_before=1,
        replicas_after=1,
    )

    evaluations = evaluate_causes(
        [item.to_candidate() for item in profile.candidate_specs],
        bundle,
    )
    detail = analyze_root_cause(evaluations)

    assert detail.root_cause == "insufficient_evidence"
    assert detail.selected_candidate_id == "none"


def test_repeated_deploy_cycle_uses_nearest_preceding_replica_reduction() -> None:
    bundle = admission_bundle(changed_at="2026-07-24T01:00:30Z")
    metadata = next(item for item in bundle.items if item.source == "metadata")
    metadata.value["recent_changes"].append(
        {
            "field_path": "spec.replicas",
            "target_resource": "Deployment/api-server",
            "before": 2,
            "after": 1,
            "changed_at": "2026-07-24T00:59:00Z",
        }
    )

    evaluation = lobby_evaluation(bundle)

    assert evaluation.score == 1.0
    assert evaluation.missing_evidence == []


def test_alert_alone_or_competing_reason_cannot_finalize_lobby_capacity() -> None:
    wrong_reason = lobby_evaluation(admission_bundle(reason="upstream_unavailable"))

    assert wrong_reason.score < 1.0
    assert "signal:capacity_rejection_identity_verified" in wrong_reason.missing_evidence


def test_no_room_requires_structured_exact_identity_and_time_correlation() -> None:
    valid = candidate_evaluation(admission_bundle(reason="no_room"), "no_room_capacity")
    unstructured_bundle = admission_bundle(reason="no_room")
    unstructured_logs = next(
        item for item in unstructured_bundle.items if item.source == "logs"
    )
    unstructured_logs.value["entries"][0]["streams"][0]["values"][0]["line"] = (
        "unrelated component mentions no_room in a cache key"
    )
    unstructured = candidate_evaluation(unstructured_bundle, "no_room_capacity")
    wrong_identity_bundle = admission_bundle(reason="no_room")
    wrong_identity_logs = next(
        item for item in wrong_identity_bundle.items if item.source == "logs"
    )
    wrong_identity_logs.value["entries"][0]["streams"][0]["values"][0][
        "line"
    ] = wrong_identity_logs.value["entries"][0]["streams"][0]["values"][0][
        "line"
    ].replace('"resource_name":"api-server"', '"resource_name":"other-api"')
    wrong_identity = candidate_evaluation(wrong_identity_bundle, "no_room_capacity")
    stale_bundle = admission_bundle(reason="no_room")
    stale_logs = next(item for item in stale_bundle.items if item.source == "logs")
    stale_logs.value["entries"][0]["streams"][0]["values"][0]["line"] = stale_logs.value[
        "entries"
    ][0]["streams"][0]["values"][0]["line"].replace(
        "2026-07-24T01:00:50Z",
        "2026-07-24T00:30:00Z",
    )
    stale = candidate_evaluation(stale_bundle, "no_room_capacity")

    assert valid.score == 1.0
    assert valid.missing_evidence == []
    for invalid in (unstructured, wrong_identity, stale):
        assert invalid.score < 1.0
        assert "signal:no_room_identity_verified" in invalid.missing_evidence


def test_competing_structured_rejection_reasons_fail_closed() -> None:
    bundle = admission_bundle(reason="no_room")
    log_item = next(item for item in bundle.items if item.source == "logs")
    sample = dict(log_item.value["entries"][0]["streams"][0]["values"][0])
    sample["line"] = sample["line"].replace(
        '"reason":"no_room"',
        '"reason":"rate_limited"',
    )
    log_item.value["entries"][0]["streams"][0]["values"].append(sample)

    no_room = candidate_evaluation(bundle, "no_room_capacity")
    capacity = candidate_evaluation(bundle, "lobby_capacity_saturation")

    assert no_room.score < 1.0
    assert capacity.score < 1.0


def test_legacy_detail_reason_uses_trusted_loki_sample_timestamp() -> None:
    bundle = admission_bundle()
    log_item = next(item for item in bundle.items if item.source == "logs")
    sample = log_item.value["entries"][0]["streams"][0]["values"][0]
    sample["line"] = (
        '{"event":"find_game_rejected","outcome":"rejected",'
        '"namespace":"sandbox","resource_kind":"Deployment",'
        '"resource_name":"api-server","symptom":"admission_failure",'
        '"service":"matchmaking","sli":"admission",'
        '"detail":{"reason":"rate_limited"}}'
    )
    sample["timestamp"] = "2026-07-24T01:00:50Z"

    evaluation = lobby_evaluation(bundle)

    assert evaluation.score == 1.0
    assert evaluation.missing_evidence == []


def test_proofs_from_different_alert_identities_never_combine() -> None:
    bundle = admission_bundle()
    metrics = next(item for item in bundle.items if item.source == "metrics")
    alerts = metrics.value["alertmanager"]["alerts"]
    alerts.append(
        {
            **alerts[0],
            "startsAt": "2026-07-24T01:01:10Z",
            "labels": {
                **alerts[0]["labels"],
                "opsia_resource_name": "other-api",
            },
        }
    )
    logs = next(item for item in bundle.items if item.source == "logs")
    sample = logs.value["entries"][0]["streams"][0]["values"][0]
    sample["line"] = sample["line"].replace(
        '"resource_name":"api-server"',
        '"resource_name":"other-api"',
    )
    metadata = next(item for item in bundle.items if item.source == "metadata")
    metadata.value["recent_changes"][0]["target_resource"] = "Deployment/third-api"

    evaluation = lobby_evaluation(bundle)

    assert evaluation.score < 1.0
    assert "signal:standard_sli_alert_identity_verified" in evaluation.missing_evidence
    assert "signal:capacity_rejection_identity_verified" in evaluation.missing_evidence
