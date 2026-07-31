"""Kubernetes execution adapter for bounded RCA test fixtures."""

from __future__ import annotations

import re
from collections.abc import Callable

from domains.rca.test_scenario_adapters import (
    RcaTestCleanupPlan,
    RcaTestCleanupResource,
    RcaTestFixtureTarget,
    ScenarioAdapterCapabilities,
    ScenarioExecutionAdapter,
)
from domains.rca.test_scenarios import KubernetesDeploymentTrigger, RcaTestScenario
from packages.config.constants import Sandbox
from packages.contracts.event_bus.interfaces import JsonObject

RCA_TEST_RUN_ANNOTATION = "kubeheal.io/rca-test-run"
RCA_TEST_EXPIRES_AT_ANNOTATION = "kubeheal.io/rca-test-expires-at"
RCA_TEST_RESOURCE_LABEL = "kubeheal.io/rca-test"
RCA_TEST_RUN_LABEL = "kubeheal.io/rca-test-run"
RCA_TEST_RESOURCE_NAME_PATTERN = re.compile(r"^rca-test-[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
KUBERNETES_OBSERVATION_PREDICATES = frozenset(
    {
        "pod_waiting_reasons",
        "pod_terminated_reasons",
        "event_reasons",
        "event_message_any",
    }
)


def validate_rca_test_fixture_target(namespace: str, resource_name: str) -> None:
    if namespace != Sandbox.NAMESPACE:
        raise ValueError("RCA test fixture namespace must be sandbox")
    if len(resource_name) > 63 or RCA_TEST_RESOURCE_NAME_PATTERN.fullmatch(resource_name) is None:
        raise ValueError("invalid RCA test fixture resource name")


def kubernetes_fixture_target(scenario: RcaTestScenario) -> RcaTestFixtureTarget:
    if not isinstance(scenario.trigger, KubernetesDeploymentTrigger):
        raise ValueError("RCA test scenario does not have a Kubernetes fixture target")
    target = RcaTestFixtureTarget(
        namespace=scenario.safety.namespace,
        resource_name=scenario.trigger.params.resource_name,
    )
    validate_rca_test_fixture_target(target.namespace, target.resource_name)
    return target


def rca_test_fixture_owned_by_run(resource: JsonObject, run_id: str) -> bool:
    metadata = resource.get("metadata")
    if not isinstance(metadata, dict):
        return False
    annotations = metadata.get("annotations")
    return isinstance(annotations, dict) and annotations.get(RCA_TEST_RUN_ANNOTATION) == run_id


ManifestMutator = Callable[[JsonObject, JsonObject, str], None]


def _wrong_image_tag(container: JsonObject, _pod_spec: JsonObject, short_run: str) -> None:
    container["image"] = f"registry.k8s.io/pause:rca-test-missing-{short_run}"
    container.pop("command", None)


def _registry_unavailable(container: JsonObject, _pod_spec: JsonObject, short_run: str) -> None:
    container["image"] = f"127.0.0.1:65534/rca-test/unavailable:{short_run}"
    container.pop("command", None)


def _oom_killed(container: JsonObject, _pod_spec: JsonObject, _short_run: str) -> None:
    container["command"] = ["/bin/sh", "-c", "echo RCA_TEST_OOM; sleep 2; tail /dev/zero"]


def _config_env_error(container: JsonObject, _pod_spec: JsonObject, _short_run: str) -> None:
    container["command"] = [
        "/bin/sh",
        "-c",
        "echo 'FATAL: required environment variable DATABASE_URL is not set'; exit 1",
    ]


def _startup_failure(container: JsonObject, _pod_spec: JsonObject, _short_run: str) -> None:
    container["command"] = ["/bin/sh", "-c", "echo 'FATAL: startup failed'; exit 1"]


def _dependency_failure(container: JsonObject, _pod_spec: JsonObject, _short_run: str) -> None:
    container["command"] = ["/bin/sh", "-c", "echo 'dependency connection refused'; exit 1"]


def _insufficient_cpu(container: JsonObject, _pod_spec: JsonObject, _short_run: str) -> None:
    container["resources"] = {"requests": {"cpu": "100000", "memory": "8Mi"}}


def _insufficient_memory(container: JsonObject, _pod_spec: JsonObject, _short_run: str) -> None:
    container["resources"] = {"requests": {"cpu": "10m", "memory": "100Ti"}}


def _affinity_mismatch(_container: JsonObject, pod_spec: JsonObject, _short_run: str) -> None:
    pod_spec["nodeSelector"] = {"kubeheal.io/rca-test-node": "absent"}


def _upstream_empty(container: JsonObject, _pod_spec: JsonObject, _short_run: str) -> None:
    container["command"] = ["/bin/sh", "-c", "httpd -f -p 8080"]
    container["ports"] = [{"name": "http", "containerPort": 8080}]


def _readiness_failure(container: JsonObject, _pod_spec: JsonObject, _short_run: str) -> None:
    container["command"] = [
        "/bin/sh",
        "-c",
        "httpd -p 8080; while true; do echo 'ERROR readiness probe failed'; sleep 5; done",
    ]
    container["ports"] = [{"name": "http", "containerPort": 8080}]
    container["readinessProbe"] = {
        "httpGet": {"path": "/", "port": 9090},
        "initialDelaySeconds": 2,
        "periodSeconds": 3,
    }


def _http_5xx(container: JsonObject, _pod_spec: JsonObject, _short_run: str) -> None:
    container["image"] = "python:3.13-alpine"
    container["command"] = [
        "python",
        "-c",
        (
            "from http.server import BaseHTTPRequestHandler,HTTPServer;"
            "H=type('H',(BaseHTTPRequestHandler,),{"
            "'do_GET':lambda s:(s.send_response(500),s.end_headers(),"
            "s.wfile.write(b'intentional_error_endpoint'))});"
            "HTTPServer(('0.0.0.0',8080),H).serve_forever()"
        ),
    ]
    container["ports"] = [{"name": "http", "containerPort": 8080}]


KUBERNETES_MANIFEST_MUTATORS: dict[str, ManifestMutator] = {
    "wrong_image_tag": _wrong_image_tag,
    "registry_unavailable": _registry_unavailable,
    "oom_killed": _oom_killed,
    "config_env_error": _config_env_error,
    "app_startup_failure": _startup_failure,
    "bad_image_rollout": _startup_failure,
    "dependency_connection_failure": _dependency_failure,
    "insufficient_cpu": _insufficient_cpu,
    "insufficient_memory": _insufficient_memory,
    "affinity_mismatch": _affinity_mismatch,
    "upstream_empty": _upstream_empty,
    "readiness_probe_failure": _readiness_failure,
    "http_5xx": _http_5xx,
}
SERVICE_FAULT_MODES = frozenset({"readiness_probe_failure", "upstream_empty", "http_5xx"})


def build_kubernetes_manifests(
    scenario: RcaTestScenario,
    run_id: str,
    expires_at: str,
) -> list[JsonObject]:
    if scenario.availability not in {"ready", "verification_pending"} or not isinstance(
        scenario.trigger, KubernetesDeploymentTrigger
    ):
        raise ValueError(
            f"RCA test scenario cannot create a Kubernetes fixture: {scenario.scenario_id}"
        )
    params = scenario.trigger.params
    mutator = KUBERNETES_MANIFEST_MUTATORS.get(params.fault_mode)
    if mutator is None:
        raise ValueError(f"unsupported Kubernetes RCA test fault mode: {params.fault_mode}")

    name = params.resource_name
    labels = {
        "app": name,
        RCA_TEST_RESOURCE_LABEL: "true",
        RCA_TEST_RUN_LABEL: run_id,
    }
    annotations = {
        RCA_TEST_RUN_ANNOTATION: run_id,
        RCA_TEST_EXPIRES_AT_ANNOTATION: expires_at,
    }
    container: JsonObject = {
        "name": "fault",
        "image": "busybox:1.36.1",
        "command": ["/bin/sh", "-c", "sleep 3600"],
        "resources": {
            "requests": {"cpu": "10m", "memory": "8Mi"},
            "limits": {"cpu": "50m", "memory": "32Mi"},
        },
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
        },
    }
    pod_spec: JsonObject = {
        "automountServiceAccountToken": False,
        "containers": [container],
    }
    mutator(container, pod_spec, run_id.replace("-", "")[:12])

    deployment: JsonObject = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": name,
            "namespace": scenario.safety.namespace,
            "labels": labels,
            "annotations": annotations,
        },
        "spec": {
            "replicas": params.replicas,
            "selector": {"matchLabels": {"app": name}},
            "template": {
                "metadata": {"labels": dict(labels)},
                "spec": pod_spec,
            },
        },
    }
    manifests = [deployment]
    if params.fault_mode in SERVICE_FAULT_MODES:
        selector = (
            {"app": f"{name}-missing"} if params.fault_mode == "upstream_empty" else {"app": name}
        )
        manifests.append(
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {
                    "name": name,
                    "namespace": scenario.safety.namespace,
                    "labels": labels,
                    "annotations": annotations,
                },
                "spec": {
                    "selector": selector,
                    "ports": [{"name": "http", "port": 80, "targetPort": "http"}],
                },
            }
        )
    return manifests


