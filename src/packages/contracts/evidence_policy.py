"""Typed server-owned evidence profile and query provenance contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from packages.contracts.modeling import StrictModel

EvidenceProfile = Literal["standard", "demo", "management"]
EvidenceQuerySource = Literal["kubernetes", "prometheus", "loki", "tempo", "metadata"]
EvidenceBackendScope = Literal["cluster_local", "shared"]
EvidenceQueryScope = Literal["cluster", "namespace"]
TEMPO_RECENT_TRACE_QUERY_NAME = "cluster_recent_traces"
TEMPO_RECENT_TRACE_RANGE_SECONDS = 15 * 60


class EvidenceQueryProvenance(StrictModel):
    """Bind a compiled query to one cluster and its effective telemetry scope."""

    cluster_id: str = Field(min_length=1, max_length=253)
    evidence_profile: EvidenceProfile
    backend_scope: EvidenceBackendScope = "cluster_local"
    query_scope: EvidenceQueryScope
    namespaces: tuple[str, ...] = Field(default=(), max_length=50)
    required_matchers: tuple[str, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def require_effective_scope(self) -> EvidenceQueryProvenance:
        if self.query_scope == "namespace" and not self.namespaces:
            raise ValueError("namespace queries require an exact namespace")
        if self.query_scope == "namespace" and not self.required_matchers:
            raise ValueError("namespace queries require a matcher")
        if self.backend_scope == "shared" and not self.required_matchers:
            raise ValueError("shared backend queries require a matcher")
        return self


class EvidencePolicyQuery(StrictModel):
    """One compiled provider query accepted by the cluster-agent policy."""

    source: EvidenceQuerySource
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1_000)
    query: str = Field(min_length=1, max_length=4_000)
    provenance: EvidenceQueryProvenance
    range_seconds: int | None = Field(default=None, ge=1, le=86_400)
    step_seconds: int | None = Field(default=None, ge=1, le=3_600)
    label_selector: str | None = Field(default=None, min_length=1, max_length=4_000)
    collection_scope: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def require_scope_matchers_in_query(self) -> EvidencePolicyQuery:
        missing = [
            matcher for matcher in self.provenance.required_matchers if matcher not in self.query
        ]
        if missing:
            raise ValueError("query does not contain every required scope matcher")
        if self.step_seconds is not None and self.range_seconds is None:
            raise ValueError("step_seconds requires range_seconds")
        return self
