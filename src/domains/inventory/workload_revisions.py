"""Exact observed workload revision history and rollback preview projection."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from packages.contracts.gateway.responses import (
    WorkloadRevisionHistoryResponse,
    WorkloadRollbackChange,
    WorkloadRollbackCurrent,
    WorkloadRollbackRevision,
)
from packages.contracts.parity import ResourceRef

MAX_REVISION_SCAN = 1000
MAX_REVISION_PAGE = 50
MAX_DIFF_CHANGES = 200
MAX_DIFF_VALUE_LENGTH = 500
SUPPORTED_REVISION_KINDS = {
    "deployment": "replicaset",
    "statefulset": "controllerrevision",
    "daemonset": "controllerrevision",
}
SENSITIVE_PATH_TOKENS = frozenset(
    {"authorization", "credential", "password", "secret", "token", "value"}
)


def workload_template_sha256(template: Mapping[str, Any]) -> str:
    encoded = json.dumps(template, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return f"sha256:{hashlib.sha256(encoded.encode()).hexdigest()}"


def workload_revision_history_response(
    db: Any,
    *,
    workspace_id: str,
    resource: Mapping[str, Any],
    cursor: int,
    limit: int,
) -> WorkloadRevisionHistoryResponse:
    """Project one snapshot-consistent, bounded revision history."""

    current_ref, current_resource_version = _inventory_ref(resource)
    snapshot_id = str(resource.get("snapshot_id") or "")
    raw = _mapping(resource.get("raw"))
    current_template = _mapping(raw.get("pod_template"))
    current_sha = workload_template_sha256(current_template)
    current = WorkloadRollbackCurrent(
        resource=current_ref,
        resource_version=current_resource_version,
        template_sha256=current_sha,
    )
    expected_kind = SUPPORTED_REVISION_KINDS.get(current_ref.kind.casefold())
    expected_count = _non_negative_int(raw.get("revision_history_count"))
    history_complete = raw.get("revision_history_complete") is True
    if (
        expected_kind is None
        or not snapshot_id
        or not current_template
        or expected_count is None
        or not history_complete
    ):
        return _unavailable(snapshot_id, current, "revision_history_incomplete")

    reader = getattr(db, "list_inventory_resources", None)
    if not callable(reader):
        return _unavailable(snapshot_id, current, "revision_history_repository_unavailable")
    rows = reader(
        workspace_id=workspace_id,
        cluster_id=str(resource.get("cluster_id") or ""),
        resource_type="workload_revision",
        namespace=current_ref.namespace,
        include_deleted=False,
        limit=MAX_REVISION_SCAN,
    )
    if not isinstance(rows, list):
        return _unavailable(snapshot_id, current, "revision_history_repository_unavailable")

    candidates = [
        row
        for row in rows
        if isinstance(row, Mapping)
        and str(row.get("snapshot_id") or "") == snapshot_id
        and str(_mapping(row.get("raw")).get("owner_uid") or "") == current_ref.uid
        and str(_mapping(row.get("raw")).get("owner_kind") or "").casefold()
        == current_ref.kind.casefold()
        and str(row.get("kind") or "").casefold() == expected_kind
    ]
    projected = [_project_revision(row, current, current_template) for row in candidates]
    valid = [item for item in projected if item is not None]
    identities = [(item.resource.uid, item.resource_version, item.revision) for item in valid]
    if (
        len(candidates) != expected_count
        or len(valid) != expected_count
        or len(identities) != len(set(identities))
    ):
        return _unavailable(snapshot_id, current, "revision_history_incomplete")

    rollbackable = [item for item in valid if item.template_sha256 != current_sha]
    rollbackable.sort(key=_revision_sort_key, reverse=True)
    effective_limit = max(1, min(limit, MAX_REVISION_PAGE))
    effective_cursor = max(0, cursor)
    page = rollbackable[effective_cursor : effective_cursor + effective_limit]
    next_offset = effective_cursor + len(page)
    next_cursor = next_offset if next_offset < len(rollbackable) else None
    return WorkloadRevisionHistoryResponse(
        availability="available" if rollbackable else "unavailable",
        completeness="exact",
        reason=None if rollbackable else "no_previous_revision",
        snapshot_id=snapshot_id,
        current=current,
        revisions=page,
        next_cursor=next_cursor,
    )


def workload_rollback_available(db: Any, *, workspace_id: str, resource: Mapping[str, Any]) -> bool:
    try:
        response = workload_revision_history_response(
            db,
            workspace_id=workspace_id,
            resource=resource,
            cursor=0,
            limit=1,
        )
    except (TypeError, ValueError, KeyError):
        return False
    return response.availability == "available" and response.completeness == "exact"


def workload_revision_selection(
    db: Any,
    *,
    workspace_id: str,
    resource: Mapping[str, Any],
    uid: str,
    resource_version: str,
) -> tuple[WorkloadRollbackRevision, dict[str, Any]] | None:
    """Resolve one target after the complete history has been revalidated."""

    response = workload_revision_history_response(
        db,
        workspace_id=workspace_id,
        resource=resource,
        cursor=0,
        limit=MAX_REVISION_PAGE,
    )
    if response.availability != "available" or response.completeness != "exact":
        return None
    reader = getattr(db, "list_inventory_resources", None)
    if not callable(reader):
        return None
    rows = reader(
        workspace_id=workspace_id,
        cluster_id=str(resource.get("cluster_id") or ""),
        resource_type="workload_revision",
        namespace=response.current.resource.namespace,
        include_deleted=False,
        limit=MAX_REVISION_SCAN,
    )
    snapshot_id = str(resource.get("snapshot_id") or "")
    expected_kind = SUPPORTED_REVISION_KINDS.get(response.current.resource.kind.casefold())
    current_template = _mapping(_mapping(resource.get("raw")).get("pod_template"))
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping):
            continue
        raw = _mapping(row.get("raw"))
        if (
            not snapshot_id
            or expected_kind is None
            or str(row.get("snapshot_id") or "") != snapshot_id
            or str(row.get("kind") or "").casefold() != expected_kind
            or str(row.get("namespace") or "") != str(response.current.resource.namespace or "")
            or str(raw.get("owner_uid") or "") != response.current.resource.uid
            or str(raw.get("owner_kind") or "").casefold()
            != response.current.resource.kind.casefold()
        ):
            continue
        try:
            row_ref, row_resource_version = _inventory_ref(row)
        except (TypeError, ValueError):
            continue
        if row_ref.uid != uid or row_resource_version != resource_version:
            continue
        projected = _project_revision(row, response.current, current_template)
        template = _mapping(raw.get("template"))
        if projected is None or projected.template_sha256 == response.current.template_sha256:
            return None
        return projected, template
    return None


def _project_revision(
    row: Mapping[str, Any],
    current: WorkloadRollbackCurrent,
    current_template: Mapping[str, Any],
) -> WorkloadRollbackRevision | None:
    raw = _mapping(row.get("raw"))
    template = _mapping(raw.get("template"))
    revision = str(raw.get("revision") or "").strip()
    if not template or not revision:
        return None
    try:
        revision_ref, resource_version = _inventory_ref(row)
    except (TypeError, ValueError):
        return None
    target_sha = workload_template_sha256(template)
    changes = _template_changes(current_template, template)
    preview_revision = _preview_revision(
        current=current,
        target=revision_ref,
        target_resource_version=resource_version,
        revision=revision,
        target_template_sha256=target_sha,
        changes=changes,
    )
    return WorkloadRollbackRevision(
        revision=revision,
        resource=revision_ref,
        resource_version=resource_version,
        created_at=str(raw.get("created_at")) if raw.get("created_at") else None,
        template_sha256=target_sha,
        preview_revision=preview_revision,
        changes=changes,
    )


def _inventory_ref(resource: Mapping[str, Any]) -> tuple[ResourceRef, str]:
    api_version = str(resource.get("api_version") or "").strip().strip("/")
    api_group, separator, version = api_version.rpartition("/")
    if not separator:
        api_group, version = "", api_version
    resource_version = str(resource.get("resource_version") or "").strip()
    uid = str(resource.get("uid") or "").strip()
    if not api_version or not version or not resource_version or not uid:
        raise ValueError("exact inventory resource identity is unavailable")
    namespace = resource.get("namespace")
    return (
        ResourceRef(
            api_group=api_group,
            version=version,
            kind=str(resource.get("kind") or ""),
            namespace=str(namespace) if namespace is not None else None,
            name=str(resource.get("name") or ""),
            uid=uid,
        ),
        resource_version,
    )


def _template_changes(
    current: Mapping[str, Any], target: Mapping[str, Any]
) -> list[WorkloadRollbackChange]:
    before = _flatten(current)
    after = _flatten(target)
    changes: list[WorkloadRollbackChange] = []
    for path in sorted(set(before) | set(after)):
        if before.get(path) == after.get(path):
            continue
        changes.append(
            WorkloadRollbackChange(
                path=path,
                before=_display_value(path, before.get(path)),
                after=_display_value(path, after.get(path)),
            )
        )
        if len(changes) >= MAX_DIFF_CHANGES:
            break
    return changes


def _flatten(value: Any, path: str = "") -> dict[str, Any]:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key in sorted(value, key=str):
            child = f"{path}/{_json_pointer_token(str(key))}"
            result.update(_flatten(value[key], child))
        return result
    if isinstance(value, list):
        result = {}
        for index, item in enumerate(value):
            result.update(_flatten(item, f"{path}/{index}"))
        return result
    return {path or "/": value}


def _display_value(path: str, value: Any) -> str:
    tokens = {token.casefold() for token in path.split("/") if token}
    if tokens & SENSITIVE_PATH_TOKENS:
        return "<redacted>"
    if value is None:
        return "<missing>"
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return encoded.strip('"')[:MAX_DIFF_VALUE_LENGTH]


def _preview_revision(
    *,
    current: WorkloadRollbackCurrent,
    target: ResourceRef,
    target_resource_version: str,
    revision: str,
    target_template_sha256: str,
    changes: list[WorkloadRollbackChange],
) -> str:
    encoded = json.dumps(
        {
            "current": current.model_dump(),
            "target": target.model_dump(),
            "target_resource_version": target_resource_version,
            "revision": revision,
            "target_template_sha256": target_template_sha256,
            "changes": [item.model_dump() for item in changes],
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"sha256:{hashlib.sha256(encoded.encode()).hexdigest()}"


def _revision_sort_key(item: WorkloadRollbackRevision) -> tuple[int, str, str]:
    try:
        number = int(item.revision)
    except ValueError:
        number = -1
    return number, item.created_at or "", item.resource.uid


def _unavailable(
    snapshot_id: str,
    current: WorkloadRollbackCurrent,
    reason: str,
) -> WorkloadRevisionHistoryResponse:
    return WorkloadRevisionHistoryResponse(
        availability="unavailable",
        completeness="partial",
        reason=reason,
        snapshot_id=snapshot_id or "unavailable",
        current=current,
        revisions=[],
    )


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _json_pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")
