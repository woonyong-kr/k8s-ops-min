"""Declarative, sandbox-only RCA fault scenario catalog.

The catalog is repository-owned input.  A Bruno/API caller selects only a
``cluster_id`` and ``scenario_id``; it cannot submit a manifest, shell command,
namespace, or synthetic evidence.  Typed trigger adapters turn the selected
recipe into a bounded agent action later in the pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import Field, ValidationError, field_validator, model_validator

from packages.contracts.gateway.base import StrictModel

CATALOG_DIR = Path(__file__).resolve().parent / "test_scenario_catalog"
CATALOG_PATTERNS = ("*.yaml", "*.yml")
TEST_RESOURCE_LABEL = "kubeheal.io/rca-test"

ExecutionMode = Literal["real", "hybrid", "external"]
Availability = Literal[
    "ready",
    "verification_pending",
    "fixture_required",
    "detector_gap",
]
EvidenceSource = Literal["kubernetes", "metrics", "logs", "traces", "metadata"]
KubernetesFaultMode = Literal[
    "oom_killed",
    "config_env_error",
    "app_startup_failure",
    "bad_image_rollout",
    "dependency_connection_failure",
    "wrong_image_tag",
    "registry_unavailable",
    "registry_auth_denied",
    "upstream_empty",
    "readiness_probe_failure",
    "http_5xx",
    "dns_failure",
    "network_timeout",
    "insufficient_cpu",
    "insufficient_memory",
    "affinity_mismatch",
    "pvc_pending",
    "missing_secret",
    "missing_secret_key",
    "deployment_progress_deadline",
    "replica_unavailable",
]

CANONICAL_ROOT_CAUSES = frozenset(
    {
        "app_startup_failure",
        "application_5xx_spike",
        "backend_readiness_failure",
        "bad_image_rollout",
        "config_env_error",
        "database_connectivity_failure",
        "database_credential_or_config_error",
        "dependency_connection_failure",
        "deployment_progress_deadline_exceeded",
        "gitops_sync_failed",
        "insufficient_cpu",
        "insufficient_memory",
        "manifest_validation_failed",
        "missing_image_pull_secret",
        "missing_secret_reference",
        "network_path_timeout",
        "node_affinity_or_taint_mismatch",
        "oom_killed",
        "pvc_pending",
        "registry_unavailable",
        "replica_unavailable_after_rollout",
        "secret_key_missing",
        "service_dns_resolution_failure",
        "upstream_unavailable",
        "wrong_image_tag",
    }
)


class TestScenarioCatalogError(RuntimeError):
    """Fail-fast catalog load error shown during gateway startup/tests."""

    __test__ = False


class ScenarioExpected(StrictModel):
    root_cause: str = Field(min_length=1, max_length=120, pattern=r"^[a-z][a-z0-9_]*$")
    symptom: str = Field(min_length=1, max_length=160)


class ScenarioSafety(StrictModel):
    namespace: Literal["sandbox"]
    cleanup_required: Literal[True]
    ttl_seconds: int = Field(ge=30, le=900)
    management_cluster_allowed: Literal[False]
    resource_name_prefix: Literal["rca-test-"] = "rca-test-"
    max_concurrent_runs: int = Field(default=1, ge=1, le=3)


class KubernetesDeploymentTriggerParams(StrictModel):
    # Scenario별 고정 Deployment를 재사용한다. 매 run은 pod-template run label과
    # 잘못된 tag만 바꿔 누적 리소스를 만들지 않는다.
    resource_name: str = Field(
        min_length=10,
        max_length=63,
        pattern=r"^rca-test-[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$",
    )
    fault_mode: KubernetesFaultMode
    replicas: Literal[1] = 1
    labels: dict[str, str]
    image_repository: Literal["registry.k8s.io/pause"] | None = None
    image_tag_strategy: Literal["missing_run_suffix"] | None = None

    @field_validator("labels")
    @classmethod
    def _require_test_label(cls, labels: dict[str, str]) -> dict[str, str]:
        if labels.get(TEST_RESOURCE_LABEL) != "true":
            raise ValueError(f"labels must include {TEST_RESOURCE_LABEL}=true")
        return labels

    @model_validator(mode="after")
    def _validate_wrong_tag_recipe(self) -> KubernetesDeploymentTriggerParams:
        image_fields = (self.image_repository, self.image_tag_strategy)
        if self.fault_mode == "wrong_image_tag" and None in image_fields:
            raise ValueError("wrong_image_tag requires an allowlisted image/tag strategy")
        if self.fault_mode != "wrong_image_tag" and any(image_fields):
            raise ValueError("image fields are only allowed for wrong_image_tag")
        return self


class KubernetesDeploymentTrigger(StrictModel):
    adapter: Literal["kubernetes.deployment"]
    params: KubernetesDeploymentTriggerParams


class GitOpsFixtureTriggerParams(StrictModel):
    fixture_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9.-]*$")
    fault_mode: Literal["sync_failure", "manifest_invalid"]


class GitOpsFixtureTrigger(StrictModel):
    adapter: Literal["gitops.fixture"]
    params: GitOpsFixtureTriggerParams


class ExternalFixtureTriggerParams(StrictModel):
    fixture_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9.-]*$")
    fault_mode: Literal["database_connectivity", "database_credentials"]


class ExternalFixtureTrigger(StrictModel):
    adapter: Literal["external.fixture"]
    params: ExternalFixtureTriggerParams


ScenarioTrigger = Annotated[
    KubernetesDeploymentTrigger | GitOpsFixtureTrigger | ExternalFixtureTrigger,
    Field(discriminator="adapter"),
]


class ScenarioObservation(StrictModel):
    timeout_seconds: int = Field(ge=15, le=300)
    poll_seconds: int = Field(ge=1, le=10)
    pod_waiting_reasons: list[str] = Field(default_factory=list, max_length=10)
    pod_terminated_reasons: list[str] = Field(default_factory=list, max_length=10)
    event_reasons: list[str] = Field(default_factory=list, max_length=10)
    event_message_any: list[str] = Field(default_factory=list, max_length=20)
    log_message_any: list[str] = Field(default_factory=list, max_length=20)
    deployment_condition_reasons: list[str] = Field(default_factory=list, max_length=10)
    external_status_any: list[str] = Field(default_factory=list, max_length=10)

    @property
    def configured_predicates(self) -> tuple[str, ...]:
        return tuple(
            field
            for field in (
                "pod_waiting_reasons",
                "pod_terminated_reasons",
                "event_reasons",
                "event_message_any",
                "log_message_any",
                "deployment_condition_reasons",
                "external_status_any",
            )
            if getattr(self, field)
        )

    @model_validator(mode="after")
    def _require_typed_predicate(self) -> ScenarioObservation:
        if not self.configured_predicates:
            raise ValueError("observe must contain at least one typed predicate")
        return self


class KubernetesManifestDeleteCleanupParams(StrictModel):
    propagation_policy: Literal["Foreground"] = "Foreground"


class KubernetesManifestDeleteCleanup(StrictModel):
    adapter: Literal["kubernetes.manifest_delete"]
    params: KubernetesManifestDeleteCleanupParams


class FixtureResetCleanupParams(StrictModel):
    fixture_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9.-]*$")


class FixtureResetCleanup(StrictModel):
    adapter: Literal["fixture.reset"]
    params: FixtureResetCleanupParams


ScenarioCleanup = Annotated[
    KubernetesManifestDeleteCleanup | FixtureResetCleanup,
    Field(discriminator="adapter"),
]


class RcaTestScenario(StrictModel):
    scenario_id: str = Field(
        min_length=3,
        max_length=100,
        pattern=r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$",
    )
    version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=500)
    execution: ExecutionMode
    availability: Availability
    availability_reason: str | None = Field(default=None, max_length=500)
    verification_work_needed: list[str] = Field(default_factory=list, max_length=10)
    fixture_requirements: list[str] = Field(default_factory=list, max_length=10)
    detector_work_needed: list[str] = Field(default_factory=list, max_length=10)
    expected: ScenarioExpected
    evidence_sources: list[EvidenceSource] = Field(min_length=1, max_length=5)
    safety: ScenarioSafety
    trigger: ScenarioTrigger
    observe: ScenarioObservation
    cleanup: ScenarioCleanup

    @model_validator(mode="after")
    def _require_honest_availability_metadata(self) -> RcaTestScenario:
        if len(self.evidence_sources) != len(set(self.evidence_sources)):
            raise ValueError("evidence_sources must not contain duplicates")
        if self.availability == "fixture_required":
            if not self.availability_reason or not self.fixture_requirements:
                raise ValueError(
                    "fixture_required needs availability_reason and fixture_requirements"
                )
        elif self.availability == "detector_gap":
            if not self.availability_reason or not self.detector_work_needed:
                raise ValueError("detector_gap needs availability_reason and detector_work_needed")
        elif self.availability == "verification_pending":
            if not self.availability_reason or not self.verification_work_needed:
                raise ValueError(
                    "verification_pending needs availability_reason and verification_work_needed"
                )
        elif (
            self.verification_work_needed or self.fixture_requirements or self.detector_work_needed
        ):
            raise ValueError("ready scenario cannot declare unavailable-work metadata")

        if self.availability != "verification_pending" and self.verification_work_needed:
            raise ValueError("verification_work_needed is only valid for verification_pending")
        if self.availability != "fixture_required" and self.fixture_requirements:
            raise ValueError("fixture_requirements is only valid for fixture_required")
        if self.availability != "detector_gap" and self.detector_work_needed:
            raise ValueError("detector_work_needed is only valid for detector_gap")

        if isinstance(self.trigger, KubernetesDeploymentTrigger):
            if not isinstance(self.cleanup, KubernetesManifestDeleteCleanup):
                raise ValueError("kubernetes.deployment requires manifest_delete cleanup")
            if not self.trigger.params.resource_name.startswith(self.safety.resource_name_prefix):
                raise ValueError("test resource name must use the safety prefix")
        else:
            if not isinstance(self.cleanup, FixtureResetCleanup):
                raise ValueError("fixture trigger requires fixture.reset cleanup")
            if self.trigger.params.fixture_id != self.cleanup.params.fixture_id:
                raise ValueError("trigger and cleanup fixture_id must match")
        return self


class ScenarioCatalogDocument(StrictModel):
    scenarios: list[RcaTestScenario] = Field(min_length=1)


def parse_test_scenario_file(path: Path) -> tuple[RcaTestScenario, ...]:
    """Parse one catalog document and fail on YAML or schema errors."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise TestScenarioCatalogError(
            f"RCA test scenario YAML parse failed: {path.name} — {exc}"
        ) from exc
    try:
        document = ScenarioCatalogDocument.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors(include_url=False)[0]
        location = ".".join(str(part) for part in first["loc"])
        raise TestScenarioCatalogError(
            f"RCA test scenario schema invalid: {path.name}:{location} — {first['msg']}"
        ) from exc
    return tuple(document.scenarios)


