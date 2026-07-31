from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml
from revert_image_digests import (
    GIT_SHA,
    IMAGE_DIGEST,
    KUBERNETES_NAME,
    BootstrapTarget,
    DeploymentRollbackState,
    ProtectedTarget,
    RollbackPlan,
    RollbackTarget,
)


def require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def image_repository(image: str) -> str:
    """Return an image repository while excluding a tag or digest."""
    if "@" in image:
        repository, _, digest = image.partition("@")
        if not repository or not digest:
            raise ValueError("image reference is malformed")
        return repository
    last_slash = image.rfind("/")
    last_colon = image.rfind(":")
    if last_colon <= last_slash:
        raise ValueError("verified live image source must contain an explicit tag")
    return image[:last_colon]


def parse_verified_live_images(values: Sequence[str]) -> dict[str, str]:
    """Parse explicit live tag-to-digest attestations without accepting wildcards."""
    mappings: dict[str, str] = {}
    for value in values:
        source, separator, digest = value.partition("=")
        if (
            not separator
            or not source
            or not digest
            or any(character.isspace() for character in value)
        ):
            raise ValueError("verified live image must use TAG=DIGEST without whitespace")
        if source in mappings:
            raise ValueError(f"duplicate verified live image: {source}")
        if not IMAGE_DIGEST.fullmatch(digest):
            raise ValueError("verified live image target must use an immutable sha256 digest")
        if image_repository(source) != image_repository(digest):
            raise ValueError("verified live image tag and digest must use the same repository")
        mappings[source] = digest
    return mappings


def expected_deployment_containers(
    manifest: Path,
    *,
    managed_image: str | None = None,
    managed_repository: str | None = None,
) -> tuple[tuple[str, str], ...]:
    if managed_image is not None and managed_repository is not None:
        raise ValueError("managed_image and managed_repository are mutually exclusive")
    expected: list[tuple[str, str]] = []
    for index, value in enumerate(yaml.safe_load_all(manifest.read_text(encoding="utf-8"))):
        document = require_mapping(value, f"manifest document {index}")
        if document.get("kind") != "Deployment":
            continue
        metadata = require_mapping(document.get("metadata"), f"manifest document {index}.metadata")
        deployment = metadata.get("name")
        if not isinstance(deployment, str) or not KUBERNETES_NAME.fullmatch(deployment):
            raise ValueError(f"manifest document {index} has an invalid deployment name")
        spec = require_mapping(document.get("spec"), f"deployment/{deployment}.spec")
        template = require_mapping(spec.get("template"), f"deployment/{deployment}.template")
        pod_spec = require_mapping(template.get("spec"), f"deployment/{deployment}.podSpec")
        containers = pod_spec.get("containers")
        if not isinstance(containers, list) or not containers:
            raise ValueError(f"deployment/{deployment} must contain containers")
        for container_index, raw_container in enumerate(containers):
            container = require_mapping(
                raw_container, f"deployment/{deployment}.containers[{container_index}]"
            )
            name = container.get("name")
            if not isinstance(name, str) or not KUBERNETES_NAME.fullmatch(name):
                raise ValueError(f"deployment/{deployment} has an invalid container name")
            image = container.get("image")
            if managed_image is not None and image != managed_image:
                continue
            if managed_repository is not None:
                if not isinstance(image, str) or image_repository(image) != managed_repository:
                    continue
            expected.append((deployment, name))
    if not expected:
        raise ValueError("manifest must contain at least one Deployment container")
    if len(expected) != len(set(expected)):
        raise ValueError("manifest contains duplicate deployment containers")
    return tuple(expected)


def live_deployment_images(document: Any) -> dict[tuple[str, str], str]:
    root = require_mapping(document, "live deployments")
    items = root.get("items")
    if not isinstance(items, list):
        raise ValueError("live deployments.items must be a list")

    images: dict[tuple[str, str], str] = {}
    for index, raw_item in enumerate(items):
        item = require_mapping(raw_item, f"live deployments.items[{index}]")
        metadata = require_mapping(
            item.get("metadata"), f"live deployments.items[{index}].metadata"
        )
        name = metadata.get("name")
        if not isinstance(name, str):
            raise ValueError(f"live deployments.items[{index}] has no name")
        spec = require_mapping(item.get("spec"), f"deployment/{name}.spec")
        template = require_mapping(spec.get("template"), f"deployment/{name}.template")
        pod_spec = require_mapping(template.get("spec"), f"deployment/{name}.podSpec")
        containers = pod_spec.get("containers")
        if not isinstance(containers, list):
            raise ValueError(f"deployment/{name}.containers must be a list")
        for container_index, raw_container in enumerate(containers):
            container = require_mapping(
                raw_container, f"deployment/{name}.containers[{container_index}]"
            )
            container_name = container.get("name")
            image = container.get("image")
            if not isinstance(container_name, str) or not isinstance(image, str):
                raise ValueError(f"deployment/{name} container name and image must be strings")
            identity = (name, container_name)
            if identity in images:
                raise ValueError(f"live deployments contain duplicate container {identity}")
            images[identity] = image
    return images


