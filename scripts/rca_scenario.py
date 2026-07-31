#!/usr/bin/env python3
"""Validate or scaffold repository-owned RCA test scenarios."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import get_args

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) in sys.path:
    sys.path.remove(str(SRC))
sys.path.insert(0, str(SRC))

from domains.rca.test_scenario_contract import (  # noqa: E402
    TestScenarioContractError,
    validate_scenario_cross_contracts,
    validate_test_scenario_catalog,
)
from domains.rca.test_scenarios import (  # noqa: E402
    CANONICAL_ROOT_CAUSES,
    CATALOG_DIR,
    KubernetesFaultMode,
    TestScenarioCatalogError,
    load_test_scenario_catalog,
    validate_root_cause_coverage,
)
from packages.ai.rule_catalog import CatalogRuleSpec, validate_catalog_yaml  # noqa: E402
from services.ai.agent.playbooks.recovery import (  # noqa: E402
    registered_recovery_rules,
)

CAUSE_CATALOG_DIR = ROOT / "src" / "services" / "ai" / "agent" / "causes" / "catalog"
DEFAULT_TESTS_DIR = ROOT / "tests"


def load_cause_rules(directory: Path) -> tuple[CatalogRuleSpec, ...]:
    paths = sorted(path for pattern in ("*.yaml", "*.yml") for path in directory.glob(pattern))
    if not paths:
        raise TestScenarioContractError(f"RCA cause catalog is empty: {directory}")
    rules: list[CatalogRuleSpec] = []
    owners: dict[str, str] = {}
    for path in paths:
        result = validate_catalog_yaml(path.read_text(encoding="utf-8"))
        if not result.valid:
            issue = result.errors[0]
            raise TestScenarioContractError(
                f"RCA cause catalog invalid: {path.name}: {issue.detail}"
            )
        for rule in result.rules:
            owner = owners.get(rule.rule_id)
            if owner is not None:
                raise TestScenarioContractError(
                    f"RCA cause rule id duplicate: {rule.rule_id} ({owner} <-> {path.name})"
                )
            owners[rule.rule_id] = path.name
            rules.append(rule)
    return tuple(rules)


def recovery_root_causes() -> frozenset[str]:
    return frozenset(
        str(root_cause)
        for rule in registered_recovery_rules()
        for root_cause in getattr(rule, "root_causes", ())
    )


def validate_command(args: argparse.Namespace) -> int:
    scenario_dir = Path(args.catalog_dir).resolve()
    cause_dir = Path(args.cause_catalog_dir).resolve()
    validate_test_scenario_catalog(scenario_dir)
    scenarios = load_test_scenario_catalog(scenario_dir)
    validate_root_cause_coverage(scenarios, CANONICAL_ROOT_CAUSES)
    cause_rules = load_cause_rules(cause_dir)
    validate_scenario_cross_contracts(
        scenarios,
        cause_rules=cause_rules,
        recovery_root_causes=recovery_root_causes(),
    )
    print(f"scenario schema: valid ({len(scenarios)} scenarios)")
    print("adapter: valid")
    print("cause/evidence/recovery: valid")
    return 0


def _scenario_slug(scenario_id: str) -> str:
    return scenario_id.replace(".", "_").replace("-", "_")


def _resource_name(scenario_id: str) -> str:
    suffix = scenario_id.replace(".", "-").replace("_", "-")
    return f"rca-test-{suffix}"[:63].rstrip("-")


def _scaffold_document(args: argparse.Namespace) -> dict[str, object]:
    trigger_params: dict[str, object] = {
        "resource_name": _resource_name(args.scenario_id),
        "fault_mode": args.fault_mode,
        "replicas": 1,
        "labels": {"kubeheal.io/rca-test": "true"},
    }
    if args.fault_mode == "wrong_image_tag":
        trigger_params.update(
            {
                "image_repository": "registry.k8s.io/pause",
                "image_tag_strategy": "missing_run_suffix",
            }
        )
    return {
        "scenarios": [
            {
                "scenario_id": args.scenario_id,
                "version": 1,
                "title": args.scenario_id,
                "description": "검증 전 시나리오 골격입니다. 실제 장애 설명으로 교체해야 합니다.",
                "execution": "real",
                "availability": "verification_pending",
                "availability_reason": "Live end-to-end completion has not been verified.",
                "verification_work_needed": [
                    "Add deterministic fixture assertions.",
                    "Verify real evidence, RCA, recovery, and cleanup end to end.",
                ],
                "expected": {
                    "root_cause": args.root_cause,
                    "symptom": args.symptom,
                },
                "evidence_sources": ["kubernetes"],
                "safety": {
                    "namespace": "sandbox",
                    "cleanup_required": True,
                    "ttl_seconds": 300,
                    "management_cluster_allowed": False,
                    "resource_name_prefix": "rca-test-",
                    "max_concurrent_runs": 1,
                },
                "trigger": {
                    "adapter": "kubernetes.deployment",
                    "params": trigger_params,
                },
                "observe": {
                    "timeout_seconds": 90,
                    "poll_seconds": 2,
                    "event_message_any": ["실제 관측 이벤트 문자열로 교체해야 합니다"],
                },
                "cleanup": {
                    "adapter": "kubernetes.manifest_delete",
                    "params": {"propagation_policy": "Foreground"},
                },
            }
        ]
    }


def _fixture_test_text(scenario_id: str, yaml_path: Path, test_path: Path) -> str:
    relative_yaml = Path(os.path.relpath(yaml_path, test_path.parent)).as_posix()
    return f'''"""Fixture contract scaffold for {scenario_id}."""

from pathlib import Path

from domains.rca.test_scenarios import parse_test_scenario_file


SCENARIO_FILE = Path(__file__).resolve().parent / "{relative_yaml}"


def test_{_scenario_slug(scenario_id)}_stays_pending_until_live_completion() -> None:
    scenario = parse_test_scenario_file(SCENARIO_FILE)[0]

    assert scenario.scenario_id == "{scenario_id}"
    assert scenario.availability == "verification_pending"
    assert scenario.verification_work_needed
    assert scenario.cleanup.adapter == "kubernetes.manifest_delete"
'''


def scaffold_command(args: argparse.Namespace) -> int:
    catalog_dir = Path(args.catalog_dir).resolve()
    tests_dir = Path(args.tests_dir).resolve()
    slug = _scenario_slug(args.scenario_id)
    yaml_path = catalog_dir / f"{slug}.yaml"
    test_path = tests_dir / f"test_rca_scenario_{slug}.py"
    existing = [path for path in (yaml_path, test_path) if path.exists()]
    if existing:
        joined = ", ".join(str(path) for path in existing)
        raise FileExistsError(f"refusing to overwrite; already exists: {joined}")

    catalog_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)
    yaml_path.write_text(
        yaml.safe_dump(_scaffold_document(args), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    test_path.write_text(
        _fixture_test_text(args.scenario_id, yaml_path, test_path),
        encoding="utf-8",
    )
    print(yaml_path)
    print(test_path)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate all RCA scenario contracts")
    validate.add_argument("--catalog-dir", default=str(CATALOG_DIR))
    validate.add_argument("--cause-catalog-dir", default=str(CAUSE_CATALOG_DIR))
    validate.set_defaults(handler=validate_command)

    scaffold = subparsers.add_parser("scaffold", help="create an unverified scenario skeleton")
    scaffold.add_argument("scenario_id")
    scaffold.add_argument("--root-cause", required=True)
    scaffold.add_argument("--symptom", required=True)
    scaffold.add_argument(
        "--fault-mode", choices=sorted(get_args(KubernetesFaultMode)), required=True
    )
    scaffold.add_argument("--catalog-dir", default=str(CATALOG_DIR))
    scaffold.add_argument("--tests-dir", default=str(DEFAULT_TESTS_DIR))
    scaffold.set_defaults(handler=scaffold_command)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return int(args.handler(args))
    except (FileExistsError, OSError, TestScenarioCatalogError, TestScenarioContractError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