def _catalog_paths(directory: Path) -> list[Path]:
    return sorted(path for pattern in CATALOG_PATTERNS for path in directory.glob(pattern))


def validate_root_cause_coverage(
    scenarios: tuple[RcaTestScenario, ...] | list[RcaTestScenario],
    required_root_causes: frozenset[str],
) -> None:
    """Require every canonical cause at least once without imposing catalog cardinality."""
    actual = {scenario.expected.root_cause for scenario in scenarios}
    missing = sorted(required_root_causes - actual)
    unexpected = sorted(actual - required_root_causes)
    if missing or unexpected:
        raise TestScenarioCatalogError(
            "RCA test scenario root-cause coverage mismatch: "
            f"missing={missing}, unexpected={unexpected}"
        )


def load_test_scenario_catalog(
    catalog_dir: Path | None = None,
) -> tuple[RcaTestScenario, ...]:
    """Load a deterministic catalog, rejecting empty, invalid, or duplicate input."""
    directory = CATALOG_DIR if catalog_dir is None else catalog_dir
    paths = _catalog_paths(directory)
    if not paths:
        raise TestScenarioCatalogError(f"RCA test scenario catalog is empty: {directory}")

    scenarios: list[RcaTestScenario] = []
    owners: dict[str, str] = {}
    for path in paths:
        for scenario in parse_test_scenario_file(path):
            previous = owners.get(scenario.scenario_id)
            if previous is not None:
                raise TestScenarioCatalogError(
                    "RCA test scenario_id duplicate: "
                    f"'{scenario.scenario_id}' ({previous} <-> {path.name})"
                )
            owners[scenario.scenario_id] = path.name
            scenarios.append(scenario)

    if catalog_dir is None or directory.resolve() == CATALOG_DIR.resolve():
        validate_root_cause_coverage(scenarios, CANONICAL_ROOT_CAUSES)
    return tuple(scenarios)


def test_scenario_by_id(
    scenario_id: str,
    catalog: tuple[RcaTestScenario, ...] | None = None,
) -> RcaTestScenario | None:
    scenarios = load_test_scenario_catalog() if catalog is None else catalog
    return next((scenario for scenario in scenarios if scenario.scenario_id == scenario_id), None)


def test_scenario_catalog_body() -> list[dict[str, Any]]:
    return [scenario.model_dump(mode="json") for scenario in load_test_scenario_catalog()]