def expected_repository_containers(
    live_document: Any, *, managed_repository: str
) -> tuple[tuple[str, str], ...]:
    if (
        not managed_repository
        or any(character.isspace() for character in managed_repository)
        or "@" in managed_repository
        or managed_repository.rfind(":") > managed_repository.rfind("/")
    ):
        raise ValueError("managed_repository must not contain a tag or digest")
    expected = tuple(
        identity
        for identity, image in live_deployment_images(live_document).items()
        if image_repository(image) == managed_repository
    )
    if not expected:
        raise ValueError("live deployments must contain at least one managed repository container")
    return expected


def build_plan(
    *,
    expected: tuple[tuple[str, str], ...],
    live_document: Any,
    namespace: str,
    previous_release_sha: str,
    verified_live_images: Mapping[str, str] | None = None,
    managed_repository: str | None = None,
) -> RollbackPlan:
    if not KUBERNETES_NAME.fullmatch(namespace):
        raise ValueError("namespace is not a Kubernetes name")
    if not GIT_SHA.fullmatch(previous_release_sha):
        raise ValueError("previous_release_sha must be a full lowercase Git SHA")

    live_images = live_deployment_images(live_document)
    live_deployments = {deployment for deployment, _container in live_images}
    verified = dict(verified_live_images or {})
    used_attestations: set[str] = set()
    targets: list[RollbackTarget] = []
    bootstrap_targets: list[BootstrapTarget] = []
    protected_targets: list[ProtectedTarget] = []
    for deployment, container in expected:
        image = live_images.get((deployment, container))
        if image is None:
            if deployment not in live_deployments:
                bootstrap_targets.append(
                    BootstrapTarget(
                        namespace=namespace,
                        resource=f"deployment/{deployment}",
                        container=container,
                        state="not_present_before_rollout",
                    )
                )
                continue
            raise ValueError(
                f"existing live deployment container is missing: {deployment}/{container}"
            )
        if not IMAGE_DIGEST.fullmatch(image):
            live_tag = image
            image = verified.get(live_tag)
            if image is None:
                raise ValueError(
                    f"live deployment image is not digest-pinned: {deployment}/{container}"
                )
            used_attestations.add(live_tag)
        targets.append(
            RollbackTarget(
                namespace=namespace,
                resource=f"deployment/{deployment}",
                container=container,
                image=image,
            )
        )
    if managed_repository is not None:
        expected_identities = set(expected)
        for (deployment, container), observed_image in live_images.items():
            if (deployment, container) in expected_identities:
                continue
            if image_repository(observed_image) != managed_repository:
                continue
            image = observed_image
            if not IMAGE_DIGEST.fullmatch(image):
                image = verified.get(observed_image, "")
                if not image:
                    raise ValueError(
                        "protected live deployment image is not digest-pinned: "
                        f"{deployment}/{container}"
                    )
                used_attestations.add(observed_image)
            protected_targets.append(
                ProtectedTarget(
                    namespace=namespace,
                    resource=f"deployment/{deployment}",
                    container=container,
                    image=image,
                    state="outside_manifest_scope_before_rollout",
                )
            )
    unused_attestations = sorted(set(verified) - used_attestations)
    if unused_attestations:
        raise ValueError(f"verified live image was not observed: {unused_attestations[0]}")
    if not targets and not bootstrap_targets and not protected_targets:
        raise ValueError("no managed deployment container was captured")
    live_items = require_mapping(live_document, "live deployments").get("items")
    if not isinstance(live_items, list):
        raise ValueError("live deployments.items must be a list")
    deployments_by_name: dict[str, dict[str, Any]] = {}
    for index, raw_item in enumerate(live_items):
        item = require_mapping(raw_item, f"live deployments.items[{index}]")
        metadata = require_mapping(
            item.get("metadata"), f"live deployments.items[{index}].metadata"
        )
        name = metadata.get("name")
        if not isinstance(name, str) or name in deployments_by_name:
            raise ValueError("live deployments must have unique string names")
        deployments_by_name[name] = item
    deployment_states: list[DeploymentRollbackState] = []
    for resource in sorted({target.resource for target in targets}):
        name = resource.removeprefix("deployment/")
        deployment = deployments_by_name[name]
        spec = require_mapping(deployment.get("spec"), f"deployment/{name}.spec")
        template = require_mapping(spec.get("template"), f"deployment/{name}.template")
        strategy = require_mapping(spec.get("strategy", {}), f"deployment/{name}.strategy")
        deployment_states.append(
            DeploymentRollbackState(
                namespace=namespace,
                resource=resource,
                strategy=copy.deepcopy(strategy),
                template=copy.deepcopy(template),
            )
        )
    return RollbackPlan(
        previous_release_sha=previous_release_sha,
        targets=tuple(targets),
        bootstrap_targets=tuple(bootstrap_targets),
        protected_targets=tuple(protected_targets),
        deployment_states=tuple(deployment_states),
    )


