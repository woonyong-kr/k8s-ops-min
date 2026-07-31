"""Recovery dispatcher가 소비하는 읽기 전용 GitOps 권위 계약."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from packages.contracts.event_bus.interfaces import JsonObject


@dataclass(frozen=True)
class GitOpsAuthorityQuery:
    correlation_id: str
    workspace_id: str
    incident_id: str
    cluster_id: str
    namespace: str
    resource_kind: str
    resource_name: str


@dataclass(frozen=True)
class GitOpsAuthorityContext:
    workspace_id: str
    repository_id: str
    binding_id: str
    application_id: str
    workflow_run_id: str
    environment: str
    cluster_id: str
    manifest_path: str
    repo_ref: str
    base_branch: str
    commit_sha: str
    source_type: str
    source_manifest_sha256: str
    resource: str
    desired_manifest: JsonObject
    changes: tuple[JsonObject, ...]
    evidence: JsonObject


class GitOpsAuthorityReadPort(Protocol):
    async def load_authority(
        self,
        query: GitOpsAuthorityQuery,
    ) -> GitOpsAuthorityContext | None: ...
