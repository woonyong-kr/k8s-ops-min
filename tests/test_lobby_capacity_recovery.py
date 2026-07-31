import asyncio
from dataclasses import replace

from domains.rca.events import (
    ClusterEvidenceReceivedBody,
    IncidentRecord,
    RcaActionRequiredBody,
    RcaCompletedBody,
    RcaReportDetail,
    RecoveryActionSelectedBody,
    RecoveryPlannedBody,
)
from packages.contracts.gitops_authority import GitOpsAuthorityContext
from services.ai.agent.defaults import ActionRoutes
from services.ai.agent.pipeline.evidence import EvidenceBuilder
from services.ai.agent.recovery.catalog import registered_recovery_rules
from services.ai.agent.recovery.dispatch import (
    RecoveryActionPreflight,
    scalar_replacements_for,
)
from services.ai.agent.recovery.engine import RecoveryPlanner


def lobby_report() -> RcaCompletedBody:
    incident = IncidentRecord(
        incident_id="incident-lobby",
        cluster_id="game-server111-7224",
        resource_kind="Deployment",
        resource_name="api-server",
        namespace="target",
        symptom="application admission failure ratio high",
        severity="warning",
        first_seen_at="2026-07-24T00:00:00Z",
        summary="new player admissions are being rejected",
        workspace_id="workspace-1",
    )
    detail = RcaReportDetail(
        root_cause="lobby_capacity_saturation",
        confidence=0.98,
        selected_candidate_id="lobby_capacity_saturation",
        supporting_evidence=["object://evidence/incident-lobby.json#metrics"],
        missing_evidence=[],
        reason="the lobby was reduced from two replicas to one before traffic increased",
    )
    return RcaCompletedBody(
        root_cause=detail.root_cause,
        action="restore the approved lobby replica count",
        evidence_ref="object://evidence/incident-lobby.json",
        workspace_id="workspace-1",
        incident=incident,
        rca_detail=detail,
    )


def lobby_authority(
    *,
    changes: tuple[dict[str, object], ...],
    current_replicas: int = 1,
) -> GitOpsAuthorityContext:
    return GitOpsAuthorityContext(
        workspace_id="workspace-1",
        repository_id="repo-1",
        binding_id="binding-1",
        application_id="app-1",
        workflow_run_id="run-1",
        environment="target",
        cluster_id="game-server111-7224",
        manifest_path="k8s/api-server.yaml",
        repo_ref="example/game-server",
        base_branch="main",
        commit_sha="a" * 40,
        source_type="raw-yaml",
        source_manifest_sha256="sha256:" + "b" * 64,
        resource="Deployment/api-server",
        desired_manifest={
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "api-server", "namespace": "target"},
            "spec": {
                "replicas": current_replicas,
                "template": {
                    "spec": {
                        "containers": [
                            {"name": "api-server", "image": "example/game:v2"}
                        ]
                    }
                },
            },
        },
        changes=changes,
        evidence={},
    )


def test_lobby_capacity_recommends_only_gitops_safe_pr() -> None:
    matching = [
        rule
        for rule in registered_recovery_rules()
        if "lobby_capacity_saturation" in getattr(rule, "root_causes", ())
    ]

    assert len(matching) == 1
    assert len(matching[0].action_specs) == 1
    spec = matching[0].action_specs[0]
    assert spec.action_type == "replica_scale"
    assert spec.route == ActionRoutes().safe_pr
    assert spec.params == {
        "strategy": "last_approved_snapshot",
        "allow_bounded_scale_out": True,
        "verification_contract": "protected_workload_continuity",
    }
    assert all(action.action_type != "deployment_scale" for action in matching[0].action_specs)

    planned = RecoveryPlanner().plan_body(lobby_report())

    assert isinstance(planned, RecoveryPlannedBody)
    assert planned.plan is not None
    recommended = next(
        candidate
        for candidate in planned.plan.candidates
        if candidate.action_id == planned.plan.recommended_action_id
    )
    assert recommended.draft.action_type == "replica_scale"
    assert recommended.route == ActionRoutes().safe_pr
    assert recommended.draft.params["strategy"] == "last_approved_snapshot"
    assert (
        recommended.draft.params["verification_contract"]
        == "protected_workload_continuity"
    )


