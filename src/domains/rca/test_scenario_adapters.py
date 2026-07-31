"""Execution adapter registry for repository-owned RCA test scenarios."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

from domains.rca.test_scenarios import RcaTestScenario
from packages.contracts.event_bus.interfaces import JsonObject


class TestScenarioAdapterError(RuntimeError):
    """The scenario references an unknown or incompatible execution adapter."""


@dataclass(frozen=True)
class RcaTestFixtureTarget:
    namespace: str
    resource_name: str


@dataclass(frozen=True)
class RcaTestCleanupResource:
    kind: str
    api_prefix: str
    plural: str

    def url(self, base_url: str, namespace: str, resource_name: str) -> str:
        return f"{base_url}/{self.api_prefix}/namespaces/{namespace}/{self.plural}/{resource_name}"


@dataclass(frozen=True)
class RcaTestCleanupPlan:
    adapter: str
    propagation_policy: str
    resources: tuple[RcaTestCleanupResource, ...]


@dataclass(frozen=True)
class ScenarioAdapterCapabilities:
    trigger: bool
    fault_modes: frozenset[str]
    observation: bool
    observation_predicates: frozenset[str]
    cleanup: bool


FixtureTargetBuilder = Callable[[RcaTestScenario], RcaTestFixtureTarget]
TriggerBuilder = Callable[[RcaTestScenario, str, str], list[JsonObject]]
ObservationMatcher = Callable[[RcaTestScenario, JsonObject, str], bool]
CleanupBuilder = Callable[[str, str], RcaTestCleanupPlan]


@dataclass(frozen=True)
class ScenarioExecutionAdapter:
    trigger_adapter: str
    cleanup_adapter: str
    capabilities: ScenarioAdapterCapabilities
    fixture_target_builder: FixtureTargetBuilder | None = None
    trigger_builder: TriggerBuilder | None = None
    observation_matcher: ObservationMatcher | None = None
    cleanup_builder: CleanupBuilder | None = None

    def fixture_target(self, scenario: RcaTestScenario) -> RcaTestFixtureTarget:
        if self.fixture_target_builder is None:
            raise TestScenarioAdapterError(
                f"RCA test trigger adapter is not executable: {self.trigger_adapter}"
            )
        return self.fixture_target_builder(scenario)

    def build_trigger(
        self,
        scenario: RcaTestScenario,
        run_id: str,
        expires_at: str,
    ) -> list[JsonObject]:
        if self.trigger_builder is None:
            raise TestScenarioAdapterError(
                f"RCA test trigger adapter is not executable: {self.trigger_adapter}"
            )
        return self.trigger_builder(scenario, run_id, expires_at)

    def matches_observation(
        self,
        scenario: RcaTestScenario,
        snapshot: JsonObject,
        run_id: str,
    ) -> bool:
        if self.observation_matcher is None:
            raise TestScenarioAdapterError(
                f"RCA test observation adapter is not executable: {self.trigger_adapter}"
            )
        return self.observation_matcher(scenario, snapshot, run_id)

    def build_cleanup(self, namespace: str, resource_name: str) -> RcaTestCleanupPlan:
        if self.cleanup_builder is None:
            raise TestScenarioAdapterError(
                f"RCA test cleanup adapter is not executable: {self.cleanup_adapter}"
            )
        return self.cleanup_builder(namespace, resource_name)


class TestScenarioAdapterRegistry:
    def __init__(self, adapters: tuple[ScenarioExecutionAdapter, ...]) -> None:
        self._by_trigger: dict[str, ScenarioExecutionAdapter] = {}
        self._by_cleanup: dict[str, ScenarioExecutionAdapter] = {}
        for adapter in adapters:
            if adapter.trigger_adapter in self._by_trigger:
                raise TestScenarioAdapterError(
                    f"RCA test trigger adapter duplicate: {adapter.trigger_adapter}"
                )
            self._by_trigger[adapter.trigger_adapter] = adapter
            self._by_cleanup.setdefault(adapter.cleanup_adapter, adapter)

    def adapter_for(self, scenario: RcaTestScenario) -> ScenarioExecutionAdapter:
        adapter = self._by_trigger.get(scenario.trigger.adapter)
        if adapter is None:
            raise TestScenarioAdapterError(
                f"RCA test trigger adapter is not registered: {scenario.trigger.adapter}"
            )
        if scenario.cleanup.adapter != adapter.cleanup_adapter:
            raise TestScenarioAdapterError(
                "RCA test trigger/cleanup adapter pair is invalid: "
                f"{scenario.trigger.adapter} -> {scenario.cleanup.adapter}"
            )
        return adapter

    def cleanup_adapter(self, cleanup_adapter: str) -> ScenarioExecutionAdapter:
        adapter = self._by_cleanup.get(cleanup_adapter)
        if adapter is None:
            raise TestScenarioAdapterError(
                f"RCA test cleanup adapter is not registered: {cleanup_adapter}"
            )
        return adapter


def _unavailable_adapter(trigger: str, cleanup: str) -> ScenarioExecutionAdapter:
    return ScenarioExecutionAdapter(
        trigger_adapter=trigger,
        cleanup_adapter=cleanup,
        capabilities=ScenarioAdapterCapabilities(
            trigger=False,
            fault_modes=frozenset(),
            observation=False,
            observation_predicates=frozenset(),
            cleanup=False,
        ),
    )


@lru_cache(maxsize=1)
def default_test_scenario_adapter_registry() -> TestScenarioAdapterRegistry:
    from domains.rca.test_scenario_kubernetes import KUBERNETES_DEPLOYMENT_ADAPTER

    return TestScenarioAdapterRegistry(
        (
            KUBERNETES_DEPLOYMENT_ADAPTER,
            _unavailable_adapter("gitops.fixture", "fixture.reset"),
            _unavailable_adapter("external.fixture", "fixture.reset"),
        )
    )
