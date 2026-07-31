"""Fail closed when a rendered or live gateway enables development auth bypass."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

AUTH_BYPASS_ENV = "DEV_AUTH_BYPASS"
GATEWAY_DEPLOYMENT = "api-gateway"


def _metadata_name(document: Mapping[str, Any]) -> str:
    metadata = document.get("metadata")
    if not isinstance(metadata, Mapping):
        return ""
    return str(metadata.get("name") or "")


def _containers(document: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    spec = document.get("spec")
    if not isinstance(spec, Mapping):
        return []
    template = spec.get("template")
    if not isinstance(template, Mapping):
        return []
    pod_spec = template.get("spec")
    if not isinstance(pod_spec, Mapping):
        return []
    containers = pod_spec.get("containers")
    if not isinstance(containers, list):
        return []
    return [item for item in containers if isinstance(item, Mapping)]


def verify_deployments(documents: Sequence[Mapping[str, Any]], *, source: str) -> None:
    """Require a literal zero on the gateway and reject every nonzero declaration."""
    gateway_values: list[str] = []
    for document in documents:
        if document.get("kind") != "Deployment":
            continue
        deployment_name = _metadata_name(document)
        for container in _containers(document):
            env = container.get("env")
            if not isinstance(env, list):
                continue
            for item in env:
                if not isinstance(item, Mapping) or item.get("name") != AUTH_BYPASS_ENV:
                    continue
                value = item.get("value")
                container_name = str(container.get("name") or "unknown")
                if value != "0" or "valueFrom" in item:
                    raise RuntimeError(
                        f"{source}: {deployment_name}/{container_name} must set "
                        f"{AUTH_BYPASS_ENV}=0 as a literal"
                    )
                if deployment_name == GATEWAY_DEPLOYMENT:
                    gateway_values.append(value)

    if gateway_values != ["0"]:
        raise RuntimeError(
            f"{source}: {GATEWAY_DEPLOYMENT} must declare exactly one literal {AUTH_BYPASS_ENV}=0"
        )


def _rendered_documents(manifest: Path | None) -> list[Mapping[str, Any]]:
    if manifest is None:
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ["kubectl", "kustomize", str(root / "deploy" / "management")],
            check=True,
            capture_output=True,
            text=True,
        )
        content = result.stdout
    else:
        content = manifest.read_text(encoding="utf-8")
    return [item for item in yaml.safe_load_all(content) if isinstance(item, Mapping)]


def _live_documents(context: str, namespace: str) -> list[Mapping[str, Any]]:
    result = subprocess.run(
        [
            "kubectl",
            "--context",
            context,
            "-n",
            namespace,
            "get",
            "deployments.apps",
            "-o",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    items = payload.get("items") if isinstance(payload, Mapping) else None
    if not isinstance(items, list):
        raise RuntimeError("live deployment response is missing items")
    return [item for item in items if isinstance(item, Mapping)]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("rendered", "live"))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--context", default=os.getenv("MGMT_CONTEXT", ""))
    parser.add_argument("--namespace", default="management")
    args = parser.parse_args(argv)

    if args.mode == "rendered":
        documents = _rendered_documents(args.manifest)
        source = str(args.manifest or "rendered management manifest")
    else:
        if args.manifest is not None:
            parser.error("--manifest is only valid in rendered mode")
        if not args.context:
            parser.error("--context or MGMT_CONTEXT is required in live mode")
        documents = _live_documents(args.context, args.namespace)
        source = f"live {args.context}/{args.namespace}"

    verify_deployments(documents, source=source)
    print(f"auth bypass verification passed: {source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