def test_lobby_safe_pr_restores_exact_previous_approved_replicas() -> None:
    planned = RecoveryPlanner().plan_body(lobby_report())
    assert isinstance(planned, RecoveryPlannedBody)
    assert planned.plan is not None
    selected = next(
        candidate
        for candidate in planned.plan.candidates
        if candidate.action_id == planned.plan.recommended_action_id
    )
    authority = lobby_authority(
        changes=(
            {
                "field_path": "spec.replicas",
                "old_desired": 2,
                "new_desired": 1,
            },
        )
    )

    replacements = scalar_replacements_for("replica_scale", selected, authority)

    assert len(replacements) == 1
    assert replacements[0].field_path == "spec.replicas"
    assert replacements[0].current_value == 1
    assert replacements[0].desired_value == 2


def test_lobby_safe_pr_scales_out_once_without_approved_previous_value() -> None:
    planned = RecoveryPlanner().plan_body(lobby_report())
    assert isinstance(planned, RecoveryPlannedBody)
    assert planned.plan is not None
    selected = next(
        candidate
        for candidate in planned.plan.candidates
        if candidate.action_id == planned.plan.recommended_action_id
    )

    replacements = scalar_replacements_for(
        "replica_scale",
        selected,
        lobby_authority(changes=()),
    )

    assert len(replacements) == 1
    assert replacements[0].field_path == "spec.replicas"
    assert replacements[0].current_value == 1
    assert replacements[0].desired_value == 2


def test_legacy_lobby_plan_without_scale_out_flag_still_scales_once() -> None:
    planned = RecoveryPlanner().plan_body(lobby_report())
    assert isinstance(planned, RecoveryPlannedBody)
    assert planned.plan is not None
    selected = next(
        candidate
        for candidate in planned.plan.candidates
        if candidate.action_id == planned.plan.recommended_action_id
    )
    legacy_params = dict(selected.draft.params)
    legacy_params.pop("allow_bounded_scale_out")
    legacy_selected = replace(
        selected,
        draft=replace(selected.draft, params=legacy_params),
    )

    replacements = scalar_replacements_for(
        "replica_scale",
        legacy_selected,
        lobby_authority(changes=()),
    )

    assert len(replacements) == 1
    assert replacements[0].current_value == 1
    assert replacements[0].desired_value == 2


class LobbyAuthorityPort:
    async def load_authority(self, query):
        return lobby_authority(
            current_replicas=2,
            changes=(
                {
                    "field_path": "spec.replicas",
                    "old_desired": 3,
                    "new_desired": 2,
                },
            )
        )


