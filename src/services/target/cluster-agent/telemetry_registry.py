"""텔레메트리 소스 레지스트리 — provider 가 @telemetry.source 로 자기 계약을 선언.

기존에는 "prometheus=metrics, loki=logs, tempo=traces" 지식이 4곳(Literal 타입,
SOURCE_EVIDENCE_KEYS dict, to_provider_query if-elif, agent 의 역방향 dict)에
흩어져 있었음. 이제 provider 클래스 위 데코레이터 선언이 단일 출처:

    @telemetry.source(source="prometheus", evidence_key="metrics",
                      query_type=PrometheusInstantQuery)
    class PrometheusMetricsProvider: ...

새 소스 추가 = providers/ 아래 파일 1개. collector/queries/agent 는
소스 목록을 모른 채 telemetry.spec()/sources() 만 읽음.
중복 등록은 즉시 예외(fail-fast). 이 모듈은 의존 없는 leaf(순환 import 방지).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class TelemetrySourceSpec:
    """텔레메트리 소스 1개의 계약."""

    source: str  # 쿼리 정의가 참조하는 소스 이름(예: "prometheus")
    evidence_key: str  # 증거 payload 의 키(예: "metrics")
    query_type: type  # 이 소스의 쿼리 값 객체(예: PrometheusInstantQuery)
    empty_payload: Callable[[], object] = dict  # 수집 실패 시 빈 payload 모양
    range_query_type: type | None = None  # range query 지원 소스만 선언


class TelemetryRegistry:
    """소스 계약의 주소록. 등록은 @telemetry.source, 조회는 spec()/sources()."""

    def __init__(self) -> None:
        """Create an empty registry for telemetry source contracts."""
        self._specs: dict[str, TelemetrySourceSpec] = {}

    def source(
        self,
        *,
        source: str,
        evidence_key: str,
        query_type: type,
        empty_payload: Callable[[], object] = dict,
        range_query_type: type | None = None,
    ) -> Callable[[type], type]:
        """provider 클래스 데코레이터 — 소스 계약을 스스로 선언함."""
        spec = TelemetrySourceSpec(
            source,
            evidence_key,
            query_type,
            empty_payload,
            range_query_type,
        )

        def decorate(cls: type) -> type:
            existing = self._specs.get(source)
            if existing is not None and not _same_contract(existing, spec):
                raise ValueError(f"duplicate telemetry source: {source}")
            # 동일 계약 재선언(모듈 재로딩)은 멱등 — 다른 계약만 차단함.
            self._specs[source] = spec
            cls.__source_spec__ = spec  # type: ignore[attr-defined]
            cls.source = source  # type: ignore[attr-defined]
            cls.evidence_key = evidence_key  # type: ignore[attr-defined]
            return cls

        return decorate

    def spec(self, source: str) -> TelemetrySourceSpec:
        """Return the source contract or fail if the source is unknown."""
        try:
            return self._specs[source]
        except KeyError as exc:
            supported = ", ".join(self.source_names())
            raise ValueError(
                f"unsupported telemetry query source: {source}; supported: {supported}"
            ) from exc

    def sources(self) -> tuple[TelemetrySourceSpec, ...]:
        """Return all registered source contracts in name order."""
        return tuple(self._specs[name] for name in self.source_names())

    def source_names(self) -> tuple[str, ...]:
        """Return all registered source names in sorted order."""
        return tuple(sorted(self._specs))

    def evidence_keys(self) -> dict[str, str]:
        """source → evidence_key 매핑."""
        return {spec.source: spec.evidence_key for spec in self.sources()}

    def source_for_provider(self, provider_key: str) -> str | None:
        """evidence_key(provider_key) → source 역방향 조회."""
        for spec in self._specs.values():
            if spec.evidence_key == provider_key:
                return spec.source
        return None

    def query_type_for(self, source: str) -> type:
        """Return the query value type for one source."""
        return self.spec(source).query_type

    def range_query_type_for(self, source: str) -> type | None:
        """Return the range query value type when the source supports it."""
        return self.spec(source).range_query_type

    def describe(self) -> str:
        """Build a short text table of registered sources."""
        rows = ["TELEMETRY SOURCES (한눈에 보기)", ""]
        for spec in self.sources():
            rows.append(
                f"{spec.source:<12} evidence_key={spec.evidence_key:<8}"
                f" query={spec.query_type.__name__}"
            )
        return "\n".join(rows)


def _same_contract(a: TelemetrySourceSpec, b: TelemetrySourceSpec) -> bool:
    """모듈 재로딩 시 클래스 객체는 달라져도 계약(이름 기준)이 같으면 동일 선언."""
    return (
        a.evidence_key == b.evidence_key
        and a.query_type.__name__ == b.query_type.__name__
        and a.empty_payload == b.empty_payload
        and _type_name(a.range_query_type) == _type_name(b.range_query_type)
    )


def _type_name(value: type | None) -> str | None:
    """Return a type name while keeping None as None."""
    return value.__name__ if value is not None else None


telemetry = TelemetryRegistry()


def registered_telemetry_sources() -> tuple[TelemetrySourceSpec, ...]:
    """조회 관례(registered_*) — 등록된 소스 계약 전체."""
    return telemetry.sources()


def ensure_sources_loaded() -> None:
    """providers 패키지를 임포트해 @telemetry.source 등록을 보장함."""
    if not telemetry.source_names():
        import importlib

        importlib.import_module("providers")


__all__ = [
    "TelemetryRegistry",
    "TelemetrySourceSpec",
    "ensure_sources_loaded",
    "registered_telemetry_sources",
    "telemetry",
]