def write_plan(path: Path, plan: RollbackPlan) -> None:
    document = {
        "version": 4,
        "previous_release_sha": plan.previous_release_sha,
        "targets": [asdict(target) for target in plan.targets],
        "bootstrap_targets": [asdict(target) for target in plan.bootstrap_targets],
        "protected_targets": [asdict(target) for target in plan.protected_targets],
        "deployment_states": [asdict(state) for state in plan.deployment_states],
    }
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write("\n")


def capture(
    *,
    context: str,
    namespace: str,
    previous_release_sha: str,
    output: Path,
    manifest: Path | None = None,
    managed_image: str | None = None,
    managed_repository: str | None = None,
    verified_live_images: Mapping[str, str] | None = None,
) -> RollbackPlan:
    if not context or any(character.isspace() for character in context):
        raise ValueError("context must be a non-empty name without whitespace")
    context_result = subprocess.run(
        ("kubectl", "config", "get-contexts", context, "-o", "name"),
        check=True,
        capture_output=True,
        text=True,
    )
    if context_result.stdout.strip() != context:
        raise RuntimeError(f"kubectl context was not found: {context}")
    live_result = subprocess.run(
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
    )
    live_document = json.loads(live_result.stdout)
    if managed_repository is not None and manifest is not None:
        expected = expected_deployment_containers(
            manifest,
            managed_repository=managed_repository,
        )
    elif managed_repository is not None:
        expected = expected_repository_containers(
            live_document,
            managed_repository=managed_repository,
        )
    else:
        if manifest is None or managed_image is None:
            raise ValueError("manifest and managed_image are required without managed_repository")
        expected = expected_deployment_containers(manifest, managed_image=managed_image)
    plan = build_plan(
        expected=expected,
        live_document=live_document,
        namespace=namespace,
        previous_release_sha=previous_release_sha,
        verified_live_images=verified_live_images,
        managed_repository=managed_repository,
    )
    write_plan(output, plan)
    return plan


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture digest-pinned live Deployments as a fail-closed rollback plan."
    )
    parser.add_argument("--context", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--manifest", type=Path)
    managed = parser.add_mutually_exclusive_group(required=True)
    managed.add_argument("--managed-image")
    managed.add_argument("--managed-repository")
    parser.add_argument("--previous-release-sha", required=True)
    parser.add_argument(
        "--verified-live-image",
        action="append",
        default=[],
        metavar="TAG=DIGEST",
        help="Allow one exact observed mutable tag after attesting its immutable digest.",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    plan = capture(
        context=args.context,
        namespace=args.namespace,
        previous_release_sha=args.previous_release_sha,
        output=args.output,
        manifest=args.manifest,
        managed_image=args.managed_image,
        managed_repository=args.managed_repository,
        verified_live_images=parse_verified_live_images(args.verified_live_image),
    )
    print(
        f"captured {len(plan.targets)} digest-pinned deployment container(s); "
        f"recorded {len(plan.bootstrap_targets)} bootstrap target(s)"
        f"; protected {len(plan.protected_targets)} outside-manifest target(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
