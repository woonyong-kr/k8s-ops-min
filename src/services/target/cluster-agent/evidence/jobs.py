from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Protocol

from queries import TelemetryQueryDefinition, compile_policy_query_definition
from telemetry_registry import telemetry

from packages.config.constants import CommandStatus
from packages.config.logs import CONTEXT_KEY, get_logger
from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.gateway.fields import Gateway
from packages.contracts.interfaces import ManagementPlaneClient

DEFAULT_JOB_POLL_SECONDS = 1.0
DEFAULT_JOB_POLL_TIMEOUT_SECONDS = 10
ALLOW_PARTIAL_FAILURE_POLICY = "allow_partial"
STRICT_FAILURE_POLICY = "strict"
RCA_TEST_EVIDENCE_SCOPE = "rca_test_run"
LOGGER = get_logger(__name__)


class EvidenceSource(Protocol):
    """Describe the collector interface used by evidence jobs."""

    async def collect(self, *evidence_keys: str) -> JsonObject:
        """Collect evidence for selected provider keys."""
        ...


class EvidenceJobScheduler:
    """Schedule, poll, run, and report provider evidence jobs."""

    def __init__(
        self,
        *,
        cluster_id: str,
        workspace_id: str,
        agent_id: str,
        source_id: str,
        collector: EvidenceSource,
        provider_keys: tuple[str, ...],
        provider_worker_counts: Mapping[str, int],
        interval_seconds: int,
    ) -> None:
        """Store scheduler config and prepare worker state."""
        self.cluster_id = cluster_id
        self.workspace_id = workspace_id
        self.agent_id = agent_id
        self.source_id = source_id
        self.collector = collector
        self.provider_keys = provider_keys
        self.provider_worker_counts = {
            provider_key: max(0, provider_worker_counts.get(provider_key, 1))
            for provider_key in provider_keys
        }
        self.provider_intervals = {
            provider_key: max(1, interval_seconds) for provider_key in provider_keys
        }
        self.enabled_provider_keys = set(provider_keys)
        self.next_provider_runs = {provider_key: 0.0 for provider_key in provider_keys}
        self.interval_seconds = interval_seconds
        self._schedule_revision = 0
        self._client: ManagementPlaneClient | None = None
        self._worker_tasks: dict[str, list[asyncio.Task[None]]] = {
            provider_key: [] for provider_key in provider_keys
        }
        self._worker_serials = {provider_key: 0 for provider_key in provider_keys}
        self._workers_started = False

    async def run(self, client: ManagementPlaneClient) -> None:
        """Run provider workers and the schedule loop until cancelled."""
        self._client = client
        self._workers_started = True
        self.reconcile_worker_pools(client)
        try:
            await self.schedule_forever(client)
        finally:
            self._workers_started = False
            self._client = None
            await self.stop_workers()

    async def schedule_forever(self, client: ManagementPlaneClient) -> None:
        """Keep scheduling due evidence jobs in a loop."""
        while True:
            try:
                await self.schedule_once(client)
            except Exception as exc:
                LOGGER.warning(
                    "evidence_schedule_failed",
                    extra={CONTEXT_KEY: {"cluster_id": self.cluster_id}},
                    exc_info=exc,
                )
            await asyncio.sleep(1)

    async def schedule_once(
        self,
        client: ManagementPlaneClient,
        now: float | None = None,
    ) -> str | None:
        """Schedule one evidence window when any provider is due."""
        now = time.time() if now is None else now
        due_provider_keys = self.due_provider_keys(now)
        if not due_provider_keys:
            return None

        schedule_revision = self._schedule_revision
        window_start = self.new_window_start(now)
        response = await client.schedule_evidence_jobs(
            self.source_id,
            window_start,
            list(due_provider_keys),
        )
        if schedule_revision == self._schedule_revision:
            for provider_key in due_provider_keys:
                self.next_provider_runs[provider_key] = now + self.provider_intervals[provider_key]
        return str(response.get(Gateway.EVIDENCE_KEY) or window_start)

    def due_provider_keys(self, now: float) -> tuple[str, ...]:
        """Return enabled provider keys that are ready to run now."""
        return tuple(
            provider_key
            for provider_key in self.provider_keys
            if provider_key in self.enabled_provider_keys
            and now >= self.next_provider_runs[provider_key]
        )

    async def work_forever(
        self,
        client: ManagementPlaneClient,
        provider_key: str,
        worker_id: str,
    ) -> None:
        """Poll and process jobs for one provider forever."""
        while True:
            try:
                processed = await self.work_once(client, provider_key, worker_id)
            except Exception as exc:
                LOGGER.warning(
                    "evidence_worker_failed",
                    extra={CONTEXT_KEY: {"worker_id": worker_id, "provider_key": provider_key}},
                    exc_info=exc,
                )
                processed = False
            if not processed:
                await asyncio.sleep(DEFAULT_JOB_POLL_SECONDS)

    async def work_once(
        self,
        client: ManagementPlaneClient,
        provider_key: str,
        worker_id: str,
    ) -> bool:
        """Poll one provider job, run it, and report the result."""
        job = await client.poll_evidence_job(
            provider_key,
            self.agent_id,
            DEFAULT_JOB_POLL_TIMEOUT_SECONDS,
        )
        if job is None:
            return False

        job_id = str(job[Gateway.JOB_ID])
        lease_id = str(job[Gateway.LEASE_ID])
        try:
            result = await self.collect_job(job, provider_key)
        except Exception as exc:
            await client.complete_evidence_job(
                job_id,
                self.agent_id,
                lease_id,
                CommandStatus.FAILED,
                {},
                str(exc),
            )
            return True

        await client.complete_evidence_job(
            job_id,
            self.agent_id,
            lease_id,
            CommandStatus.COMPLETED,
            result,
            "",
        )
        return True

    async def collect_job(self, job: JsonObject, provider_key: str) -> JsonObject:
        """Collect evidence for one leased provider job."""
        definitions = self.job_query_definitions(job, provider_key)
        failure_policy = str(job.get(Gateway.FAILURE_POLICY) or ALLOW_PARTIAL_FAILURE_POLICY)
        if hasattr(self.collector, "collect_query_policy"):
            result = await self.collector.collect_query_policy(
                provider_key,
                definitions,
                failure_policy=failure_policy,
            )
        else:
            result = await self.collector.collect(provider_key)
        require_run_scoped_provider_evidence(job, provider_key, result)
        return result

    def job_query_definitions(
        self,
        job: JsonObject,
        provider_key: str,
    ) -> tuple[TelemetryQueryDefinition, ...]:
        """Build query definitions from the job provider policy."""
        source = telemetry.source_for_provider(provider_key)
        if source is None:
            return ()
        provider_policy = job.get(Gateway.PROVIDER_POLICY, {})
        if not isinstance(provider_policy, Mapping):
            return ()
        query_rows = provider_policy.get("queries", [])
        if not isinstance(query_rows, list):
            return ()
        definitions: list[TelemetryQueryDefinition] = []
        for query in query_rows:
            if not isinstance(query, Mapping):
                continue
            definitions.append(
                compile_policy_query_definition(
                    query,
                    source=source,
                    cluster_id=self.cluster_id,
                )
            )
        return tuple(definitions)

    def configure_schedule(
        self,
        *,
        provider_intervals: Mapping[str, int],
        enabled_provider_keys: set[str],
    ) -> None:
        """Update enabled providers and their schedule intervals."""
        unknown_keys = set(provider_intervals) - set(self.provider_keys)
        if unknown_keys:
            raise ValueError(f"unknown evidence providers in schedule: {sorted(unknown_keys)}")
        previous_enabled_provider_keys = set(self.enabled_provider_keys)
        previous_provider_intervals = dict(self.provider_intervals)
        next_enabled_provider_keys = {
            provider_key
            for provider_key in enabled_provider_keys
            if provider_key in self.provider_keys
        }
        for provider_key, interval_seconds in provider_intervals.items():
            self.provider_intervals[provider_key] = max(1, interval_seconds)
            self.next_provider_runs.setdefault(provider_key, 0.0)
        schedule_changed = (
            next_enabled_provider_keys != previous_enabled_provider_keys
            or any(
                previous_provider_intervals.get(provider_key)
                != self.provider_intervals.get(provider_key)
                for provider_key in next_enabled_provider_keys
            )
        )
        self.enabled_provider_keys = next_enabled_provider_keys
        if schedule_changed:
            self.align_enabled_provider_runs()

    def register_provider(
        self,
        provider_key: str,
        *,
        worker_count: int,
        interval_seconds: int,
        enabled: bool,
    ) -> None:
        """Register or update one provider added by a revision-bound runtime integration."""
        if not provider_key.strip():
            raise ValueError("evidence provider key is required")
        previous_enabled = provider_key in self.enabled_provider_keys
        previous_interval = self.provider_intervals.get(provider_key)
        if provider_key not in self.provider_keys:
            self.provider_keys = (*self.provider_keys, provider_key)
            self._worker_tasks[provider_key] = []
            self._worker_serials[provider_key] = 0
        self.provider_worker_counts[provider_key] = max(0, worker_count)
        self.provider_intervals[provider_key] = max(1, interval_seconds)
        self.next_provider_runs.setdefault(provider_key, 0.0)
        if enabled:
            self.enabled_provider_keys.add(provider_key)
        else:
            self.enabled_provider_keys.discard(provider_key)
        if previous_enabled != enabled or (
            enabled and previous_interval != self.provider_intervals[provider_key]
        ):
            self.align_enabled_provider_runs()
        if self._client is not None:
            self.reconcile_worker_pool(provider_key, self._client)

    def align_enabled_provider_runs(self) -> None:
        """Make a changed provider set enter the next evidence window atomically.

        Provider workers may finish before another provider's independently scheduled job
        is inserted. Resetting all enabled due-times together makes the scheduler queue the
        full changed set in one transaction, so the first completed provider cannot seal a
        partial window that later results are unable to enrich.
        """
        self._schedule_revision += 1
        for provider_key in self.enabled_provider_keys:
            self.next_provider_runs[provider_key] = 0.0

    def unregister_provider(self, provider_key: str) -> None:
        """Disable and remove one runtime provider from future schedules."""
        if provider_key not in self.provider_keys:
            return
        self.enabled_provider_keys.discard(provider_key)
        self.provider_keys = tuple(key for key in self.provider_keys if key != provider_key)
        for task in self._worker_tasks.pop(provider_key, []):
            task.cancel()
        self._worker_serials.pop(provider_key, None)
        self.provider_worker_counts.pop(provider_key, None)
        self.provider_intervals.pop(provider_key, None)
        self.next_provider_runs.pop(provider_key, None)
        self.align_enabled_provider_runs()

    def set_worker_counts(
        self,
        provider_worker_counts: Mapping[str, int],
    ) -> None:
        """Update desired worker counts for each provider."""
        for provider_key in self.provider_keys:
            self.provider_worker_counts[provider_key] = max(
                0,
                provider_worker_counts.get(provider_key, self.provider_worker_counts[provider_key]),
            )
            if self._client is not None:
                self.reconcile_worker_pool(provider_key, self._client)

    def current_worker_counts(self) -> dict[str, int]:
        """Return desired or running worker counts by provider."""
        self.prune_finished_workers()
        if not self._workers_started:
            return dict(self.provider_worker_counts)
        return {
            provider_key: len(self._worker_tasks[provider_key])
            for provider_key in self.provider_keys
        }

    def reconcile_worker_pools(self, client: ManagementPlaneClient) -> None:
        """Make all provider worker pools match desired counts."""
        for provider_key in self.provider_keys:
            self.reconcile_worker_pool(provider_key, client)

    def reconcile_worker_pool(
        self,
        provider_key: str,
        client: ManagementPlaneClient,
    ) -> None:
        """Start or stop workers for one provider."""
        self.prune_finished_workers()
        if not self._workers_started:
            return

        tasks = self._worker_tasks[provider_key]
        desired_count = self.provider_worker_counts[provider_key]
        while len(tasks) < desired_count:
            worker_id = self.next_worker_id(provider_key)
            task = asyncio.create_task(
                self.work_forever(client, provider_key, worker_id),
                name=f"evidence-{worker_id}",
            )
            tasks.append(task)

        while len(tasks) > desired_count:
            task = tasks.pop()
            task.cancel()

    def prune_finished_workers(self) -> None:
        """Remove completed worker tasks from local state."""
        for provider_key, tasks in self._worker_tasks.items():
            self._worker_tasks[provider_key] = [task for task in tasks if not task.done()]

    async def stop_workers(self) -> None:
        """Cancel all running provider worker tasks."""
        tasks = [task for provider_tasks in self._worker_tasks.values() for task in provider_tasks]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for provider_key in self.provider_keys:
            self._worker_tasks[provider_key] = []

    def next_worker_id(self, provider_key: str) -> str:
        """Return a new stable worker id for one provider."""
        worker_index = self._worker_serials[provider_key]
        self._worker_serials[provider_key] += 1
        return f"{provider_key}-worker-{worker_index}"

    def new_window_start(self, now: float) -> str:
        """Round a timestamp down to the current evidence window."""
        interval = max(1, self.interval_seconds)
        current = int(now)
        window_start = current - (current % interval)
        return datetime.fromtimestamp(window_start, UTC).isoformat()


