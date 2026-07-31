"""incident-worker — evidence.built -> incident.detected -> evidence.bundle.built."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator

from domains.rca.events import Evidence, EvidenceBuiltBody, IncidentRecord
from domains.rca.timeline import incident_timeline_event
from domains.target.management_guard import is_management_registration
from domains.timeline.repository import TimelineLedgerAppend
from packages.contracts.event_bus.bodies import EventBody
from packages.contracts.stores import RcaStore
from packages.runtime.app import App, EventContext
from services.ai.agent.defaults import EvidenceDefaults
from services.ai.agent.pipeline import IncidentPipeline
from services.ai.agent.pipeline.incident_signal import incident_claim_identity

app = App("incident-worker")
pipeline = IncidentPipeline()
defaults = EvidenceDefaults()


@app.on(EvidenceBuiltBody)
async def on_evidence_built(
    evt: EvidenceBuiltBody,
    ctx: EventContext[RcaStore],
) -> AsyncIterator[EventBody]:
    if await evidence_is_from_management_cluster(evt.evidence, ctx.db):
        return
    evt = await hydrate_evidence_built(evt, ctx)
    bodies = pipeline.build_bodies(evt.evidence, ctx.correlation_id)
    if not bodies.detected_body.detected:
        return
    incident = bodies.detected_body.incident
    if incident is None:
        return
    identity = incident_claim_identity(evt.evidence, incident)
    if identity is None:
        return
    claimed = await ctx.db.claim_incident_signal(
        evt.evidence.workspace_id,
        evt.evidence.cluster_id,
        identity.signal_key,
        ctx.correlation_id,
        identity.payload,
    )
    if not claimed:
        return
    if not await append_confirmed_incident_timeline(ctx, incident):
        return
    yield bodies.detected_body
    yield bodies.next_body


async def evidence_is_from_management_cluster(evidence: Evidence, db: object) -> bool:
    """Keep read-only management snapshots out of the target incident lifecycle."""

    getter = getattr(db, "get_cluster_registration", None)
    if not callable(getter):
        return False
    registration = getter(evidence.workspace_id, evidence.cluster_id)
    if inspect.isawaitable(registration):
        registration = await registration
    return is_management_registration(registration)


async def append_confirmed_incident_timeline(
    ctx: EventContext[RcaStore],
    incident: IncidentRecord,
) -> bool:
    """Append one claimed incident fact before any incident outbox body exists."""
    event = incident_timeline_event(
        source_event_id=ctx.event_id,
        source_created_at=ctx.created_at,
        correlation_id=ctx.correlation_id,
        incident=incident,
    )
    append = await ctx.db.append_timeline_event(event)
    if not isinstance(append, TimelineLedgerAppend):
        raise TypeError("incident timeline ledger append returned an invalid result")
    return append.inserted


async def hydrate_evidence_built(
    evt: EvidenceBuiltBody,
    ctx: EventContext[RcaStore],
) -> EvidenceBuiltBody:
    """reference evidence.built 이벤트면 저장된 Evidence 원문을 복원한다."""
    if has_inline_evidence(evt.evidence):
        return evt
    correlation_id = evt.correlation_id or ctx.correlation_id
    kind = evt.kind or defaults.kind
    payload = await ctx.db.get_evidence_payload(evt.evidence.workspace_id, correlation_id, kind)
    if not isinstance(payload, dict):
        return evt
    evidence = Evidence.from_body(payload)  # type: ignore[assignment]
    if not isinstance(evidence, Evidence):
        return evt
    return EvidenceBuiltBody(
        evidence=evidence,
        correlation_id=correlation_id,
        kind=kind,
        payload_size=evt.payload_size,
        summary=evt.summary,
    )


def has_inline_evidence(evidence: Evidence) -> bool:
    return bool(evidence.kubernetes or evidence.metrics or evidence.logs or evidence.traces)


if __name__ == "__main__":
    app.run()