class LobbyEvidencePort:
    def __init__(
        self,
        *,
        include_active_workload: bool = True,
        tamper_metadata_lineage: bool = False,
        unhealthy_protected_workload: bool = False,
        protected_candidate_surge: bool = False,
        protected_statuses_truncated: bool = False,
        include_sli_metrics: bool = True,
        include_exact_alert: bool = True,
        evidence_cadence_seconds: int | None = 30,
        missing_workload_uid: bool = False,
    ) -> None:
        self.include_active_workload = include_active_workload
        self.tamper_metadata_lineage = tamper_metadata_lineage
        self.unhealthy_protected_workload = unhealthy_protected_workload
        self.protected_candidate_surge = protected_candidate_surge
        self.protected_statuses_truncated = protected_statuses_truncated
        self.include_sli_metrics = include_sli_metrics
        self.include_exact_alert = include_exact_alert
        self.evidence_cadence_seconds = evidence_cadence_seconds
        self.missing_workload_uid = missing_workload_uid

    async def get_cluster_registration(
        self,
        workspace_id: str,
        cluster_id: str,
    ):
        assert (workspace_id, cluster_id) == (
            "workspace-1",
            "game-server111-7224",
        )
        return {
            "settings": {
                "evidence_interval_seconds": self.evidence_cadence_seconds,
            }
        }

    async def list_alert_events(
        self,
        workspace_id: str,
        *,
        rule_name: str | None = None,
        source: str | None = None,
        incident_ids: tuple[str, ...] | None = None,
        limit: int,
    ):
        assert workspace_id == "workspace-1"
        assert rule_name == "OpsiaSliFailureRatioHigh"
        assert source == "alertmanager"
        assert set(incident_ids or ()) == {"correlation-lobby", "incident-lobby"}
        assert limit == 10
        if not self.include_exact_alert:
            return []
        return [
            {
                "event_id": "alert-lobby",
                "rule_id": "opsia-sli",
                "rule_name": "OpsiaSliFailureRatioHigh",
                "source": "alertmanager",
                "subject_key": "target:Deployment:api-server",
                "series_identity": {
                    "namespace": "target",
                    "resource_kind": "Deployment",
                    "resource_name": "api-server",
                    "service": "matchmaking",
                    "sli": "admission",
                    "symptom": "admission_failure",
                },
                "incident_id": "incident-lobby",
                "status": "firing",
                "observed_value": 0.8,
                "threshold": 0.2,
                "fired_at": "2026-07-24T00:59:00Z",
                "subject": {
                    "cluster": "game-server111-7224",
                    "namespace": "target",
                    "kind": "Deployment",
                    "name": "api-server",
                },
            }
        ]

    async def get_evidence_payload(
        self,
        workspace_id: str,
        correlation_id: str,
        kind: str,
    ):
        assert (workspace_id, correlation_id, kind) == (
            "workspace-1",
            "correlation-lobby",
            "rca_bundle",
        )
        workloads = []
        if self.include_active_workload:
            protected_workload = {
                    "workload": {
                        "kind": "Deployment",
                        "namespace": "target",
                        "name": "arena-a",
                        "uid": (
                            None if self.missing_workload_uid else "arena-a-uid"
                        ),
                    },
                    "deployment_status": {
                        "desired_replicas": 1,
                        "ready_replicas": (
                            0 if self.unhealthy_protected_workload else 1
                        ),
                        "updated_replicas": (
                            0 if self.unhealthy_protected_workload else 1
                        ),
                        "available_replicas": (
                            0 if self.unhealthy_protected_workload else 1
                        ),
                        "unavailable_replicas": (
                            1
                            if (
                                self.unhealthy_protected_workload
                                or self.protected_candidate_surge
                            )
                            else 0
                        ),
                    },
                    "deployment_labels": {
                        "opsia.dev/recovery-continuity": "protected",
                    },
                    "pod_template_labels": {
                        "opsia.dev/recovery-continuity": "protected",
                    },
                    "pod_statuses": [
                        {
                            "uid": "arena-a-pod",
                            "ready": True,
                            "restart_count": 0,
                            "start_time": "2026-07-24T00:00:00Z",
                        },
                        *(
                            [
                                {
                                    "uid": "arena-a-candidate-pod",
                                    "ready": False,
                                    "restart_count": 0,
                                    "start_time": "2026-07-24T00:30:00Z",
                                }
                            ]
                            if self.protected_candidate_surge
                            else []
                        ),
                    ],
                }
            if self.protected_statuses_truncated:
                protected_workload["pod_statuses_truncated"] = True
                protected_workload["pod_status_count"] = 11
            workloads.append(protected_workload)
            workloads.append(
                {
                    "workload": {
                        "kind": "Deployment",
                        "namespace": "target",
                        "name": "unlabelled-gateway",
                        "uid": "gateway-uid",
                    },
                    "deployment_status": {
                        "desired_replicas": 1,
                        "ready_replicas": 1,
                        "updated_replicas": 1,
                        "available_replicas": 1,
                        "unavailable_replicas": 0,
                    },
                    "deployment_labels": {},
                    "pod_template_labels": {},
                    "pod_statuses": [
                        {
                            "uid": "gateway-pod",
                            "ready": True,
                            "restart_count": 0,
                            "start_time": "2026-07-24T00:00:00Z",
                        }
                    ],
                }
            )
        sli_labels = {
            "namespace": "target",
            "resource_kind": "Deployment",
            "resource_name": "api-server",
            "service": "matchmaking",
            "sli": "admission",
            "symptom": "admission_failure",
        }
        metric_results = {
            "opsia_continuity_active_sessions": {
                "samples": (
                    [
                        {
                            "metric": {
                                "namespace": "target",
                                "resource_kind": "Deployment",
                                "resource_name": "arena-a",
                                "continuity_id": "session-a",
                                "pod_uid": "arena-a-pod",
                            },
                            "value": 1,
                            "timestamp": 1784854800.0,
                        }
                    ]
                    if self.include_active_workload
                    else []
                )
            }
        }
        if self.include_sli_metrics:
            metric_results.update(
                {
                    "opsia_sli_failure_ratio": {
                        "samples": [{"metric": sli_labels, "value": 0.8}]
                    },
                    "opsia_sli_request_rate": {
                        "samples": [{"metric": sli_labels, "value": 40.0}]
                    },
                }
            )
        raw = ClusterEvidenceReceivedBody(
            workspace_id=workspace_id,
            cluster_id="game-server111-7224",
            agent_id="agent-lobby",
            source_id="source-lobby",
            window_start="2026-07-24T01:00:00Z",
            evidence_key="workspace-1:game-server111-7224:window:1",
            kubernetes={},
            metrics={"results": metric_results},
            logs=[],
            traces={},
            metadata={"current_workload_snapshots": workloads},
        )
        payload = EvidenceBuilder().build_evidence(raw, correlation_id).to_body()
        if self.tamper_metadata_lineage:
            payload["metadata"]["_lineage"]["evidence_key"] = "spoofed-window"  # type: ignore[index]
        return payload