def require_run_scoped_provider_evidence(
    job: JsonObject,
    provider_key: str,
    result: JsonObject,
) -> None:
    """Reject empty strict RCA test results without manufacturing evidence."""
    release_context = job_release_context(job)
    if (
        job.get(Gateway.FAILURE_POLICY) != STRICT_FAILURE_POLICY
        or release_context.get("evidence_scope") != RCA_TEST_EVIDENCE_SCOPE
    ):
        return
    if provider_has_actual_evidence(provider_key, result.get(provider_key), release_context):
        return
    raise RuntimeError(f"{provider_key} provider returned no evidence for strict run-scoped job")


def job_release_context(job: JsonObject) -> Mapping[str, object]:
    provider_policy = job.get(Gateway.PROVIDER_POLICY)
    if not isinstance(provider_policy, Mapping):
        return {}
    release_context = provider_policy.get("release_context")
    return release_context if isinstance(release_context, Mapping) else {}


def provider_has_actual_evidence(
    provider_key: str,
    payload: object,
    release_context: Mapping[str, object],
) -> bool:
    if provider_key == "kubernetes":
        return kubernetes_has_run_pod(payload, release_context)
    if provider_key == "metrics":
        return metrics_have_samples(payload)
    if provider_key == "logs":
        return logs_have_lines(payload)
    if provider_key == "traces":
        return traces_have_results(payload)
    if provider_key == "metadata":
        return metadata_has_context(payload, release_context)
    return False


