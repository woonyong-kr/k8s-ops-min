from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

KUBERNETES_NAME = re.compile(r"^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$")
DEPLOYMENT_RESOURCE = re.compile(r"^deployment/[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$")
IMAGE_DIGEST = re.compile(r"^[A-Za-z0-9._:/-]+@sha256:[0-9a-f]{64}$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
TIMEOUT = re.compile(r"^[1-9][0-9]*[smh]$")
MAX_ROLLBACK_STATUS_WORKERS = 8


@dataclass(frozen=True)
class RollbackTarget:
    namespace: str
    resource: str
    container: str
    image: str


@dataclass(frozen=True)
class BootstrapTarget:
    namespace: str
    resource: str
    container: str
    state: str


@dataclass(frozen=True)
class ProtectedTarget:
    namespace: str
    resource: str
    container: str
    image: str
    state: str


@dataclass(frozen=True)
class DeploymentRollbackState:
    namespace: str
    resource: str
    strategy: dict[str, Any]
    template: dict[str, Any]


@dataclass(frozen=True)
class RollbackPlan:
    previous_release_sha: str
    targets: tuple[RollbackTarget, ...]
    bootstrap_targets: tuple[BootstrapTarget, ...] = ()
    protected_targets: tuple[ProtectedTarget, ...] = ()
    deployment_states: tuple[DeploymentRollbackState, ...] = ()


def require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def require_text(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def parse_target(value: Any, index: int) -> RollbackTarget:
    mapping = require_mapping(value, f"targets[{index}]")
    expected = {"namespace", "resource", "container", "image"}
    if set(mapping) != expected:
        raise ValueError(f"targets[{index}] must contain exactly {sorted(expected)}")

    target = RollbackTarget(
        namespace=require_text(mapping, "namespace"),
        resource=require_text(mapping, "resource"),
        container=require_text(mapping, "container"),
        image=require_text(mapping, "image"),
    )
    if not KUBERNETES_NAME.fullmatch(target.namespace):
        raise ValueError(f"targets[{index}].namespace is not a Kubernetes name")
    if not DEPLOYMENT_RESOURCE.fullmatch(target.resource):
        raise ValueError(f"targets[{index}].resource must be deployment/<name>")
    if not KUBERNETES_NAME.fullmatch(target.container):
        raise ValueError(f"targets[{index}].container is not a Kubernetes name")
    if not IMAGE_DIGEST.fullmatch(target.image):
        raise ValueError(f"targets[{index}].image must use an immutable sha256 digest")
    return target


def parse_bootstrap_target(value: Any, index: int) -> BootstrapTarget:
    mapping = require_mapping(value, f"bootstrap_targets[{index}]")
    expected = {"namespace", "resource", "container", "state"}
    if set(mapping) != expected:
        raise ValueError(f"bootstrap_targets[{index}] must contain exactly {sorted(expected)}")
    target = BootstrapTarget(
        namespace=require_text(mapping, "namespace"),
        resource=require_text(mapping, "resource"),
        container=require_text(mapping, "container"),
        state=require_text(mapping, "state"),
    )
    if not KUBERNETES_NAME.fullmatch(target.namespace):
        raise ValueError(f"bootstrap_targets[{index}].namespace is not a Kubernetes name")
    if not DEPLOYMENT_RESOURCE.fullmatch(target.resource):
        raise ValueError(f"bootstrap_targets[{index}].resource must be deployment/<name>")
    if not KUBERNETES_NAME.fullmatch(target.container):
        raise ValueError(f"bootstrap_targets[{index}].container is not a Kubernetes name")
    if target.state != "not_present_before_rollout":
        raise ValueError(f"bootstrap_targets[{index}].state must be not_present_before_rollout")
    return target


def parse_protected_target(value: Any, index: int) -> ProtectedTarget:
    mapping = require_mapping(value, f"protected_targets[{index}]")
    expected = {"namespace", "resource", "container", "image", "state"}
    if set(mapping) != expected:
        raise ValueError(f"protected_targets[{index}] must contain exactly {sorted(expected)}")
    target = ProtectedTarget(
        namespace=require_text(mapping, "namespace"),
        resource=require_text(mapping, "resource"),
        container=require_text(mapping, "container"),
        image=require_text(mapping, "image"),
        state=require_text(mapping, "state"),
    )
    if not KUBERNETES_NAME.fullmatch(target.namespace):
        raise ValueError(f"protected_targets[{index}].namespace is not a Kubernetes name")
    if not DEPLOYMENT_RESOURCE.fullmatch(target.resource):
        raise ValueError(f"protected_targets[{index}].resource must be deployment/<name>")
    if not KUBERNETES_NAME.fullmatch(target.container):
        raise ValueError(f"protected_targets[{index}].container is not a Kubernetes name")
    if not IMAGE_DIGEST.fullmatch(target.image):
        raise ValueError(f"protected_targets[{index}].image must use an immutable sha256 digest")
    if target.state != "outside_manifest_scope_before_rollout":
        raise ValueError(
            f"protected_targets[{index}].state must be outside_manifest_scope_before_rollout"
        )
    return target


def parse_deployment_state(value: Any, index: int) -> DeploymentRollbackState:
    mapping = require_mapping(value, f"deployment_states[{index}]")
    expected = {"namespace", "resource", "strategy", "template"}
    if set(mapping) != expected:
        raise ValueError(f"deployment_states[{index}] must contain exactly {sorted(expected)}")
    strategy = require_mapping(mapping.get("strategy"), f"deployment_states[{index}].strategy")
    template = require_mapping(mapping.get("template"), f"deployment_states[{index}].template")
    state = DeploymentRollbackState(
        namespace=require_text(mapping, "namespace"),
        resource=require_text(mapping, "resource"),
        strategy=strategy,
        template=template,
    )
    if not KUBERNETES_NAME.fullmatch(state.namespace):
        raise ValueError(f"deployment_states[{index}].namespace is not a Kubernetes name")
    if not DEPLOYMENT_RESOURCE.fullmatch(state.resource):
        raise ValueError(f"deployment_states[{index}].resource must be deployment/<name>")
    pod_spec = require_mapping(template.get("spec"), f"deployment_states[{index}].template.spec")
    containers = pod_spec.get("containers")
    if not isinstance(containers, list) or not containers:
        raise ValueError(f"deployment_states[{index}].template.spec.containers must be a list")
    return state


def load_plan(path: Path) -> RollbackPlan:
    document = require_mapping(json.loads(path.read_text(encoding="utf-8")), "plan")
    version = document.get("version")
    if version == 1:
        expected_fields = {"version", "previous_release_sha", "targets"}
    elif version == 2:
        expected_fields = {
            "version",
            "previous_release_sha",
            "targets",
            "bootstrap_targets",
        }
    elif version == 3:
        expected_fields = {
            "version",
            "previous_release_sha",
            "targets",
            "bootstrap_targets",
            "protected_targets",
        }
    elif version == 4:
        expected_fields = {
            "version",
            "previous_release_sha",
            "targets",
            "bootstrap_targets",
            "protected_targets",
            "deployment_states",
        }
    else:
        raise ValueError("plan version must be 1, 2, 3, or 4")
    if set(document) != expected_fields:
        raise ValueError(f"plan must contain exactly {sorted(expected_fields)}")

    previous_release_sha = require_text(document, "previous_release_sha")
    if not GIT_SHA.fullmatch(previous_release_sha):
        raise ValueError("previous_release_sha must be a full lowercase Git SHA")

    raw_targets = document["targets"]
    if not isinstance(raw_targets, list):
        raise ValueError("targets must be a list")
    targets = tuple(parse_target(value, index) for index, value in enumerate(raw_targets))
    raw_bootstrap_targets = document.get("bootstrap_targets", [])
    if not isinstance(raw_bootstrap_targets, list):
        raise ValueError("bootstrap_targets must be a list")
    bootstrap_targets = tuple(
        parse_bootstrap_target(value, index) for index, value in enumerate(raw_bootstrap_targets)
    )
    raw_protected_targets = document.get("protected_targets", [])
    if not isinstance(raw_protected_targets, list):
        raise ValueError("protected_targets must be a list")
    protected_targets = tuple(
        parse_protected_target(value, index) for index, value in enumerate(raw_protected_targets)
    )
    raw_deployment_states = document.get("deployment_states", [])
    if not isinstance(raw_deployment_states, list):
        raise ValueError("deployment_states must be a list")
    deployment_states = tuple(
        parse_deployment_state(value, index) for index, value in enumerate(raw_deployment_states)
    )
    if not targets and not bootstrap_targets and not protected_targets:
        raise ValueError("plan must contain at least one target")
    identities = [
        (target.namespace, target.resource, target.container)
        for target in (*targets, *bootstrap_targets, *protected_targets)
    ]
    if len(identities) != len(set(identities)):
        raise ValueError("plan must not contain duplicate deployment containers")
    state_identities = [(state.namespace, state.resource) for state in deployment_states]
    if len(state_identities) != len(set(state_identities)):
        raise ValueError("plan must not contain duplicate deployment states")
    expected_state_identities = {(target.namespace, target.resource) for target in targets}
    if version == 4 and set(state_identities) != expected_state_identities:
        raise ValueError("deployment_states must cover every existing rollout deployment")
    return RollbackPlan(
        previous_release_sha=previous_release_sha,
        targets=targets,
        bootstrap_targets=bootstrap_targets,
        protected_targets=protected_targets,
        deployment_states=deployment_states,
    )


def deployment_state_patch(state: DeploymentRollbackState) -> list[dict[str, Any]]:
    return [
        {"op": "replace", "path": "/spec/strategy", "value": state.strategy},
        {"op": "replace", "path": "/spec/template", "value": state.template},
    ]


def restore_deployment_states(plan: RollbackPlan, *, context: str) -> None:
    failures: list[tuple[str, Exception]] = []
    for state in plan.deployment_states:
        try:
            subprocess.run(
                (
                    "kubectl",
                    "--context",
                    context,
                    "-n",
                    state.namespace,
                    "patch",
                    state.resource,
                    "--type=json",
                    "--patch-file=/dev/stdin",
                ),
                check=True,
                input=json.dumps(deployment_state_patch(state), separators=(",", ":")),
                text=True,
            )
        except Exception as error:  # noqa: BLE001 - restore every captured workload state
            failures.append((f"{state.namespace}/{state.resource}", error))
    if failures:
        details = "; ".join(f"{identity}: {error}" for identity, error in failures)
        raise RuntimeError(f"deployment state restoration failed: {details}") from failures[0][1]


def kubectl_commands(
    plan: RollbackPlan, *, context: str, timeout: str
) -> tuple[tuple[str, ...], ...]:
    if not context or any(character.isspace() for character in context):
        raise ValueError("context must be a non-empty name without whitespace")
    if not TIMEOUT.fullmatch(timeout):
        raise ValueError("timeout must be a positive Kubernetes duration such as 300s")

    context_command = ("kubectl", "config", "get-contexts", context, "-o", "name")
    set_image_commands: list[tuple[str, ...]] = []
    rollout_status_commands: list[tuple[str, ...]] = []
    for (namespace, resource), targets in grouped_targets(plan).items():
        prefix = ("kubectl", "--context", context, "-n", namespace)
        set_image_commands.append(
            (
                *prefix,
                "set",
                "image",
                resource,
                *(f"{target.container}={target.image}" for target in targets),
            )
        )
        rollout_status_commands.append(
            (*prefix, "rollout", "status", resource, f"--timeout={timeout}")
        )
    return (context_command, *set_image_commands, *rollout_status_commands)


def grouped_targets(
    plan: RollbackPlan,
) -> dict[tuple[str, str], tuple[RollbackTarget, ...]]:
    grouped: dict[tuple[str, str], list[RollbackTarget]] = {}
    for target in plan.targets:
        grouped.setdefault((target.namespace, target.resource), []).append(target)
    return {identity: tuple(targets) for identity, targets in grouped.items()}


def bootstrap_delete_commands(plan: RollbackPlan, *, context: str) -> tuple[tuple[str, ...], ...]:
    resources = sorted({(target.namespace, target.resource) for target in plan.bootstrap_targets})
    return tuple(
        (
            "kubectl",
            "--context",
            context,
            "-n",
            namespace,
            "delete",
            resource,
            "--ignore-not-found",
            "--wait=true",
        )
        for namespace, resource in resources
    )


def timeout_seconds(timeout: str) -> float:
    if not TIMEOUT.fullmatch(timeout):
        raise ValueError("timeout must be a positive Kubernetes duration such as 300s")
    multipliers = {"s": 1, "m": 60, "h": 3600}
    return float(int(timeout[:-1]) * multipliers[timeout[-1]])


def _run_rollout_status(command: tuple[str, ...], *, deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError(f"shared rollout deadline expired before {command[-2]}")
    bounded_command = (*command[:-1], f"--timeout={max(1, math.ceil(remaining))}s")
    subprocess.run(bounded_command, check=True, timeout=remaining)


def wait_for_rollout_statuses(
    commands: tuple[tuple[str, ...], ...],
    *,
    timeout: str,
    max_workers: int = MAX_ROLLBACK_STATUS_WORKERS,
) -> None:
    if not commands:
        return
    worker_count = min(max_workers, len(commands))
    if worker_count < 1:
        raise ValueError("max_workers must be positive")

    deadline = time.monotonic() + timeout_seconds(timeout)
    failures: list[tuple[tuple[str, ...], Exception]] = []
    with ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="rollback-status",
    ) as executor:
        futures = {
            executor.submit(_run_rollout_status, command, deadline=deadline): command
            for command in commands
        }
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as error:  # noqa: BLE001 - aggregate every kubectl failure
                failures.append((futures[future], error))

    if failures:
        failures.sort(key=lambda failure: failure[0])
        resources = [command[-2] for command, _error in failures]
        raise RuntimeError(f"rollback rollout status failed for {resources!r}") from failures[0][1]


def live_deployment_images(*, context: str, namespace: str) -> dict[tuple[str, str], str]:
    result = subprocess.run(
        (
            "kubectl",
            "--context",
            context,
            "-n",
            namespace,
            "get",
            "deployments",
            "-o",
            "json",
        ),
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    document = require_mapping(json.loads(result.stdout), "live deployments")
    items = document.get("items")
    if not isinstance(items, list):
        raise ValueError("live deployments.items must be a list")

    images: dict[tuple[str, str], str] = {}
    for index, raw_item in enumerate(items):
        item = require_mapping(raw_item, f"live deployments.items[{index}]")
        metadata = require_mapping(
            item.get("metadata"), f"live deployments.items[{index}].metadata"
        )
        name = metadata.get("name")
        if not isinstance(name, str) or not KUBERNETES_NAME.fullmatch(name):
            raise ValueError(f"live deployments.items[{index}] has an invalid name")
        spec = require_mapping(item.get("spec"), f"deployment/{name}.spec")
        template = require_mapping(spec.get("template"), f"deployment/{name}.template")
        pod_spec = require_mapping(template.get("spec"), f"deployment/{name}.podSpec")
        containers = pod_spec.get("containers")
        if not isinstance(containers, list):
            raise ValueError(f"deployment/{name}.containers must be a list")
        for container_index, raw_container in enumerate(containers):
            container = require_mapping(
                raw_container,
                f"deployment/{name}.containers[{container_index}]",
            )
            container_name = container.get("name")
            image = container.get("image")
            if not isinstance(container_name, str) or not isinstance(image, str):
                raise ValueError(f"deployment/{name} container name and image must be strings")
            identity = (f"deployment/{name}", container_name)
            if identity in images:
                raise ValueError(f"live deployments contain duplicate container {identity}")
            images[identity] = image
    return images


def verify_exact_live_digests(plan: RollbackPlan, *, context: str) -> int:
    expected = (*plan.targets, *plan.protected_targets)
    observed_by_namespace = {
        namespace: live_deployment_images(context=context, namespace=namespace)
        for namespace in dict.fromkeys(target.namespace for target in expected)
    }
    mismatches = sorted(
        (target.namespace, target.resource, target.container)
        for target in expected
        if observed_by_namespace[target.namespace].get((target.resource, target.container))
        != target.image
    )
    if mismatches:
        raise RuntimeError(f"rollback digest mismatch: {mismatches!r}")
    return len(expected)


def verify_bootstrap_targets_absent(plan: RollbackPlan, *, context: str) -> int:
    expected_absent = {
        (target.namespace, target.resource, target.container) for target in plan.bootstrap_targets
    }
    observed_by_namespace = {
        namespace: live_deployment_images(context=context, namespace=namespace)
        for namespace in dict.fromkeys(target.namespace for target in plan.bootstrap_targets)
    }
    present = sorted(
        identity
        for identity in expected_absent
        if any(
            resource == identity[1] for resource, _container in observed_by_namespace[identity[0]]
        )
    )
    if present:
        raise RuntimeError(f"bootstrap deployment still present after rollback: {present!r}")
    return len(expected_absent)


def apply_plan(plan: RollbackPlan, *, context: str, timeout: str) -> None:
    commands = kubectl_commands(plan, context=context, timeout=timeout)
    context_result = subprocess.run(commands[0], check=True, capture_output=True, text=True)
    if context_result.stdout.strip() != context:
        raise RuntimeError(f"kubectl context was not found: {context}")
    deployment_count = len(grouped_targets(plan))
    set_image_commands = commands[1 : 1 + deployment_count]
    rollout_status_commands = commands[1 + deployment_count :]
    failures: list[tuple[str, Exception]] = []
    try:
        restore_deployment_states(plan, context=context)
    except Exception as error:  # noqa: BLE001 - image rollback and verification must still run
        failures.append(("restore deployment state", error))
    for command in set_image_commands:
        try:
            subprocess.run(command, check=True)
        except Exception as error:  # noqa: BLE001 - continue restoring every captured target
            failures.append((f"set image {command[7]}", error))
    try:
        wait_for_rollout_statuses(rollout_status_commands, timeout=timeout)
    except Exception as error:  # noqa: BLE001 - exact digest verification must still run
        failures.append(("rollout status", error))
    for command in bootstrap_delete_commands(plan, context=context):
        try:
            subprocess.run(command, check=True)
        except Exception as error:  # noqa: BLE001 - continue restoring every captured target
            failures.append((f"delete bootstrap {command[6]}", error))
    try:
        verify_exact_live_digests(plan, context=context)
    except Exception as error:  # noqa: BLE001 - aggregate the fail-closed rollback evidence
        failures.append(("exact digest verification", error))
    try:
        verify_bootstrap_targets_absent(plan, context=context)
    except Exception as error:  # noqa: BLE001 - aggregate the fail-closed rollback evidence
        failures.append(("bootstrap absence verification", error))
    if failures:
        details = "; ".join(f"{label}: {error}" for label, error in failures)
        raise RuntimeError(f"rollback failed closed: {details}") from failures[0][1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Restore Kubernetes deployments to explicitly recorded image digests."
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--timeout", default="300s")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Run kubectl. Without this flag the validated command plan is printed only.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    plan = load_plan(args.plan)
    commands = kubectl_commands(plan, context=args.context, timeout=args.timeout)
    if args.apply:
        apply_plan(plan, context=args.context, timeout=args.timeout)
        print(
            f"restored {len(plan.targets)} deployment container(s) for {plan.previous_release_sha}"
        )
        return 0

    print(
        json.dumps(
            {
                "mode": "dry-run",
                "previous_release_sha": plan.previous_release_sha,
                "commands": [list(command) for command in commands],
                "bootstrap_delete_commands": [
                    list(command)
                    for command in bootstrap_delete_commands(plan, context=args.context)
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
