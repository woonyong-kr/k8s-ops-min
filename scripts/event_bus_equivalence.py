#!/usr/bin/env python3
"""Compare one delivery/redelivery scenario across in-process and NATS buses."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from typing import Any

from packages.contracts.event_bus.interfaces import EventConsumerBus, EventEnvelope
from packages.events.bus import NATS_URL_ENV, NatsEventBus
from packages.events.context import event_workspace
from packages.events.envelope import event
from packages.events.in_memory import InMemoryEventBus


def _decode(raw: bytes) -> EventEnvelope:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise TypeError("event envelope must be a JSON object")
    return EventEnvelope.from_mapping(payload)


async def run_delivery_scenario(bus: EventConsumerBus) -> dict[str, object]:
    """Run the same causal two-event flow and return transport-neutral results."""

    suffix = uuid.uuid4().hex
    input_subject = f"audit.bus_equivalence.{suffix}.requested"
    output_subject = f"audit.bus_equivalence.{suffix}.completed"
    input_subscription = f"bus-equivalence-input-{suffix}"
    output_subscription = f"bus-equivalence-output-{suffix}"

    await bus.connect()
    try:
        inputs = await bus.subscribe(input_subject, durable=input_subscription)
        outputs = await bus.subscribe(output_subject, durable=output_subscription)
        with event_workspace("workspace-equivalence"):
            emitted = await bus.emit(
                input_subject,
                "event-bus-equivalence",
                {"action": "restart", "attempt": 1},
                correlation_id="event-bus-equivalence",
            )

        first = (await inputs.fetch(batch=1, timeout=5.0))[0]
        first_data = bytes(first.data)
        await first.nak()
        redelivered = (await inputs.fetch(batch=1, timeout=5.0))[0]
        received = _decode(bytes(redelivered.data))

        completed = event(
            output_subject,
            "event-bus-equivalence-handler",
            {"result": "accepted"},
            correlation_id=received.correlation_id,
            causation_id=received.event_id,
            workspace_id=received.workspace_id,
        )
        await bus.publish_envelope(completed)
        await redelivered.ack()

        output_message = (await outputs.fetch(batch=1, timeout=5.0))[0]
        observed = _decode(bytes(output_message.data))
        await output_message.ack()

        return {
            "causation_preserved": observed.causation_id == emitted.event_id,
            "correlation_preserved": (
                received.correlation_id == emitted.correlation_id == observed.correlation_id
            ),
            "input_payload": received.payload,
            "output_payload": observed.payload,
            "redelivery_preserved": first_data == bytes(redelivered.data),
            "workspace_preserved": (
                received.workspace_id == emitted.workspace_id == observed.workspace_id
            ),
        }
    finally:
        await bus.close()


async def _compare(nats_url: str) -> dict[str, Any]:
    inprocess = await run_delivery_scenario(InMemoryEventBus())
    os.environ[NATS_URL_ENV] = nats_url
    nats = await run_delivery_scenario(NatsEventBus())
    return {"equivalent": inprocess == nats, "inprocess": inprocess, "nats": nats}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--nats-url", required=True)
    args = parser.parse_args()
    result = asyncio.run(_compare(args.nats_url))
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["equivalent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
