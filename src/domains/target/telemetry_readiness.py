"""target 관측 스택 준비도 — 실측(관측된 파드 health) 기반, 지어내지 않음.

에이전트가 자기 네임스페이스(target)에서 관측한 워크로드 health 로부터 표준 관측 스택
(minio·prometheus·loki·tempo·opentelemetry-collector)의 구성요소별 present/ready 를
계산한다. helm fullnameOverride 로 이름이 고정돼 있어 접두어 매칭이 안정적이다.

원칙: 진행률을 타이머로 채우지 않는다. 관측된 파드의 실제 health 만 반영하며, 스택
설치가 시작되지 않았으면(구성요소 0 관측) None 을 반환해 진행바 자체를 띄우지 않는다
(에이전트-우선 연결은 그 자체로 완료 상태다).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

# 표준 관측 스택 구성요소(설치 순서) — scripts/install-telemetry.sh 와 일치.
TELEMETRY_COMPONENTS: tuple[tuple[str, str], ...] = (
    ("minio", "MinIO"),
    ("prometheus", "Prometheus"),
    ("loki", "Loki"),
    ("tempo", "Tempo"),
    ("opentelemetry-collector", "OpenTelemetry"),
)

_READY_HEALTH = {"healthy", "ready"}
_NOT_READY_HEALTH = {
    "degraded",
    "warning",
    "critical",
    "failed",
    "failure",
    "unhealthy",
    "notready",
    "not-ready",
    "pending",
    "progressing",
}
_READY_STATUS = {"ready", "running", "active", "available"}


def _is_ready(row: Mapping[str, Any]) -> bool:
    """관측된 health 를 우선하고, 없으면 status 로 판정(둘 다 없으면 미준비)."""
    health = str(row.get("health") or "").strip().lower()
    if health in _READY_HEALTH:
        return True
    if health in _NOT_READY_HEALTH:
        return False
    status = str(row.get("status") or "").strip().lower()
    return status in _READY_STATUS


def _matches(name: str, key: str) -> bool:
    return name == key or name.startswith(f"{key}-")


def telemetry_stack_view(workloads: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """관측된 target-ns 워크로드로부터 스택 준비도 뷰를 만든다. 관측 0이면 None."""
    rows = [row for row in workloads if isinstance(row, Mapping)]
    components: list[dict[str, Any]] = []
    any_present = False
    ready_count = 0
    for key, label in TELEMETRY_COMPONENTS:
        matched = [row for row in rows if _matches(str(row.get("name") or ""), key)]
        present = bool(matched)
        ready = present and all(_is_ready(row) for row in matched)
        if present:
            any_present = True
        if ready:
            ready_count += 1
        components.append({"key": key, "label": label, "present": present, "ready": ready})
    if not any_present:
        return None
    total = len(TELEMETRY_COMPONENTS)
    return {
        "ready_count": ready_count,
        "total": total,
        "complete": ready_count == total,
        "components": components,
    }
