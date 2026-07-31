"""Exact, CAS-bound Argo CD and Flux controller operations."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from commands.context import CommandContext
from commands.kubernetes import validate_exact_resource
from packages.contracts.gateway.requests import StrictModel
from packages.contracts.gitops.detail import GitOpsSyncOptions
from packages.contracts.parity import ResourceRef

GitOpsAgentAction = Literal[
    "reconcile",
    "sync_with_source",
    "suspend",
    "resume",
    "sync",
    "refresh",
]

RECONCILE_ANNOTATION = "reconcile.fluxcd.io/requestedAt"
ARGO_REFRESH_ANNOTATION = "argocd.argoproj.io/refresh"
ARGO_SUSPENDED_PRUNE_ANNOTATION = "opsia.io/gitops-suspended-prune"
ARGO_SUSPENDED_SELF_HEAL_ANNOTATION = "opsia.io/gitops-suspended-self-heal"


class GitOpsResourceCommandPayload(StrictModel):
    action: GitOpsAgentAction
    requested_at: str = Field(min_length=1, max_length=64)
    resource_ref: ResourceRef
    resource_version: str = Field(min_length=1, max_length=253)
    source_ref: ResourceRef | None = None
    source_resource_version: str | None = Field(default=None, min_length=1, max_length=253)
    sync_options: GitOpsSyncOptions | None = None
    refresh_mode: Literal["normal", "hard"] | None = None

    @field_validator("requested_at")
    @classmethod
    def require_offset_timestamp(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("requested_at must be an RFC3339 timestamp") from error
        if parsed.tzinfo is None:
            raise ValueError("requested_at must include a UTC offset")
        return value

    @model_validator(mode="after")
    def action_payload_is_closed(self) -> GitOpsResourceCommandPayload:
        if (self.source_ref is None) != (self.source_resource_version is None):
            raise ValueError("GitOps source identity and resourceVersion must be paired")
        if self.action == "sync_with_source" and self.source_ref is None:
            raise ValueError("sync-with-source requires an exact source identity")
        if self.action != "sync_with_source" and self.source_ref is not None:
            raise ValueError("source identity is valid only for sync-with-source")
        if self.action == "sync" and self.sync_options is None:
            self.sync_options = GitOpsSyncOptions()
        if self.action != "sync" and self.sync_options is not None:
            raise ValueError("sync options are valid only for Argo sync")
        if self.action == "refresh" and self.refresh_mode is None:
            self.refresh_mode = "normal"
        if self.action != "refresh" and self.refresh_mode is not None:
            raise ValueError("refresh mode is valid only for Argo refresh")
        return self


class GitOpsKubernetesIdentity(StrictModel):
    api_group: str
    version: str
    kind: str
    resource: str
    actions: tuple[GitOpsAgentAction, ...]


GITOPS_KUBERNETES_IDENTITIES = (
    GitOpsKubernetesIdentity(
        api_group="argoproj.io",
        version="v1alpha1",
        kind="Application",
        resource="applications",
        actions=("sync", "suspend", "resume", "refresh"),
    ),
    GitOpsKubernetesIdentity(
        api_group="kustomize.toolkit.fluxcd.io",
        version="v1",
        kind="Kustomization",
        resource="kustomizations",
        actions=("reconcile", "sync_with_source", "suspend", "resume"),
    ),
    GitOpsKubernetesIdentity(
        api_group="helm.toolkit.fluxcd.io",
        version="v2",
        kind="HelmRelease",
        resource="helmreleases",
        actions=("reconcile", "sync_with_source", "suspend", "resume"),
    ),
    GitOpsKubernetesIdentity(
        api_group="source.toolkit.fluxcd.io",
        version="v1",
        kind="GitRepository",
        resource="gitrepositories",
        actions=("reconcile",),
    ),
    GitOpsKubernetesIdentity(
        api_group="source.toolkit.fluxcd.io",
        version="v1",
        kind="OCIRepository",
        resource="ocirepositories",
        actions=("reconcile",),
    ),
    GitOpsKubernetesIdentity(
        api_group="source.toolkit.fluxcd.io",
        version="v1",
        kind="HelmRepository",
        resource="helmrepositories",
        actions=("reconcile",),
    ),
    GitOpsKubernetesIdentity(
        api_group="source.toolkit.fluxcd.io",
        version="v1",
        kind="Bucket",
        resource="buckets",
        actions=("reconcile",),
    ),
    GitOpsKubernetesIdentity(
        api_group="source.toolkit.fluxcd.io",
        version="v1",
        kind="HelmChart",
        resource="helmcharts",
        actions=("reconcile",),
    ),
)


async def execute_gitops_resource_command(
    ctx: CommandContext[GitOpsResourceCommandPayload],
) -> dict[str, Any]:
    payload = ctx.payload
    identity = gitops_kubernetes_identity(payload.resource_ref, payload.action)
    root = await _get(ctx, identity, payload.resource_ref)
    validate_exact_resource(root, payload.resource_ref, payload.resource_version)
    if payload.action != "refresh" and _metadata(root).get("deletionTimestamp"):
        raise ValueError("selected GitOps resource is pending deletion")

    if payload.action == "sync_with_source":
        await _sync_with_source(ctx, root, identity)
    else:
        patch = _action_patch(root, payload)
        await _patch(ctx, identity, payload.resource_ref, patch)

    return ctx.ok(
        f"GitOps {payload.action} accepted",
        applied=True,
        operation=payload.action,
        resource={
            "api_group": payload.resource_ref.api_group,
            "version": payload.resource_ref.version,
            "kind": payload.resource_ref.kind,
            "namespace": payload.resource_ref.namespace,
            "name": payload.resource_ref.name,
            "uid": payload.resource_ref.uid,
        },
        requested_at=payload.requested_at,
    )


def gitops_kubernetes_identity(
    resource_ref: ResourceRef, action: GitOpsAgentAction
) -> GitOpsKubernetesIdentity:
    for identity in GITOPS_KUBERNETES_IDENTITIES:
        if (
            identity.api_group == resource_ref.api_group
            and identity.version == resource_ref.version
            and identity.kind.casefold() == resource_ref.kind.casefold()
        ):
            if action not in identity.actions:
                raise ValueError(f"{action} is not supported for {identity.kind}")
            if not resource_ref.namespace:
                raise ValueError("GitOps resources require an exact namespace")
            return identity
    raise ValueError("unsupported GitOps resource identity")


async def _sync_with_source(
    ctx: CommandContext[GitOpsResourceCommandPayload],
    root: Mapping[str, Any],
    root_identity: GitOpsKubernetesIdentity,
) -> None:
    payload = ctx.payload
    if payload.source_ref is None or payload.source_resource_version is None:
        raise ValueError("sync-with-source requires an exact source identity")
    expected_source = _root_source_ref(root, payload.resource_ref)
    if not _same_ref(expected_source, payload.source_ref):
        raise ValueError("selected GitOps source identity is stale")
    source_identity = gitops_kubernetes_identity(payload.source_ref, "reconcile")
    source = await _get(ctx, source_identity, payload.source_ref)
    validate_exact_resource(source, payload.source_ref, payload.source_resource_version)
    if _metadata(source).get("deletionTimestamp"):
        raise ValueError("selected GitOps source is pending deletion")
    await _patch(
        ctx,
        source_identity,
        payload.source_ref,
        _reconcile_patch(payload.source_resource_version, payload.requested_at),
    )
    await _patch(
        ctx,
        root_identity,
        payload.resource_ref,
        _reconcile_patch(payload.resource_version, payload.requested_at),
    )


def _action_patch(root: Mapping[str, Any], payload: GitOpsResourceCommandPayload) -> dict[str, Any]:
    if payload.action == "reconcile":
        return _reconcile_patch(payload.resource_version, payload.requested_at)
    if payload.action == "refresh":
        return {
            "metadata": {
                "resourceVersion": payload.resource_version,
                "annotations": {ARGO_REFRESH_ANNOTATION: payload.refresh_mode or "normal"},
            }
        }
    if payload.action == "sync":
        if _argo_operation_in_progress(root):
            raise ValueError("GitOps sync operation is already in progress")
        return _argo_sync_patch(root, payload)
    if payload.action in {"suspend", "resume"}:
        if payload.resource_ref.api_group == "argoproj.io":
            return _argo_suspend_patch(root, payload.resource_version, payload.action == "suspend")
        return {
            "metadata": {"resourceVersion": payload.resource_version},
            "spec": {"suspend": payload.action == "suspend"},
        }
    raise ValueError("unsupported GitOps action")


def _argo_sync_patch(
    root: Mapping[str, Any], payload: GitOpsResourceCommandPayload
) -> dict[str, Any]:
    options = payload.sync_options or GitOpsSyncOptions()
    selective = bool(options.resources)
    if selective:
        _require_live_argo_ownership(root, options)
    sync: dict[str, Any] = {
        "revision": "" if selective else (options.revision or ""),
        "prune": False if selective else options.prune,
        "dryRun": options.dry_run,
    }
    if options.apply_only and not selective:
        apply_strategy: dict[str, Any] = {}
        if options.force:
            apply_strategy["force"] = True
        sync["syncStrategy"] = {"apply": apply_strategy}
    elif options.force:
        sync["syncStrategy"] = {"hook": {"force": True}}
    if options.sync_options:
        sync["syncOptions"] = list(options.sync_options)
    if selective:
        sync["resources"] = [resource.model_dump(mode="json") for resource in options.resources]
    metadata: dict[str, Any] = {"resourceVersion": payload.resource_version}
    if not options.dry_run:
        metadata["annotations"] = {ARGO_REFRESH_ANNOTATION: "hard"}
    return {
        "metadata": metadata,
        "operation": {
            "initiatedBy": {"username": "opsia"},
            "sync": sync,
        },
    }


def _require_live_argo_ownership(root: Mapping[str, Any], options: GitOpsSyncOptions) -> None:
    raw_resources = _mapping(root.get("status")).get("resources")
    if not isinstance(raw_resources, list):
        raise ValueError("GitOps controller resource ownership is unavailable")
    owned = {
        (
            str(item.get("group") or ""),
            str(item.get("kind") or "").casefold(),
            str(item.get("namespace") or ""),
            str(item.get("name") or ""),
        )
        for item in raw_resources
        if isinstance(item, Mapping)
    }
    selected = {
        (item.api_group, item.kind.casefold(), item.namespace or "", item.name)
        for item in options.resources
    }
    if not selected.issubset(owned):
        raise ValueError("selected GitOps resource ownership is stale")


def _argo_suspend_patch(
    root: Mapping[str, Any], resource_version: str, suspend: bool
) -> dict[str, Any]:
    metadata = _metadata(root)
    annotations = _mapping(metadata.get("annotations"))
    if suspend:
        spec = _mapping(root.get("spec"))
        automated = _mapping(_mapping(spec.get("syncPolicy")).get("automated"))
        return {
            "metadata": {
                "resourceVersion": resource_version,
                "annotations": {
                    ARGO_SUSPENDED_PRUNE_ANNOTATION: str(automated.get("prune") is True).lower(),
                    ARGO_SUSPENDED_SELF_HEAL_ANNOTATION: str(
                        automated.get("selfHeal") is True
                    ).lower(),
                },
            },
            "spec": {"syncPolicy": {"automated": None}},
        }
    if (
        ARGO_SUSPENDED_PRUNE_ANNOTATION not in annotations
        or ARGO_SUSPENDED_SELF_HEAL_ANNOTATION not in annotations
    ):
        raise ValueError("Argo auto-sync restore evidence is unavailable")
    return {
        "metadata": {
            "resourceVersion": resource_version,
            "annotations": {
                ARGO_SUSPENDED_PRUNE_ANNOTATION: None,
                ARGO_SUSPENDED_SELF_HEAL_ANNOTATION: None,
            },
        },
        "spec": {
            "syncPolicy": {
                "automated": {
                    "prune": annotations[ARGO_SUSPENDED_PRUNE_ANNOTATION] == "true",
                    "selfHeal": annotations[ARGO_SUSPENDED_SELF_HEAL_ANNOTATION] == "true",
                }
            }
        },
    }


def _root_source_ref(root: Mapping[str, Any], root_ref: ResourceRef) -> ResourceRef:
    spec = _mapping(root.get("spec"))
    if root_ref.kind.casefold() == "helmrelease":
        source = _mapping(_mapping(_mapping(spec.get("chart")).get("spec")).get("sourceRef"))
    else:
        source = _mapping(spec.get("sourceRef"))
    kind = str(source.get("kind") or "")
    name = str(source.get("name") or "")
    namespace = str(source.get("namespace") or root_ref.namespace or "")
    identity = next(
        (
            value
            for value in GITOPS_KUBERNETES_IDENTITIES
            if value.kind.casefold() == kind.casefold()
            and value.api_group == "source.toolkit.fluxcd.io"
        ),
        None,
    )
    if identity is None or not name or not namespace:
        raise ValueError("GitOps source reference is unavailable")
    return ResourceRef(
        api_group=identity.api_group,
        version=identity.version,
        kind=identity.kind,
        namespace=namespace,
        name=name,
        uid="unresolved",
    )


def _same_ref(left: ResourceRef, right: ResourceRef) -> bool:
    return (
        left.api_group == right.api_group
        and left.version == right.version
        and left.kind.casefold() == right.kind.casefold()
        and left.namespace == right.namespace
        and left.name == right.name
    )


def _argo_operation_in_progress(root: Mapping[str, Any]) -> bool:
    if root.get("operation") is not None:
        return True
    phase = _mapping(_mapping(root.get("status")).get("operationState")).get("phase")
    return phase in {"Running", "Terminating"}


def _reconcile_patch(resource_version: str, requested_at: str) -> dict[str, Any]:
    return {
        "metadata": {
            "resourceVersion": resource_version,
            "annotations": {RECONCILE_ANNOTATION: requested_at},
        }
    }


async def _get(
    ctx: CommandContext[GitOpsResourceCommandPayload],
    identity: GitOpsKubernetesIdentity,
    resource_ref: ResourceRef,
) -> dict[str, Any]:
    return await ctx.kubernetes.get_namespaced_resource(
        api_group=identity.api_group,
        version=identity.version,
        namespace=resource_ref.namespace or "",
        resource=identity.resource,
        name=resource_ref.name,
    )


async def _patch(
    ctx: CommandContext[GitOpsResourceCommandPayload],
    identity: GitOpsKubernetesIdentity,
    resource_ref: ResourceRef,
    body: dict[str, Any],
) -> dict[str, Any]:
    return await ctx.kubernetes.patch_namespaced_resource(
        api_group=identity.api_group,
        version=identity.version,
        namespace=resource_ref.namespace or "",
        resource=identity.resource,
        name=resource_ref.name,
        body=body,
    )


def _metadata(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(value.get("metadata"))


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
