"""Provider-neutral allowlist projection for the Applications product surface."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from domains.cost.observation_projection import cost_workload_allocation
from domains.inventory_filter.graph import build_resource_graph

JsonObject = dict[str, Any]
Completeness = Literal["exact", "partial", "unavailable"]
APPLICATION_TOPOLOGY_NODE_LIMIT = 200
APPLICATION_TOPOLOGY_EDGE_LIMIT = 1000

SENSITIVE_PATH_PARTS = frozenset(
    {"secret", "password", "passwd", "token", "credential", "private_key", "data"}
)
DRIFTING_CLASSIFICATIONS = frozenset(
    {"adoption_required", "conflict_or_manual_change", "drift", "intended_change"}
)


def application_card(
    application: Mapping[str, Any],
    *,
    bindings: Sequence[Mapping[str, Any]],
    runs: Sequence[Mapping[str, Any]],
    inventory_rows: Sequence[Mapping[str, Any]],
    inventory_context: Mapping[str, Any],
    incident_evidence: Mapping[str, Any],
) -> JsonObject:
    inventory = inventory_projection(inventory_rows, inventory_context=inventory_context)
    drift = drift_projection(runs)
    current = current_deployment_projection(runs, inventory=inventory)
    delivery = delivery_projection(runs)
    batch_runtime = batch_runtime_projection(inventory_rows, inventory=inventory)
    return {
        "id": str(application.get("application_id") or ""),
        "name": str(application.get("name") or ""),
        "environments": sorted(
            {
                str(binding.get("environment")).strip().casefold()
                for binding in bindings
                if str(binding.get("environment") or "").strip()
            }
        ),
        "lifecycle_status": str(application.get("status") or "unknown").casefold(),
        "repository_ref": _optional_text(application.get("repo_ref")),
        "default_branch": _optional_text(application.get("default_branch")),
        "manifest_path": _optional_text(application.get("manifest_path")),
        "health": inventory["health"],
        "runtime_readiness": inventory["runtime_readiness"],
        "current_deployment": current,
        "delivery": delivery,
        "batch_runtime": batch_runtime,
        "has_drift": (
            True
            if drift["status"] == "drifted"
            else False
            if drift["status"] == "in_sync"
            else None
        ),
        "drift_summary": drift["summary"],
        "resource_counts": inventory["resource_counts"],
        "resource_counts_completeness": inventory["resource_counts_completeness"],
        "open_incidents": (
            int(incident_evidence["open_count"])
            if incident_evidence.get("complete") is True
            and incident_evidence.get("open_count") is not None
            else None
        ),
    }


def application_detail(
    application: Mapping[str, Any],
    *,
    bindings: Sequence[Mapping[str, Any]],
    runs: Sequence[Mapping[str, Any]],
    inventory_rows: Sequence[Mapping[str, Any]],
    inventory_context: Mapping[str, Any],
    incident_evidence: Mapping[str, Any],
    scope: Mapping[str, Any],
    workload_scope: Mapping[str, Any] | None = None,
    workload_runtime_rows: Sequence[Mapping[str, Any]] = (),
    workload_runtime_truncated: bool = False,
    workload_cost_evidence: Sequence[Mapping[str, Any]] = (),
) -> JsonObject:
    card = application_card(
        application,
        bindings=bindings,
        runs=runs,
        inventory_rows=inventory_rows,
        inventory_context=inventory_context,
        incident_evidence=incident_evidence,
    )
    inventory = inventory_projection(inventory_rows, inventory_context=inventory_context)
    incidents = list(incident_evidence.get("items") or [])[:3]
    drift = drift_projection(runs)
    resolved_workload_scope = dict(workload_scope or _unavailable_workload_scope())
    workload = workload_detail_projection(
        resolved_workload_scope,
        runtime_rows=workload_runtime_rows,
        runtime_truncated=workload_runtime_truncated,
        inventory_context=inventory_context,
        application_id=str(application.get("application_id") or ""),
        cost_evidence=workload_cost_evidence,
    )
    selected_scope = "workload" if workload is not None else "application"
    return {
        **card,
        "endpoints": inventory["endpoints"],
        "endpoints_completeness": inventory["resource_counts_completeness"],
        "recent_incidents": [
            {
                "id": str(item.get("id") or ""),
                "title": _bounded_optional_text(item.get("title")),
                "status": str(item.get("status") or "unknown"),
                "started_at": _optional_text(item.get("started_at")),
            }
            for item in incidents
        ],
        "recent_activity": recent_activity_projection(runs, incidents),
        "topology": topology_projection(
            inventory_rows,
            inventory_context=inventory_context,
            application_id=str(application.get("application_id") or ""),
        ),
        "history": history_projection(runs, incidents, incident_evidence=incident_evidence),
        "source": source_evidence_projection(application, drift=drift),
        "scope": {
            **dict(scope),
            "selected_scope": selected_scope,
            "workload_scope": resolved_workload_scope,
        },
        "workload": workload,
    }


def _unavailable_workload_scope() -> JsonObject:
    return {
        "availability": "unavailable",
        "completeness": "unavailable",
        "application_scope_available": False,
        "selected_workload_key": None,
        "workloads": [],
        "partial_reason_codes": [],
    }


def workload_scope_projection(
    application: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    *,
    inventory_context: Mapping[str, Any],
    scope: Mapping[str, Any],
    requested_workload_key: str | None,
) -> JsonObject:
    """Return only direct rendered-manifest workload choices.

    Application membership is established before this projection by the
    immutable rendered-manifest to inventory link.  This function deliberately
    does not discover a workload from labels, owner names, resource prefixes,
    or a browser-supplied identity.
    """

    revision = _nonnegative_int(inventory_context.get("snapshot_revision")) or 0
    if revision <= 0:
        return {
            "availability": "unavailable",
            "completeness": "unavailable",
            "application_scope_available": False,
            "selected_workload_key": None,
            "workloads": [],
            "partial_reason_codes": [],
        }

    selected_instance = _selected_instance_scope(scope)
    reasons = {
        str(reason)
        for reason in inventory_context.get("partial_reason_codes") or []
        if _optional_text(reason) is not None
    }
    source_complete = (
        inventory_context.get("resources_complete") is True
        and inventory_context.get("application_bindings_complete") is True
        and all(row.get("binding_complete") is True for row in rows)
    )
    if inventory_context.get("resources_complete") is not True:
        reasons.add("source_resources_incomplete")
    if inventory_context.get("application_bindings_complete") is not True:
        reasons.add("application_bindings_incomplete")
    if not selected_instance:
        reasons.add("selected_instance_scope_unavailable")

    workloads: list[JsonObject] = []
    for row in rows:
        if str(row.get("resource_type") or "").casefold() != "workload":
            continue
        item = _workload_scope_item(row, selected_instance=selected_instance)
        if item is None:
            reasons.add("workload_identity_incomplete")
            continue
        workloads.append(item)
    workloads.sort(
        key=lambda item: (
            str(_mapping(item.get("resource")).get("kind")).casefold(),
            str(_mapping(item.get("resource")).get("namespace") or "").casefold(),
            str(_mapping(item.get("resource")).get("name")).casefold(),
            str(item.get("key")),
        )
    )

    selected = next(
        (item for item in workloads if item["key"] == requested_workload_key),
        None,
    )
    if requested_workload_key is not None and selected is None:
        # Never echo a caller-provided opaque key.  This lets the browser
        # canonicalize an old or unauthorized deep link without disclosing
        # whether it belongs to another binding, application, or workspace.
        reasons.add("requested_workload_unavailable")

    exact = source_complete and not reasons
    if requested_workload_key is None and exact and len(workloads) == 1:
        selected = workloads[0]
    selected_key = str(selected["key"]) if selected is not None else None
    application_scope_available = not (exact and len(workloads) == 1)
    completeness: Completeness = "exact" if exact else "partial"
    return {
        "availability": "available",
        "completeness": completeness,
        "application_scope_available": application_scope_available,
        "selected_workload_key": selected_key,
        "workloads": workloads,
        "partial_reason_codes": [] if exact else sorted(reasons),
    }


def workload_detail_projection(
    workload_scope: Mapping[str, Any],
    *,
    runtime_rows: Sequence[Mapping[str, Any]],
    runtime_truncated: bool,
    inventory_context: Mapping[str, Any],
    application_id: str,
    cost_evidence: Sequence[Mapping[str, Any]] = (),
) -> JsonObject | None:
    """Project a selected workload without borrowing app-level channels."""

    selected_key = _optional_text(workload_scope.get("selected_workload_key"))
    if selected_key is None:
        return None
    workload = next(
        (
            item
            for item in workload_scope.get("workloads") or []
            if isinstance(item, Mapping) and str(item.get("key") or "") == selected_key
        ),
        None,
    )
    if workload is None:
        return None

    topology = workload_topology_projection(
        runtime_rows,
        inventory_context=inventory_context,
        application_id=application_id,
        root_key=selected_key,
        truncated=runtime_truncated,
    )
    runtime = workload_runtime_projection(
        runtime_rows,
        topology=topology,
        inventory_context=inventory_context,
    )
    resource = _mapping(workload.get("resource"))
    workload_scope_contract = _mapping(workload.get("scope"))
    pod_names = tuple(
        str(node.get("name") or "")
        for node in topology.get("nodes") or []
        if isinstance(node, Mapping) and str(node.get("resource_type") or "") == "pod"
    )
    replicas_value = _mapping(runtime.get("runtime_readiness")).get("total_pods")
    cost = cost_workload_allocation(
        cluster_id=str(workload_scope_contract.get("cluster_id") or ""),
        namespace=str(resource.get("namespace") or ""),
        workload_name=str(resource.get("name") or ""),
        pod_names=pod_names,
        replicas=replicas_value if isinstance(replicas_value, int) else None,
        evidence_windows=cost_evidence,
        membership_complete=topology.get("completeness") == "exact",
    )
    return {
        "workload": dict(workload),
        "runtime_readiness": runtime["runtime_readiness"],
        "resource_counts": runtime["resource_counts"],
        "resource_counts_completeness": runtime["resource_counts_completeness"],
        "topology": topology,
        "history": {
            "availability": "unavailable",
            "reason_codes": ["workload_history_link_not_persisted"],
        },
        "cost": cost.model_dump(mode="json"),
        "actions": {
            "availability": "unavailable",
            "reason_codes": ["workload_action_capabilities_not_connected"],
        },
    }


def workload_topology_projection(
    rows: Sequence[Mapping[str, Any]],
    *,
    inventory_context: Mapping[str, Any],
    application_id: str,
    root_key: str,
    truncated: bool,
) -> JsonObject:
    """Build one workload neighborhood from persisted relationship evidence.

    The only traversal is server-side and follows graph-module ``owns`` and
    ``selects`` edges away from the selected manifest-bound root, plus a
    directly attached ``runs_on`` node.  It never guesses by a label, prefix,
    or unproved name relationship.
    """

    revision = _nonnegative_int(inventory_context.get("snapshot_revision")) or 0
    if revision <= 0 or not rows:
        return {
            "availability": "unavailable",
            "completeness": "unavailable",
            "observed_at": None,
            "nodes": None,
            "edges": None,
            "partial_reason_codes": [],
        }
    reasons = {
        str(reason)
        for reason in inventory_context.get("partial_reason_codes") or []
        if _optional_text(reason) is not None
    }
    source_complete = (
        inventory_context.get("resources_complete") is True
        and inventory_context.get("application_bindings_complete") is True
        and not truncated
    )
    if inventory_context.get("resources_complete") is not True:
        reasons.add("source_resources_incomplete")
    if inventory_context.get("application_bindings_complete") is not True:
        reasons.add("application_bindings_incomplete")
    if truncated:
        reasons.add("workload_runtime_evidence_budget_exceeded")
    graph = build_resource_graph(
        [
            {
                "resource": _topology_graph_item(row, application_id=application_id)["resource"],
                "application_ids": [application_id] if application_id else [],
                "application_binding_completeness": "exact",
            }
            for row in rows
        ],
        snapshot_revision=revision,
        filter_fingerprint=f"application-workload:{application_id}:{root_key}",
        source_complete=source_complete,
        labels_complete=inventory_context.get("labels_complete") is True,
        truncated=truncated,
        node_limit=APPLICATION_TOPOLOGY_NODE_LIMIT,
        edge_limit=APPLICATION_TOPOLOGY_EDGE_LIMIT,
        partial_reason_codes=sorted(reasons),
        cluster={"cluster_id": str(rows[0].get("cluster_id") or "")},
    )
    node_ids = {str(node["node_id"]) for node in graph["nodes"]}
    if root_key not in node_ids:
        reasons.update(str(reason) for reason in graph["partial_reason_codes"])
        reasons.add("workload_root_not_in_graph_budget")
        return {
            "availability": "available",
            "completeness": "partial",
            "observed_at": _optional_text(inventory_context.get("observed_at")),
            "nodes": [],
            "edges": [],
            "partial_reason_codes": sorted(reasons),
        }

    edges = [dict(edge) for edge in graph["edges"]]
    selected_nodes = {root_key}
    changed = True
    while changed:
        changed = False
        for edge in edges:
            if str(edge.get("from_node_id") or "") in selected_nodes and str(
                edge.get("kind") or ""
            ) in {"owns", "selects"}:
                child = str(edge.get("to_node_id") or "")
                if child and child not in selected_nodes:
                    selected_nodes.add(child)
                    changed = True
    for edge in edges:
        if (
            str(edge.get("from_node_id") or "") in selected_nodes
            and str(edge.get("kind") or "") == "runs_on"
        ):
            node_id = str(edge.get("to_node_id") or "")
            if node_id:
                selected_nodes.add(node_id)
    selected_edges = [
        edge
        for edge in edges
        if str(edge.get("from_node_id") or "") in selected_nodes
        and str(edge.get("to_node_id") or "") in selected_nodes
    ]
    selected_rows = sorted(
        (node for node in graph["nodes"] if str(node["node_id"]) in selected_nodes),
        key=lambda node: (
            str(node["node_id"]) != root_key,
            str(node["node_id"]),
        ),
    )
    reasons.update(str(reason) for reason in graph["partial_reason_codes"])
    complete = not reasons
    return {
        "availability": "available",
        "completeness": "exact" if complete else "partial",
        "observed_at": _optional_text(inventory_context.get("observed_at")),
        "nodes": [_topology_node(node) for node in selected_rows],
        "edges": [_topology_edge(edge) for edge in selected_edges],
        "partial_reason_codes": [] if complete else sorted(reasons),
    }


def workload_runtime_projection(
    rows: Sequence[Mapping[str, Any]],
    *,
    topology: Mapping[str, Any],
    inventory_context: Mapping[str, Any],
) -> JsonObject:
    """Use only graph-selected workload/pod records for workload runtime facts."""

    if topology.get("availability") != "available" or topology.get("nodes") is None:
        return _unavailable_workload_runtime()
    node_ids = {
        str(node.get("id") or "")
        for node in topology.get("nodes") or []
        if isinstance(node, Mapping)
    }
    runtime_rows = [
        row
        for row in rows
        if str(row.get("id") or "") in node_ids
        and str(row.get("resource_type") or "") in {"workload", "pod"}
    ]
    if not runtime_rows:
        return _unavailable_workload_runtime()
    complete = topology.get("completeness") == "exact"
    runtime_context = {
        "snapshot_revision": inventory_context.get("snapshot_revision"),
        "resources_complete": complete,
        "application_bindings_complete": complete,
    }
    prepared = [{**dict(row), "binding_complete": complete} for row in runtime_rows]
    return inventory_projection(prepared, inventory_context=runtime_context)


def _unavailable_workload_runtime() -> JsonObject:
    return {
        "runtime_readiness": {
            "completeness": "unavailable",
            "status": "unknown",
            "ready_pods": None,
            "total_pods": None,
            "restarts": None,
        },
        "resource_counts": None,
        "resource_counts_completeness": "unavailable",
    }


def _selected_instance_scope(scope: Mapping[str, Any]) -> Mapping[str, Any] | None:
    selected_id = _optional_text(scope.get("selected_instance_id"))
    if selected_id is None:
        return None
    return next(
        (
            _mapping(item.get("scope"))
            for item in scope.get("instances") or []
            if isinstance(item, Mapping) and str(item.get("id") or "") == selected_id
        ),
        None,
    )


def _workload_scope_item(
    row: Mapping[str, Any],
    *,
    selected_instance: Mapping[str, Any] | None,
) -> JsonObject | None:
    key = _optional_text(row.get("id"))
    cluster_id = _optional_text(row.get("cluster_id"))
    kind = _optional_text(row.get("kind"))
    name = _optional_text(row.get("name"))
    uid = _optional_text(row.get("uid"))
    if None in {key, cluster_id, kind, name, uid} or selected_instance is None:
        return None
    api_group, version = _api_group_and_version(_optional_text(row.get("api_version")) or "")
    namespace = _optional_text(row.get("namespace"))
    scope_cluster = _optional_text(selected_instance.get("cluster_id"))
    if scope_cluster != cluster_id:
        return None
    namespaces = tuple(str(value) for value in selected_instance.get("namespaces") or [])
    if namespace is not None and namespaces and namespace not in namespaces:
        return None
    return {
        "key": key,
        "resource": {
            "api_group": api_group,
            "version": version,
            "kind": kind,
            "namespace": namespace,
            "name": name,
            "uid": uid,
        },
        "scope": {
            "workspace_id": str(selected_instance.get("workspace_id") or ""),
            "cluster_id": cluster_id,
            "namespaces": [namespace] if namespace is not None else [],
            "freshness": str(selected_instance.get("freshness") or "partial"),
        },
        "observed_at": _optional_text(row.get("observed_at")),
    }


def _api_group_and_version(api_version: str) -> tuple[str, str]:
    group, separator, version = api_version.partition("/")
    return (group, version) if separator else ("", group)


def inventory_projection(
    rows: Sequence[Mapping[str, Any]],
    *,
    inventory_context: Mapping[str, Any],
) -> JsonObject:
    revision = int(inventory_context.get("snapshot_revision") or 0)
    if revision <= 0:
        return {
            "health": {
                "status": "unknown",
                "ready_pods": None,
                "total_pods": None,
                "restarts": None,
            },
            "runtime_readiness": {
                "completeness": "unavailable",
                "status": "unknown",
                "ready_pods": None,
                "total_pods": None,
                "restarts": None,
            },
            "resource_counts": None,
            "resource_counts_completeness": "unavailable",
            "endpoints": None,
            "image": None,
            "image_digest": None,
        }
    complete = bool(inventory_context.get("resources_complete")) and bool(
        inventory_context.get("application_bindings_complete")
    )
    complete = complete and all(row.get("binding_complete") is True for row in rows)
    completeness: Completeness = "exact" if complete else "partial"
    counts = Counter(str(row.get("kind") or "Unknown") for row in rows)
    pods = [row for row in rows if str(row.get("resource_type") or "") == "pod"]
    degraded = any(str(row.get("health") or "").casefold() != "healthy" for row in rows)
    health_status = "degraded" if degraded else "healthy" if complete else "unknown"
    ready_values = [_pod_ready(row) for row in pods]
    ready_pods = (
        sum(1 for ready in ready_values if ready)
        if complete and all(ready is not None for ready in ready_values)
        else None
    )
    restart_values = [
        _nonnegative_int(_mapping(row.get("summary")).get("restart_total")) for row in pods
    ]
    restarts = (
        sum(value for value in restart_values if value is not None)
        if complete and all(value is not None for value in restart_values)
        else None
    )
    endpoints = _endpoints(rows)
    images = sorted(
        {
            image
            for row in pods
            if (image := _optional_text(_mapping(row.get("summary")).get("image"))) is not None
        }
    )
    image = images[0] if len(images) == 1 else None
    digest = image.rsplit("@", 1)[1] if image and "@sha256:" in image else None
    return {
        "health": {
            "status": health_status,
            "ready_pods": ready_pods,
            "total_pods": len(pods) if complete else None,
            "restarts": restarts,
        },
        "runtime_readiness": {
            "completeness": completeness,
            "status": health_status,
            "ready_pods": ready_pods,
            "total_pods": len(pods) if complete else None,
            "restarts": restarts,
        },
        "resource_counts": [
            {"kind": kind, "count": count} for kind, count in sorted(counts.items())
        ],
        "resource_counts_completeness": completeness,
        "endpoints": endpoints,
        "image": image,
        "image_digest": digest,
    }


def deployment_history_projection(runs: Sequence[Mapping[str, Any]]) -> list[JsonObject]:
    return [
        {
            "id": str(run.get("workflow_run_id") or ""),
            "environment": _optional_text(run.get("environment")),
            "cluster_id": str(run.get("cluster_id") or ""),
            "git_sha": str(run.get("commit_sha") or ""),
            "version": _run_version(run),
            "deployed_at": _optional_text(run.get("updated_at")),
            "deployed_by": _run_actor(run),
            "status": _deployment_status(run.get("status")),
            "gitops_change_id": _run_change_id(run),
        }
        for run in runs
    ]


def current_deployment_projection(
    runs: Sequence[Mapping[str, Any]],
    *,
    inventory: Mapping[str, Any],
) -> JsonObject | None:
    succeeded = next(
        (run for run in runs if str(run.get("status") or "").casefold() == "succeeded"),
        None,
    )
    if succeeded is None:
        return None
    return {
        "version": _run_version(succeeded),
        "image": _optional_text(inventory.get("image")),
        "image_digest": _optional_text(inventory.get("image_digest")),
        "git_sha": str(succeeded.get("commit_sha") or ""),
        "deployed_at": _optional_text(succeeded.get("updated_at")),
        "deployed_by": _run_actor(succeeded),
    }


def delivery_projection(runs: Sequence[Mapping[str, Any]]) -> JsonObject:
    """Project the latest observed delivery attempt, separate from last success."""

    latest = next(
        (run for run in runs if _optional_text(run.get("workflow_run_id")) is not None),
        None,
    )
    if latest is None:
        return {
            "availability": "unavailable",
            "status": None,
            "workflow_run_id": None,
            "observed_at": None,
        }
    return {
        "availability": "available",
        "status": _deployment_status(latest.get("status")),
        "workflow_run_id": _optional_text(latest.get("workflow_run_id")),
        "observed_at": _optional_text(latest.get("updated_at")),
    }


def batch_runtime_projection(
    rows: Sequence[Mapping[str, Any]],
    *,
    inventory: Mapping[str, Any],
) -> JsonObject:
    """Expose Job/CronJob counters only when they are actually observed.

    The current collector can omit batch workloads.  An absent batch row is therefore
    an unavailable signal, never a synthetic idle or zero state.
    """

    batch_rows = [
        row for row in rows if str(row.get("kind") or "").casefold() in {"job", "cronjob"}
    ]
    if not batch_rows:
        return {
            "availability": "unavailable",
            "completeness": "unavailable",
            "status": None,
            "active_runs": None,
            "failed_runs": None,
            "succeeded_runs": None,
        }

    active_values = [_batch_counter(row, "active", "active_runs") for row in batch_rows]
    failed_values = [_batch_counter(row, "failed", "failed_runs") for row in batch_rows]
    succeeded_values = [_batch_counter(row, "succeeded", "succeeded_runs") for row in batch_rows]
    suspended_values = [_batch_suspended(row) for row in batch_rows]
    counters_complete = all(
        value is not None
        for values in (active_values, failed_values, succeeded_values)
        for value in values
    )
    inventory_complete = inventory.get("resource_counts_completeness") == "exact"
    completeness: Completeness = "exact" if inventory_complete and counters_complete else "partial"
    active_runs = _sum_known_counters(active_values, complete=counters_complete)
    failed_runs = _sum_known_counters(failed_values, complete=counters_complete)
    succeeded_runs = _sum_known_counters(succeeded_values, complete=counters_complete)
    status = (
        "running"
        if active_runs is not None and active_runs > 0
        else "failed"
        if failed_runs is not None and failed_runs > 0
        else "succeeded"
        if succeeded_runs is not None and succeeded_runs > 0
        else "suspended"
        if any(suspended_values)
        else "unknown"
    )
    return {
        "availability": "available",
        "completeness": completeness,
        "status": status,
        "active_runs": active_runs,
        "failed_runs": failed_runs,
        "succeeded_runs": succeeded_runs,
    }


def topology_projection(
    rows: Sequence[Mapping[str, Any]],
    *,
    inventory_context: Mapping[str, Any],
    application_id: str,
) -> JsonObject:
    """Return only server-built, authorized application relationship evidence."""

    revision = _nonnegative_int(inventory_context.get("snapshot_revision")) or 0
    if revision <= 0:
        return {
            "availability": "unavailable",
            "completeness": "unavailable",
            "observed_at": None,
            "nodes": None,
            "edges": None,
            "partial_reason_codes": [],
        }

    resources_complete = inventory_context.get("resources_complete") is True
    bindings_complete = inventory_context.get("application_bindings_complete") is True
    labels_complete = inventory_context.get("labels_complete") is True
    reasons = {
        str(reason)
        for reason in inventory_context.get("partial_reason_codes") or []
        if _optional_text(reason) is not None
    }
    if not resources_complete:
        reasons.add("source_resources_incomplete")
    if not bindings_complete:
        reasons.add("application_bindings_incomplete")
    if not labels_complete:
        reasons.add("source_labels_incomplete")

    graphs = []
    for cluster_id in sorted(
        {str(row.get("cluster_id") or "") for row in rows if row.get("cluster_id")}
    ):
        cluster_rows = [row for row in rows if str(row.get("cluster_id") or "") == cluster_id]
        graph = build_resource_graph(
            [_topology_graph_item(row, application_id=application_id) for row in cluster_rows],
            snapshot_revision=revision,
            filter_fingerprint=f"application:{application_id}:{cluster_id}",
            source_complete=resources_complete and bindings_complete,
            labels_complete=labels_complete,
            truncated=False,
            partial_reason_codes=sorted(reasons),
            cluster={"cluster_id": cluster_id},
        )
        graphs.append(graph)
        reasons.update(str(reason) for reason in graph["partial_reason_codes"])

    all_nodes = [_topology_node(node) for graph in graphs for node in graph["nodes"]]
    all_edges = [_topology_edge(edge) for graph in graphs for edge in graph["edges"]]
    nodes = all_nodes[:APPLICATION_TOPOLOGY_NODE_LIMIT]
    node_ids = {str(node["id"]) for node in nodes}
    edges = [
        edge
        for edge in all_edges
        if str(edge["from_id"]) in node_ids and str(edge["to_id"]) in node_ids
    ][:APPLICATION_TOPOLOGY_EDGE_LIMIT]
    if len(all_nodes) > len(nodes):
        reasons.add("application_topology_node_budget_exceeded")
    if len(all_edges) > len(edges):
        reasons.add("application_topology_edge_budget_exceeded")
    topology_complete = (
        bool(graphs)
        and all(graph["relation_completeness"] == "exact" for graph in graphs)
        and len(all_nodes) == len(nodes)
        and len(all_edges) == len(edges)
    )
    if not graphs and resources_complete and bindings_complete and labels_complete:
        topology_complete = True
    return {
        "availability": "available",
        "completeness": "exact" if topology_complete else "partial",
        "observed_at": _optional_text(inventory_context.get("observed_at")),
        "nodes": nodes,
        "edges": edges,
        "partial_reason_codes": [] if topology_complete else sorted(reasons),
    }


def detail_scope_projection(
    application: Mapping[str, Any],
    bindings: Sequence[Mapping[str, Any]],
    *,
    requested_instance_id: str | None,
    freshness_by_cluster: Mapping[str, str],
) -> JsonObject:
    """Project only immutable, authorized deployment-binding instances.

    The instance ID is the stored binding identity.  Names, environments, and
    clusters are display evidence only; callers cannot reconstruct an instance
    address from them.
    """

    workspace_id = _optional_text(application.get("workspace_id"))
    items: list[JsonObject] = []
    reasons: set[str] = set()
    for binding in bindings:
        binding_id = _optional_text(binding.get("binding_id"))
        cluster_id = _optional_text(binding.get("cluster_id"))
        environment = _optional_text(binding.get("environment"))
        if binding_id is None or cluster_id is None or environment is None or workspace_id is None:
            reasons.add("deployment_binding_identity_incomplete")
            continue
        namespace = _optional_text(binding.get("namespace"))
        items.append(
            {
                "id": binding_id,
                "environment": environment,
                "status": _optional_text(binding.get("status")) or "unknown",
                "scope": {
                    "workspace_id": workspace_id,
                    "cluster_id": cluster_id,
                    "namespaces": [namespace] if namespace is not None else [],
                    "freshness": str(freshness_by_cluster.get(cluster_id) or "partial"),
                },
            }
        )
    items.sort(
        key=lambda item: (
            str(item["environment"]).casefold(),
            str(_mapping(item["scope"]).get("cluster_id")).casefold(),
            str((_mapping(item["scope"]).get("namespaces") or [""])[0]).casefold(),
            str(item["id"]),
        )
    )
    if not items:
        return {
            "availability": "unavailable",
            "completeness": "unavailable",
            "selected_instance_id": None,
            "instances": [],
            "partial_reason_codes": [],
        }
    selected = next(
        (item for item in items if item["id"] == requested_instance_id),
        None,
    )
    if requested_instance_id is not None and selected is None:
        return {
            "availability": "available",
            "completeness": "partial",
            "selected_instance_id": None,
            "instances": items,
            "partial_reason_codes": ["requested_instance_not_authorized"],
        }
    selected = selected or items[0]
    return {
        "availability": "available",
        "completeness": "partial" if reasons else "exact",
        "selected_instance_id": str(selected["id"]),
        "instances": items,
        "partial_reason_codes": sorted(reasons),
    }


def history_projection(
    runs: Sequence[Mapping[str, Any]],
    incidents: Sequence[Mapping[str, Any]],
    *,
    incident_evidence: Mapping[str, Any],
) -> JsonObject:
    """Keep bounded delivery and incident evidence separate from browser ordering."""

    entries = [
        {
            "id": f"delivery:{run_id}",
            "type": "delivery",
            "status": _deployment_status(run.get("status")),
            "summary": _bounded_optional_text(run.get("summary")),
            "occurred_at": _optional_text(run.get("updated_at")),
            "workflow_run_id": run_id,
            "gitops_change_id": _run_change_id(run),
        }
        for run in runs
        if (run_id := _optional_text(run.get("workflow_run_id"))) is not None
    ]
    entries.extend(
        {
            "id": f"incident:{incident_id}",
            "type": "incident",
            "status": str(incident.get("status") or "unknown"),
            "summary": _bounded_optional_text(incident.get("title")),
            "occurred_at": _optional_text(incident.get("updated_at") or incident.get("started_at")),
            "workflow_run_id": None,
            "gitops_change_id": None,
        }
        for incident in incidents
        if (incident_id := _optional_text(incident.get("id"))) is not None
    )
    entries.sort(
        key=lambda item: (
            str(item.get("occurred_at") or ""),
            str(item["type"]),
            str(item["id"]),
        ),
        reverse=True,
    )
    reasons = {"bounded_workflow_history"}
    if incident_evidence.get("complete") is not True:
        reasons.add("incident_source_incomplete")
    reasons.update(
        reason
        for value in incident_evidence.get("scope_partial_reason_codes") or []
        if (reason := _optional_text(value)) is not None
    )
    return {
        "availability": "available",
        "completeness": "partial",
        "entries": entries[:6],
        "partial_reason_codes": sorted(reasons),
    }


def source_evidence_projection(
    application: Mapping[str, Any],
    *,
    drift: Mapping[str, Any],
) -> JsonObject:
    """Expose registered source provenance and observed source-to-runtime conflict."""

    repository_ref = _optional_text(application.get("repo_ref"))
    default_branch = _optional_text(application.get("default_branch"))
    manifest_path = _optional_text(application.get("manifest_path"))
    if repository_ref is None:
        return {
            "availability": "unavailable",
            "completeness": "unavailable",
            "conflict": None,
            "repository_ref": None,
            "default_branch": None,
            "manifest_path": None,
            "partial_reason_codes": [],
        }
    reasons = []
    if default_branch is None:
        reasons.append("default_branch_unavailable")
    if manifest_path is None:
        reasons.append("manifest_path_unavailable")
    drift_status = str(drift.get("status") or "unknown")
    conflict = (
        "conflict"
        if drift_status == "drifted"
        else "aligned"
        if drift_status == "in_sync"
        else "unknown"
    )
    return {
        "availability": "available",
        "completeness": "exact" if not reasons else "partial",
        "conflict": conflict,
        "repository_ref": repository_ref,
        "default_branch": default_branch,
        "manifest_path": manifest_path,
        "partial_reason_codes": reasons,
    }


def _topology_graph_item(row: Mapping[str, Any], *, application_id: str) -> JsonObject:
    return {
        "resource": {
            "inventory_key": str(row.get("id") or ""),
            "cluster_id": str(row.get("cluster_id") or ""),
            "resource_type": str(row.get("resource_type") or ""),
            "api_version": str(row.get("api_version") or ""),
            "kind": str(row.get("kind") or ""),
            "namespace": _optional_text(row.get("namespace")),
            "name": str(row.get("name") or ""),
            "uid": _optional_text(row.get("uid")),
            "status": str(row.get("status") or ""),
            "health": str(row.get("health") or ""),
            "labels": _mapping(row.get("labels")),
            "summary": _mapping(row.get("summary")),
            "observed_at": _optional_text(row.get("observed_at")),
        },
        "application_ids": [application_id] if application_id else [],
        "application_binding_completeness": (
            "exact" if row.get("binding_complete") is True else "partial"
        ),
    }


def _topology_node(node: Mapping[str, Any]) -> JsonObject:
    identity = _mapping(node.get("identity"))
    return {
        "id": str(node.get("node_id") or ""),
        "cluster_id": str(identity.get("cluster_id") or ""),
        "resource_type": str(identity.get("resource_type") or ""),
        "kind": str(identity.get("kind") or ""),
        "namespace": _optional_text(identity.get("namespace")),
        "name": str(identity.get("name") or ""),
        "status": str(node.get("status") or "unknown"),
        "health": str(node.get("health") or "unknown"),
        "observed_at": _optional_text(node.get("observed_at")),
    }


def _topology_edge(edge: Mapping[str, Any]) -> JsonObject:
    evidence = _mapping(edge.get("evidence"))
    return {
        "id": str(edge.get("edge_id") or ""),
        "from_id": str(edge.get("from_node_id") or ""),
        "to_id": str(edge.get("to_node_id") or ""),
        "type": str(edge.get("kind") or ""),
        "evidence_type": str(evidence.get("type") or ""),
        "authority": str(evidence.get("authority") or ""),
        "observed_at": _optional_text(evidence.get("observed_at")),
    }


def drift_projection(runs: Sequence[Mapping[str, Any]]) -> JsonObject:
    evidence = _latest_diff_evidence(runs)
    if evidence is None:
        return {"status": "unknown", "summary": None, "differences": [], "observed_at": None}
    diff, observed_at = evidence
    raw_changes = diff.get("changes")
    if not isinstance(raw_changes, list):
        return {"status": "unknown", "summary": None, "differences": [], "observed_at": observed_at}
    changes = [dict(change) for change in raw_changes if isinstance(change, Mapping)]
    drifting = [
        change
        for change in changes
        if str(change.get("classification") or "") in DRIFTING_CLASSIFICATIONS
    ]
    has_changes = diff.get("has_changes")
    if drifting:
        status = "drifted"
        summary = f"{len(drifting)} field{'s' if len(drifting) != 1 else ''} differ"
    elif has_changes is False or (
        not changes and str(diff.get("status") or "") in {"no_change", "already_converged"}
    ):
        status = "in_sync"
        summary = None
    elif changes and all(
        str(change.get("classification") or "") == "already_converged" for change in changes
    ):
        status = "in_sync"
        summary = None
    else:
        status = "unknown"
        summary = None
    return {
        "status": status,
        "summary": summary,
        "differences": [_drift_difference(diff, change) for change in drifting],
        "observed_at": observed_at,
    }


def recent_activity_projection(
    runs: Sequence[Mapping[str, Any]],
    incidents: Sequence[Mapping[str, Any]],
) -> list[JsonObject]:
    activity = [
        {
            "id": str(run.get("workflow_run_id") or ""),
            "type": "deployment",
            "summary": _bounded_optional_text(run.get("summary")),
            "occurred_at": _optional_text(run.get("updated_at")),
        }
        for run in runs[:3]
    ]
    activity.extend(
        {
            "id": str(incident.get("id") or ""),
            "type": "incident",
            "summary": _bounded_optional_text(incident.get("title")),
            "occurred_at": _optional_text(incident.get("updated_at") or incident.get("started_at")),
        }
        for incident in incidents[:3]
    )
    return sorted(
        activity,
        key=lambda item: (str(item.get("occurred_at") or ""), str(item["id"])),
        reverse=True,
    )[:3]


def _latest_diff_evidence(
    runs: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], str | None] | None:
    for run in runs:
        for raw_step in run.get("steps") or []:
            step = _mapping(raw_step)
            if str(step.get("name") or "") != "diff":
                continue
            details = _mapping(step.get("details"))
            diff = _mapping(details.get("diff")) if "diff" in details else details
            if diff:
                return diff, _optional_text(step.get("updated_at") or run.get("updated_at"))
    return None


def _drift_difference(diff: Mapping[str, Any], change: Mapping[str, Any]) -> JsonObject:
    path = str(change.get("field_path") or "")
    old_value, old_redacted = _safe_diff_value(path, change.get("new_desired"))
    new_value, new_redacted = _safe_diff_value(path, change.get("live"))
    value_redacted = old_redacted or new_redacted
    if value_redacted:
        old_value = None
        new_value = None
    return {
        "resource": str(diff.get("resource") or ""),
        "field_path": path,
        "old_value": old_value,
        "new_value": new_value,
        "value_redacted": value_redacted,
        "changed_by": _bounded_optional_text(change.get("changed_by") or change.get("actor")),
        "changed_at": _optional_text(change.get("changed_at")),
    }


def _safe_diff_value(path: str, value: Any) -> tuple[str | int | float | bool | None, bool]:
    normalized = path.casefold().replace("-", "_")
    if any(part in normalized for part in SENSITIVE_PATH_PARTS):
        return None, True
    if value is None or isinstance(value, (bool, int, float)):
        return value, False
    if isinstance(value, str) and len(value) <= 512:
        return value, False
    return None, True


def _endpoints(rows: Sequence[Mapping[str, Any]]) -> list[JsonObject]:
    endpoints: dict[tuple[str, str], JsonObject] = {}
    for row in rows:
        if str(row.get("resource_type") or "") != "service":
            continue
        summary = _mapping(row.get("summary"))
        urls = []
        external_url = _optional_text(summary.get("external_url"))
        if external_url:
            urls.append(external_url)
        hosts = summary.get("external_hosts")
        if isinstance(hosts, list):
            urls.extend(
                f"http://{host}" for value in hosts if (host := _optional_text(value)) is not None
            )
        for url in sorted(set(urls)):
            key = (str(row.get("id") or ""), url)
            endpoints[key] = {
                "id": key[0],
                "kind": str(row.get("kind") or "Service"),
                "name": str(row.get("name") or ""),
                "url": url,
            }
    return [endpoints[key] for key in sorted(endpoints)]


def _pod_ready(row: Mapping[str, Any]) -> bool | None:
    conditions = _mapping(row.get("summary")).get("conditions")
    if not isinstance(conditions, list):
        return None
    for raw_condition in conditions:
        condition = _mapping(raw_condition)
        if str(condition.get("type") or "") == "Ready":
            value = str(condition.get("status") or "").casefold()
            return True if value == "true" else False if value == "false" else None
    return None


def _batch_counter(row: Mapping[str, Any], *keys: str) -> int | None:
    summary = _mapping(row.get("summary"))
    for key in keys:
        if key in summary:
            return _nonnegative_int(summary.get(key))
    return None


def _batch_suspended(row: Mapping[str, Any]) -> bool:
    summary = _mapping(row.get("summary"))
    return any(summary.get(key) is True for key in ("suspended", "suspend"))


def _sum_known_counters(values: Sequence[int | None], *, complete: bool) -> int | None:
    return sum(value for value in values if value is not None) if complete else None


def _run_version(run: Mapping[str, Any]) -> str | None:
    metadata = _mapping(run.get("metadata"))
    for key in ("version", "release", "image_tag"):
        value = _bounded_optional_text(metadata.get(key))
        if value is not None:
            return value
    return None


def _run_actor(run: Mapping[str, Any]) -> str | None:
    metadata = _mapping(run.get("metadata"))
    for key in ("deployed_by", "actor", "requested_by"):
        value = _bounded_optional_text(metadata.get(key))
        if value is not None:
            return value
    return None


def _run_change_id(run: Mapping[str, Any]) -> str | None:
    metadata = _mapping(run.get("metadata"))
    for key in ("gitops_change_id", "change_id"):
        value = _bounded_optional_text(metadata.get(key))
        if value is not None:
            return value
    return None


def _deployment_status(value: Any) -> str:
    status = str(value or "").casefold()
    if status == "succeeded":
        return "succeeded"
    if status == "failed":
        return "failed"
    if status in {"applying", "rollout_waiting"}:
        return "running"
    if status in {"started", "rendering", "diffing", "policy_checking", "waiting_for_approval"}:
        return "pending"
    return "unknown"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _optional_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _bounded_optional_text(value: Any) -> str | None:
    text = _optional_text(value)
    return text[:500] if text is not None else None


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None
