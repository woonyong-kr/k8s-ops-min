from __future__ import annotations

import argparse
import copy
import json
import math
import os
import subprocess
import time
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import yaml
from capture_image_digests import image_repository, live_deployment_images, require_mapping
from revert_image_digests import IMAGE_DIGEST, TIMEOUT, RollbackPlan, load_plan

MAX_ROLLOUT_STATUS_WORKERS = 8
MAX_SPEC_PATCH_WORKERS = 8


def bounded_waves[T](items: tuple[T, ...], *, size: int) -> tuple[tuple[T, ...], ...]:
    if size < 1:
        raise ValueError("wave size must be positive")
    return tuple(items[index : index + size] for index in range(0, len(items), size))


def rollout_targets(plan: RollbackPlan) -> tuple[Any, ...]:
    return (*plan.targets, *plan.bootstrap_targets)


def render_bootstrap_manifest(plan: RollbackPlan, *, manifest: Path, image: str) -> str:
    if not IMAGE_DIGEST.fullmatch(image):
        raise ValueError("image must use an immutable sha256 digest")
    expected = {
        (target.namespace, target.resource, target.container) for target in plan.bootstrap_targets
    }
    if not expected:
        return ""

    expected_by_resource: dict[tuple[str, str], set[str]] = {}
    for namespace, resource, container in expected:
        expected_by_resource.setdefault((namespace, resource), set()).add(container)

    rendered: list[dict[str, Any]] = []
    observed: set[tuple[str, str, str]] = set()
    for index, value in enumerate(yaml.safe_load_all(manifest.read_text(encoding="utf-8"))):
        document = require_mapping(value, f"manifest document {index}")
        if document.get("kind") != "Deployment":
            continue
        metadata = require_mapping(document.get("metadata"), f"manifest document {index}.metadata")
        name = metadata.get("name")
        namespace = metadata.get("namespace")
        if not isinstance(name, str):
            raise ValueError(f"manifest document {index} has no deployment name")
        matching_namespaces = {
            target_namespace
            for target_namespace, resource in expected_by_resource
            if resource == f"deployment/{name}"
        }
        if not matching_namespaces:
            continue
        if len(matching_namespaces) != 1:
            raise ValueError(f"bootstrap deployment namespace is ambiguous: {name}")
        expected_namespace = next(iter(matching_namespaces))
        if namespace is None:
            metadata["namespace"] = expected_namespace
        elif namespace != expected_namespace:
            raise ValueError(f"bootstrap deployment namespace mismatch: {name}")

        spec = require_mapping(document.get("spec"), f"deployment/{name}.spec")
        template = require_mapping(spec.get("template"), f"deployment/{name}.template")
        pod_spec = require_mapping(template.get("spec"), f"deployment/{name}.podSpec")
        containers = pod_spec.get("containers")
        if not isinstance(containers, list) or not containers:
            raise ValueError(f"deployment/{name} must contain containers")
        expected_containers = expected_by_resource[(expected_namespace, f"deployment/{name}")]
        for container_index, raw_container in enumerate(containers):
            container = require_mapping(
                raw_container,
                f"deployment/{name}.containers[{container_index}]",
            )
            container_name = container.get("name")
            if container_name not in expected_containers:
                continue
            source_image = container.get("image")
            if not isinstance(source_image, str) or image_repository(
                source_image
            ) != image_repository(image):
                raise ValueError(f"bootstrap manifest repository mismatch: {name}/{container_name}")
            container["image"] = image
            observed.add((expected_namespace, f"deployment/{name}", container_name))
        rendered.append(document)

    if observed != expected:
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        raise ValueError(f"bootstrap manifest target mismatch: missing={missing!r} extra={extra!r}")
    resources = [
        (document["metadata"]["namespace"], document["metadata"]["name"]) for document in rendered
    ]
    if len(resources) != len(set(resources)):
        raise ValueError("bootstrap manifest contains duplicate Deployments")
    return yaml.safe_dump_all(rendered, sort_keys=False)


