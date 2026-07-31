"""DB read model을 교차 검증해 recovery용 GitOps 권위 context를 만든다."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from domains.gitops.source_patch import RAW_YAML, canonical_manifest_digest
from packages.contracts.gitops_authority import (
    GitOpsAuthorityContext,
    GitOpsAuthorityQuery,
    GitOpsAuthorityReadPort,
)
from services.ai.agent.workload_target import (
    WORKLOAD_SNAPSHOT_SOURCE,
    resolve_workload_target,
)

GITOPS_CHANGE_CONTEXT_EVIDENCE_KIND = "gitops_change_context"
RCA_EVIDENCE_KIND = "rca_bundle"
TRUSTED_SOURCE_ORIGINS = frozenset(
    {"git_cache", "github_contents", "github_tree", "git_repo_path"}
)
KUSTOMIZE = "kustomize"


class DatabaseGitOpsAuthorityReadPort(GitOpsAuthorityReadPort):
    """AsyncDb의 기존 read 메서드를 조합하며 새 저장 모델은 만들지 않는다."""

    def __init__(self, db: Any) -> None:
        self.db = db

    async def load_authority(
        self,
        query: GitOpsAuthorityQuery,
    ) -> GitOpsAuthorityContext | None:
        if self.db is None:
            return None
        exact = await self.db.get_evidence_payload(
            query.workspace_id,
            query.correlation_id,
            GITOPS_CHANGE_CONTEXT_EVIDENCE_KIND,
        )
        if isinstance(exact, Mapping):
            # 같은 correlation의 권위 이벤트가 존재하면 그것만 신뢰한다. 손상된
            # exact record를 RCA fallback으로 우회하면 변조를 숨길 수 있다.
            identity = identity_from_evidence(exact)
            if not all(
                text(identity, key)
                for key in ("application_id", "environment", "branch")
            ):
                return None
        evidence = await self.db.get_evidence_payload(
            query.workspace_id,
            query.correlation_id,
            RCA_EVIDENCE_KIND,
        )
        evidence = dict(evidence) if isinstance(evidence, Mapping) else {}
        if not isinstance(exact, Mapping):
            identity = identity_from_rca_evidence(evidence)
            if not identity:
                # A malformed embedded identity must not be bypassed. Only an
                # RCA bundle with no GitOps identity may use the durable current
                # binding + approved snapshot read model.
                if rca_evidence_has_gitops_identity(evidence):
                    return None
                identity = await self._load_approved_snapshot_identity(query)
        if not identity_matches_query(identity, query):
            return None

        workflow_run_id = text(identity, "workflow_run_id")
        binding_id = text(identity, "binding_id")
        commit_sha = text(identity, "commit_sha")
        manifest_path = text(identity, "manifest_path")
        run = await self.db.get_workflow_run(workflow_run_id)
        if not isinstance(run, Mapping):
            # Approved resource snapshots are the durable deployment authority;
            # workflow runs are bounded operational history. Retention of the
            # latter must not invalidate an otherwise complete snapshot whose
            # binding, repository and artifact provenance are still verified
            # below. Exact incident/change evidence continues to fail closed.
            if text(identity, "authority_source") != "approved_snapshot":
                return None
            run = {
                key: identity.get(key)
                for key in (
                    "workspace_id",
                    "application_id",
                    "binding_id",
                    "workflow_run_id",
                    "environment",
                    "cluster_id",
                    "commit_sha",
                )
            }
        run = dict(run)
        identity = enriched_identity(identity, run)
        application_id = text(identity, "application_id")
        resource_diff = await self.db.get_completed_workload_resource_diff(
            query.workspace_id,
            workflow_run_id,
            binding_id,
            query.cluster_id,
            query.namespace,
            query.resource_kind,
            query.resource_name,
        )
        application = await self.db.get_application(query.workspace_id, application_id)
        binding = await self.db.get_deployment_binding(query.workspace_id, binding_id)
        if not all(isinstance(value, Mapping) for value in (application, binding)):
            return None
        application = dict(application)
        binding = dict(binding)
        identity = enriched_identity(identity, application)
        repository = await self.db.get_repository_by_ref(
            query.workspace_id,
            text(identity, "repo_ref"),
        )
        if not isinstance(repository, Mapping):
            return None
        repository = dict(repository)
        if isinstance(resource_diff, Mapping):
            resource_diff = dict(resource_diff)
            diff = mapping(resource_diff.get("diff_details"))
            if not completed_resource_diff_matches_identity(
                resource_diff,
                identity,
                query,
            ):
                return None
            basis = mapping(diff.get("basis"))
            desired = mapping(diff.get("desired_manifest"))
            resource = text(diff, "resource")
            artifact_digest = text(basis, "artifact_digest")
        elif text(identity, "authority_source") == "approved_snapshot":
            resource = f"{query.resource_kind}/{query.resource_name}"
            artifact_digest = text(identity, "artifact_digest")
            diff = {}
            desired = {}
        else:
            return None
        if not artifact_digest:
            return None
        provenance = await self.db.get_manifest_artifact_provenance(
            query.workspace_id,
            binding_id,
            commit_sha,
            manifest_path,
            f"{query.resource_kind.casefold()}/{query.resource_name}",
            artifact_digest,
        )
        if not isinstance(provenance, Mapping):
            return None
        provenance = dict(provenance)
        if not diff:
            desired = mapping(provenance.get("desired_manifest"))
            historical_changes = await self._load_recent_replica_changes(
                query,
                identity,
                desired,
            )
            diff = {
                **identity,
                "namespace": query.namespace,
                "resource": resource,
                "desired_manifest": desired,
                "basis": {
                    "artifact_digest": artifact_digest,
                    "old_desired_source": "last_approved_snapshot",
                },
                "changes": historical_changes,
            }
        if not authority_rows_match(
            query,
            identity,
            run,
            diff,
            application,
            binding,
            repository,
            provenance,
            desired,
            artifact_digest,
        ):
            return None
        changes = diff.get("changes")
        if not isinstance(changes, list):
            return None
        source_type = text(provenance, "source_type")
        return GitOpsAuthorityContext(
            workspace_id=query.workspace_id,
            repository_id=text(identity, "repository_id"),
            binding_id=binding_id,
            application_id=application_id,
            workflow_run_id=workflow_run_id,
            environment=text(identity, "environment"),
            cluster_id=query.cluster_id,
            manifest_path=manifest_path,
            repo_ref=text(identity, "repo_ref"),
            base_branch=text(identity, "branch"),
            commit_sha=commit_sha,
            # Structured recovery patches describe the selected rendered
            # object's semantic field paths. The SCM worker independently
            # resolves a Kustomize binding to one exact raw file at commit_sha
            # before materializing those paths.
            source_type=RAW_YAML if source_type == KUSTOMIZE else source_type,
            source_manifest_sha256=text(provenance, "source_manifest_sha256"),
            resource=resource,
            desired_manifest=desired,
            changes=tuple(dict(item) for item in changes if isinstance(item, Mapping)),
            evidence=evidence,
        )

    async def _load_approved_snapshot_identity(
        self,
        query: GitOpsAuthorityQuery,
    ) -> dict[str, object]:
        """Resolve one exact current GitOps source without relying on a recent change.

        Resource pressure incidents often have no deployment immediately before
        the incident. The active binding and last approved resource snapshot are
        still durable authority, so use them only when exactly one binding owns
        the queried workload and its snapshot identity is complete.
        """

        list_targets = getattr(self.db, "list_active_github_poll_targets", None)
        get_snapshot = getattr(self.db, "get_last_approved_resource_snapshot", None)
        if not callable(list_targets) or not callable(get_snapshot):
            return {}
        raw_targets = await list_targets(workspace_id=query.workspace_id, limit=1000)
        if not isinstance(raw_targets, list):
            return {}

        identities: list[dict[str, object]] = []
        for raw_target in raw_targets:
            if not isinstance(raw_target, Mapping):
                continue
            target = dict(raw_target)
            if (
                text(target, "workspace_id") != query.workspace_id
                or text(target, "cluster_id") != query.cluster_id
            ):
                continue
            binding_id = text(target, "binding_id")
            if not binding_id:
                continue
            snapshot = await get_snapshot(
                query.workspace_id,
                binding_id,
                query.cluster_id,
                query.namespace,
                f"{query.resource_kind}/{query.resource_name}",
            )
            if not isinstance(snapshot, Mapping):
                continue
            snapshot = dict(snapshot)
            if not approved_snapshot_matches_query(snapshot, query, binding_id):
                continue
            identity = {
                "workspace_id": query.workspace_id,
                "repository_id": target.get("repository_id"),
                "binding_id": binding_id,
                "application_id": target.get("application_id"),
                "workflow_run_id": snapshot.get("workflow_run_id"),
                "environment": target.get("environment"),
                "cluster_id": query.cluster_id,
                "commit_sha": snapshot.get("commit_sha"),
                "manifest_path": target.get("manifest_path"),
                "repo_ref": target.get("repo_ref"),
                "branch": target.get("branch"),
                "namespace": query.namespace,
                "resource": f"{query.resource_kind}/{query.resource_name}",
                "artifact_digest": snapshot.get("artifact_digest"),
                "authority_source": "approved_snapshot",
            }
            if identity_matches_query(identity, query):
                identities.append(identity)
        return identities[0] if len(identities) == 1 else {}

    async def _load_recent_replica_changes(
        self,
        query: GitOpsAuthorityQuery,
        identity: Mapping[str, object],
        desired: Mapping[str, object],
    ) -> list[dict[str, object]]:
        """Recover the latest exact replicas transition for an approved snapshot.

        The current snapshot proves the manifest identity, while workload change
        history provides the prior scalar value. Only the newest replicas change
        on the same binding, repository, and manifest path is considered.
        """

        list_diffs = getattr(
            self.db,
            "list_recent_completed_workload_resource_diffs",
            None,
        )
        current = nested_int(desired, "spec", "replicas")
        if not callable(list_diffs) or current is None:
            return []
        rows = await list_diffs(
            query.workspace_id,
            text(identity, "binding_id"),
            query.cluster_id,
            query.namespace,
            query.resource_kind,
            query.resource_name,
            limit=20,
        )
        if not isinstance(rows, list):
            return []
        for value in rows:
            if not isinstance(value, Mapping):
                continue
            row = dict(value)
            if not historical_diff_matches_identity(row, identity, query):
                continue
            diff = mapping(row.get("diff_details"))
            raw_changes = diff.get("changes")
            if not isinstance(raw_changes, list):
                continue
            replica_changes = [
                dict(change)
                for change in raw_changes
                if isinstance(change, Mapping)
                and text(change, "field_path").endswith("spec.replicas")
            ]
            if not replica_changes:
                continue
            if len(replica_changes) != 1:
                return []
            change = replica_changes[0]
            before = scalar_int(change.get("old_desired", change.get("before")))
            after = scalar_int(change.get("new_desired", change.get("after")))
            if before is None or after != current or before == current:
                return []
            return [change]
        return []


def completed_resource_diff_matches_identity(
    record: Mapping[str, object],
    identity: Mapping[str, object],
    query: GitOpsAuthorityQuery,
) -> bool:
    return bool(
        text(record, "workspace_id") == query.workspace_id
        and text(record, "workflow_run_id") == text(identity, "workflow_run_id")
        and text(record, "binding_id") == text(identity, "binding_id")
        and text(record, "cluster_id") == query.cluster_id
        and text(record, "namespace") == query.namespace
        and text(record, "resource_kind").casefold()
        == query.resource_kind.casefold()
        and text(record, "resource_name") == query.resource_name
        and text(record, "repository_id") == text(identity, "repository_id")
        and text(record, "manifest_path") == text(identity, "manifest_path")
        and text(record, "commit_sha") == text(identity, "commit_sha")
    )


def historical_diff_matches_identity(
    record: Mapping[str, object],
    identity: Mapping[str, object],
    query: GitOpsAuthorityQuery,
) -> bool:
    return bool(
        text(record, "workspace_id") == query.workspace_id
        and text(record, "binding_id") == text(identity, "binding_id")
        and text(record, "cluster_id") == query.cluster_id
        and text(record, "namespace") == query.namespace
        and text(record, "resource_kind").casefold()
        == query.resource_kind.casefold()
        and text(record, "resource_name") == query.resource_name
        and text(record, "repository_id") == text(identity, "repository_id")
        and text(record, "manifest_path") == text(identity, "manifest_path")
    )


def scalar_int(value: object) -> int | None:
    if type(value) is int:
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def nested_int(value: Mapping[str, object], *path: str) -> int | None:
    current: object = value
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return scalar_int(current)


def identity_from_evidence(payload: Mapping[str, object]) -> dict[str, object]:
    return {
        key: payload.get(key)
        for key in (
            "workspace_id",
            "repository_id",
            "binding_id",
            "application_id",
            "workflow_run_id",
            "environment",
            "cluster_id",
            "commit_sha",
            "manifest_path",
            "repo_ref",
            "branch",
            "resource",
        )
    }


def identity_from_rca_evidence(payload: Mapping[str, object]) -> dict[str, object]:
    """evidence-worker가 incident 시점 workload change에서 붙인 identity만 읽는다."""

    metadata = mapping(payload.get("metadata"))
    change_context = mapping(metadata.get("change_context"))
    gitops = mapping(change_context.get("gitops"))
    if not gitops or not rca_target_lineage_is_consistent(payload, change_context, gitops):
        return {}
    resource_kind = text(gitops, "resource_kind")
    resource_name = text(gitops, "resource_name")
    return {
        "workspace_id": gitops.get("workspace_id"),
        "repository_id": gitops.get("repository_id"),
        "binding_id": gitops.get("binding_id"),
        "workflow_run_id": gitops.get("workflow_run_id"),
        "cluster_id": gitops.get("cluster_id"),
        "commit_sha": gitops.get("commit_sha"),
        "manifest_path": gitops.get("manifest_path"),
        "repo_ref": gitops.get("repo_ref"),
        "resource": f"{resource_kind}/{resource_name}",
        "namespace": gitops.get("namespace"),
    }


def rca_evidence_has_gitops_identity(payload: Mapping[str, object]) -> bool:
    metadata = mapping(payload.get("metadata"))
    change_context = mapping(metadata.get("change_context"))
    return bool(mapping(change_context.get("gitops")))


def approved_snapshot_matches_query(
    snapshot: Mapping[str, object],
    query: GitOpsAuthorityQuery,
    binding_id: str,
) -> bool:
    body = mapping(snapshot.get("snapshot"))
    resource_kind, separator, resource_name = text(body, "resource").partition("/")
    return bool(
        text(snapshot, "workspace_id") == query.workspace_id
        and text(snapshot, "binding_id") == binding_id
        and text(snapshot, "cluster_id") == query.cluster_id
        and text(snapshot, "namespace") == query.namespace
        and text(snapshot, "resource_kind").casefold() == query.resource_kind.casefold()
        and text(snapshot, "resource_name") == query.resource_name
        and separator
        and resource_kind.casefold() == query.resource_kind.casefold()
        and resource_name == query.resource_name
        and text(body, "namespace") == query.namespace
        and all(text(snapshot, key) for key in ("workflow_run_id", "commit_sha"))
        and bool(text(snapshot, "artifact_digest"))
    )


def rca_target_lineage_is_consistent(
    payload: Mapping[str, object],
    change_context: Mapping[str, object],
    gitops: Mapping[str, object],
) -> bool:
    gitops_target = {
        "namespace": text(gitops, "namespace"),
        "resource_kind": text(gitops, "resource_kind"),
        "resource_name": text(gitops, "resource_name"),
    }
    if not all(gitops_target.values()):
        return False
    kubernetes_resource = mapping(mapping(payload.get("kubernetes")).get("resource"))
    incident_target = {
        "namespace": text(kubernetes_resource, "namespace"),
        "resource_kind": text(kubernetes_resource, "kind"),
        "resource_name": text(kubernetes_resource, "name"),
    }
    declared_target = normalized_target(change_context.get("gitops_target"))
    declared_original = normalized_target(change_context.get("original_target"))
    resolution = text(change_context, "gitops_target_resolution")
    if resolution or any(declared_target.values()) or any(declared_original.values()):
        trusted_resolution = resolve_workload_target(
            incident_target["namespace"],
            incident_target["resource_kind"],
            incident_target["resource_name"],
            mapping(payload.get("metadata")),
        )
        return bool(
            resolution == WORKLOAD_SNAPSHOT_SOURCE
            and trusted_resolution.resolved
            and targets_equal(trusted_resolution.identity(), declared_target)
            and targets_equal(declared_target, gitops_target)
            and targets_equal(declared_original, incident_target)
            and declared_original["namespace"] == declared_target["namespace"]
            and declared_original["resource_kind"].casefold() in {"pod", "replicaset"}
            and declared_target["resource_kind"].casefold() == "deployment"
        )
    return targets_equal(gitops_target, incident_target)


def normalized_target(value: object) -> dict[str, str]:
    target = mapping(value)
    return {
        "namespace": text(target, "namespace"),
        "resource_kind": text(target, "resource_kind"),
        "resource_name": text(target, "resource_name"),
    }


def targets_equal(left: Mapping[str, str], right: Mapping[str, str]) -> bool:
    return bool(
        left.get("namespace")
        and left.get("namespace") == right.get("namespace")
        and left.get("resource_kind", "").casefold()
        == right.get("resource_kind", "").casefold()
        and left.get("resource_name")
        and left.get("resource_name") == right.get("resource_name")
    )


def enriched_identity(
    identity: Mapping[str, object],
    row: Mapping[str, object],
) -> dict[str, object]:
    enriched = dict(identity)
    for key, row_key in (
        ("application_id", "application_id"),
        ("environment", "environment"),
        ("branch", "default_branch"),
    ):
        if not text(enriched, key):
            enriched[key] = row.get(row_key)
    return enriched


def identity_matches_query(
    identity: Mapping[str, object],
    query: GitOpsAuthorityQuery,
) -> bool:
    resource_kind, _, resource_name = text(identity, "resource").partition("/")
    return bool(
        text(identity, "workspace_id") == query.workspace_id
        and text(identity, "cluster_id") == query.cluster_id
        and (
            not text(identity, "namespace")
            or text(identity, "namespace") == query.namespace
        )
        and resource_kind.casefold() == query.resource_kind.casefold()
        and resource_name == query.resource_name
        and all(
            text(identity, key)
            for key in (
                "repository_id",
                "binding_id",
                "workflow_run_id",
                "commit_sha",
                "manifest_path",
                "repo_ref",
            )
        )
    )


def authority_rows_match(
    query: GitOpsAuthorityQuery,
    identity: Mapping[str, object],
    run: Mapping[str, object],
    diff: Mapping[str, object],
    application: Mapping[str, object],
    binding: Mapping[str, object],
    repository: Mapping[str, object],
    provenance: Mapping[str, object],
    desired: Mapping[str, object],
    artifact_digest: str,
) -> bool:
    exact = {
        "workspace_id": query.workspace_id,
        "repository_id": text(identity, "repository_id"),
        "binding_id": text(identity, "binding_id"),
        "application_id": text(identity, "application_id"),
        "workflow_run_id": text(identity, "workflow_run_id"),
        "environment": text(identity, "environment"),
        "commit_sha": text(identity, "commit_sha"),
        "manifest_path": text(identity, "manifest_path"),
    }
    run_exact = {key: value for key, value in exact.items() if key in run}
    diff_exact = {key: value for key, value in exact.items() if key in diff}
    provenance_exact = {
        key: value
        for key, value in exact.items()
        if key
        in {
            "workspace_id",
            "repository_id",
            "binding_id",
            "application_id",
            "workflow_run_id",
            "environment",
            "commit_sha",
            "manifest_path",
        }
    }
    source_count = provenance.get("source_document_count")
    artifact_count = provenance.get("artifact_count")
    trusted_source_shape = (
        text(provenance, "source_type") == RAW_YAML
        and provenance.get("source_is_file") is True
        and type(source_count) is int
        and source_count == 1
        and type(artifact_count) is int
        and artifact_count == 1
    ) or (
        text(provenance, "source_type") == KUSTOMIZE
        and provenance.get("source_is_file") is False
        and type(source_count) is int
        and source_count >= 1
        and type(artifact_count) is int
        and artifact_count == source_count
    )
    return bool(
        text(desired, "kind").casefold() == query.resource_kind.casefold()
        and canonical_manifest_digest(desired) == artifact_digest
        and mapping(diff.get("basis")).get("old_desired_source") == "last_approved_snapshot"
        and resource_refs_equal(text(diff, "resource"), text(identity, "resource"))
        and text(diff, "namespace") == query.namespace
        and all(text(run, key) == value for key, value in run_exact.items())
        and all(text(diff, key) == value for key, value in diff_exact.items())
        and text(application, "application_id") == exact["application_id"]
        and text(application, "workspace_id") == query.workspace_id
        and text(application, "repository_id") == exact["repository_id"]
        and text(application, "status") == "active"
        # Application.manifest_path is the repository-wide default. A cluster
        # binding may intentionally select a different overlay, so source-path
        # authority comes from the exact binding and artifact provenance below.
        and text(application, "repo_ref") == text(identity, "repo_ref")
        and text(application, "default_branch") == text(identity, "branch")
        and text(binding, "workspace_id") == query.workspace_id
        and text(binding, "binding_id") == exact["binding_id"]
        and text(binding, "repository_id") == exact["repository_id"]
        and text(binding, "cluster_id") == query.cluster_id
        and text(binding, "namespace") == query.namespace
        and text(binding, "manifest_path") == exact["manifest_path"]
        and text(binding, "environment") == exact["environment"]
        and text(binding, "status") == "active"
        and text(repository, "workspace_id") == query.workspace_id
        and text(repository, "repository_id") == exact["repository_id"]
        and text(repository, "repo_ref") == text(identity, "repo_ref")
        and text(repository, "default_branch") == text(identity, "branch")
        and text(repository, "status") == "active"
        and all(text(provenance, key) == value for key, value in provenance_exact.items())
        and text(provenance, "artifact_digest") == artifact_digest
        and text(provenance, "repo_ref") == text(identity, "repo_ref")
        and text(provenance, "branch") == text(identity, "branch")
        and text(provenance, "source_origin") in TRUSTED_SOURCE_ORIGINS
        and trusted_source_shape
        and re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            text(provenance, "source_manifest_sha256"),
        )
        is not None
    )


def mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def resource_refs_equal(left: str, right: str) -> bool:
    left_kind, left_separator, left_name = left.partition("/")
    right_kind, right_separator, right_name = right.partition("/")
    return bool(
        left_separator
        and right_separator
        and left_kind.casefold() == right_kind.casefold()
        and left_name
        and left_name == right_name
    )


def text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    return item.strip() if isinstance(item, str) else ""
