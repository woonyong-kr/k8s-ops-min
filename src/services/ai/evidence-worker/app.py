"""evidence-worker — cluster.evidence.received -> evidence.built."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from domains.gitops.events import GitOpsChangeContextDetectedBody
from domains.rca.events import (
    ClusterEvidenceReceivedBody,
    compact_evidence_built_body,
    rca_enriched_evidence_key,
)
from packages.contracts.event_bus.bodies import EventBody, JsonObject
from packages.contracts.stores import RcaStore
from packages.runtime.app import App, EventContext
from services.ai.agent.pipeline import EvidencePipeline
from services.ai.agent.workload_target import WorkloadTarget, resolve_workload_target

app = App("evidence-worker")
pipeline = EvidencePipeline()
GITOPS_CHANGE_CONTEXT_EVIDENCE_KIND = "gitops_change_context"
RECENT_CHANGE_LIMIT = 5
ALERTMANAGER_SOURCE_ID = "alertmanager-webhook"
RCA_ENRICHED_SOURCE_ID = "rca-evidence-worker"
ALIGNED_EVIDENCE_BEFORE_SECONDS = 600
ALIGNED_EVIDENCE_AFTER_SECONDS = 60
ALIGNED_EVIDENCE_LIMIT = 12
ALIGNED_EVIDENCE_REFS_KEY = "aligned_evidence_refs"
SliIdentity = tuple[str, str, str, str, str, str]


@app.on(ClusterEvidenceReceivedBody)
async def on_cluster_evidence(
    evt: ClusterEvidenceReceivedBody,
    ctx: EventContext[RcaStore],
) -> AsyncIterator[EventBody]:
    evt = await hydrate_evidence(evt, ctx)
    evt = await attach_aligned_cluster_evidence(evt, ctx)
    evt = await attach_gitops_change_context(evt, ctx)
    enriched_key = rca_enriched_evidence_key(
        evt.workspace_id,
        evt.cluster_id,
        ctx.correlation_id,
    )
    persisted_evt = replace(evt, evidence_key=enriched_key)
    evidence = pipeline.build_evidence(persisted_evt, ctx.correlation_id)
    window_saved = await ctx.db.upsert_rca_enriched_evidence_window(
        evidence_key=enriched_key,
        workspace_id=evidence.workspace_id,
        cluster_id=evidence.cluster_id,
        correlation_id=ctx.correlation_id,
        window_start=evt.window_start or ctx.created_at or datetime.now(UTC).isoformat(),
        # Derived windows are a clickable read model, never a raw Agent join candidate.
        source_id=RCA_ENRICHED_SOURCE_ID,
        agent_id=None,
        payload=evidence.to_body(),
    )
    if not window_saved:
        raise RuntimeError("failed to persist exact RCA evidence window")
    await ctx.db.save_evidence(
        ctx.correlation_id,
        evidence.workspace_id,
        pipeline.kind,
        evidence.to_body(),
    )
    yield compact_evidence_built_body(evidence, ctx.correlation_id, pipeline.kind)


@app.on(GitOpsChangeContextDetectedBody)
async def on_gitops_change_context(
    evt: GitOpsChangeContextDetectedBody,
    ctx: EventContext[RcaStore],
) -> None:
    await ctx.db.save_evidence(
        ctx.correlation_id,
        evt.workspace_id,
        GITOPS_CHANGE_CONTEXT_EVIDENCE_KIND,
        evt.to_body(),
    )


async def hydrate_evidence(
    evt: ClusterEvidenceReceivedBody,
    ctx: EventContext[RcaStore],
) -> ClusterEvidenceReceivedBody:
    """reference 이벤트면 evidence_windows.payload의 원문으로 복원한다."""
    if has_inline_evidence(evt) or not evt.evidence_key:
        return evt
    payload = await ctx.db.get_evidence_window_payload_for_workspace(
        evt.workspace_id,
        evt.evidence_key,
    )
    if not payload_identity_matches_reference(payload, evt):
        return evt
    hydrated = ClusterEvidenceReceivedBody.from_body(payload)
    return hydrated if isinstance(hydrated, ClusterEvidenceReceivedBody) else evt


def payload_identity_matches_reference(
    payload: object,
    evt: ClusterEvidenceReceivedBody,
) -> bool:
    """Reject a claim-check payload that is not the exact referenced tenant source."""

    if not isinstance(payload, dict):
        return False
    return (
        str(payload.get("workspace_id") or "") == evt.workspace_id
        and str(payload.get("cluster_id") or "") == evt.cluster_id
        and payload.get("source_id") == evt.source_id
        and payload.get("evidence_key") == evt.evidence_key
    )


def has_inline_evidence(evt: ClusterEvidenceReceivedBody) -> bool:
    return bool(evt.kubernetes or evt.metrics or evt.logs or evt.traces)


async def attach_aligned_cluster_evidence(
    evt: ClusterEvidenceReceivedBody,
    ctx: EventContext[RcaStore],
) -> ClusterEvidenceReceivedBody:
    """Join an Alertmanager claim to one exact-identity adjacent Agent window."""

    if not evt.window_start:
        return evt
    if evt.source_id != ALERTMANAGER_SOURCE_ID:
        return await attach_aligned_alertmanager_evidence(evt, ctx)
    identity = standard_sli_identity(evt.metrics)
    if identity is None or not sli_identity_matches_incident(identity, evt.kubernetes):
        return evt
    rows = await ctx.db.list_aligned_evidence_window_payloads(
        evt.workspace_id,
        evt.cluster_id,
        evt.window_start,
        exclude_source_id=ALERTMANAGER_SOURCE_ID,
        before_seconds=ALIGNED_EVIDENCE_BEFORE_SECONDS,
        after_seconds=ALIGNED_EVIDENCE_AFTER_SECONDS,
        limit=ALIGNED_EVIDENCE_LIMIT,
    )
    candidates: list[tuple[float, JsonObject, str]] = []
    for row in rows:
        payload = row.get("payload")
        if not isinstance(payload, dict) or not payload_matches_sli_identity(
            payload, identity
        ):
            continue
        distance = aligned_window_distance_seconds(row, payload, evt.window_start)
        if distance is None:
            continue
        evidence_key = str(row.get("evidence_key") or payload.get("evidence_key") or "").strip()
        if evidence_key:
            candidates.append((distance, payload, evidence_key))
    selected = unique_nearest_window(candidates)
    if selected is not None:
        payload, evidence_key = selected
        return merge_aligned_evidence(evt, payload, evidence_key)
    return evt


async def attach_aligned_alertmanager_evidence(
    evt: ClusterEvidenceReceivedBody,
    ctx: EventContext[RcaStore],
) -> ClusterEvidenceReceivedBody:
    """Complete the inverse arrival order: Agent window followed by an alert."""

    identities = standard_sli_result_identities(evt.metrics)
    if not identities or not evt.window_start:
        return evt
    rows = await ctx.db.list_aligned_alertmanager_window_payloads(
        evt.workspace_id,
        evt.cluster_id,
        evt.window_start,
        source_id=ALERTMANAGER_SOURCE_ID,
        before_seconds=ALIGNED_EVIDENCE_AFTER_SECONDS,
        after_seconds=ALIGNED_EVIDENCE_BEFORE_SECONDS,
        limit=ALIGNED_EVIDENCE_LIMIT,
    )
    candidates: list[tuple[float, JsonObject, str]] = []
    for row in rows:
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        alert_metrics = payload.get("metrics")
        if not isinstance(alert_metrics, dict):
            continue
        alert_identity = standard_sli_identity(alert_metrics)
        if alert_identity is None or not any(
            same_sli_identity(alert_identity, identity) for identity in identities
        ):
            continue
        alert_kubernetes = payload.get("kubernetes")
        if not isinstance(alert_kubernetes, dict) or not sli_identity_matches_incident(
            alert_identity,
            alert_kubernetes,
        ):
            continue
        distance = inverse_window_distance_seconds(row, payload, evt.window_start)
        if distance is None:
            continue
        evidence_key = str(row.get("evidence_key") or payload.get("evidence_key") or "").strip()
        if evidence_key:
            candidates.append((distance, payload, evidence_key))
    selected = unique_nearest_window(candidates)
    if selected is not None:
        payload, evidence_key = selected
        return merge_alertmanager_evidence(evt, payload, evidence_key)
    return evt


def standard_sli_result_identities(
    metrics: JsonObject,
) -> list[SliIdentity]:
    results = metrics.get("results")
    result = results.get("opsia_sli_failure_ratio") if isinstance(results, dict) else None
    if not isinstance(result, dict):
        return []
    identities: list[SliIdentity] = []
    for sample in metric_samples(result):
        labels = sample.get("metric")
        if not isinstance(labels, dict):
            continue
        identity = (
            str(labels.get("namespace") or "").strip(),
            str(labels.get("resource_kind") or "").strip(),
            str(labels.get("resource_name") or "").strip(),
            str(labels.get("service") or "").strip(),
            str(labels.get("sli") or "").strip(),
            str(labels.get("symptom") or "").strip(),
        )
        if all(identity):
            identities.append(identity)
    return identities


def same_sli_identity(
    left: SliIdentity,
    right: SliIdentity,
) -> bool:
    return (
        left[0] == right[0]
        and left[1].casefold() == right[1].casefold()
        and left[2:] == right[2:]
    )


def sli_identity_matches_incident(
    identity: SliIdentity,
    kubernetes: JsonObject,
) -> bool:
    """Bind the standard SLI labels to the incident resource in the same event."""

    resource = evidence_resource(kubernetes)
    if not all(resource.get(key) for key in ("namespace", "kind", "name")):
        return False
    symptom = str(kubernetes.get("symptom") or "").strip()
    return (
        identity[0] == resource["namespace"]
        and identity[1].casefold() == resource["kind"].casefold()
        and identity[2] == resource["name"]
        and (not symptom or identity[5] == symptom)
    )


def merge_alertmanager_evidence(
    agent_evt: ClusterEvidenceReceivedBody,
    alert_payload: JsonObject,
    evidence_key: str,
) -> ClusterEvidenceReceivedBody:
    alert_metrics = alert_payload.get("metrics")
    alertmanager = alert_metrics.get("alertmanager") if isinstance(alert_metrics, dict) else None
    alert_kubernetes = alert_payload.get("kubernetes")
    if not isinstance(alertmanager, dict) or not isinstance(alert_kubernetes, dict):
        return agent_evt
    metrics = dict(agent_evt.metrics)
    metrics["alertmanager"] = dict(alertmanager)
    kubernetes = dict(agent_evt.kubernetes)
    for key in ("resource", "symptom", "severity", "category"):
        if alert_kubernetes.get(key) not in (None, "", [], {}):
            kubernetes[key] = alert_kubernetes[key]
    metadata = dict(agent_evt.metadata)
    aligned_refs = metadata.get(ALIGNED_EVIDENCE_REFS_KEY)
    refs = dict(aligned_refs) if isinstance(aligned_refs, dict) else {}
    refs["metrics"] = evidence_key
    metadata[ALIGNED_EVIDENCE_REFS_KEY] = refs
    metadata["aligned_alertmanager_evidence"] = {
        "evidence_key": evidence_key,
        "window_start": str(alert_payload.get("window_start") or ""),
        "join": "workspace_cluster_time_standard_sli_identity",
    }
    return replace(
        agent_evt,
        kubernetes=kubernetes,
        metrics=metrics,
        metadata=metadata,
    )


def aligned_window_distance_seconds(
    row: JsonObject,
    payload: JsonObject,
    alert_started_at: str,
) -> float | None:
    """Return outcome-neutral distance for one trusted Agent window."""

    alert_time = parse_timestamp(alert_started_at)
    window_time = trusted_window_time(row, payload)
    if alert_time is None or window_time is None:
        return None
    seconds = (alert_time - window_time).total_seconds()
    if not -ALIGNED_EVIDENCE_AFTER_SECONDS <= seconds <= ALIGNED_EVIDENCE_BEFORE_SECONDS:
        return None
    return abs(seconds)


def inverse_window_distance_seconds(
    row: JsonObject,
    payload: JsonObject,
    agent_window_start: str,
) -> float | None:
    """Return outcome-neutral distance for one trusted Alertmanager window."""

    agent_time = parse_timestamp(agent_window_start)
    alert_time = trusted_window_time(row, payload)
    if agent_time is None or alert_time is None:
        return None
    seconds = (agent_time - alert_time).total_seconds()
    if not -ALIGNED_EVIDENCE_AFTER_SECONDS <= seconds <= ALIGNED_EVIDENCE_BEFORE_SECONDS:
        return None
    return abs(seconds)


def trusted_window_time(
    row: JsonObject,
    payload: JsonObject,
) -> datetime | None:
    """Use a window timestamp only when the persisted column and payload agree."""

    row_time = parse_timestamp(str(row.get("window_start") or ""))
    payload_time = parse_timestamp(str(payload.get("window_start") or ""))
    if row_time is None or payload_time is None or row_time != payload_time:
        return None
    return row_time


def unique_nearest_window(
    candidates: list[tuple[float, JsonObject, str]],
) -> tuple[JsonObject, str] | None:
    """Select by identity/time only and fail closed when nearest time is ambiguous."""

    if not candidates:
        return None
    nearest_distance = min(item[0] for item in candidates)
    nearest = [item for item in candidates if item[0] == nearest_distance]
    if len(nearest) != 1:
        return None
    _, payload, evidence_key = nearest[0]
    return payload, evidence_key


def parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def standard_sli_identity(metrics: JsonObject) -> SliIdentity | None:
    alertmanager = metrics.get("alertmanager")
    alerts = alertmanager.get("alerts") if isinstance(alertmanager, dict) else None
    identities: dict[tuple[str, str, str, str, str, str], SliIdentity] = {}
    for alert in alerts if isinstance(alerts, list) else []:
        if not isinstance(alert, dict) or str(alert.get("status") or "") != "firing":
            continue
        labels = alert.get("labels")
        if not isinstance(labels, dict):
            continue
        if str(labels.get("alertname") or "") != "OpsiaSliFailureRatioHigh":
            continue
        values = tuple(
            str(labels.get(key) or "").strip()
            for key in (
                "opsia_namespace",
                "opsia_resource_kind",
                "opsia_resource_name",
                "opsia_service",
                "opsia_sli",
                "opsia_symptom",
            )
        )
        if all(values):
            identity = values  # type: ignore[assignment]
            normalized = (
                identity[0],
                identity[1].casefold(),
                identity[2],
                identity[3],
                identity[4],
                identity[5],
            )
            identities[normalized] = identity
    return next(iter(identities.values())) if len(identities) == 1 else None


def payload_matches_sli_identity(
    payload: JsonObject,
    identity: SliIdentity,
) -> bool:
    metrics = payload.get("metrics")
    results = metrics.get("results") if isinstance(metrics, dict) else None
    result = results.get("opsia_sli_failure_ratio") if isinstance(results, dict) else None
    if not isinstance(result, dict):
        return False
    for sample in metric_samples(result):
        labels = sample.get("metric")
        if not isinstance(labels, dict):
            continue
        observed = (
            str(labels.get("namespace") or "").strip(),
            str(labels.get("resource_kind") or "").strip(),
            str(labels.get("resource_name") or "").strip(),
            str(labels.get("service") or "").strip(),
            str(labels.get("sli") or "").strip(),
            str(labels.get("symptom") or "").strip(),
        )
        if (
            observed[0] == identity[0]
            and observed[1].casefold() == identity[1].casefold()
            and observed[2:] == identity[2:]
        ):
            return True
    return False


def metric_samples(result: JsonObject) -> list[JsonObject]:
    for key in ("samples", "result", "data"):
        value = result.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def merge_aligned_evidence(
    alert_evt: ClusterEvidenceReceivedBody,
    payload: JsonObject,
    evidence_key: str,
) -> ClusterEvidenceReceivedBody:
    joined_metrics = payload.get("metrics")
    metrics = dict(joined_metrics) if isinstance(joined_metrics, dict) else {}
    metrics["alertmanager"] = alert_evt.metrics["alertmanager"]
    joined_metadata = payload.get("metadata")
    metadata = dict(joined_metadata) if isinstance(joined_metadata, dict) else {}
    metadata[ALIGNED_EVIDENCE_REFS_KEY] = {
        source: evidence_key
        for source in ("logs", "traces")
        if source_payload_present(payload, source)
    }
    metadata["aligned_evidence"] = {
        "evidence_key": evidence_key,
        "source_id": str(payload.get("source_id") or ""),
        "window_start": str(payload.get("window_start") or ""),
        "join": "workspace_cluster_time_standard_sli_identity",
    }
    logs = payload.get("logs")
    traces = payload.get("traces")
    collection_status = payload.get("collection_status")
    return replace(
        alert_evt,
        metrics=metrics,
        logs=[item for item in logs if isinstance(item, dict)] if isinstance(logs, list) else [],
        traces=dict(traces) if isinstance(traces, dict) else {},
        metadata=metadata,
        collection_status=(
            dict(collection_status)
            if isinstance(collection_status, dict)
            else alert_evt.collection_status
        ),
    )


def source_payload_present(payload: JsonObject, source: str) -> bool:
    value: Any = payload.get(source)
    return value not in (None, "", [], {})


async def attach_gitops_change_context(
    evt: ClusterEvidenceReceivedBody,
    ctx: EventContext[RcaStore],
) -> ClusterEvidenceReceivedBody:
    resource = evidence_resource(evt.kubernetes)
    target = resolve_workload_target(
        resource.get("namespace"),
        resource.get("kind"),
        resource.get("name"),
        evt.metadata,
    )
    changed_before = evt.window_start or ctx.created_at or datetime.now(UTC).isoformat()
    if not all(
        [
            evt.workspace_id,
            evt.cluster_id,
            target.namespace,
            target.resource_kind,
            target.resource_name,
            changed_before,
        ]
    ):
        return evt
    changes = await ctx.db.list_recent_workload_changes_for_evidence(
        evt.workspace_id,
        evt.cluster_id,
        target.namespace,
        target.resource_kind,
        target.resource_name,
        changed_before,
        limit=RECENT_CHANGE_LIMIT,
    )
    if not changes:
        return evt
    metadata = merge_gitops_change_context(evt.metadata, changes, target=target)
    return replace(evt, metadata=metadata)


def evidence_resource(kubernetes: JsonObject) -> JsonObject:
    resource = kubernetes.get("resource")
    if not isinstance(resource, dict):
        return {}
    return {
        key: str(value).strip()
        for key, value in resource.items()
        if key in {"kind", "name", "namespace"} and str(value).strip()
    }


def merge_gitops_change_context(
    metadata: JsonObject,
    changes: list[JsonObject],
    *,
    target: WorkloadTarget | None = None,
) -> JsonObject:
    merged = dict(metadata)
    change_context = (
        dict(merged.get("change_context")) if isinstance(merged.get("change_context"), dict) else {}
    )
    existing_changes = change_context.get("recent_changes")
    recent_changes = list(existing_changes) if isinstance(existing_changes, list) else []
    recent_changes.extend(recent_change_payload(change) for change in changes)
    change_context["recent_changes"] = recent_changes
    latest = changes[0]
    # GitOps identity와 target lineage는 agent 입력이 아니라 서버의 exact
    # workload-change 조회 결과만 권위로 사용한다.
    change_context["gitops"] = gitops_context_payload(latest)
    for key in ("gitops_target", "original_target", "gitops_target_resolution"):
        change_context.pop(key, None)
    if target is not None:
        change_context.update(target.resolution_metadata())
    image_context = image_context_payload(latest)
    if image_context:
        change_context.setdefault("image", image_context)
    merged["change_context"] = change_context
    return merged


def recent_change_payload(change: JsonObject) -> JsonObject:
    image_before = optional_text(change.get("image_before"))
    image_after = optional_text(change.get("image_after"))
    payload: JsonObject = {
        "change_type": "image" if image_before or image_after else "manifest",
        "changed_at": timestamp_text(change.get("changed_at")),
        "target_resource": (f"{change.get('resource_kind')}/{change.get('resource_name')}"),
        "field": "image" if image_before or image_after else "manifest",
        "source": "gitops",
        "repository_id": optional_text(change.get("repository_id")),
        "repo_ref": optional_text(change.get("repo_ref")),
        "manifest_path": optional_text(change.get("manifest_path")),
        "commit_sha": optional_text(change.get("commit_sha")),
        "workflow_run_id": optional_text(change.get("workflow_run_id")),
        "pr_url": optional_text(change.get("pr_url")),
    }
    if image_before:
        payload["before"] = image_before
    if image_after:
        payload["after"] = image_after
    replica_change = replica_field_change(change.get("diff_details"))
    if replica_change is not None:
        payload.update(
            {
                "change_type": "replicas",
                "field": "spec.replicas",
                "field_path": "spec.replicas",
                "before": replica_change[0],
                "after": replica_change[1],
            }
        )
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def replica_field_change(value: object) -> tuple[int, int] | None:
    details = value if isinstance(value, dict) else {}
    basis = details.get("basis")
    if (
        not isinstance(basis, dict)
        or str(basis.get("old_desired_source") or "") != "last_approved_snapshot"
    ):
        return None
    changes = details.get("changes")
    for change in changes if isinstance(changes, list) else []:
        if not isinstance(change, dict):
            continue
        if str(change.get("field_path") or "") != "spec.replicas":
            continue
        before = change.get("before", change.get("live", change.get("old_desired")))
        after = change.get("after", change.get("new_desired"))
        if isinstance(before, bool) or isinstance(after, bool):
            continue
        try:
            return int(before), int(after)
        except (TypeError, ValueError):
            continue
    return None


def gitops_context_payload(change: JsonObject) -> JsonObject:
    payload: JsonObject = {
        "workspace_id": optional_text(change.get("workspace_id")),
        "cluster_id": optional_text(change.get("cluster_id")),
        "namespace": optional_text(change.get("namespace")),
        "resource_kind": optional_text(change.get("resource_kind")),
        "resource_name": optional_text(change.get("resource_name")),
        "repository_id": optional_text(change.get("repository_id")),
        "binding_id": optional_text(change.get("binding_id")),
        "repo_ref": optional_text(change.get("repo_ref")),
        "manifest_path": optional_text(change.get("manifest_path")),
        "commit_sha": optional_text(change.get("commit_sha")),
        "workflow_run_id": optional_text(change.get("workflow_run_id")),
        "pr_url": optional_text(change.get("pr_url")),
    }
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def image_context_payload(change: JsonObject) -> JsonObject:
    previous = optional_text(change.get("image_before"))
    current = optional_text(change.get("image_after"))
    if not previous and not current:
        return {}
    return {
        key: value
        for key, value in {
            "previous": previous,
            "current": current,
            "changed": previous != current,
        }.items()
        if value not in (None, "", [], {})
    }


def optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def timestamp_text(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return optional_text(value)


if __name__ == "__main__":
    app.run()
