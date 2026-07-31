from __future__ import annotations

from packages.contracts.event_bus.interfaces import JsonObject
from providers.kubernetes_utils import (
    K8S_DEPLOYMENT_REVISION_ANNOTATION,
    K8S_KIND_DEPLOYMENT,
    K8S_KIND_REPLICA_SET,
    list_items,
    metadata,
    object_or_empty,
)


def replicasets_for_deployment(
    deployment: JsonObject,
    replicasets: list[JsonObject],
) -> list[JsonObject]:
    """Return ReplicaSets owned by this Deployment."""
    meta = metadata(deployment)
    deployment_uid = str(meta.get("uid") or "")
    deployment_name = str(meta.get("name") or "")

    return [
        replicaset
        for replicaset in replicasets
        if is_owned_by_deployment(replicaset, deployment_uid, deployment_name)
    ]


def sorted_replicasets_for_deployment(
    deployment: JsonObject,
    replicasets: list[JsonObject],
) -> list[JsonObject]:
    """Return owned ReplicaSets sorted by revision."""
    return sorted(
        replicasets_for_deployment(deployment, replicasets),
        key=replicaset_revision_number,
    )


def pods_for_deployment(
    deployment: JsonObject,
    replicasets: list[JsonObject],
    pods: list[JsonObject],
) -> list[JsonObject]:
    """Return Pods owned by this Deployment."""
    owned_replicasets = replicasets_for_deployment(deployment, replicasets)
    if not owned_replicasets:
        return []

    replicaset_uids = {
        str(metadata(replicaset).get("uid"))
        for replicaset in owned_replicasets
        if metadata(replicaset).get("uid")
    }
    replicaset_names = {
        str(metadata(replicaset).get("name"))
        for replicaset in owned_replicasets
        if metadata(replicaset).get("name")
    }
    return sorted(
        [pod for pod in pods if is_owned_by_replicaset(pod, replicaset_uids, replicaset_names)],
        key=lambda pod: str(metadata(pod).get("name") or ""),
    )


def is_owned_by_replicaset(
    pod: JsonObject,
    replicaset_uids: set[str],
    replicaset_names: set[str],
) -> bool:
    """Check whether a Pod belongs to one ReplicaSet."""
    for owner in list_items(metadata(pod).get("ownerReferences")):
        if owner.get("kind") != K8S_KIND_REPLICA_SET:
            continue
        owner_uid = str(owner.get("uid") or "")
        owner_name = str(owner.get("name") or "")
        if replicaset_uids:
            if owner_uid and owner_uid in replicaset_uids:
                return True
            continue
        if owner_name and owner_name in replicaset_names:
            return True
    return False


def is_owned_by_deployment(
    replicaset: JsonObject,
    deployment_uid: str,
    deployment_name: str,
) -> bool:
    """Check whether a ReplicaSet belongs to a Deployment."""
    for owner in list_items(metadata(replicaset).get("ownerReferences")):
        if owner.get("kind") != K8S_KIND_DEPLOYMENT:
            continue
        owner_uid = str(owner.get("uid") or "")
        owner_name = str(owner.get("name") or "")
        if deployment_uid:
            if owner_uid and owner_uid == deployment_uid:
                return True
            continue
        if deployment_name and owner_name == deployment_name:
            return True
    return False


def replicaset_revision_number(replicaset: JsonObject) -> int:
    """Return the ReplicaSet revision as a sortable number."""
    annotations = object_or_empty(metadata(replicaset).get("annotations"))
    revision = annotations.get(K8S_DEPLOYMENT_REVISION_ANNOTATION)
    try:
        return int(str(revision))
    except (TypeError, ValueError):
        return -1
