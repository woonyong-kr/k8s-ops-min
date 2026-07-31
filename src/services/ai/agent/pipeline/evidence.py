from __future__ import annotations

from dataclasses import dataclass, field
from os import environ

from domains.rca.events import ClusterEvidenceReceivedBody, Evidence
from packages.contracts.event_bus.bodies import JsonObject
from services.ai.agent.defaults import EvidenceDefaults

EVIDENCE_LINEAGE_KEY = "_lineage"
EVIDENCE_SOURCE_SCHEMA_VERSION = 1
SERVICE_NAME = "cluster-agent"
COLLECTOR_VERSION_ENV = "EVIDENCE_COLLECTOR_VERSION"
GIT_SHA_ENV = "GIT_SHA"
IMAGE_TAG_ENV = "IMAGE_TAG"


@dataclass(frozen=True)
class EvidenceBuilder:
    defaults: EvidenceDefaults = field(default_factory=EvidenceDefaults)

    @property
    def kind(self) -> str:
        return self.defaults.kind

    def build_evidence(self, evt: ClusterEvidenceReceivedBody, correlation_id: str) -> Evidence:
        evidence_ref = f"{self.defaults.object_ref_prefix}/{correlation_id}.json"
        metadata = dict(evt.metadata)
        if evt.collection_status:
            # Evidence wire 계약에 필드를 추가하면 구버전 strict consumer가 DLQ로
            # 보낼 수 있으므로 기존 metadata 확장점에 수집 상태를 보존한다.
            metadata["collection_status"] = dict(evt.collection_status)
        return Evidence(
            cluster_id=evt.cluster_id,
            kubernetes=with_lineage(evt.kubernetes, lineage_for(evt, "kubernetes")),
            metrics=with_lineage(evt.metrics, lineage_for(evt, "metrics")),
            logs=[
                with_lineage(entry, lineage_for(evt, "logs"))
                for entry in evt.logs
                if isinstance(entry, dict)
            ],
            traces=with_lineage(evt.traces, lineage_for(evt, "traces")),
            metadata=with_lineage(metadata, lineage_for(evt, "metadata")),
            object_ref=evidence_ref,
            workspace_id=evt.workspace_id,
        )


def lineage_for(evt: ClusterEvidenceReceivedBody, source: str) -> JsonObject:
    """수집 원문에 붙일 최소 lineage.

    새 최상위 이벤트 필드를 늘리면 구버전 워커가 unknown field 로 DLQ 를 만들 수 있다.
    그래서 기존 JSON payload 내부에만 저장하고, 조회 API에서 필요한 필드만 승격한다.
    """
    values: JsonObject = {
        "schema_version": EVIDENCE_SOURCE_SCHEMA_VERSION,
        "source": source,
        "collector": SERVICE_NAME,
        "collector_version": collector_version(),
        "workspace_id": evt.workspace_id,
        "cluster_id": evt.cluster_id,
        "agent_id": evt.agent_id,
        "source_id": evt.source_id,
        "window_start": evt.window_start,
        "evidence_key": evt.evidence_key,
    }
    return {key: value for key, value in values.items() if value is not None}


def collector_version() -> str:
    return (
        environ.get(COLLECTOR_VERSION_ENV)
        or environ.get(GIT_SHA_ENV)
        or environ.get(IMAGE_TAG_ENV)
        or "unknown"
    )


def with_lineage(payload: JsonObject, lineage: JsonObject) -> JsonObject:
    if not payload:
        return payload
    data = dict(payload)
    current = data.get(EVIDENCE_LINEAGE_KEY)
    if isinstance(current, dict):
        data[EVIDENCE_LINEAGE_KEY] = {**lineage, **current}
    else:
        data[EVIDENCE_LINEAGE_KEY] = lineage
    return data
