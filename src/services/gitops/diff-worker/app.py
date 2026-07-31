"""diff-worker: manifest.rendered -> desired.diff.detected."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from os import getenv
from typing import Any

from domains.gitops.diffing import (
    MISSING,
    ManagedFieldSnapshot,
    build_adoption_required_changes,
    build_diff_basis,
    compare_managed_fields,
    extract_declared_field_paths,
    rendered_manifest_to_object,
    resource_ref,
    snapshot_from_kubernetes_object,
    snapshot_from_rendered_manifest,
    summarize_status,
)
from domains.gitops.events import (
    DesiredDesiredDiffDetectedBody,
    Diff,
    GitOpsChangeContextDetectedBody,
    ManifestRenderedBody,
    RenderedManifest,
)
from domains.gitops.live_projection import reconstruct_live_object
from packages.config.constants import RiskLevel, Sandbox
from packages.config.environments import is_production_environment
from packages.contracts.event_bus.bodies import EventBody
from packages.runtime.app import App, EventContext

app = App("diff-worker")

UNKNOWN_ACTUAL_IMAGE = "unknown"
RESOURCE_NOT_INSPECTED = "resource-not-inspected"
REQUIRE_APPROVED_SNAPSHOT_ENV = "GITOPS_REQUIRE_APPROVED_SNAPSHOT"
CHANGE_CONTEXT_SOURCE = "gitops"
SSA_EXECUTION_BOUNDARY = "cluster_agent"
SSA_EVIDENCE_UNAVAILABLE = "unavailable"
REDACTED_CHANGE_VALUE = "redacted"
SENSITIVE_CHANGE_TOKENS = (
    "secret",
    "token",
    "password",
    "credential",
    "private",
    "key",
    "env",
    "data",
    "binarydata",
)
PROBE_SUFFIXES = ("readinessProbe", "livenessProbe", "startupProbe")


@dataclass(frozen=True)
class FieldPolicy:
    declared_fields: list[str]
    managed_fields: list[str]
    ignored_fields: list[str]
    unknown_fields: list[str]
    last_approved_snapshot: dict[str, object]
    source: str


async def load_actual_resource_image(evt: ManifestRenderedBody, ctx: EventContext[Any]) -> str:
    try:
        reader = ctx.db.get_actual_resource_image
    except AttributeError:
        return UNKNOWN_ACTUAL_IMAGE
    actual = await reader(
        evt.workspace_id,
        evt.cluster_id,
        evt.rendered_manifest.metadata.namespace or Sandbox.NAMESPACE,
        resource_ref(evt.rendered_manifest.kind, evt.rendered_manifest.metadata.name),
    )
    return str(actual) if actual else UNKNOWN_ACTUAL_IMAGE


async def load_actual_resource_manifest(
    evt: ManifestRenderedBody, ctx: EventContext[Any]
) -> tuple[dict[str, Any] | None, bool]:
    """Load the exact observed object used for managed-field comparison.

    The boolean distinguishes an inventory-backed miss from a legacy store that
    does not implement the manifest reader.  A real miss must not be replaced
    with the new desired manifest because that would falsely classify an
    unapplied replica change as already converged.
    """

    reader = getattr(ctx.db, "get_actual_resource_manifest", None)
    if not callable(reader):
        return None, False
    observed = await reader(
        evt.workspace_id,
        evt.cluster_id,
        evt.rendered_manifest.metadata.namespace or Sandbox.NAMESPACE,
        resource_ref(evt.rendered_manifest.kind, evt.rendered_manifest.metadata.name),
    )
    if not isinstance(observed, Mapping):
        return None, True
    raw = observed.get("raw")
    live = reconstruct_live_object(
        str(observed.get("kind") or evt.rendered_manifest.kind),
        raw if isinstance(raw, Mapping) else None,
    )
    return live, True


def build_desired_diff(
    evt: ManifestRenderedBody,
    actual_image: str,
    *,
    actual_manifest: Mapping[str, Any] | None = None,
    inventory_manifest_supported: bool = False,
) -> Diff:
    rendered = evt.rendered_manifest
    namespace = rendered.metadata.namespace or Sandbox.NAMESPACE
    policy = load_field_policy(rendered)
    new_desired, ssa_meta = load_new_desired_snapshot(rendered)
    live = load_live_snapshot(
        rendered,
        actual_image,
        actual_manifest=actual_manifest,
        inventory_manifest_supported=inventory_manifest_supported,
    )
    old_desired = load_previous_desired_snapshot(live, policy)
    changes = compare_managed_fields(
        old_desired=old_desired.fields,
        live=live.fields,
        new_desired=new_desired.fields,
        managed_fields=policy.managed_fields,
        ignored_fields=policy.ignored_fields,
    )
    if (
        live.source == "inventory_resource_missing"
        and old_desired.source == "last_approved_snapshot"
    ):
        # A resource absent from the inventory projection is unobserved, not
        # proof that every unchanged declared field drifted.  Keep real Git
        # changes actionable and let the drift reconciler handle independent
        # resource-absence checks.
        changes = [
            change
            for change in changes
            if change.get("old_desired") != change.get("new_desired")
        ]
    changes.extend(
        build_adoption_required_changes(
            live=live.fields,
            new_desired=new_desired.fields,
            unknown_fields=policy.unknown_fields,
        )
    )
    status = summarize_status(changes)
    basis = build_diff_basis(
        old_desired=old_desired,
        live=live,
        new_desired=new_desired,
        declared_fields=policy.declared_fields,
        policy_managed_fields=policy.managed_fields,
        ignored_fields=policy.ignored_fields,
        unknown_fields=policy.unknown_fields,
        policy_source=policy.source,
    )
    basis.update(ssa_meta)
    if rendered.artifact_digest:
        basis["artifact_digest"] = rendered.artifact_digest
    image_path = managed_image_path(rendered)
    return Diff(
        resource=new_desired.resource or resource_ref(rendered.kind, rendered.metadata.name),
        namespace=namespace,
        desired_image=str(new_desired.fields.get(image_path, rendered.spec.image)),
        actual_image=str(live.fields.get(image_path, actual_image)),
        risk=risk_for_diff(namespace, status, evt.environment),
        workspace_id=evt.workspace_id,
        repository_id=evt.repository_id,
        watch_target_id=evt.watch_target_id,
        binding_id=evt.binding_id,
        application_id=evt.application_id,
        workflow_run_id=evt.workflow_run_id,
        environment=evt.environment,
        cluster_id=evt.cluster_id,
        manifest_path=evt.manifest_path,
        resource_class=rendered.resource_class,
        desired_manifest=rendered.manifest,
        status=status,
        has_changes=has_actionable_changes(changes),
        changes=changes,
        basis=basis,
    )


def load_field_policy(rendered: RenderedManifest) -> FieldPolicy:
    declared_fields = sorted(
        set(
            rendered.declared_fields
            or extract_declared_field_paths(rendered_manifest_to_object(rendered))
        )
    )
    ignored_fields = sorted(set(rendered.ignored_fields))
    has_explicit_policy = bool(
        rendered.managed_fields or rendered.ignored_fields or rendered.last_approved_snapshot
    )
    if has_explicit_policy:
        managed_fields = sorted(set(rendered.managed_fields))
        if not managed_fields and rendered.last_approved_snapshot:
            managed_fields = sorted(set(rendered.last_approved_snapshot))
        source = "rendered_policy"
    elif approved_snapshot_required():
        managed_fields = []
        source = "missing_approved_policy"
    else:
        managed_fields = declared_fields
        source = "dev_declared_fields_fallback"
    unknown_fields = sorted(set(declared_fields) - set(managed_fields) - set(ignored_fields))
    return FieldPolicy(
        declared_fields=declared_fields,
        managed_fields=managed_fields,
        ignored_fields=ignored_fields,
        unknown_fields=unknown_fields,
        last_approved_snapshot=dict(rendered.last_approved_snapshot),
        source=source,
    )


def load_new_desired_snapshot(
    rendered: RenderedManifest,
) -> tuple[ManagedFieldSnapshot, dict[str, object]]:
    """Use rendered intent until an agent-produced SSA observation is persisted."""
    return (
        snapshot_from_rendered_manifest(rendered),
        {
            "ssa_execution_boundary": SSA_EXECUTION_BOUNDARY,
            "ssa_evidence": SSA_EVIDENCE_UNAVAILABLE,
        },
    )


def load_live_snapshot(
    rendered: RenderedManifest,
    actual_image: str,
    *,
    actual_manifest: Mapping[str, Any] | None = None,
    inventory_manifest_supported: bool = False,
) -> ManagedFieldSnapshot:
    if actual_manifest is not None:
        return snapshot_from_kubernetes_object(
            actual_manifest,
            source="observed_actual_manifest",
        )
    if inventory_manifest_supported:
        return ManagedFieldSnapshot(
            resource=resource_ref(rendered.kind, rendered.metadata.name),
            namespace=rendered.metadata.namespace or Sandbox.NAMESPACE,
            fields={},
            source="inventory_resource_missing",
        )

    # Compatibility for stores that have not implemented the full inventory
    # manifest reader.  Production stores use the exact branch above.
    live = rendered_manifest_to_object(rendered)
    if rendered.kind == "Deployment" and rendered.spec.image:
        containers = (
            live.setdefault("spec", {})
            .setdefault("template", {})
            .setdefault("spec", {})
            .setdefault("containers", [])
        )
        if containers and isinstance(containers[0], dict):
            containers[0]["image"] = actual_image
    return snapshot_from_kubernetes_object(live, source="observed_actual_image")


def load_previous_desired_snapshot(
    live: ManagedFieldSnapshot, policy: FieldPolicy
) -> ManagedFieldSnapshot:
    if policy.last_approved_snapshot:
        return ManagedFieldSnapshot(
            resource=live.resource,
            namespace=live.namespace,
            fields=dict(policy.last_approved_snapshot),
            source="last_approved_snapshot",
        )

    if approved_snapshot_required():
        return ManagedFieldSnapshot(
            resource=live.resource,
            namespace=live.namespace,
            fields={},
            source="missing_last_approved_snapshot",
        )

    return ManagedFieldSnapshot(
        resource=live.resource,
        namespace=live.namespace,
        fields=dict(live.fields),
        source="dev_previous_live_fallback",
    )


def managed_image_path(rendered: RenderedManifest) -> str:
    return f"spec.template.spec.containers[name={rendered.metadata.name}].image"


def env_enabled(name: str, default: str = "") -> bool:
    return getenv(name, default).lower() in {"1", "true", "yes", "on"}


def approved_snapshot_required() -> bool:
    return env_enabled(REQUIRE_APPROVED_SNAPSHOT_ENV)


def risk_for_diff(namespace: str, status: str, environment: str = "") -> RiskLevel:
    if namespace != Sandbox.NAMESPACE:
        return RiskLevel.NON_SANDBOX_NAMESPACE
    if is_production_environment(environment) and status != "no_change":
        return RiskLevel.REVIEW_REQUIRED
    if status in {"review_required", "adoption_required"}:
        return RiskLevel.REVIEW_REQUIRED
    return RiskLevel.SANDBOX_ONLY


def has_actionable_changes(changes: list[dict[str, object]]) -> bool:
    return any(change.get("classification") != "already_converged" for change in changes)


def build_change_context_event(
    evt: ManifestRenderedBody, diff: Diff
) -> GitOpsChangeContextDetectedBody:
    return GitOpsChangeContextDetectedBody(
        metadata={"change_context": build_change_context(evt, diff)},
        workspace_id=evt.workspace_id,
        repository_id=evt.repository_id,
        watch_target_id=evt.watch_target_id,
        binding_id=evt.binding_id,
        application_id=evt.application_id,
        workflow_run_id=evt.workflow_run_id,
        environment=evt.environment,
        cluster_id=evt.cluster_id,
        commit_sha=evt.commit_sha,
        manifest_path=evt.manifest_path,
        repo_ref=evt.repo_ref,
        branch=evt.branch,
        resource=diff.resource,
    )


def build_change_context(evt: ManifestRenderedBody, diff: Diff) -> dict[str, object]:
    resource_kind, resource_name = split_resource(diff.resource)
    rollback_available = is_rollback_available(diff)
    context: dict[str, object] = {
        "resource": {
            "namespace": diff.namespace,
            "workload_kind": resource_kind,
            "workload_name": resource_name,
        },
        "recent_changes": [
            recent_change_payload(diff, change)
            for change in diff.changes
            if include_recent_change(change)
        ],
        "gitops": gitops_context(evt, diff),
        "image": image_context(diff),
        "rollout": rollout_context(diff, rollback_available),
        "config": config_context(diff),
        "risk": risk_context(diff, rollback_available),
    }
    return {key: value for key, value in context.items() if value not in ({}, [], None, "")}


def include_recent_change(change: Mapping[str, object]) -> bool:
    return bool(str(change.get("field_path") or ""))


def recent_change_payload(diff: Diff, change: Mapping[str, object]) -> dict[str, object]:
    field_path = str(change.get("field_path") or "")
    change_type = change_type_for_field(field_path, change)
    payload: dict[str, object] = {
        "change_type": change_type,
        "target_resource": target_resource_label(diff.resource),
        "field": field_path,
        "classification": str(change.get("classification") or ""),
        "source": CHANGE_CONTEXT_SOURCE,
    }
    before = safe_change_value(field_path, change.get("before", change.get("live", MISSING)))
    after = safe_change_value(field_path, change.get("after", change.get("new_desired", MISSING)))
    if before != MISSING:
        payload["before"] = before
    if after != MISSING:
        payload["after"] = after
    references = merge_reference_summaries(
        collect_reference_summary(change.get("before")),
        collect_reference_summary(change.get("after")),
    )
    if references:
        payload["references"] = references
    return {key: value for key, value in payload.items() if value not in (None, "", {}, [])}


def change_type_for_field(field_path: str, change: Mapping[str, object]) -> str:
    normalized = field_path.casefold()
    references = merge_reference_summaries(
        collect_reference_summary(change.get("before")),
        collect_reference_summary(change.get("after")),
    )
    if references.get("secrets"):
        return "secret_ref"
    if references.get("config_maps"):
        return "config_ref"
    if normalized.endswith(".image"):
        return "image"
    if any(probe.casefold() in normalized for probe in PROBE_SUFFIXES):
        return "probe"
    if "selector" in normalized:
        return "selector"
    if "resources" in normalized:
        return "resource"
    if "replicas" in normalized:
        return "replicas"
    if "env" in normalized or normalized in {"data", "binarydata"}:
        return "config"
    return "manifest"


def gitops_context(evt: ManifestRenderedBody, diff: Diff) -> dict[str, object]:
    return compact_dict(
        {
            "repository": evt.repo_ref,
            "repository_id": diff.repository_id,
            "branch": evt.branch,
            "manifest_path": diff.manifest_path,
            "commit_sha": evt.commit_sha,
            "watch_target_id": diff.watch_target_id,
            "binding_id": diff.binding_id,
            "application_id": diff.application_id,
            "workflow_run_id": diff.workflow_run_id,
            "environment": diff.environment,
        }
    )


def image_context(diff: Diff) -> dict[str, object]:
    desired = normalize_unknown(diff.desired_image)
    actual = normalize_unknown(diff.actual_image)
    context = {
        "current": desired,
        "previous": actual,
        "digest": image_digest(desired),
        "changed_recently": bool(desired and actual and desired != actual),
    }
    return compact_dict(context)


def rollout_context(diff: Diff, rollback_available: bool) -> dict[str, object]:
    return compact_dict(
        {
            "rollback_available": rollback_available,
            "rollback_source": "gitops_diff" if rollback_available else None,
            "artifact_digest": diff.basis.get("artifact_digest"),
            "status": diff.status,
        }
    )


def config_context(diff: Diff) -> dict[str, object]:
    references: dict[str, list[dict[str, object]]] = {}
    for change in diff.changes:
        references = merge_reference_summaries(
            references,
            collect_reference_summary(change.get("before")),
            collect_reference_summary(change.get("after")),
        )
    context: dict[str, object] = {
        "config_map_ref_changed": bool(references.get("config_maps")),
        "secret_ref_changed": bool(references.get("secrets")),
    }
    context.update(references)
    return compact_dict(context)


def risk_context(diff: Diff, rollback_available: bool) -> dict[str, object]:
    approval_required = bool(diff.has_changes and diff.risk != RiskLevel.SANDBOX_ONLY)
    reasons = [
        item
        for item in (
            diff.status,
            "non_sandbox_namespace" if diff.risk == RiskLevel.NON_SANDBOX_NAMESPACE else "",
            str(diff.basis.get("policy_source") or ""),
        )
        if item
    ]
    return compact_dict(
        {
            "risk_level": str(diff.risk),
            "approval_required": approval_required,
            "rollback_available": rollback_available,
            "has_changes": diff.has_changes,
            "reasons": reasons,
        }
    )


def is_rollback_available(diff: Diff) -> bool:
    if not diff.desired_manifest:
        return False
    for change in diff.changes:
        if not include_recent_change(change):
            continue
        before = change.get("before", change.get("live", MISSING))
        if before != MISSING:
            return True
    return False


def safe_change_value(field_path: str, value: object) -> object:
    if value == MISSING:
        return MISSING
    references = collect_reference_summary(value)
    if references:
        return references
    if is_sensitive_field(field_path):
        return REDACTED_CHANGE_VALUE
    return value


def is_sensitive_field(field_path: str) -> bool:
    normalized = field_path.casefold()
    return any(token in normalized for token in SENSITIVE_CHANGE_TOKENS)


def collect_reference_summary(value: object) -> dict[str, list[dict[str, object]]]:
    references: dict[str, list[dict[str, object]]] = {}
    collect_references(value, references)
    return references


def collect_references(value: object, references: dict[str, list[dict[str, object]]]) -> None:
    if isinstance(value, Mapping):
        secret_key_ref = value.get("secretKeyRef")
        if isinstance(secret_key_ref, Mapping):
            add_reference(
                references, "secrets", secret_key_ref.get("name"), secret_key_ref.get("key")
            )
        config_map_key_ref = value.get("configMapKeyRef")
        if isinstance(config_map_key_ref, Mapping):
            add_reference(
                references,
                "config_maps",
                config_map_key_ref.get("name"),
                config_map_key_ref.get("key"),
            )
        secret_ref = value.get("secretRef")
        if isinstance(secret_ref, Mapping):
            add_reference(references, "secrets", secret_ref.get("name"), secret_ref.get("key"))
        config_map_ref = value.get("configMapRef")
        if isinstance(config_map_ref, Mapping):
            add_reference(
                references,
                "config_maps",
                config_map_ref.get("name"),
                config_map_ref.get("key"),
            )
        secret_volume = value.get("secret")
        if isinstance(secret_volume, Mapping):
            add_reference(references, "secrets", secret_volume.get("secretName"), None)
        config_map_volume = value.get("configMap")
        if isinstance(config_map_volume, Mapping):
            add_reference(references, "config_maps", config_map_volume.get("name"), None)
        for item in value.values():
            collect_references(item, references)
    elif isinstance(value, list):
        for item in value:
            collect_references(item, references)


def add_reference(
    references: dict[str, list[dict[str, object]]],
    bucket: str,
    name: object,
    key: object,
) -> None:
    if name in (None, ""):
        return
    item = {"name": str(name)}
    if key not in (None, ""):
        item["key"] = str(key)
    rows = references.setdefault(bucket, [])
    if item not in rows:
        rows.append(item)


def merge_reference_summaries(
    *summaries: dict[str, list[dict[str, object]]],
) -> dict[str, list[dict[str, object]]]:
    merged: dict[str, list[dict[str, object]]] = {}
    for summary in summaries:
        for bucket, rows in summary.items():
            for row in rows:
                current = merged.setdefault(bucket, [])
                if row not in current:
                    current.append(row)
    return {key: value for key, value in merged.items() if value}


def compact_dict(value: Mapping[str, object]) -> dict[str, object]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}


def normalize_unknown(value: object) -> str | None:
    text = str(value or "")
    if not text or text in {UNKNOWN_ACTUAL_IMAGE, RESOURCE_NOT_INSPECTED}:
        return None
    return text


def image_digest(image: str | None) -> str | None:
    if not image or "@sha256:" not in image:
        return None
    return f"sha256:{image.rsplit('@sha256:', 1)[1]}"


def split_resource(resource: str) -> tuple[str, str]:
    if "/" not in resource:
        return "unknown", resource or "unknown"
    kind, name = resource.split("/", 1)
    return kind or "unknown", name or "unknown"


def target_resource_label(resource: str) -> str:
    kind, name = split_resource(resource)
    return f"{kind[:1].upper()}{kind[1:]}/{name}"


@app.on(ManifestRenderedBody)
async def on_manifest_rendered(
    evt: ManifestRenderedBody, ctx: EventContext
) -> AsyncIterator[EventBody]:
    actual_manifest, inventory_manifest_supported = await load_actual_resource_manifest(evt, ctx)
    actual_image = (
        await load_actual_resource_image(evt, ctx)
        if evt.rendered_manifest.spec.image
        else RESOURCE_NOT_INSPECTED
    )
    diff = build_desired_diff(
        evt,
        actual_image,
        actual_manifest=actual_manifest,
        inventory_manifest_supported=inventory_manifest_supported,
    )
    yield DesiredDesiredDiffDetectedBody(diff=diff)
    yield build_change_context_event(evt, diff)


if __name__ == "__main__":
    app.run()
