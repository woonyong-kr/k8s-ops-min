"""Versioned, server-owned descriptor for the read-only demo workspace seed."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import Field, model_validator

from packages.contracts.cost.observations import MAX_SAFE_JSON_INTEGER
from packages.contracts.demo_seed import DEMO_SEED_MARKER_KEY
from packages.contracts.gateway.requests import InventoryResource
from packages.contracts.identity import DEFAULT_WORKSPACE_ID
from packages.contracts.modeling import StrictModel
from packages.contracts.traffic.observations import MAX_TRAFFIC_TOTAL_COUNT

DEMO_WORKSPACE_DESCRIPTOR_VERSION = 1


class DemoWorkspaceIdentity(StrictModel):
    workspace_id: str = Field(min_length=1, max_length=120)
    owner_user_id: str = Field(
        min_length=1,
        max_length=253,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@+-]*$",
    )


class DemoClusterDescriptor(StrictModel):
    cluster_id: str = Field(min_length=1, max_length=253)
    agent_id: str = Field(min_length=1, max_length=253)
    name: str = Field(min_length=1, max_length=253)
    environment: Literal["demo"] = "demo"
    settings: dict[str, Any] = Field(default_factory=dict)


class DemoInventoryDescriptor(StrictModel):
    replace: Literal[True] = True
    resources: list[InventoryResource] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    health: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_complete_inventory_proof(self) -> DemoInventoryDescriptor:
        if self.summary.get("resources_complete") is not True:
            raise ValueError("demo inventory must prove resources_complete")
        if self.summary.get("labels_complete") is not True:
            raise ValueError("demo inventory must prove labels_complete")
        return self


class DemoGitOpsSourceDescriptor(StrictModel):
    """One validated repository source exposed as a demo application."""

    name: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[a-z0-9][a-z0-9-]{0,118}[a-z0-9]$",
    )
    manifest_path: str = Field(
        min_length=1,
        max_length=500,
        pattern=r"^[^\\]+$",
    )
    source_type: Literal["raw-yaml", "kustomize", "helm"]
    values_path: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
        pattern=r"^[^\\]+$",
    )
    namespace: str = Field(
        min_length=1,
        max_length=63,
        pattern=r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$",
    )
    environment: Literal["demo"] = "demo"
    interval_seconds: int = Field(default=300, ge=30, le=3600)

    @model_validator(mode="after")
    def require_repository_relative_path(self) -> DemoGitOpsSourceDescriptor:
        for field, path in (
            ("manifest", self.manifest_path),
            ("values", self.values_path),
        ):
            if path is None:
                continue
            parts = path.split("/")
            if path.startswith("/") or any(part in {"", ".", ".."} for part in parts):
                raise ValueError(f"demo GitOps {field} path must be repository-relative")
        if self.values_path is not None and self.source_type != "helm":
            raise ValueError("demo GitOps values path is valid only for Helm sources")
        return self


class DemoGitOpsRepositoryDescriptor(StrictModel):
    """Credential-free public GitHub repository attached by the seed CLI."""

    runtime_evidence_version: Literal[1]
    repo_ref: str = Field(
        min_length=3,
        max_length=240,
        pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$",
    )
    default_branch: Literal["main"] = "main"
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    catalog_scenario_count: int = Field(ge=1, le=500)
    sources: list[DemoGitOpsSourceDescriptor] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def require_unique_source_identities(self) -> DemoGitOpsRepositoryDescriptor:
        names = [source.name for source in self.sources]
        paths = [
            (source.manifest_path, source.source_type, source.values_path)
            for source in self.sources
        ]
        if len(names) != len(set(names)):
            raise ValueError("demo GitOps source names must be unique")
        if len(paths) != len(set(paths)):
            raise ValueError("demo GitOps source paths must be unique")
        return self


class DemoCostNamespaceRateDescriptor(StrictModel):
    """One exact synthetic namespace allocation authored in integer micro-USD."""

    namespace: str = Field(
        min_length=1,
        max_length=63,
        pattern=r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$",
    )
    hourly_rate_micros: int = Field(ge=0, le=MAX_SAFE_JSON_INTEGER)
    storage_rate_micros: int = Field(ge=0, le=MAX_SAFE_JSON_INTEGER)


class DemoTrafficFlowDescriptor(StrictModel):
    """One bounded Caretta-shaped flow retained as explicit synthetic evidence."""

    source_name: str = Field(min_length=1, max_length=253)
    source_namespace: str = Field(
        min_length=1,
        max_length=63,
        pattern=r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$",
    )
    source_kind: str = Field(min_length=1, max_length=120)
    target_name: str = Field(min_length=1, max_length=512)
    target_namespace: str | None = Field(
        default=None,
        max_length=63,
        pattern=r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$",
    )
    target_kind: str = Field(min_length=1, max_length=120)
    target_service: str | None = Field(default=None, min_length=1, max_length=253)
    port: int = Field(ge=1, le=65_535)
    connections: int = Field(ge=0, le=MAX_TRAFFIC_TOTAL_COUNT)


class DemoSyntheticObservationDescriptor(StrictModel):
    """Descriptor-owned observations stored through the canonical Agent evidence ledger."""

    runtime_evidence_version: Literal[1]
    origin: Literal["descriptor-owned-synthetic"]
    cost_namespace_rates: list[DemoCostNamespaceRateDescriptor] = Field(
        min_length=1,
        max_length=100,
    )
    traffic_source: Literal["caretta"]
    traffic_flows: list[DemoTrafficFlowDescriptor] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def require_unique_observation_identities(self) -> DemoSyntheticObservationDescriptor:
        namespaces = [item.namespace for item in self.cost_namespace_rates]
        if len(namespaces) != len(set(namespaces)):
            raise ValueError("demo Cost namespace rates must be unique")
        flow_identities = [
            (
                item.source_namespace,
                item.source_name,
                item.target_namespace,
                item.target_name,
                item.port,
            )
            for item in self.traffic_flows
        ]
        if len(flow_identities) != len(set(flow_identities)):
            raise ValueError("demo Traffic flow identities must be unique")
        return self


class DemoRcaTimelineStepDescriptor(StrictModel):
    """One ordered, descriptor-authored stage in a synthetic RCA scenario."""

    stage: Literal["incident_detected", "rca_completed"]
    offset_seconds: int = Field(ge=0, le=3600)
    summary: str = Field(min_length=1, max_length=500)


class DemoRcaScenarioDescriptor(StrictModel):
    """Synthetic incident proof that never claims Agent or AI observation."""

    runtime_evidence_version: Literal[1]
    origin: Literal["descriptor-owned-synthetic"]
    analysis_mode: Literal["none"]
    incident_id: str = Field(min_length=1, max_length=253)
    cause_id: str = Field(
        min_length=1,
        max_length=253,
        pattern=r"^[a-z0-9][a-z0-9._-]{0,252}$",
    )
    namespace: str = Field(
        min_length=1,
        max_length=63,
        pattern=r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$",
    )
    resource_kind: str = Field(min_length=1, max_length=120)
    resource_name: str = Field(min_length=1, max_length=253)
    symptom: str = Field(min_length=1, max_length=500)
    severity: Literal["high", "medium"]
    category: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=1000)
    root_cause: str = Field(min_length=1, max_length=1000)
    confidence: float = Field(ge=0, le=1)
    action: str = Field(min_length=1, max_length=1000)
    impact: list[str] = Field(min_length=1, max_length=20)
    supporting_evidence: list[str] = Field(min_length=1, max_length=20)
    missing_evidence: list[str] = Field(min_length=1, max_length=20)
    timeline: list[DemoRcaTimelineStepDescriptor] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def require_complete_ordered_synthetic_timeline(self) -> DemoRcaScenarioDescriptor:
        if [step.stage for step in self.timeline] != ["incident_detected", "rca_completed"]:
            raise ValueError("demo RCA timeline must contain detected then completed stages")
        offsets = [step.offset_seconds for step in self.timeline]
        if offsets != sorted(offsets) or offsets[0] == offsets[1]:
            raise ValueError("demo RCA timeline offsets must increase")
        return self


class DemoWorkspaceDescriptor(StrictModel):
    schema_version: Literal[DEMO_WORKSPACE_DESCRIPTOR_VERSION]
    descriptor_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,119}$")
    workspace: DemoWorkspaceIdentity
    cluster: DemoClusterDescriptor
    inventory: DemoInventoryDescriptor
    gitops: DemoGitOpsRepositoryDescriptor | None = None
    observations: DemoSyntheticObservationDescriptor | None = None
    rca: DemoRcaScenarioDescriptor | None = None

    @model_validator(mode="after")
    def require_dedicated_workspace(self) -> DemoWorkspaceDescriptor:
        if self.workspace.workspace_id == DEFAULT_WORKSPACE_ID:
            raise ValueError("demo seed must use a dedicated non-default workspace")
        if DEMO_SEED_MARKER_KEY in self.cluster.settings:
            raise ValueError(f"{DEMO_SEED_MARKER_KEY} is reserved for the seed authority")
        if self.observations is not None:
            inventory_namespaces = {
                str(namespace) for namespace in self.inventory.summary.get("namespaces", ())
            }
            observation_namespaces = {
                item.namespace for item in self.observations.cost_namespace_rates
            }
            observation_namespaces.update(
                namespace
                for flow in self.observations.traffic_flows
                for namespace in (flow.source_namespace, flow.target_namespace)
                if namespace is not None
            )
            if not observation_namespaces.issubset(inventory_namespaces):
                raise ValueError("demo observations must reference inventoried namespaces")
        if self.rca is not None:
            resource_identities = {
                (
                    item.kind.casefold(),
                    item.namespace or "",
                    item.name,
                )
                for item in self.inventory.resources
            }
            rca_identity = (
                self.rca.resource_kind.casefold(),
                self.rca.namespace,
                self.rca.resource_name,
            )
            if rca_identity not in resource_identities:
                raise ValueError("demo RCA must reference an inventoried resource")
        return self

    def digest(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def with_owner_user_id(self, owner_user_id: str) -> DemoWorkspaceDescriptor:
        """Return a fully revalidated descriptor whose digest binds the runtime owner."""

        payload = self.model_dump(mode="python")
        payload["workspace"]["owner_user_id"] = owner_user_id
        return type(self).model_validate(payload)

    def seed_marker(self) -> dict[str, str | int]:
        return {
            "descriptor_id": self.descriptor_id,
            "schema_version": self.schema_version,
            "digest": self.digest(),
        }