def kubernetes_has_run_pod(
    payload: object,
    release_context: Mapping[str, object],
) -> bool:
    if not isinstance(payload, Mapping):
        return False
    pods = payload.get("pods")
    if not isinstance(pods, list):
        return False
    raw_expected_names = release_context.get("pod_names")
    expected_names = (
        {str(name).strip() for name in raw_expected_names if str(name).strip()}
        if isinstance(raw_expected_names, list)
        else set()
    )
    observed_names = {
        str(pod.get("name") or "").strip()
        for pod in pods
        if isinstance(pod, Mapping) and str(pod.get("name") or "").strip()
    }
    return bool(observed_names & expected_names) if expected_names else bool(observed_names)


def metrics_have_samples(payload: object) -> bool:
    if not isinstance(payload, Mapping):
        return False
    results = payload.get("results")
    if not isinstance(results, Mapping):
        return False
    for value in results.values():
        if not isinstance(value, Mapping):
            continue
        samples = value.get("samples")
        if isinstance(samples, list) and samples:
            return True
        series = value.get("series")
        if isinstance(series, list) and any(
            isinstance(item, Mapping) and bool(item.get("values")) for item in series
        ):
            return True
        raw_result = value.get("result")
        if raw_result not in (None, "", [], {}):
            return True
    return False