def _run_scoped_snapshot(
    snapshot: JsonObject,
    run_id: str,
) -> tuple[list[JsonObject], list[JsonObject]]:
    raw_pods = snapshot.get("pods")
    pods = [
        pod
        for pod in (raw_pods if isinstance(raw_pods, list) else [])
        if isinstance(pod, dict)
        and isinstance(pod.get("labels"), dict)
        and pod["labels"].get(RCA_TEST_RUN_LABEL) == run_id
    ]
    pod_names = {str(pod.get("name") or "") for pod in pods}
    raw_events = snapshot.get("events")
    events = [
        item
        for item in (raw_events if isinstance(raw_events, list) else [])
        if isinstance(item, dict) and str(item.get("involved_name") or "") in pod_names
    ]
    return pods, events


def match_kubernetes_observation(
    scenario: RcaTestScenario,
    snapshot: JsonObject,
    run_id: str,
) -> bool:
    configured = set(scenario.observe.configured_predicates)
    unsupported = sorted(configured - KUBERNETES_OBSERVATION_PREDICATES)
    if unsupported:
        raise ValueError(
            f"unsupported Kubernetes RCA observation predicates: {', '.join(unsupported)}"
        )
    pods, events = _run_scoped_snapshot(snapshot, run_id)
    observe = scenario.observe

    def pod_values_match(field: str, expected_values: list[str]) -> bool:
        expected = set(expected_values)
        return any(
            expected.intersection(str(value) for value in pod.get(field, [])) for pod in pods
        )

    checks: dict[str, Callable[[], bool]] = {
        "pod_waiting_reasons": lambda: pod_values_match(
            "waiting_reasons", observe.pod_waiting_reasons
        ),
        "pod_terminated_reasons": lambda: pod_values_match(
            "terminated_reasons", observe.pod_terminated_reasons
        ),
        "event_reasons": lambda: any(
            str(item.get("reason") or "").casefold()
            in {value.casefold() for value in observe.event_reasons}
            for item in events
        ),
        "event_message_any": lambda: any(
            any(
                pattern.casefold() in str(item.get("message") or "").casefold()
                for pattern in observe.event_message_any
            )
            for item in events
        ),
    }
    return bool(configured) and all(checks[predicate]() for predicate in sorted(configured))


def build_kubernetes_cleanup_plan(
    namespace: str,
    resource_name: str,
) -> RcaTestCleanupPlan:
    validate_rca_test_fixture_target(namespace, resource_name)
    return RcaTestCleanupPlan(
        adapter="kubernetes.manifest_delete",
        propagation_policy="Foreground",
        resources=(
            RcaTestCleanupResource("Service", "api/v1", "services"),
            RcaTestCleanupResource("Deployment", "apis/apps/v1", "deployments"),
        ),
    )


KUBERNETES_DEPLOYMENT_ADAPTER = ScenarioExecutionAdapter(
    trigger_adapter="kubernetes.deployment",
    cleanup_adapter="kubernetes.manifest_delete",
    capabilities=ScenarioAdapterCapabilities(
        trigger=True,
        fault_modes=frozenset(KUBERNETES_MANIFEST_MUTATORS),
        observation=True,
        observation_predicates=KUBERNETES_OBSERVATION_PREDICATES,
        cleanup=True,
    ),
    fixture_target_builder=kubernetes_fixture_target,
    trigger_builder=build_kubernetes_manifests,
    observation_matcher=match_kubernetes_observation,
    cleanup_builder=build_kubernetes_cleanup_plan,
)