def lobby_selection_event() -> RecoveryActionSelectedBody:
    planned = RecoveryPlanner().plan_body(lobby_report())
    assert isinstance(planned, RecoveryPlannedBody)
    assert planned.plan is not None
    selected = next(
        candidate
        for candidate in planned.plan.candidates
        if candidate.action_id == planned.plan.recommended_action_id
    )
    return RecoveryActionSelectedBody(
        plan=planned.plan,
        selected=selected,
        selected_by="operator-1",
        auto_selected=False,
        reason="restore capacity",
        workspace_id="workspace-1",
    )


def test_safe_pr_preflight_snapshots_arbitrary_active_workload_identity() -> None:
    prepared = asyncio.run(
        RecoveryActionPreflight(
            LobbyAuthorityPort(),
            LobbyEvidencePort(),
        ).prepare(lobby_selection_event(), "correlation-lobby")
    )

    assert not isinstance(prepared, RcaActionRequiredBody)
    assert prepared.draft.params["expected_replicas"] == 3
    assert prepared.draft.params["authorized_changes"] == [
        {
            "field_path": "spec.replicas",
            "current_value": 2,
            "desired_value": 3,
        }
    ]
    assert prepared.draft.params["protected_baseline"] == [
        {
            "kind": "Deployment",
            "namespace": "target",
            "name": "arena-a",
            "uid": "arena-a-uid",
            "pod_uids": ["arena-a-pod"],
            "pod_start_times": ["2026-07-24T00:00:00Z"],
            "restart_count": 0,
        }
    ]
    assert prepared.draft.params["protected_session_baseline"] == [
        {
            "kind": "Deployment",
            "namespace": "target",
            "name": "arena-a",
            "continuity_id": "session-a",
            "pod_uid": "arena-a-pod",
            "value": 1.0,
            "sample_timestamp": 1784854800.0,
        }
    ]
    assert prepared.draft.params["verification_failure_ratio_before"] == 0.8
    assert prepared.draft.params["verification_request_rate_baseline"] == 40.0
    assert prepared.draft.params["verification_evidence_cadence_seconds"] == 30
    assert prepared.draft.params["verification_alert_before"]["alert_event_id"] == (
        "alert-lobby"
    )


def test_safe_pr_preflight_accepts_same_exact_alert_after_resolution() -> None:
    class ResolvedLobbyEvidencePort(LobbyEvidencePort):
        async def list_alert_events(
            self,
            workspace_id: str,
            *,
            rule_name: str | None = None,
            source: str | None = None,
            incident_ids: tuple[str, ...] | None = None,
            limit: int,
        ):
            alerts = await super().list_alert_events(
                workspace_id,
                rule_name=rule_name,
                source=source,
                incident_ids=incident_ids,
                limit=limit,
            )
            alerts[0]["status"] = "resolved"
            alerts[0]["resolved_at"] = "2026-07-24T01:01:00Z"
            return alerts

    prepared = asyncio.run(
        RecoveryActionPreflight(
            LobbyAuthorityPort(),
            ResolvedLobbyEvidencePort(),
        ).prepare(lobby_selection_event(), "correlation-lobby")
    )

    assert not isinstance(prepared, RcaActionRequiredBody)
    assert prepared.draft.params["verification_alert_before"][
        "alert_event_id"
    ] == "alert-lobby"
    assert prepared.draft.params["verification_alert_before"]["threshold"] == 0.2


