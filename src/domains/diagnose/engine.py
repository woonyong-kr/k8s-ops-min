"""Diagnose adapter over the existing evidence-grounded Opsia AI facade."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from domains.ai.context_facade import answer_from_context
from packages.contracts.diagnose import (
    DiagnoseAgentAvailability,
    DiagnoseAgentSelection,
    DiagnoseEventDraft,
    DiagnoseEventStream,
    DiagnoseRun,
    DiagnoseRunRepository,
    DiagnoseTarget,
)
from packages.contracts.gateway.requests import AiAssistantContext, AiAssistantFilters

LOGGER = logging.getLogger(__name__)
DIAGNOSE_AGENT_ID = "operations-ai"
DIAGNOSE_AGENT_LABEL = "Opsia AI"
DIAGNOSE_DISCLOSURE_REVISION = "opsia-ai-investigation-v1"
INITIAL_INVESTIGATION_QUESTION = (
    "Analyze the selected Kubernetes resource's current observed health. "
    "Identify the most likely cause only when supported by evidence, explain missing evidence, "
    "and propose safe next steps without claiming that any change was executed."
)


class ContextDiagnoseEngine:
    """Runs durable investigations with the existing authorized evidence facade."""

    def __init__(
        self,
        *,
        db: Any,
        current: Any,
        repository: DiagnoseRunRepository,
        stream: DiagnoseEventStream,
        llm: Any | None,
    ) -> None:
        self._db = db
        self._current = current
        self._repository = repository
        self._stream = stream
        self._llm = llm
        self._tasks: set[asyncio.Task[None]] = set()

    async def availability(
        self,
        *,
        target: DiagnoseTarget,
        agent: DiagnoseAgentSelection,
    ) -> DiagnoseAgentAvailability:
        if agent.agent_id != DIAGNOSE_AGENT_ID:
            return DiagnoseAgentAvailability(
                available=False,
                reason="the requested Diagnose agent is not configured",
            )
        if not agent.isolated or agent.model is not None or agent.effort != "medium":
            return DiagnoseAgentAvailability(
                available=False,
                reason="the requested Diagnose agent settings are not supported",
            )
        if _resource_type(target.resource.kind) is None:
            return DiagnoseAgentAvailability(
                available=False,
                reason="the selected resource kind is not supported by the evidence engine",
            )
        return DiagnoseAgentAvailability(available=True)

    async def start(self, run: DiagnoseRun) -> None:
        self._schedule(run, INITIAL_INVESTIGATION_QUESTION, initial=True)

    async def continue_run(self, run: DiagnoseRun, question: str) -> None:
        self._schedule(run, question, initial=False)

    def _schedule(self, run: DiagnoseRun, question: str, *, initial: bool) -> None:
        task = asyncio.create_task(
            self._execute(run, question, initial=initial),
            name=f"diagnose-{run.run_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(_log_task_failure)

    async def _execute(self, run: DiagnoseRun, question: str, *, initial: bool) -> None:
        try:
            _require_current_target(self._db, run)
            if initial:
                await self._append(
                    run,
                    DiagnoseEventDraft(
                        kind="turn",
                        payload={"question": None},
                        occurred_at=datetime.now(UTC),
                    ),
                )
            answer = await answer_from_context(
                self._db,
                current=self._current,
                workspace_id=run.target.scope.workspace_id,
                context=_assistant_context(run),
                message=question,
                llm=self._llm,
            )
            current = await self._repository.get_run(
                scope=run.target.scope,
                run_id=run.run_id,
            )
            if current is None or current.status != "running":
                return
            await self._append(
                current,
                DiagnoseEventDraft(
                    kind="verdict",
                    payload={
                        "answer": answer.answer,
                        "evidence": [item.model_dump(mode="json") for item in answer.evidence],
                        "action": (
                            answer.action.model_dump(mode="json")
                            if answer.action is not None
                            else None
                        ),
                    },
                    occurred_at=datetime.now(UTC),
                ),
            )
            transition = await self._repository.transition(
                current,
                expected_statuses=("running",),
                next_status="completed",
                event=DiagnoseEventDraft(
                    kind="closed",
                    payload={"status": "completed"},
                    occurred_at=datetime.now(UTC),
                ),
            )
            if transition.changed:
                assert transition.event is not None
                await self._stream.publish(
                    transition.event,
                    scope=transition.run.target.scope,
                )
        except Exception as error:
            transition = await self._repository.transition(
                run,
                expected_statuses=("running",),
                next_status="failed",
                status_reason=_bounded_reason(error),
                event=DiagnoseEventDraft(
                    kind="error",
                    payload={"code": "investigation_failed", "status": "failed"},
                    occurred_at=datetime.now(UTC),
                ),
            )
            if transition.changed:
                assert transition.event is not None
                await self._stream.publish(
                    transition.event,
                    scope=transition.run.target.scope,
                )

    async def _append(self, run: DiagnoseRun, draft: DiagnoseEventDraft) -> None:
        event = await self._repository.append_event(run, draft)
        await self._stream.publish(event, scope=run.target.scope)


def _assistant_context(run: DiagnoseRun) -> AiAssistantContext:
    namespace = run.target.resource.namespace
    return AiAssistantContext(
        screen="resources",
        filters=AiAssistantFilters(
            clusters=[run.target.scope.cluster_id],
            namespaces=([f"{run.target.scope.cluster_id}/{namespace}"] if namespace else []),
            resource_types=[],
        ),
        selection={
            "type": "resource",
            "identity": (
                f"{run.target.resource.kind}/{namespace or '_'}/{run.target.resource.name}"
            ),
        },
    )


def _require_current_target(db: Any, run: DiagnoseRun) -> None:
    resource = run.target.resource
    resource_type = _resource_type(resource.kind)
    if resource_type is None:
        raise RuntimeError("the selected resource kind is not supported by the evidence engine")
    reader = getattr(db, "get_inventory_resource_by_api_version", None)
    if not callable(reader):
        raise RuntimeError("exact inventory identity is unavailable")
    api_version = (
        f"{resource.api_group}/{resource.version}" if resource.api_group else resource.version
    )
    observed = reader(
        workspace_id=run.target.scope.workspace_id,
        cluster_id=run.target.scope.cluster_id,
        resource_type=resource_type,
        api_version=api_version,
        kind=resource.kind,
        namespace=resource.namespace,
        name=resource.name,
    )
    if not isinstance(observed, dict) or str(observed.get("uid") or "") != resource.uid:
        raise RuntimeError("the selected resource identity is no longer current")


def _resource_type(kind: str) -> str | None:
    normalized = kind.strip().lower()
    if normalized == "pod":
        return "pod"
    if normalized in {"deployment", "statefulset", "daemonset", "replicaset"}:
        return "workload"
    if normalized in {"service", "node", "namespace", "event"}:
        return normalized
    return None


def _bounded_reason(error: Exception) -> str:
    message = str(error).strip()
    return (message or error.__class__.__name__)[:1000]


def _log_task_failure(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    try:
        error = task.exception()
    except asyncio.CancelledError:
        return
    if error is not None:
        LOGGER.error(
            "diagnose_background_task_failed",
            exc_info=(type(error), error, error.__traceback__),
        )