def existing_deployment_spec_patches(
    plan: RollbackPlan,
    *,
    manifest: Path,
    image: str,
) -> tuple[tuple[str, str, list[dict[str, Any]]], ...]:
    if not IMAGE_DIGEST.fullmatch(image):
        raise ValueError("image must use an immutable sha256 digest")
    if not plan.deployment_states:
        return ()

    states = {(state.namespace, state.resource): state for state in plan.deployment_states}
    target_containers: dict[tuple[str, str], set[str]] = {}
    for target in plan.targets:
        target_containers.setdefault((target.namespace, target.resource), set()).add(
            target.container
        )

    patches: list[tuple[str, str, list[dict[str, Any]]]] = []
    observed: set[tuple[str, str]] = set()
    for index, value in enumerate(yaml.safe_load_all(manifest.read_text(encoding="utf-8"))):
        document = require_mapping(value, f"manifest document {index}")
        if document.get("kind") != "Deployment":
            continue
        metadata = require_mapping(document.get("metadata"), f"manifest document {index}.metadata")
        name = metadata.get("name")
        if not isinstance(name, str):
            raise ValueError(f"manifest document {index} has no deployment name")
        namespaces = {
            namespace for namespace, resource in states if resource == f"deployment/{name}"
        }
        if not namespaces:
            continue
        if len(namespaces) != 1:
            raise ValueError(f"deployment namespace is ambiguous: {name}")
        namespace = next(iter(namespaces))
        declared_namespace = metadata.get("namespace")
        if declared_namespace is not None and declared_namespace != namespace:
            raise ValueError(f"deployment/{name} namespace mismatch")
        identity = (namespace, f"deployment/{name}")
        if identity in observed:
            raise ValueError(f"manifest contains duplicate Deployment: {name}")

        spec = require_mapping(document.get("spec"), f"deployment/{name}.spec")
        template = copy.deepcopy(
            require_mapping(spec.get("template"), f"deployment/{name}.template")
        )
        pod_spec = require_mapping(template.get("spec"), f"deployment/{name}.podSpec")
        containers = pod_spec.get("containers")
        if not isinstance(containers, list) or not containers:
            raise ValueError(f"deployment/{name} must contain containers")
        state = states[identity]
        live_pod_spec = require_mapping(
            state.template.get("spec"), f"deployment/{name}.livePodSpec"
        )
        live_containers = live_pod_spec.get("containers")
        if not isinstance(live_containers, list) or not live_containers:
            raise ValueError(f"deployment/{name} live containers must be a list")
        live_images = {
            require_mapping(container, f"deployment/{name}.liveContainer").get(
                "name"
            ): require_mapping(container, f"deployment/{name}.liveContainer").get("image")
            for container in live_containers
        }
        canonical_names: set[str] = set()
        for container_index, raw_container in enumerate(containers):
            container = require_mapping(
                raw_container,
                f"deployment/{name}.containers[{container_index}]",
            )
            container_name = container.get("name")
            if not isinstance(container_name, str) or container_name in canonical_names:
                raise ValueError(f"deployment/{name} has invalid or duplicate container names")
            canonical_names.add(container_name)
            if container_name in target_containers[identity]:
                container["image"] = image
                continue
            live_image = live_images.get(container_name)
            if live_image is not None:
                if not isinstance(live_image, str) or not IMAGE_DIGEST.fullmatch(live_image):
                    raise ValueError(f"deployment/{name}/{container_name} live image is mutable")
                container["image"] = live_image
                continue
            canonical_image = container.get("image")
            if not isinstance(canonical_image, str) or not IMAGE_DIGEST.fullmatch(canonical_image):
                raise ValueError(f"deployment/{name}/{container_name} new image must be immutable")
        unexpected_live = set(live_images) - canonical_names
        if unexpected_live:
            raise ValueError(
                f"deployment/{name} has live containers outside canonical manifest: "
                f"{sorted(unexpected_live)!r}"
            )
        strategy = copy.deepcopy(
            require_mapping(spec.get("strategy", state.strategy), f"deployment/{name}.strategy")
        )
        patches.append(
            (
                namespace,
                f"deployment/{name}",
                [
                    {"op": "replace", "path": "/spec/strategy", "value": strategy},
                    {"op": "replace", "path": "/spec/template", "value": template},
                ],
            )
        )
        observed.add(identity)

    missing = sorted(set(states) - observed)
    if missing:
        raise ValueError(f"manifest is missing existing rollout deployments: {missing!r}")
    return tuple(patches)


