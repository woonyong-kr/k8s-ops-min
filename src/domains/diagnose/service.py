"""Application service for launching injected, durable Diagnose investigations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from packages.contracts.diagnose import (
    DiagnoseAgentAvailability,
    DiagnoseAgentSelection,
    DiagnoseEngine,
    DiagnoseEventDraft,
    DiagnoseEventStream,
    DiagnoseRun,
    DiagnoseRunCreateRequest,
    DiagnoseRunLaunchResult,
    DiagnoseRunRepository,
    DiagnoseRunTransition,
    DiagnoseTarget,
)

RunIdFactory = Callable[[], str]
Clock = Callable[[], datetime]


class UnavailableDiagnoseEngine:
    """Safe default until the composition root injects a real Python adapter."""

    async def availability(
        self,
        *,
        target: DiagnoseTarget,
        agent: DiagnoseAgentSelection,
    ) -> DiagnoseAgentAvailability:
        return DiagnoseAgentAvailability(
            available=False,
            reason="no Diagnose engine adapter is configured",
        )

    async def start(self, run: DiagnoseRun) -> None:
        raise RuntimeError("a Diagnose engine adapter is required before a run can start")

    async def continue_run(self, run: DiagnoseRun, question: str) -> None:
        del run, question
        raise RuntimeError("a Diagnose engine adapter is required before a turn can start")


class DiagnoseService:
    """Coordinates durable state first, stream fan-out second, and engine dispatch last."""

    def __init__(
        self,
        *,
        repository: DiagnoseRunRepository,
        stream: DiagnoseEventStream,
        engine: DiagnoseEngine | None = None,
        run_id_factory: RunIdFactory = lambda: str(uuid4()),
        now: Clock = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._stream = stream
        self._engine = engine or UnavailableDiagnoseEngine()
        self._run_id_factory = run_id_factory
        self._now = now

    async def create_run(
        self,
        request: DiagnoseRunCreateRequest,
        *,
        requested_by: str,
    ) -> DiagnoseRunLaunchResult:
        availability = await self._engine.availability(target=request.target, agent=request.agent)
        timestamp = self._now()
        status = "queued" if availability.available else "unavailable"
        candidate = DiagnoseRun.from_create_request(
            run_id=self._run_id_factory(),
            request=request,
            requested_by=requested_by,
            status=status,
            status_reason=availability.reason,
            occurred_at=timestamp,
        )
        creation = await self._repository.create_or_get_active(
            candidate,
            DiagnoseEventDraft(
                kind="phase",
                payload=_phase_payload(candidate.status, candidate.status_reason),
                occurred_at=timestamp,
            ),
        )
        if not creation.created:
            return DiagnoseRunLaunchResult(run=creation.run, created=False, deduplicated=True)

        assert creation.initial_event is not None
        await self._stream.publish(creation.initial_event, scope=creation.run.target.scope)
        if not availability.available:
            return DiagnoseRunLaunchResult(run=creation.run, created=True, deduplicated=False)

        try:
            await self._engine.start(creation.run)
        except Exception as error:
            transition = await self._repository.transition(
                creation.run,
                expected_statuses=("queued",),
                next_status="failed",
                status_reason=_adapter_error_reason(error),
                event=DiagnoseEventDraft(
                    kind="error",
                    payload={"code": "engine_start_failed", "status": "failed"},
                    occurred_at=self._now(),
                ),
            )
            return await self._launch_after_transition(transition, created=True)

        transition = await self._repository.transition(
            creation.run,
            expected_statuses=("queued",),
            next_status="running",
            event=DiagnoseEventDraft(
                kind="phase",
                payload=_phase_payload("running"),
                occurred_at=self._now(),
            ),
        )
        return await self._launch_after_transition(transition, created=True)

    async def _launch_after_transition(
        self,
        transition: DiagnoseRunTransition,
        *,
        created: bool,
    ) -> DiagnoseRunLaunchResult:
        """Publish only an event that the durable repository already committed."""

        if transition.changed:
            assert transition.event is not None
            await self._stream.publish(transition.event, scope=transition.run.target.scope)
        return DiagnoseRunLaunchResult(
            run=transition.run,
            created=created,
            deduplicated=not created,
        )

    async def add_turn(self, run: DiagnoseRun, question: str) -> DiagnoseRunTransition:
        timestamp = self._now()
        transition = await self._repository.transition(
            run,
            expected_statuses=("completed",),
            next_status="running",
            event=DiagnoseEventDraft(
                kind="turn",
                payload={"question": question},
                occurred_at=timestamp,
            ),
        )
        if not transition.changed:
            return transition
        assert transition.event is not None
        await self._stream.publish(transition.event, scope=transition.run.target.scope)
        try:
            await self._engine.continue_run(transition.run, question)
        except Exception as error:
            failed = await self._repository.transition(
                transition.run,
                expected_statuses=("running",),
                next_status="failed",
                status_reason=_adapter_error_reason(error),
                event=DiagnoseEventDraft(
                    kind="error",
                    payload={"code": "engine_turn_failed", "status": "failed"},
                    occurred_at=self._now(),
                ),
            )
            if failed.changed:
                assert failed.event is not None
                await self._stream.publish(failed.event, scope=failed.run.target.scope)
            return failed
        return transition

    async def stop_run(self, run: DiagnoseRun) -> DiagnoseRunTransition:
        transition = await self._repository.transition(
            run,
            expected_statuses=("queued", "running", "awaiting_confirmation"),
            next_status="stopped",
            event=DiagnoseEventDraft(
                kind="closed",
                payload={"status": "stopped", "reason": "user_requested"},
                occurred_at=self._now(),
            ),
        )
        if transition.changed:
            assert transition.event is not None
            await self._stream.publish(transition.event, scope=transition.run.target.scope)
        return transition


def _phase_payload(status: str, reason: str | None = None) -> dict[str, str]:
    payload = {"status": status}
    if reason is not None:
        payload["reason"] = reason
    return payload


def _adapter_error_reason(error: Exception) -> str:
    """Persist a bounded adapter failure reason without manufacturing a verdict."""

    message = str(error).strip()
    return message[:1000] if message else error.__class__.__name__
