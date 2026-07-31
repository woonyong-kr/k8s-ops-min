from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest

from domains.rca.repository import (
    ALERTMANAGER_EVIDENCE_ACTIVE,
    ALERTMANAGER_EVIDENCE_ORPHAN,
    ALERTMANAGER_EVIDENCE_PENDING,
    ALERTMANAGER_EVIDENCE_TERMINAL,
    RcaRepository,
)


class ScalarResult:
    def __init__(self, value: object | None) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object | None:
        return self.value


class ScalarConnection:
    def __init__(self, values: list[object | None]) -> None:
        self.values = list(values)
        self.executed = 0

    def execute(self, _statement: object) -> ScalarResult:
        self.executed += 1
        return ScalarResult(self.values.pop(0))


def repository_with(
    *values: object | None,
) -> tuple[RcaRepository, ScalarConnection]:
    repository = object.__new__(RcaRepository)
    connection = ScalarConnection(list(values))

    @contextmanager
    def open_connection() -> Any:
        yield connection

    repository.connection = open_connection  # type: ignore[method-assign]
    return repository, connection


@pytest.mark.parametrize(
    ("values", "expected"),
    (
        (("incident_resolved",), ALERTMANAGER_EVIDENCE_TERMINAL),
        (("incident_expired",), ALERTMANAGER_EVIDENCE_TERMINAL),
        (("pr_created",), ALERTMANAGER_EVIDENCE_ACTIVE),
        ((None, "completed"), ALERTMANAGER_EVIDENCE_TERMINAL),
        ((None, "verification_pending"), ALERTMANAGER_EVIDENCE_ACTIVE),
        ((None, None, 7), ALERTMANAGER_EVIDENCE_ACTIVE),
        ((None, None, None, 9), ALERTMANAGER_EVIDENCE_ACTIVE),
        (
            (None, None, None, None, "event-1", "event-1"),
            ALERTMANAGER_EVIDENCE_PENDING,
        ),
        (
            (None, None, None, None, "event-1", None, "event-1"),
            ALERTMANAGER_EVIDENCE_ORPHAN,
        ),
        (
            (None, None, None, None, "event-1", None, None),
            ALERTMANAGER_EVIDENCE_PENDING,
        ),
        ((None, None, None, None, None), ALERTMANAGER_EVIDENCE_ORPHAN),
    ),
)
def test_alertmanager_evidence_disposition_follows_durable_lineage(
    values: tuple[object | None, ...],
    expected: str,
) -> None:
    repository, connection = repository_with(*values)

    disposition = repository.get_alertmanager_evidence_disposition(
        "workspace-1",
        "incident-1",
        "event-1",
    )

    assert disposition == expected
    assert connection.executed == len(values)
