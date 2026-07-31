#!/usr/bin/env python3
"""BQ-009 recovery patch 6종을 production generator/materializer로 정적 채점한다."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) in sys.path:
    sys.path.remove(str(SCRIPT_DIR))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from domains.gitops.source_patch import (  # noqa: E402
    canonical_manifest_digest,
    materialize_scalar_patch,
    materialize_scalar_rollback,
    parse_scalar_patch_plan,
    parse_single_manifest,
)
from domains.rca.events import HealingActionDraft, RecoveryActionCandidate  # noqa: E402
from packages.contracts.gitops_authority import GitOpsAuthorityContext  # noqa: E402
from services.ai.agent.recovery.dispatch import authority_safe_pr_patches  # noqa: E402

BASE_SHA = "a" * 40
ACTIONS = (
    "oom_memory",
    "image_rollback",
    "image_tag_fix",
    "replica_scale",
    "probe_fix",
    "selector_fix",
)


def approved_source() -> str:
    return (
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "metadata:\n"
        "  name: checkout-api\n"
        "spec:\n"
        "  replicas: 2 # approved replica count\n"
        "  selector:\n"
        "    matchLabels:\n"
        "      app: checkout-old\n"
        "  template:\n"
        "    metadata:\n"
        "      labels:\n"
        "        app: checkout-api\n"
        "    spec:\n"
        "      containers:\n"
        "        - name: checkout-api\n"
        "          image: ghcr.io/project/checkout-api:v2\n"
        "          resources:\n"
        "            requests:\n"
        "              memory: 256Mi\n"
        "            limits:\n"
        "              memory: 512Mi\n"
        "          readinessProbe:\n"
        "            httpGet:\n"
        "              path: /health\n"
        "              port: 8080\n"
        "            timeoutSeconds: 1\n"
    )


def authority(source: str) -> GitOpsAuthorityContext:
    manifest = parse_single_manifest(source, "raw-yaml")
    return GitOpsAuthorityContext(
        workspace_id="workspace-1",
        repository_id="repo-1",
        binding_id="binding-1",
        application_id="app-1",
        workflow_run_id="workflow-1",
        environment="sandbox",
        cluster_id="cluster-1",
        manifest_path="deploy/app.yaml",
        repo_ref="project/repo",
        base_branch="main",
        commit_sha=BASE_SHA,
        source_type="raw-yaml",
        source_manifest_sha256=canonical_manifest_digest(manifest),
        resource="deployment/checkout-api",
        desired_manifest=manifest,
        changes=(
            {
                "field_path": "spec.template.spec.containers[name=checkout-api].image",
                "old_desired": "ghcr.io/project/checkout-api:v1",
                "new_desired": "ghcr.io/project/checkout-api:v2",
            },
        ),
        evidence={"metrics": {"container_memory_working_set_bytes": 600 * 1024**2}},
    )


def candidate(action_type: str) -> RecoveryActionCandidate:
    draft = HealingActionDraft(
        action_type=action_type,
        namespace="sandbox",
        resource_kind="Deployment",
        resource_name="checkout-api",
        reason="static scorer",
        risk_level="medium",
        dry_run=True,
        source_evidence=["static-fixture"],
        params={"root_cause": "static-fixture"},
    )
    return RecoveryActionCandidate(
        action_id=f"static:{action_type}",
        title=f"{action_type} patch",
        description="authority-pinned static scorer patch",
        draft=draft,
        route="draft_pr",
        rank=1,
        score=0.8,
        risk_level="medium",
        blast_radius="target_workload",
        approval_required=True,
        prerequisites=["approved snapshot"],
        validation_checks=["workload ready"],
        rollback_plan="use exact inverse rollback",
        evidence_refs=["static-fixture"],
    )


def verify_action(action_type: str, source: str) -> None:
    patches = authority_safe_pr_patches(candidate(action_type), authority(source))
    if len(patches) != 1:
        raise AssertionError("generator did not return exactly one structured patch")
    plan = parse_scalar_patch_plan(patches[0].content)
    if plan is None or plan.action_type != action_type:
        raise AssertionError("structured patch action does not match")
    patched = materialize_scalar_patch(source, plan)
    if patched == source:
        raise AssertionError("forward patch did not change the manifest")
    patched_manifest = parse_single_manifest(patched, "raw-yaml")
    restored = materialize_scalar_rollback(
        patched,
        plan,
        expected_source_sha256=canonical_manifest_digest(patched_manifest),
    )
    if restored != source:
        raise AssertionError("inverse rollback did not restore the exact source bytes")


def main() -> int:
    source = approved_source()
    passed = 0
    for action_type in ACTIONS:
        try:
            verify_action(action_type, source)
        except Exception as exc:
            print(f"[FAIL] {action_type}: {type(exc).__name__}: {exc}")
            continue
        passed += 1
        print(f"[PASS] {action_type}")
    summary = {"failed": len(ACTIONS) - passed, "passed": passed, "total": len(ACTIONS)}
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
