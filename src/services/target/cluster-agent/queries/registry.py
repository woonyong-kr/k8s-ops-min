from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Self

from telemetry_registry import ensure_sources_loaded, telemetry

from packages.contracts.evidence_policy import EvidencePolicyQuery
from packages.contracts.kubernetes_discovery import DynamicResourceCollectionSpec
from packages.contracts.target import (
    KUBERNETES_ALL_NAMESPACES_QUERY,
    KUBERNETES_QUERY_SCOPE_CLUSTER_ACCESS,
    KUBERNETES_QUERY_SCOPE_CLUSTER_DISCOVERY,
    KUBERNETES_QUERY_SCOPE_CLUSTER_EVENTS,
    KUBERNETES_QUERY_SCOPE_DYNAMIC_RESOURCE,
    KUBERNETES_QUERY_SCOPE_NAMESPACE,
)

# 소스 목록은 @telemetry.source 로 등록된 provider 가 단일 출처.
TelemetrySource = str


@dataclass(frozen=True)
class TelemetryQueryDefinition:
    """Store one query request from policy or a debug command.
    It can become the query object used by one provider.
    """

    source: TelemetrySource
    name: str
    description: str
    query: str
    range_seconds: int | None = None
    step_seconds: int | None = None
    label_selector: str | None = None
    collection_scope: str | None = None
    dynamic_resource: DynamicResourceCollectionSpec | None = None

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> Self:
        """Build a query definition from a plain payload dict."""
        ensure_sources_loaded()
        source = _required_text(payload, "source")
        telemetry.spec(source)  # 미등록 소스면 supported 목록과 함께 즉시 예외

        return cls(
            source=source,
            name=_required_text(payload, "name"),
            description=str(payload.get("description", "")),
            query=_required_text(payload, "query"),
            range_seconds=_optional_positive_int(payload, "range_seconds"),
            step_seconds=_optional_positive_int(payload, "step_seconds"),
            label_selector=_optional_text(payload, "label_selector"),
            collection_scope=_optional_text(payload, "collection_scope"),
            dynamic_resource=_dynamic_resource(payload.get("dynamic_resource")),
        )

    def to_provider_query(self) -> Any:
        """등록된 소스 계약(query_type)으로 provider 쿼리 값 객체 생성."""
        ensure_sources_loaded()
        if self.range_seconds is not None:
            range_query_type = telemetry.range_query_type_for(self.source)
            if range_query_type is None:
                raise ValueError(f"telemetry source does not support range query: {self.source}")
            return range_query_type(
                self.name,
                self.description,
                self.query,
                self.range_seconds,
                self.step_seconds,
            )
        query_type = telemetry.query_type_for(self.source)
        if self.source == "kubernetes":
            return query_type(
                self.name,
                self.description,
                self.query,
                self.label_selector,
                self.collection_scope or KUBERNETES_QUERY_SCOPE_NAMESPACE,
                self.dynamic_resource,
            )
        return query_type(self.name, self.description, self.query)


def compile_policy_query_definition(
    payload: Mapping[str, Any],
    *,
    source: TelemetrySource,
    cluster_id: str,
) -> TelemetryQueryDefinition:
    """Compile one server-owned query against the exact receiving Agent cluster."""

    compiled_payload = dict(payload)
    compiled_payload.setdefault("source", source)
    if compiled_payload.get("provenance") is not None:
        compiled = EvidencePolicyQuery.model_validate(compiled_payload)
        if compiled.provenance.cluster_id != cluster_id:
            raise ValueError("evidence query cluster provenance does not match this agent")
        compiled_payload = compiled.model_dump(mode="json", exclude_none=True)
    return TelemetryQueryDefinition.from_mapping(compiled_payload)


class TelemetryQueryRegistry:
    """Keep query definitions by source and name.
    Providers use this registry to find the queries they should run.
    """

    def __init__(
        self,
        definitions: tuple[TelemetryQueryDefinition, ...] = (),
    ) -> None:
        """Create the registry and load the first definitions."""
        self.definitions: dict[tuple[TelemetrySource, str], TelemetryQueryDefinition] = {}
        self.register_many(definitions)

    def register(self, definition: TelemetryQueryDefinition) -> TelemetryQueryDefinition:
        """Store one query definition and return it."""
        self.definitions[(definition.source, definition.name)] = definition
        return definition

    def register_many(
        self,
        definitions: tuple[TelemetryQueryDefinition, ...],
    ) -> tuple[TelemetryQueryDefinition, ...]:
        """Store many query definitions in this registry."""
        for definition in definitions:
            self.register(definition)
        return definitions

    def replace_source(
        self,
        source: TelemetrySource,
        definitions: tuple[TelemetryQueryDefinition, ...],
    ) -> tuple[TelemetryQueryDefinition, ...]:
        """Replace all query definitions for one source."""
        self.definitions = {
            key: definition for key, definition in self.definitions.items() if key[0] != source
        }
        return self.register_many(definitions)

    def get(self, source: TelemetrySource, name: str) -> TelemetryQueryDefinition:
        """Find one query definition by source and name."""
        try:
            return self.definitions[(source, name)]
        except KeyError as exc:
            raise ValueError(f"unknown telemetry query: {source}/{name}") from exc

    def for_source(self, source: TelemetrySource) -> tuple[TelemetryQueryDefinition, ...]:
        """Return all query definitions for one source."""
        return tuple(
            definition
            for (definition_source, _name), definition in self.definitions.items()
            if definition_source == source
        )