def logs_have_lines(payload: object) -> bool:
    if not isinstance(payload, list):
        return False
    for entry in payload:
        if not isinstance(entry, Mapping):
            continue
        if isinstance(entry.get("line_count"), int) and entry["line_count"] > 0:
            return True
        streams = entry.get("streams")
        if isinstance(streams, list) and any(
            isinstance(stream, Mapping) and bool(stream.get("values")) for stream in streams
        ):
            return True
    return False


def traces_have_results(payload: object) -> bool:
    if not isinstance(payload, Mapping):
        return False
    results = payload.get("results")
    if not isinstance(results, Mapping):
        return False
    return any(
        isinstance(value, Mapping)
        and (
            bool(value.get("traces"))
            or isinstance(value.get("trace_count"), int)
            and value["trace_count"] > 0
        )
        for value in results.values()
    )


def metadata_has_context(payload: object, release_context: Mapping[str, object]) -> bool:
    if not isinstance(payload, Mapping):
        return False
    change_context = payload.get("change_context")
    if not isinstance(change_context, Mapping):
        return False
    if not any(value not in (None, "", [], {}) for value in change_context.values()):
        return False
    namespace = str(release_context.get("namespace") or "").strip()
    if not namespace:
        return False
    if metadata_context_namespaces(change_context) != {namespace}:
        return False
    resource_name = str(release_context.get("resource_name") or "").strip()
    if not resource_name:
        return True
    resource_kind = str(release_context.get("resource_kind") or "Deployment").strip()
    return metadata_context_workload_identities(change_context) == {
        normalized_workload_identity(resource_kind, namespace, resource_name)
    }


