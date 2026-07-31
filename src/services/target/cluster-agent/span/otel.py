from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence, Sized
from contextlib import contextmanager
from typing import Any

from span.base import TracePayload, TraceSpan, TraceTracer

try:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.trace import Span, Status, StatusCode, Tracer
except ModuleNotFoundError:
    trace = None  # type: ignore[assignment]
    OTLPSpanExporter = None  # type: ignore[assignment]
    SERVICE_NAME = "service.name"  # type: ignore[assignment]
    Resource = None  # type: ignore[assignment]
    TracerProvider = None  # type: ignore[assignment]
    BatchSpanProcessor = None  # type: ignore[assignment]
    Span = Any  # type: ignore[misc,assignment]
    Status = None  # type: ignore[assignment]
    StatusCode = None  # type: ignore[assignment]
    Tracer = Any  # type: ignore[misc,assignment]

_CONFIGURED = False


class OtelSpan:
    def __init__(self, span: Span) -> None:
        self.span = span

    def attr(self, key: str, value: Any) -> None:
        self.span.set_attribute(key, value)

    def count(self, key: str, values: Sized) -> None:
        self.attr(key, len(values))

    def flag(self, key: str, value: bool) -> None:
        self.attr(key, value)

    def http_status(self, status_code: int) -> None:
        self.attr("http.status_code", status_code)

    def fields_present(
        self,
        namespace: str,
        payload: Mapping[str, Any],
        fields: Sequence[str],
    ) -> None:
        for field in fields:
            self.flag(f"{namespace}.has_{field}", field in payload)

    def error(self, exc: Exception) -> None:
        self.span.record_exception(exc)
        self.span.set_status(Status(StatusCode.ERROR, str(exc)))


class OtelTracer:
    def __init__(self, tracer: Tracer) -> None:
        self.tracer = tracer

    @contextmanager
    def start_as_current_span(self, name: str) -> Iterator[TraceSpan]:
        with self.tracer.start_as_current_span(name) as span:
            yield OtelSpan(span)

    @contextmanager
    def start_payload_span(
        self,
        name: str,
        *,
        namespace: str,
        expected_fields: Sequence[str],
    ) -> Iterator[TracePayload]:
        payload: TracePayload = {}
        with self.start_as_current_span(name) as span:
            try:
                yield payload
            finally:
                span.fields_present(namespace, payload, expected_fields)


class NoopSpan:
    def attr(self, key: str, value: Any) -> None:
        return None

    def count(self, key: str, values: Sized) -> None:
        return None

    def flag(self, key: str, value: bool) -> None:
        return None

    def http_status(self, status_code: int) -> None:
        return None

    def fields_present(
        self,
        namespace: str,
        payload: Mapping[str, Any],
        fields: Sequence[str],
    ) -> None:
        return None

    def error(self, exc: Exception) -> None:
        return None


class NoopTracer:
    @contextmanager
    def start_as_current_span(self, name: str) -> Iterator[TraceSpan]:
        yield NoopSpan()

    @contextmanager
    def start_payload_span(
        self,
        name: str,
        *,
        namespace: str,
        expected_fields: Sequence[str],
    ) -> Iterator[TracePayload]:
        payload: TracePayload = {}
        yield payload


def configure_tracing(service_name: str, traces_endpoint: str) -> TraceTracer:
    global _CONFIGURED

    if trace is None:
        return NoopTracer()

    if not _CONFIGURED:
        resource = Resource.create({SERVICE_NAME: service_name})
        provider = TracerProvider(resource=resource)
        if traces_endpoint.strip():
            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=traces_endpoint))
            )
        trace.set_tracer_provider(provider)
        _CONFIGURED = True

    return get_tracer(service_name)


def get_tracer(name: str) -> TraceTracer:
    if trace is None:
        return NoopTracer()
    return OtelTracer(trace.get_tracer(name))