def test_safe_pr_preflight_uses_serving_pod_during_protected_candidate_surge() -> None:
    prepared = asyncio.run(
        RecoveryActionPreflight(
            LobbyAuthorityPort(),
            LobbyEvidencePort(protected_candidate_surge=True),
        ).prepare(lobby_selection_event(), "correlation-lobby")
    )

    assert not isinstance(prepared, RcaActionRequiredBody)
    assert prepared.draft.params["protected_baseline"] == [
        {
            "kind": "Deployment",
            "namespace": "target",
            "name": "arena-a",
            "uid": "arena-a-uid",
            "pod_uids": ["arena-a-pod"],
            "pod_start_times": ["2026-07-24T00:00:00Z"],
            "restart_count": 0,
        }
    ]


def test_safe_pr_preflight_records_blocker_when_continuity_is_missing() -> None:
    prepared = asyncio.run(
        RecoveryActionPreflight(
            LobbyAuthorityPort(),
            LobbyEvidencePort(include_active_workload=False),
        ).prepare(lobby_selection_event(), "correlation-lobby")
    )

    assert not isinstance(prepared, RcaActionRequiredBody)
    assert "metadata:current_workload_snapshots" in prepared.draft.params[
        "verification_blockers"
    ]
    assert prepared.draft.params["verification_merge_blocked"] is True


def test_safe_pr_preflight_records_blocker_for_inconsistent_lineage() -> None:
    prepared = asyncio.run(
        RecoveryActionPreflight(
            LobbyAuthorityPort(),
            LobbyEvidencePort(tamper_metadata_lineage=True),
        ).prepare(lobby_selection_event(), "correlation-lobby")
    )

    assert not isinstance(prepared, RcaActionRequiredBody)
    assert "metrics:opsia_continuity_active_sessions" in prepared.draft.params[
        "verification_blockers"
    ]


def test_safe_pr_preflight_records_blocker_if_workload_is_unhealthy() -> None:
    prepared = asyncio.run(
        RecoveryActionPreflight(
            LobbyAuthorityPort(),
            LobbyEvidencePort(unhealthy_protected_workload=True),
        ).prepare(lobby_selection_event(), "correlation-lobby")
    )

    assert not isinstance(prepared, RcaActionRequiredBody)
    assert "metadata:current_workload_snapshots" in prepared.draft.params[
        "verification_blockers"
    ]


def test_safe_pr_preflight_records_blocker_when_statuses_are_truncated() -> None:
    prepared = asyncio.run(
        RecoveryActionPreflight(
            LobbyAuthorityPort(),
            LobbyEvidencePort(protected_statuses_truncated=True),
        ).prepare(lobby_selection_event(), "correlation-lobby")
    )

    assert not isinstance(prepared, RcaActionRequiredBody)
    assert "metadata:current_workload_snapshots" in prepared.draft.params[
        "verification_blockers"
    ]


def test_safe_pr_preflight_records_blocker_on_null_workload_identity() -> None:
    prepared = asyncio.run(
        RecoveryActionPreflight(
            LobbyAuthorityPort(),
            LobbyEvidencePort(missing_workload_uid=True),
        ).prepare(lobby_selection_event(), "correlation-lobby")
    )

    assert not isinstance(prepared, RcaActionRequiredBody)
    assert "metadata:current_workload_snapshots" in prepared.draft.params[
        "verification_blockers"
    ]


def test_safe_pr_preflight_records_blockers_when_sli_baseline_is_missing() -> None:
    prepared = asyncio.run(
        RecoveryActionPreflight(
            LobbyAuthorityPort(),
            LobbyEvidencePort(include_sli_metrics=False),
        ).prepare(lobby_selection_event(), "correlation-lobby")
    )

    assert not isinstance(prepared, RcaActionRequiredBody)
    assert "metrics:opsia_sli_failure_ratio" in prepared.draft.params[
        "verification_blockers"
    ]
    assert "metrics:opsia_sli_request_rate" in prepared.draft.params[
        "verification_blockers"
    ]


def test_safe_pr_preflight_records_blocker_when_cadence_is_missing() -> None:
    prepared = asyncio.run(
        RecoveryActionPreflight(
            LobbyAuthorityPort(),
            LobbyEvidencePort(evidence_cadence_seconds=None),
        ).prepare(lobby_selection_event(), "correlation-lobby")
    )

    assert not isinstance(prepared, RcaActionRequiredBody)
    assert prepared.draft.params["verification_blockers"] == [
        "cluster:evidence_cadence"
    ]