def _required_text(payload: dict[str, Any], key: str) -> str:
    """Read a required text field from a payload."""
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"telemetry query field must be a non-empty string: {key}")
    return value.strip()


def _optional_positive_int(payload: dict[str, Any], key: str) -> int | None:
    """Read an optional positive integer field from a payload."""
    value = payload.get(key)
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"telemetry query field must be a positive integer: {key}") from exc
    if parsed <= 0:
        raise ValueError(f"telemetry query field must be a positive integer: {key}")
    return parsed


def _optional_text(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"telemetry query field must be a non-empty string: {key}")
    return value.strip()


def _dynamic_resource(value: object) -> DynamicResourceCollectionSpec | None:
    if value is None:
        return None
    try:
        return DynamicResourceCollectionSpec.model_validate(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid dynamic Kubernetes resource contract: {exc}") from exc


@dataclass(frozen=True)
class PrometheusInstantQuery:
    """Describe one Prometheus instant query."""

    metric_name: str
    description: str
    promql: str


@dataclass(frozen=True)
class PrometheusRangeQuery:
    """Describe one Prometheus range query."""

    metric_name: str
    description: str
    promql: str
    range_seconds: int
    step_seconds: int | None = None


@dataclass(frozen=True)
class LokiLogQuery:
    """Describe one Loki log query."""

    query_name: str
    description: str
    logql: str
    range_seconds: int | None = None
    step_seconds: int | None = None


@dataclass(frozen=True)
class OpenTelemetrySpanQuery:
    """Describe one Tempo or OpenTelemetry span query."""

    query_name: str
    description: str
    traceql: str
    range_seconds: int | None = None
    step_seconds: int | None = None


@dataclass(frozen=True)
class KubernetesSnapshotQuery:
    """Describe one bounded Kubernetes evidence query."""

    query_name: str
    description: str
    namespace: str
    label_selector: str | None = None
    collection_scope: str = KUBERNETES_QUERY_SCOPE_NAMESPACE
    dynamic_resource: DynamicResourceCollectionSpec | None = None

    def __post_init__(self) -> None:
        if self.collection_scope not in {
            KUBERNETES_QUERY_SCOPE_NAMESPACE,
            KUBERNETES_QUERY_SCOPE_CLUSTER_EVENTS,
            KUBERNETES_QUERY_SCOPE_CLUSTER_DISCOVERY,
            KUBERNETES_QUERY_SCOPE_CLUSTER_ACCESS,
            KUBERNETES_QUERY_SCOPE_DYNAMIC_RESOURCE,
        }:
            raise ValueError(f"unsupported Kubernetes collection scope: {self.collection_scope}")
        if self.collection_scope in {
            KUBERNETES_QUERY_SCOPE_CLUSTER_EVENTS,
            KUBERNETES_QUERY_SCOPE_CLUSTER_DISCOVERY,
            KUBERNETES_QUERY_SCOPE_CLUSTER_ACCESS,
            KUBERNETES_QUERY_SCOPE_DYNAMIC_RESOURCE,
        }:
            if self.namespace != KUBERNETES_ALL_NAMESPACES_QUERY:
                raise ValueError(
                    f"{self.collection_scope} collection scope requires the all-namespaces query"
                )
            if self.label_selector:
                raise ValueError(
                    f"{self.collection_scope} collection scope does not allow a label selector"
                )
        if self.collection_scope == KUBERNETES_QUERY_SCOPE_DYNAMIC_RESOURCE:
            if self.dynamic_resource is None:
                raise ValueError("dynamic Kubernetes resource collection requires an identity")
        elif self.dynamic_resource is not None:
            raise ValueError(
                "dynamic Kubernetes resource identity requires the dynamic_resource scope"
            )

    @property
    def is_cluster_wide_event_capture(self) -> bool:
        return self.collection_scope == KUBERNETES_QUERY_SCOPE_CLUSTER_EVENTS

    @property
    def is_cluster_api_discovery(self) -> bool:
        return self.collection_scope == KUBERNETES_QUERY_SCOPE_CLUSTER_DISCOVERY

    @property
    def is_dynamic_resource_collection(self) -> bool:
        return self.collection_scope == KUBERNETES_QUERY_SCOPE_DYNAMIC_RESOURCE

    @property
    def is_cluster_access_snapshot(self) -> bool:
        return self.collection_scope == KUBERNETES_QUERY_SCOPE_CLUSTER_ACCESS


@dataclass(frozen=True)
class MetadataSnapshotQuery:
    """Describe one metadata snapshot query."""

    query_name: str
    description: str
    query: str