def metadata_context_namespaces(change_context: Mapping[str, object]) -> set[str]:
    """Return namespaces found in metadata change context evidence."""
    namespaces: set[str] = set()
    add_snapshot_namespace(namespaces, change_context.get("current_workload_snapshot"))

    snapshots = change_context.get("current_workload_snapshots")
    if isinstance(snapshots, list):
        for snapshot in snapshots:
            add_snapshot_namespace(namespaces, snapshot)

    for key in ("service_selector_matches", "endpoint_slice_ready_endpoints"):
        values = change_context.get(key)
        if isinstance(values, list):
            for value in values:
                add_nested_metadata_namespaces(namespaces, value)

    for key in ("resource_quotas", "referenced_config_objects"):
        values = change_context.get(key)
        if isinstance(values, list):
            for value in values:
                add_resource_namespace(namespaces, value)

    return namespaces


def metadata_context_workload_identities(
    change_context: Mapping[str, object],
) -> set[tuple[str, str, str]]:
    """Return workload identities found in metadata snapshot evidence."""
    identities: set[tuple[str, str, str]] = set()
    add_snapshot_identity(identities, change_context.get("current_workload_snapshot"))

    snapshots = change_context.get("current_workload_snapshots")
    if isinstance(snapshots, list):
        for snapshot in snapshots:
            add_snapshot_identity(identities, snapshot)

    return identities


def add_snapshot_identity(identities: set[tuple[str, str, str]], value: object) -> None:
    """Add one workload identity from a metadata snapshot."""
    if not isinstance(value, Mapping):
        return
    workload = value.get("workload")
    if not isinstance(workload, Mapping):
        return
    kind = str(workload.get("kind") or "").strip()
    namespace = str(workload.get("namespace") or "").strip()
    name = str(workload.get("name") or "").strip()
    if kind and namespace and name:
        identities.add(normalized_workload_identity(kind, namespace, name))


def normalized_workload_identity(kind: str, namespace: str, name: str) -> tuple[str, str, str]:
    """Normalize one workload identity for strict RCA test matching."""
    return (kind.casefold(), namespace, name)


def add_snapshot_namespace(namespaces: set[str], value: object) -> None:
    """Add the workload namespace from one metadata snapshot."""
    if not isinstance(value, Mapping):
        return
    add_resource_namespace(namespaces, value.get("workload"))


def add_nested_metadata_namespaces(namespaces: set[str], value: object) -> None:
    """Add namespaces from nested Service, Pod, or EndpointSlice summaries."""
    if not isinstance(value, Mapping):
        return
    for key in ("service", "endpoint_slice"):
        add_resource_namespace(namespaces, value.get(key))
    for key in ("matched_pods", "ready_targets"):
        resources = value.get(key)
        if isinstance(resources, list):
            for resource in resources:
                add_resource_namespace(namespaces, resource)


def add_resource_namespace(namespaces: set[str], value: object) -> None:
    """Add one resource namespace when it is present."""
    if not isinstance(value, Mapping):
        return
    namespace = str(value.get("namespace") or "").strip()
    if namespace:
        namespaces.add(namespace)