def apply_existing_deployment_specs(
    plan: RollbackPlan,
    *,
    context: str,
    manifest: Path,
    image: str,
    max_workers: int = MAX_SPEC_PATCH_WORKERS,
) -> int:
    patches = existing_deployment_spec_patches(plan, manifest=manifest, image=image)
    if not patches:
        return 0
    worker_count = min(MAX_SPEC_PATCH_WORKERS, max_workers, len(patches))
    if worker_count < 1:
        raise ValueError("max_workers must be positive")

    for wave in bounded_waves(patches, size=worker_count):
        _apply_existing_deployment_spec_wave(wave, context=context)
    return len(patches)


def _apply_existing_deployment_spec_wave(
    patches: tuple[tuple[str, str, list[dict[str, Any]]], ...],
    *,
    context: str,
    deadline: float | None = None,
) -> None:
    if not patches:
        return

    failures: list[tuple[tuple[str, str], Exception]] = []
    cancelled: list[tuple[str, str]] = []
    with ThreadPoolExecutor(
        max_workers=len(patches),
        thread_name_prefix="deployment-spec-patch",
    ) as executor:
        futures = {
            executor.submit(
                _run_with_deadline,
                (
                    "kubectl",
                    "--context",
                    context,
                    "-n",
                    namespace,
                    "patch",
                    resource,
                    "--type=json",
                    "--patch-file=/dev/stdin",
                ),
                deadline=deadline,
                label=f"deployment spec patch {namespace}/{resource}",
                check=True,
                input=json.dumps(patch, separators=(",", ":")),
                text=True,
            ): (namespace, resource)
            for namespace, resource, patch in patches
        }
        for future in as_completed(futures):
            try:
                future.result()
            except CancelledError:
                cancelled.append(futures[future])
            except Exception as error:  # noqa: BLE001 - wait for and report every patch failure
                failures.append((futures[future], error))
                for pending in futures:
                    if pending is not future:
                        pending.cancel()

    if failures:
        failures.sort(key=lambda failure: failure[0])
        resources = sorted(
            [f"{namespace}/{resource}" for (namespace, resource), _error in failures]
            + [f"{namespace}/{resource}" for namespace, resource in cancelled]
        )
        first_error = failures[0][1]
        raise RuntimeError(
            f"deployment spec reconciliation failed for {resources!r}"
        ) from first_error
    if cancelled:
        resources = sorted(f"{namespace}/{resource}" for namespace, resource in cancelled)
        raise RuntimeError(f"deployment spec reconciliation cancelled for {resources!r}")


def _remaining_seconds(deadline: float, *, before: str) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError(f"shared rollout deadline expired before {before}")
    return remaining


