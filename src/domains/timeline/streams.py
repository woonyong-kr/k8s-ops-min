"""Canonical NDJSON/SSE encoding for timeline replay frames.

Keeping protocol validation at the server boundary makes a bounded historical
window explicit while preserving the exact ordered records shared by both
transports.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from packages.contracts.timeline import TimelineEvent, TimelineStreamFrame


class TimelineStreamProtocolError(ValueError):
    """Raised before an invalid replay can be written to a client."""


def encode_ndjson(
    frames: Iterable[TimelineStreamFrame],
    *,
    include_snapshot_bounds: bool = False,
) -> str:
    """Return one strict frame per line, including its required terminal record."""
    return (
        "\n".join(_encoded_frames(frames, include_snapshot_bounds=include_snapshot_bounds)) + "\n"
    )


def encode_sse(frames: Iterable[TimelineStreamFrame]) -> str:
    """Return the same records as SSE, with opaque cursors as Last-Event-ID ids."""
    encoded: list[str] = []
    for frame in _validated(frames):
        data = _encode(frame)
        encoded.append(f"id: {frame.cursor.token}\nevent: {frame.kind}\ndata: {data}")
    return "\n\n".join(encoded) + "\n\n"


def encode_sse_frame(frame: TimelineStreamFrame) -> str:
    """Encode one already-validated live SSE frame without inventing a terminal.

    A retained snapshot has its own required terminal frame.  A live response
    stays open, so it emits individual event/resync/error frames through this
    narrow encoder instead of pretending an unfinished subscription ended.
    """
    return f"id: {frame.cursor.token}\nevent: {frame.kind}\ndata: {_encode(frame)}\n\n"


def _encoded_frames(
    frames: Iterable[TimelineStreamFrame],
    *,
    include_snapshot_bounds: bool = False,
) -> tuple[str, ...]:
    return tuple(
        _encode(frame, include_snapshot_bounds=include_snapshot_bounds)
        for frame in _validated(frames)
    )


def _encode(
    frame: TimelineStreamFrame,
    *,
    include_snapshot_bounds: bool = False,
) -> str:
    payload: dict[str, object] = {
        "kind": frame.kind,
        "cursor": frame.cursor.model_dump(mode="json"),
        "pin_set_revision": frame.pin_set_revision,
    }
    if frame.kind == "snapshot":
        if frame.policy is None or frame.capabilities is None:
            raise TimelineStreamProtocolError("timeline snapshot contract is incomplete")
        payload.update(
            scopes=[scope.model_dump(mode="json") for scope in frame.scopes],
            policy=frame.policy.model_dump(mode="json"),
            capabilities=frame.capabilities.model_dump(mode="json"),
            events=[_event_payload(event) for event in frame.events],
            coverage=[item.model_dump(mode="json") for item in frame.coverage],
        )
        if include_snapshot_bounds:
            payload.update(
                truncated=frame.truncated,
                event_limit=frame.event_limit,
            )
    elif frame.kind == "event":
        if frame.event is None:
            raise TimelineStreamProtocolError("timeline event contract is incomplete")
        payload["event"] = _event_payload(frame.event)
    elif frame.kind == "coverage":
        payload["coverage"] = [item.model_dump(mode="json") for item in frame.coverage]
    elif frame.reason is not None:
        payload["reason"] = frame.reason
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _event_payload(event: TimelineEvent) -> dict[str, object]:
    payload = event.model_dump(mode="json")
    subject = payload.get("subject")
    if not isinstance(subject, dict):
        raise TimelineStreamProtocolError("timeline event subject is invalid")
    subject["kind"] = event.subject.kind
    return payload


def _validated(frames: Iterable[TimelineStreamFrame]) -> tuple[TimelineStreamFrame, ...]:
    records = tuple(frames)
    if not records:
        raise TimelineStreamProtocolError("timeline stream requires snapshot and terminal frame")
    if records[0].kind != "snapshot":
        raise TimelineStreamProtocolError("timeline stream must begin with snapshot frame")

    terminal_indices = [index for index, frame in enumerate(records) if frame.is_terminal]
    if not terminal_indices:
        raise TimelineStreamProtocolError("timeline stream terminal frame is required")
    if len(terminal_indices) != 1 or terminal_indices[0] != len(records) - 1:
        raise TimelineStreamProtocolError("timeline stream terminal frame must be last")

    last_cursor_token = records[0].cursor.token
    for frame in records[1:]:
        if frame.kind == "snapshot":
            raise TimelineStreamProtocolError("timeline stream may contain one snapshot frame")
        if frame.kind == "event":
            if frame.cursor.token == last_cursor_token:
                raise TimelineStreamProtocolError("timeline event cursor must advance")
            last_cursor_token = frame.cursor.token
            continue
    return records
