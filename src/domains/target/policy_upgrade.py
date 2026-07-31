"""Existing target-agent policy and runtime upgrade planner.

The planner is deliberately pure.  It can produce a complete dry-run report before
the repository applies one cluster through a compare-and-swap transaction.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from dataclasses import dataclass, field
from typing import Any

import yaml

from domains.target.events import TargetDesiredComponent
from domains.target.evidence_policy import (
    EVIDENCE_PROVIDER_KEYS,
    control_namespace_tuple,
    default_agent_policy,
    default_evidence_provider_policy,
    evidence_profile_for_registration,
    evidence_provider_queries,
    profile_default_query_names,
)
from domains.target.install_manifest import cluster_agent_manifest
from domains.target.management_guard import (
    MANAGEMENT_CLUSTER_ROLE,
    TARGET_CLUSTER_ROLE,
    cluster_role_from_registration,
)
from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.evidence_policy import (
    TEMPO_RECENT_TRACE_QUERY_NAME,
    EvidenceProfile,
)
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.gateway.requests import (
    AgentPolicy,
    DesiredResource,
    TargetRegisterRequest,
)
from packages.contracts.target import (
    NODE_COLLECTOR_IMAGE_KEY,
    TARGET_AGENT_IMAGE_KEY,
    TARGET_NAMESPACE,
    TARGET_OTEL_TRACES_ENDPOINT,
    TARGET_RBAC_MANIFEST_VERSION,
    TARGET_RUNTIME_CONFIG_NAME,
    TARGET_RUNTIME_IMAGE_ANNOTATION,
    TargetComponent,
    require_target_image_digest,
)
from packages.storage.engine import unit_of_work_or_null

TARGET_RBAC_ADMIN_MANIFEST_PATH = gateway_routes.TARGET_RBAC_MANIFEST_PATH
UPGRADE_PAGE_SIZE = 100
UPGRADE_ACTOR = "target-policy-upgrade"


def target_desired_components(payload: TargetRegisterRequest) -> list[TargetDesiredComponent]:
    """Return the canonical target runtime component contract."""

    return [
        TargetDesiredComponent(
            component=TargetComponent.CLUSTER_AGENT.value,
            namespace=TARGET_NAMESPACE,
            version=payload.image,
            spec={
                "deployment": "cluster-agent",
                "management_base_url": payload.management_base_url,
                "cluster_role": payload.cluster_role,
                "evidence_interval_seconds": payload.evidence_interval_seconds,
                "loki_base_url": payload.loki_base_url,
                "tempo_base_url": payload.tempo_base_url,
                "otel_traces_endpoint": payload.otel_traces_endpoint,
            },
        ),
        TargetDesiredComponent(
            component=TargetComponent.NODE_COLLECTOR.value,
            namespace=TARGET_NAMESPACE,
            version=payload.image,
            spec={
                "enabled": payload.install_node_collector,
                "daemonset": "optional-node-collector",
                "managed_by": TargetComponent.CLUSTER_AGENT.value,
            },
        ),
    ]


@dataclass(frozen=True)
class TargetUpgradePlan:
    registration_id: int
    workspace_id: str
    cluster_id: str
    changed: bool
    current_generation: int
    next_generation: int
    policy: AgentPolicy | None
    policy_existed: bool
    settings_patch: JsonObject
    desired_states: list[JsonObject]
    rbac_status: str
    rbac_actual_version: str | None
    rbac_expected_version: str
    admin_manifest_path: str
    skipped_reason: str | None = None

    def to_report(self) -> JsonObject:
        return {
            "workspace_id": self.workspace_id,
            "cluster_id": self.cluster_id,
            "changed": self.changed,
            "current_generation": self.current_generation,
            "next_generation": self.next_generation,
            "rbac_status": self.rbac_status,
            "rbac_actual_version": self.rbac_actual_version,
            "rbac_expected_version": self.rbac_expected_version,
            "admin_manifest_path": self.admin_manifest_path,
            "skipped_reason": self.skipped_reason,
        }


@dataclass(frozen=True)
class TargetPolicyUpgradeReport:
    mode: str
    target_image: str
    scanned: int
    changed: int
    applied: int
    skipped: int
    failed: int
    items: list[JsonObject] = field(default_factory=list)

    def to_body(self) -> JsonObject:
        return {
            "mode": self.mode,
            "target_image": self.target_image,
            "scanned": self.scanned,
            "changed": self.changed,
            "applied": self.applied,
            "skipped": self.skipped,
            "failed": self.failed,
            "items": self.items,
        }


def require_immutable_image(image: str) -> str:
    return require_target_image_digest(image)


def _registration_payload(registration: JsonObject, target_image: str) -> TargetRegisterRequest:
    raw_settings = registration.get("settings")
    settings = dict(raw_settings) if isinstance(raw_settings, dict) else {}
    allowed = set(TargetRegisterRequest.model_fields)
    values = {key: value for key, value in settings.items() if key in allowed}
    values.update(
        {
            "workspace_id": str(registration["workspace_id"]),
            "cluster_id": str(registration["cluster_id"]),
            "image": target_image,
        }
    )
    payload = TargetRegisterRequest.model_validate(values)
    if payload.cluster_role != MANAGEMENT_CLUSTER_ROLE and not payload.otel_traces_endpoint.strip():
        payload = payload.model_copy(update={"otel_traces_endpoint": TARGET_OTEL_TRACES_ENDPOINT})
    return payload


def _rebase_provider_queries(
    policy: AgentPolicy,
    interval_seconds: int,
    *,
    cluster_id: str,
    evidence_profile: EvidenceProfile,
    control_namespaces: tuple[str, ...] = (),
) -> AgentPolicy:
    payload = policy.model_dump()
    providers = payload["evidence"]["providers"]
    payload["evidence"]["profile"] = evidence_profile
    reserved_names = profile_default_query_names()
    for provider_key in EVIDENCE_PROVIDER_KEYS:
        default_queries = evidence_provider_queries(
            provider_key,
            cluster_id=cluster_id,
            evidence_profile=evidence_profile,
            control_namespaces=control_namespaces,
        )
        default_queries = [_self_upgrade_compatible_query(query) for query in default_queries]
        existing_provider = providers.get(provider_key)
        if existing_provider is None:
            default_provider = default_evidence_provider_policy(
                provider_key,
                interval_seconds,
                cluster_id=cluster_id,
                evidence_profile=evidence_profile,
                control_namespaces=control_namespaces,
            ).model_dump()
            default_provider["queries"] = default_queries
            providers[provider_key] = default_provider
            continue

        defaults_by_name = {str(item["name"]): copy.deepcopy(item) for item in default_queries}
        seen_defaults: set[str] = set()
        rebased: list[JsonObject] = []
        for raw_query in existing_provider.get("queries", []):
            query = dict(raw_query) if isinstance(raw_query, dict) else {}
            name = query.get("name")
            if isinstance(name, str) and name in defaults_by_name:
                if name not in seen_defaults:
                    rebased.append(copy.deepcopy(defaults_by_name[name]))
                    seen_defaults.add(name)
                continue
            if isinstance(name, str) and name in reserved_names:
                continue
            rebased.append(copy.deepcopy(query))
        for default_query in default_queries:
            name = str(default_query["name"])
            if name not in seen_defaults:
                rebased.append(copy.deepcopy(default_query))
        existing_provider["queries"] = rebased
        # Target telemetry is installed as one required stack. Legacy policies
        # carried traces.enabled=false even after Tempo gained a canonical query,
        # which silently excluded the fourth RCA signal forever.
        existing_provider["enabled"] = bool(default_queries)
    return AgentPolicy.model_validate(payload)


def _self_upgrade_compatible_query(query: JsonObject) -> JsonObject:
    """Keep policy defaults executable by the Agent performing its own upgrade.

    Tempo range-query support was added after the canonical trace query.  A
    policy carrying the new Agent Deployment cannot require that support before
    the Deployment is reconciled.  The upgraded runtime applies the canonical
    recent-query bound locally.
    """

    compatible = copy.deepcopy(query)
    if (
        compatible.get("source") == "tempo"
        and compatible.get("name") == TEMPO_RECENT_TRACE_QUERY_NAME
    ):
        compatible.pop("range_seconds", None)
        compatible.pop("step_seconds", None)
    return compatible


def _deployment_resource(policy: AgentPolicy, payload: TargetRegisterRequest) -> AgentPolicy:
    body = policy.model_dump()
    matches: list[tuple[str, int, JsonObject]] = []
    for section in ("bootstrap", "desired_state"):
        resources = body[section]["resources"]
        for index, resource in enumerate(resources):
            if (
                resource.get("kind") == "Deployment"
                and resource.get("namespace") == TARGET_NAMESPACE
                and resource.get("name") == "cluster-agent"
            ):
                matches.append((section, index, resource))
    if len(matches) > 1:
        raise ValueError("cluster-agent desired resource identity is ambiguous")

    if matches:
        section, index, resource = matches[0]
        state = copy.deepcopy(resource.get("state") or {})
        try:
            containers = state["spec"]["template"]["spec"]["containers"]
        except (KeyError, TypeError) as exc:
            raise ValueError("cluster-agent desired resource has no container list") from exc
        named = [item for item in containers if item.get("name") == "cluster-agent"]
        if len(named) != 1:
            raise ValueError("cluster-agent desired resource container identity is ambiguous")
        named[0]["image"] = payload.image
        template = state["spec"]["template"]
        if not isinstance(template, dict):
            raise ValueError("cluster-agent desired resource template must be an object")
        metadata = template.setdefault("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("cluster-agent template metadata must be an object")
        annotations = metadata.setdefault("annotations", {})
        if not isinstance(annotations, dict):
            raise ValueError("cluster-agent template annotations must be an object")
        annotations[TARGET_RUNTIME_IMAGE_ANNOTATION] = payload.image
        resource["state"] = state
        body[section]["resources"][index] = resource
    else:
        state = yaml.safe_load(cluster_agent_manifest(payload))
        template = state["spec"]["template"]
        template.setdefault("metadata", {}).setdefault("annotations", {})[
            TARGET_RUNTIME_IMAGE_ANNOTATION
        ] = payload.image
        resource = DesiredResource(
            resource_id="target-agent-deployment",
            scope="target-agent",
            kind="Deployment",
            namespace=TARGET_NAMESPACE,
            name="cluster-agent",
            action="apply",
            state=state,
        )
        body["desired_state"]["resources"].append(resource.model_dump())
    return AgentPolicy.model_validate(body)


def _runtime_config_resource(policy: AgentPolicy, payload: TargetRegisterRequest) -> AgentPolicy:
    """Own only the image leaves understood by every deployed target agent.

    The target runtime ConfigMap is merge-patched, so installation-owned
    telemetry settings (including the OTel endpoint) remain intact.  Keeping
    this resource image-only is a backward-compatibility boundary: older
    agents reject any additional key before they can apply the Deployment that
    upgrades themselves.
    """

    body = policy.model_dump()
    matches: list[tuple[str, int, JsonObject]] = []
    for section in ("bootstrap", "desired_state"):
        for index, resource in enumerate(body[section]["resources"]):
            if (
                resource.get("kind") == "ConfigMap"
                and resource.get("namespace") == TARGET_NAMESPACE
                and resource.get("name") == TARGET_RUNTIME_CONFIG_NAME
            ):
                matches.append((section, index, resource))
    if len(matches) > 1:
        raise ValueError("target runtime config desired resource identity is ambiguous")

    resource_id = "target-runtime-config-images"
    if matches:
        section, index, existing = matches[0]
        resource_id = str(existing.get("resource_id") or resource_id)
        del body[section]["resources"][index]
    state = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": TARGET_RUNTIME_CONFIG_NAME, "namespace": TARGET_NAMESPACE},
        "data": {
            TARGET_AGENT_IMAGE_KEY: payload.image,
            NODE_COLLECTOR_IMAGE_KEY: payload.image,
        },
    }
    resource = DesiredResource(
        resource_id=resource_id,
        scope="target-agent",
        kind="ConfigMap",
        namespace=TARGET_NAMESPACE,
        name=TARGET_RUNTIME_CONFIG_NAME,
        action="apply",
        state=state,
    )
    body["bootstrap"]["resources"].insert(0, resource.model_dump())
    return AgentPolicy.model_validate(body)


def target_desired_state_rows(
    payload: TargetRegisterRequest,
    existing: list[JsonObject],
) -> list[JsonObject]:
    """Rebase versions without replacing cluster-specific component configuration."""

    by_component = {str(item.get("component")): copy.deepcopy(item) for item in existing}
    defaults = {
        component.component: component.to_body() for component in target_desired_components(payload)
    }
    for component, default in defaults.items():
        row = by_component.setdefault(component, default)
        row["version"] = payload.image
    return [by_component[key] for key in sorted(by_component)]


def _policy_without_generation(policy: AgentPolicy) -> JsonObject:
    body = policy.model_dump()
    body.pop("generation", None)
    return body


def build_target_upgrade_plan(
    *,
    registration: JsonObject,
    policy: AgentPolicy | None,
    desired_states: list[JsonObject],
    target_image: str,
    rbac_actual_version: str | None,
) -> TargetUpgradePlan:
    image = require_immutable_image(target_image)
    workspace_id = str(registration["workspace_id"])
    cluster_id = str(registration["cluster_id"])
    registration_id = int(registration["id"])
    current = policy or default_agent_policy(cluster_id=cluster_id)
    if current.cluster_id != cluster_id:
        raise ValueError("stored policy cluster identity does not match registration")
    admin_path = TARGET_RBAC_ADMIN_MANIFEST_PATH.format(cluster_id=cluster_id)
    rbac_status = (
        "current" if rbac_actual_version == TARGET_RBAC_MANIFEST_VERSION else "admin_apply_required"
    )
    registration_role = cluster_role_from_registration(registration)
    if registration_role not in (TARGET_CLUSTER_ROLE, MANAGEMENT_CLUSTER_ROLE):
        return TargetUpgradePlan(
            registration_id=registration_id,
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            changed=False,
            current_generation=current.generation,
            next_generation=current.generation,
            policy=None,
            policy_existed=policy is not None,
            settings_patch={},
            desired_states=desired_states,
            rbac_status="not_applicable",
            rbac_actual_version=rbac_actual_version,
            rbac_expected_version=TARGET_RBAC_MANIFEST_VERSION,
            admin_manifest_path=admin_path,
            skipped_reason="unsupported_cluster_role",
        )
    if (
        registration_role == MANAGEMENT_CLUSTER_ROLE
        or current.cluster_role == MANAGEMENT_CLUSTER_ROLE
    ):
        return TargetUpgradePlan(
            registration_id=registration_id,
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            changed=False,
            current_generation=current.generation,
            next_generation=current.generation,
            policy=None,
            policy_existed=policy is not None,
            settings_patch={},
            desired_states=desired_states,
            rbac_status="not_applicable",
            rbac_actual_version=rbac_actual_version,
            rbac_expected_version=TARGET_RBAC_MANIFEST_VERSION,
            admin_manifest_path=admin_path,
            skipped_reason="management_cluster",
        )

    payload = _registration_payload(registration, image)
    evidence_profile = evidence_profile_for_registration(
        cluster_role=payload.cluster_role,
        environment=payload.environment,
        install_sample_workload=payload.install_sample_workload,
    )
    rebased = _rebase_provider_queries(
        current,
        payload.evidence_interval_seconds,
        cluster_id=cluster_id,
        evidence_profile=evidence_profile,
        # 등록 설정의 control_namespaces 를 기존 클러스터 정책에도 리베이스로 반영한다.
        control_namespaces=control_namespace_tuple(payload.control_namespaces),
    )
    rebased = _runtime_config_resource(rebased, payload)
    rebased = _deployment_resource(rebased, payload)
    policy_changed = _policy_without_generation(rebased) != _policy_without_generation(current)
    settings = registration.get("settings")
    current_image = str(settings.get("image") or "") if isinstance(settings, dict) else ""
    settings_patch = {"image": image} if current_image != image else {}
    current_otel_endpoint = (
        str(settings.get("otel_traces_endpoint") or "") if isinstance(settings, dict) else ""
    )
    if current_otel_endpoint != payload.otel_traces_endpoint:
        settings_patch["otel_traces_endpoint"] = payload.otel_traces_endpoint
    next_desired_states = target_desired_state_rows(payload, desired_states)
    normalized_existing = sorted(desired_states, key=lambda item: str(item.get("component")))
    desired_changed = next_desired_states != normalized_existing
    changed = policy_changed or bool(settings_patch) or desired_changed
    next_generation = current.generation + 1 if policy_changed else current.generation
    if policy_changed:
        rebased = rebased.model_copy(update={"generation": next_generation})

    return TargetUpgradePlan(
        registration_id=registration_id,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        changed=changed,
        current_generation=current.generation,
        next_generation=next_generation,
        policy=rebased,
        policy_existed=policy is not None,
        settings_patch=settings_patch,
        desired_states=next_desired_states,
        rbac_status=rbac_status,
        rbac_actual_version=rbac_actual_version,
        rbac_expected_version=TARGET_RBAC_MANIFEST_VERSION,
        admin_manifest_path=admin_path,
    )


def _rbac_actual_version(policy_status: object) -> str | None:
    if not isinstance(policy_status, dict):
        return None
    details = policy_status.get("details")
    if not isinstance(details, dict):
        return None
    rbac = details.get("target_rbac_manifest")
    if not isinstance(rbac, dict):
        return None
    value = rbac.get("actual_version")
    return str(value) if isinstance(value, str) and value else None


class TargetPolicyUpgradeService:
    def __init__(self, db: Any) -> None:
        self.db = db

    def run(self, *, target_image: str, apply: bool) -> TargetPolicyUpgradeReport:
        image = require_immutable_image(target_image)
        after_id = 0
        items: list[JsonObject] = []
        plans: list[TargetUpgradePlan] = []
        scanned = changed = applied = skipped = failed = 0
        while True:
            rows = self.db.list_target_runtime_upgrade_candidates(
                after_id=after_id,
                limit=UPGRADE_PAGE_SIZE,
            )
            if not rows:
                break
            for row in rows:
                registration = dict(row["registration"])
                after_id = max(after_id, int(registration["id"]))
                scanned += 1
                try:
                    raw_policy = row.get("policy")
                    policy = AgentPolicy.model_validate(raw_policy) if raw_policy else None
                    plan = build_target_upgrade_plan(
                        registration=registration,
                        policy=policy,
                        desired_states=[dict(item) for item in row.get("desired_states", [])],
                        target_image=image,
                        rbac_actual_version=_rbac_actual_version(row.get("policy_status")),
                    )
                    if plan.skipped_reason:
                        skipped += 1
                    if plan.changed:
                        changed += 1
                        plans.append(plan)
                    items.append(plan.to_report())
                except Exception as exc:
                    failed += 1
                    items.append(
                        {
                            "workspace_id": registration.get("workspace_id"),
                            "cluster_id": registration.get("cluster_id"),
                            "changed": False,
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        }
                    )
        if apply and failed == 0 and plans:
            try:
                with unit_of_work_or_null(self.db):
                    for plan in plans:
                        self.db.apply_target_runtime_upgrade(plan)
                applied = len(plans)
            except Exception as exc:
                failed += 1
                items.append(
                    {
                        "scope": "batch",
                        "changed": False,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
        return TargetPolicyUpgradeReport(
            mode="apply" if apply else "dry-run",
            target_image=image,
            scanned=scanned,
            changed=changed,
            applied=applied,
            skipped=skipped,
            failed=failed,
            items=items,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebase connected target-agent runtime policy")
    parser.add_argument("--target-image", default=os.environ.get("TARGET_AGENT_IMAGE", ""))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    from domains.registry import Database

    report = TargetPolicyUpgradeService(Database()).run(
        target_image=args.target_image,
        apply=args.apply,
    )
    print(json.dumps(report.to_body(), ensure_ascii=False, sort_keys=True))
    if report.failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