def _run_with_deadline(
    command: tuple[str, ...],
    *,
    deadline: float | None,
    label: str,
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    if deadline is not None:
        kwargs["timeout"] = _remaining_seconds(deadline, before=label)
    return subprocess.run(command, **kwargs)


def live_deployments(*, context: str, namespace: str, deadline: float | None = None) -> Any:
    result = _run_with_deadline(
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
        deadline=deadline,
        label="live deployment inventory",
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def verify_repository_rollout(
    plan: RollbackPlan,
    *,
    image: str,
    live_document: Any,
    require_exact_digest: bool,
) -> int:
    repository = image_repository(image)
    expected = {
        (target.resource.removeprefix("deployment/"), target.container)
        for target in rollout_targets(plan)
    }
    live_images = live_deployment_images(live_document)
    protected = {
        (target.resource.removeprefix("deployment/"), target.container): target.image
        for target in plan.protected_targets
    }
    if any(
        image_repository(protected_image) != repository for protected_image in protected.values()
    ):
        raise ValueError("protected target repository must match rollout repository")
    missing = sorted(expected - set(live_images))
    protected_changes = sorted(
        identity
        for identity, protected_image in protected.items()
        if live_images.get(identity) != protected_image
    )
    unexpected_repository_targets = sorted(
        identity
        for identity, observed_image in live_images.items()
        if image_repository(observed_image) == repository
        and identity not in expected
        and identity not in protected
    )
    if missing or unexpected_repository_targets:
        raise RuntimeError(
            "repository target set changed: "
            f"missing={missing!r} extra={unexpected_repository_targets!r}"
        )
    if protected_changes:
        raise RuntimeError(f"protected repository target changed: {protected_changes!r}")
    if not require_exact_digest:
        return len(expected)
    mismatches = sorted(identity for identity in expected if live_images[identity] != image)
    if mismatches:
        raise RuntimeError(f"repository digest mismatch: {mismatches!r}")
    return len(expected)


def verify_pre_rollout_state(plan: RollbackPlan, *, image: str, live_document: Any) -> int:
    repository = image_repository(image)
    live_images = live_deployment_images(live_document)
    existing = {
        (target.resource.removeprefix("deployment/"), target.container) for target in plan.targets
    }
    missing_existing = sorted(existing - set(live_images))
    if missing_existing:
        raise RuntimeError(f"captured deployment target disappeared: {missing_existing!r}")

    live_deployment_names = {deployment for deployment, _container in live_images}
    present_bootstrap_deployments = sorted(
        {
            target.resource.removeprefix("deployment/")
            for target in plan.bootstrap_targets
            if target.resource.removeprefix("deployment/") in live_deployment_names
        }
    )
    if present_bootstrap_deployments:
        raise RuntimeError(
            f"bootstrap deployment appeared after capture: {present_bootstrap_deployments!r}"
        )

    expected = {
        (target.resource.removeprefix("deployment/"), target.container)
        for target in rollout_targets(plan)
    }
    protected = {
        (target.resource.removeprefix("deployment/"), target.container): target.image
        for target in plan.protected_targets
    }
    protected_changes = sorted(
        identity
        for identity, protected_image in protected.items()
        if live_images.get(identity) != protected_image
    )
    if protected_changes:
        raise RuntimeError(f"protected repository target changed: {protected_changes!r}")
    unexpected_repository_targets = sorted(
        identity
        for identity, observed_image in live_images.items()
        if image_repository(observed_image) == repository
        and identity not in expected
        and identity not in protected
    )
    if unexpected_repository_targets:
        raise RuntimeError(
            f"repository target set changed: extra={unexpected_repository_targets!r}"
        )
    return len(existing)


def create_bootstrap_deployments(
    plan: RollbackPlan,
    *,
    context: str,
    image: str,
    manifest: Path | None,
    deadline: float | None = None,
) -> None:
    if not plan.bootstrap_targets:
        return
    if manifest is None:
        raise ValueError("manifest is required when the plan contains bootstrap targets")
    namespaces = {target.namespace for target in plan.bootstrap_targets}
    if len(namespaces) != 1:
        raise ValueError("bootstrap plan must target exactly one namespace")
    namespace = next(iter(namespaces))
    rendered = render_bootstrap_manifest(plan, manifest=manifest, image=image)
    _run_with_deadline(
        (
            "kubectl",
            "--context",
            context,
            "-n",
            namespace,
            "create",
            "--filename",
            "-",
        ),
        deadline=deadline,
        label="bootstrap deployment creation",
        check=True,
        input=rendered,
        text=True,
    )


def rollout_commands(
    plan: RollbackPlan, *, context: str, image: str, timeout: str
) -> tuple[tuple[str, ...], ...]:
    if not context or any(character.isspace() for character in context):
        raise ValueError("context must be a non-empty name without whitespace")
    if not IMAGE_DIGEST.fullmatch(image):
        raise ValueError("image must use an immutable sha256 digest")
    if not TIMEOUT.fullmatch(timeout):
        raise ValueError("timeout must be a positive Kubernetes duration such as 300s")

    context_command = ("kubectl", "config", "get-contexts", context, "-o", "name")
    set_image_commands: list[tuple[str, ...]] = []
    rollout_status_commands: list[tuple[str, ...]] = []
    for target in rollout_targets(plan):
        prefix = ("kubectl", "--context", context, "-n", target.namespace)
        set_image_commands.append(
            (*prefix, "set", "image", target.resource, f"{target.container}={image}")
        )
        rollout_status_commands.append(
            (*prefix, "rollout", "status", target.resource, f"--timeout={timeout}")
        )
    return (context_command, *set_image_commands, *rollout_status_commands)


def timeout_seconds(timeout: str) -> float:
    if not TIMEOUT.fullmatch(timeout):
        raise ValueError("timeout must be a positive Kubernetes duration such as 300s")
    multipliers = {"s": 1, "m": 60, "h": 3600}
    return float(int(timeout[:-1]) * multipliers[timeout[-1]])


def _run_rollout_status(command: tuple[str, ...], *, deadline: float) -> None:
    remaining = _remaining_seconds(deadline, before=command[-2])
    bounded_command = (*command[:-1], f"--timeout={max(1, math.ceil(remaining))}s")
    subprocess.run(bounded_command, check=True, timeout=remaining)


def _require_rollout_deadline(deadline: float, *, before: str) -> None:
    _remaining_seconds(deadline, before=before)


def _wait_for_rollout_status_wave(
    commands: tuple[tuple[str, ...], ...],
    *,
    deadline: float,
) -> None:
    failures: list[tuple[tuple[str, ...], Exception]] = []
    cancelled: list[tuple[str, ...]] = []
    with ThreadPoolExecutor(
        max_workers=len(commands),
        thread_name_prefix="rollout-status",
    ) as executor:
        futures = {
            executor.submit(_run_rollout_status, command, deadline=deadline): command
            for command in commands
        }
        for future in as_completed(futures):
            try:
                future.result()
            except CancelledError:
                cancelled.append(futures[future])
            except Exception as error:  # noqa: BLE001 - stop before submitting the next wave
                failures.append((futures[future], error))
                for pending in futures:
                    if pending is not future:
                        pending.cancel()

    if failures:
        failures.sort(key=lambda failure: failure[0])
        resources = sorted(
            [command[-2] for command, _error in failures] + [command[-2] for command in cancelled]
        )
        raise RuntimeError(f"rollout status failed for {resources!r}") from failures[0][1]
    if cancelled:
        resources = sorted(command[-2] for command in cancelled)
        raise RuntimeError(f"rollout status cancelled for {resources!r}")


def wait_for_rollout_statuses(
    commands: tuple[tuple[str, ...], ...],
    *,
    timeout: str,
    max_workers: int = MAX_ROLLOUT_STATUS_WORKERS,
    deadline: float | None = None,
) -> None:
    if not commands:
        return
    worker_count = min(MAX_ROLLOUT_STATUS_WORKERS, max_workers, len(commands))
    if worker_count < 1:
        raise ValueError("max_workers must be positive")

    duration = timeout_seconds(timeout)
    shared_deadline = deadline
    if shared_deadline is None:
        shared_deadline = time.monotonic() + duration
    for wave in bounded_waves(commands, size=worker_count):
        _wait_for_rollout_status_wave(wave, deadline=shared_deadline)


def _rollout_statuses_by_deployment(
    commands: tuple[tuple[str, ...], ...],
) -> dict[tuple[str, str], tuple[str, ...]]:
    statuses: dict[tuple[str, str], tuple[str, ...]] = {}
    for command in commands:
        identity = (command[4], command[-2])
        statuses.setdefault(identity, command)
    return statuses


def _status_commands_for_identities(
    statuses: dict[tuple[str, str], tuple[str, ...]],
    identities: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, ...], ...]:
    missing = sorted(set(identities) - set(statuses))
    if missing:
        raise RuntimeError(f"rollout status command is missing for {missing!r}")
    return tuple(statuses[identity] for identity in identities)


_SECRET_REF_FIELDS = {
    "secretRef": frozenset({"name", "optional"}),
    "secretKeyRef": frozenset({"name", "key", "optional"}),
    "secret": frozenset({"secretName", "optional", "defaultMode", "items"}),
}
_SENSITIVE_ENV_MARKERS = (
    "API_KEY",
    "AUTH",
    "CONNECTION",
    "CREDENTIAL",
    "DATABASE_URL",
    "DSN",
    "PASSWORD",
    "PRIVATE_KEY",
    "REDIS_URL",
    "SECRET",
    "TOKEN",
    "WEBHOOK_URL",
)
_SAFE_ANNOTATION_VALUE_KEYS = frozenset(
    {
        # Rollout identity and immutable content checksums remain useful evidence.
        "deployment.kubernetes.io/revision",
        "kubectl.kubernetes.io/restartedAt",
        "opsia.io/deployment-scope",
        "opsia.io/release-id",
        "opsia.io/release-sha",
        "opsia.io/rollout-id",
        "opsia.io/source-sha",
        "checksum/config",
        # These fixed Prometheus settings are public service-discovery metadata.
        "prometheus.io/path",
        "prometheus.io/port",
        "prometheus.io/scrape",
    }
)


def _sanitized_spec_evidence(value: Any, *, owner_key: str | None = None) -> Any:
    if isinstance(value, list):
        if owner_key in {"args", "command"}:
            return ["<redacted>" for _item in value]
        return [_sanitized_spec_evidence(item, owner_key=owner_key) for item in value]
    if not isinstance(value, dict):
        return copy.deepcopy(value)
    if owner_key == "annotations":
        return {
            str(key): (
                _sanitized_spec_evidence(item, owner_key=str(key))
                if str(key) in _SAFE_ANNOTATION_VALUE_KEYS
                else "<redacted>"
            )
            for key, item in value.items()
        }

    env_name = value.get("name")
    env_entry = isinstance(env_name, str) and "value" in value
    sensitive_name = isinstance(env_name, str) and any(
        marker in env_name.upper() for marker in _SENSITIVE_ENV_MARKERS
    )
    sanitized: dict[str, Any] = {}
    for key, item in value.items():
        allowed_ref_fields = _SECRET_REF_FIELDS.get(key)
        if allowed_ref_fields is not None and isinstance(item, dict):
            sanitized[key] = {
                field: _sanitized_spec_evidence(field_value, owner_key=field)
                for field, field_value in item.items()
                if field in allowed_ref_fields
            }
            continue
        if key == "value" and (env_entry or sensitive_name):
            sanitized[key] = "<redacted>"
            continue
        if key in {"data", "stringData"}:
            sanitized[key] = "<redacted>"
            continue
        if key == "imagePullSecrets" and isinstance(item, list):
            sanitized[key] = [
                {"name": entry.get("name")}
                for entry in item
                if isinstance(entry, dict) and isinstance(entry.get("name"), str)
            ]
            continue
        if any(marker in key.upper() for marker in _SENSITIVE_ENV_MARKERS):
            sanitized[key] = "<redacted>"
            continue
        sanitized[key] = _sanitized_spec_evidence(item, owner_key=key)
    return sanitized


def _live_deployment_specs(
    live_document: Any,
    *,
    identities: set[tuple[str, str]],
) -> dict[tuple[str, str], dict[str, Any]]:
    document = require_mapping(live_document, "live deployments")
    items = document.get("items")
    if not isinstance(items, list):
        raise ValueError("live deployments must contain an items list")
    by_resource: dict[str, set[str]] = {}
    for namespace, resource in identities:
        by_resource.setdefault(resource, set()).add(namespace)
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for index, raw_item in enumerate(items):
        item = require_mapping(raw_item, f"live deployments.items[{index}]")
        metadata = require_mapping(
            item.get("metadata"), f"live deployments.items[{index}].metadata"
        )
        name = metadata.get("name")
        if not isinstance(name, str):
            continue
        resource = f"deployment/{name}"
        matching_namespaces = by_resource.get(resource, set())
        if not matching_namespaces:
            continue
        namespace = metadata.get("namespace")
        if namespace is None and len(matching_namespaces) == 1:
            namespace = next(iter(matching_namespaces))
        identity = (namespace, resource)
        if identity not in identities:
            continue
        if identity in result:
            raise RuntimeError(f"duplicate live deployment spec: {identity!r}")
        spec = require_mapping(item.get("spec"), f"live deployment {identity!r}.spec")
        result[identity] = {
            "strategy": copy.deepcopy(spec.get("strategy", {})),
            "template": copy.deepcopy(
                require_mapping(spec.get("template"), f"live deployment {identity!r}.template")
            ),
        }
    missing = sorted(identities - set(result))
    if missing:
        raise RuntimeError(f"live deployment specs are missing for {missing!r}")
    return result


def spec_diff_document(
    plan: RollbackPlan,
    *,
    patches: tuple[tuple[str, str, list[dict[str, Any]]], ...],
    live_document: Any,
) -> dict[str, Any]:
    states = {(state.namespace, state.resource): state for state in plan.deployment_states}
    identities = {(namespace, resource) for namespace, resource, _patch in patches}
    live_specs = _live_deployment_specs(live_document, identities=identities)
    deployments: list[dict[str, Any]] = []
    for namespace, resource, patch in sorted(patches, key=lambda item: item[:2]):
        state = states[(namespace, resource)]
        captured = {"strategy": state.strategy, "template": state.template}
        before = live_specs[(namespace, resource)]
        if before != captured:
            raise RuntimeError(
                f"live deployment spec drifted after rollback capture: {namespace}/{resource}"
            )
        desired = {operation["path"]: operation["value"] for operation in patch}
        deployments.append(
            {
                "namespace": namespace,
                "resource": resource,
                "changed_paths": sorted(desired),
                "before": _sanitized_spec_evidence(before),
                "desired": _sanitized_spec_evidence(
                    {
                        "strategy": desired["/spec/strategy"],
                        "template": desired["/spec/template"],
                    }
                ),
            }
        )
    return {
        "version": 1,
        "previous_release_sha": plan.previous_release_sha,
        "capture": "fresh-live-before-mutation-verified-against-rollback-plan-v4",
        "deployments": deployments,
    }


def write_spec_diff_evidence(
    plan: RollbackPlan,
    *,
    patches: tuple[tuple[str, str, list[dict[str, Any]]], ...],
    live_document: Any,
    output: Path,
) -> None:
    document = spec_diff_document(plan, patches=patches, live_document=live_document)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write("\n")


def rollout(
    plan: RollbackPlan,
    *,
    context: str,
    image: str,
    timeout: str,
    manifest: Path | None = None,
    reconcile_existing_specs: bool = False,
    spec_diff_out: Path | None = None,
) -> None:
    if reconcile_existing_specs and manifest is None:
        raise ValueError("manifest is required when reconciling existing deployment specs")
    if reconcile_existing_specs and plan.targets and not plan.deployment_states:
        raise ValueError("deployment rollback states are required before spec reconciliation")
    if spec_diff_out is not None and not reconcile_existing_specs:
        raise ValueError("spec diff evidence requires spec reconciliation")
    commands = rollout_commands(plan, context=context, image=image, timeout=timeout)
    rollout_deadline = time.monotonic() + timeout_seconds(timeout)
    context_result = _run_with_deadline(
        commands[0],
        deadline=rollout_deadline,
        label="kubectl context validation",
        check=True,
        capture_output=True,
        text=True,
    )
    if context_result.stdout.strip() != context:
        raise RuntimeError(f"kubectl context was not found: {context}")
    targets = rollout_targets(plan)
    target_count = len(targets)
    set_image_commands = commands[1 : 1 + target_count]
    rollout_status_commands = commands[1 + target_count :]
    statuses_by_deployment = _rollout_statuses_by_deployment(rollout_status_commands)
    patches: tuple[tuple[str, str, list[dict[str, Any]]], ...] = ()
    if reconcile_existing_specs:
        assert manifest is not None
        patches = existing_deployment_spec_patches(plan, manifest=manifest, image=image)
    namespaces = {target.namespace for target in targets}
    if len(namespaces) != 1:
        raise ValueError("rollout plan must target exactly one namespace")
    namespace = next(iter(namespaces))
    live_before = live_deployments(
        context=context,
        namespace=namespace,
        deadline=rollout_deadline,
    )
    if plan.bootstrap_targets:
        verify_pre_rollout_state(plan, image=image, live_document=live_before)
    else:
        verify_repository_rollout(
            plan,
            image=image,
            live_document=live_before,
            require_exact_digest=False,
        )
    if spec_diff_out is not None:
        write_spec_diff_evidence(
            plan,
            patches=patches,
            live_document=live_before,
            output=spec_diff_out,
        )

    if plan.bootstrap_targets:
        create_bootstrap_deployments(
            plan,
            context=context,
            image=image,
            manifest=manifest,
            deadline=rollout_deadline,
        )
        live_before = live_deployments(
            context=context,
            namespace=namespace,
            deadline=rollout_deadline,
        )
        verify_repository_rollout(
            plan,
            image=image,
            live_document=live_before,
            require_exact_digest=False,
        )
    if reconcile_existing_specs:
        bootstrap_identities = tuple(
            dict.fromkeys((target.namespace, target.resource) for target in plan.bootstrap_targets)
        )
        if bootstrap_identities:
            wait_for_rollout_statuses(
                _status_commands_for_identities(statuses_by_deployment, bootstrap_identities),
                timeout=timeout,
                deadline=rollout_deadline,
            )
        for patch_wave in bounded_waves(patches, size=MAX_SPEC_PATCH_WORKERS):
            _require_rollout_deadline(rollout_deadline, before="deployment spec patch wave")
            _apply_existing_deployment_spec_wave(
                patch_wave,
                context=context,
                deadline=rollout_deadline,
            )
            identities = tuple((namespace, resource) for namespace, resource, _patch in patch_wave)
            wait_for_rollout_statuses(
                _status_commands_for_identities(statuses_by_deployment, identities),
                timeout=timeout,
                deadline=rollout_deadline,
            )
    else:
        mutation_waves = bounded_waves(
            tuple(zip(set_image_commands, rollout_status_commands, strict=True)),
            size=MAX_ROLLOUT_STATUS_WORKERS,
        )
        for mutation_wave in mutation_waves:
            _require_rollout_deadline(rollout_deadline, before="set-image wave")
            for set_image_command, _status_command in mutation_wave:
                _run_with_deadline(
                    set_image_command,
                    deadline=rollout_deadline,
                    label=f"set image {set_image_command[-2]}",
                    check=True,
                )
            wait_for_rollout_statuses(
                tuple(status_command for _set_image_command, status_command in mutation_wave),
                timeout=timeout,
                deadline=rollout_deadline,
            )
    count = verify_repository_rollout(
        plan,
        image=image,
        live_document=live_deployments(
            context=context,
            namespace=namespace,
            deadline=rollout_deadline,
        ),
        require_exact_digest=True,
    )
    print(f"verified {count} repository-matched deployment container(s) at {image}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Roll out one immutable service digest to a captured deployment set."
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--reconcile-existing-specs", action="store_true")
    parser.add_argument("--spec-diff-out", type=Path)
    parser.add_argument("--timeout", default="300s")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    plan = load_plan(args.plan)
    rollout(
        plan,
        context=args.context,
        image=args.image,
        timeout=args.timeout,
        manifest=args.manifest,
        reconcile_existing_specs=args.reconcile_existing_specs,
        spec_diff_out=args.spec_diff_out,
    )
    print(f"rolled out immutable digest to {len(rollout_targets(plan))} deployment container(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
