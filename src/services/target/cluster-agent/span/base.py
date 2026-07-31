from __future__ import annotations

from collections.abc import Mapping, Sequence, Sized
from contextlib import AbstractContextManager
from typing import Any, Protocol

TracePayload = dict[str, Any]


class TraceSpan(Protocol):
    def attr(self, key: str, value: Any) -> None: ...

    def count(self, key: str, values: Sized) -> None: ...

    def flag(self, key: str, value: bool) -> None: ...

    def http_status(self, status_code: int) -> None: ...

    def fields_present(
        self,
        namespace: str,
        payload: Mapping[str, Any],
        fields: Sequence[str],
    ) -> None: ...

    def error(self, exc: Exception) -> None: ...


class TraceTracer(Protocol):
    def start_as_current_span(self, name: str) -> AbstractContextManager[TraceSpan]: ...

    def start_payload_span(
        self,
        name: str,
        *,
        namespace: str,
        expected_fields: Sequence[str],
    ) -> AbstractContextManager[TracePayload]: ...
