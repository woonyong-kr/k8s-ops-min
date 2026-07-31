"""서비스 명부 자동 발견 — src/services/**/app.py 스캔이 단일 출처.

기존에는 같은 서비스 목록이 scripts/events.py(SERVICES), 테스트(SERVICE_ENTRYPOINTS),
deploy manifest, 문서 표에 각각 수동으로 존재했음. 이 모듈이 그 목록들을 대체함:
명부가 필요한 곳은 discover_services() 를 읽음. 서비스 추가 = app.py 생성으로 끝.

정적 스캔(import 없음)이라 부작용이 없고, 이름 중복은 즉시 예외(fail-fast).
서비스 identity 는 App("이름")/ServiceSpec(name=...) 리터럴, 없으면 디렉터리명.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# entrypoint 파일명 규약. 이 이름의 파일이 있으면 서비스로 간주함.
ENTRYPOINT_FILENAME = "app.py"
SERVICES_ROOT = Path("src") / "services"
DISCOVERY_IGNORE_PATTERN = re.compile(r"(?m)^\s*RUNTIME_DISCOVERY_IGNORE\s*=\s*True\s*$")

# 런타임 헬퍼 → 서비스 종류. \b 로 FastApiService 내부 부분 문자열 오탐 방지.
_RUNNER_KINDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bApp\("), "worker"),
    (re.compile(r"\bFastApiService\("), "http"),
    (re.compile(r"\bAsyncService\("), "async"),
)

# 서비스 이름 리터럴: App("name") 또는 App(ServiceSpec(name="name", ...))
_NAME_PATTERNS = (
    re.compile(r"""\bApp\(\s*ServiceSpec\(\s*name\s*=\s*["']([^"']+)["']"""),
    re.compile(r"""\bApp\(\s*["']([^"']+)["']\s*\)"""),
)


@dataclass(frozen=True)
class DiscoveredService:
    """발견된 서비스 1개. path 는 저장소 루트 기준 상대 경로."""

    name: str  # App 리터럴 이름(없으면 디렉터리명)
    group: str  # src/services/<group>/<dirname>/
    dirname: str
    path: Path
    kind: str  # worker | http | async

    @property
    def command(self) -> str:
        """k8s manifest 의 command 배열에 들어가는 경로 문자열."""
        return self.path.as_posix()


def discover_services(root: Path) -> tuple[DiscoveredService, ...]:
    """src/services/*/*/app.py 전수 스캔. 이름 중복 시 즉시 예외."""
    services_dir = root / SERVICES_ROOT
    found: list[DiscoveredService] = []
    seen: dict[str, Path] = {}

    for entrypoint in sorted(services_dir.glob(f"*/*/{ENTRYPOINT_FILENAME}")):
        relative = entrypoint.relative_to(root)
        source = entrypoint.read_text(encoding="utf-8")
        if DISCOVERY_IGNORE_PATTERN.search(source):
            continue
        service = DiscoveredService(
            name=_service_name(source, entrypoint.parent.name),
            group=relative.parts[2],
            dirname=entrypoint.parent.name,
            path=relative,
            kind=_service_kind(source, relative),
        )
        if service.name in seen:
            raise ValueError(
                f"서비스 이름 중복: {service.name} ({seen[service.name]} vs {relative})"
            )
        seen[service.name] = relative
        found.append(service)

    if not found:
        raise ValueError(f"서비스를 찾지 못함: {services_dir}")
    return tuple(found)


def _service_kind(source: str, path: Path) -> str:
    for pattern, kind in _RUNNER_KINDS:
        if pattern.search(source):
            return kind
    raise ValueError(f"{path}: App/FastApiService/AsyncService 선언을 찾지 못함")


def _service_name(source: str, fallback: str) -> str:
    for pattern in _NAME_PATTERNS:
        match = pattern.search(source)
        if match:
            return match.group(1)
    return fallback


def describe_services(services: tuple[DiscoveredService, ...]) -> str:
    """make services 용 한눈에 보기 표."""
    rows = ["SERVICES (한눈에 보기)", ""]
    for svc in sorted(services, key=lambda s: (s.group, s.name)):
        rows.append(f"{svc.name:<26} group={svc.group:<11} kind={svc.kind:<7} {svc.command}")
    rows.append("")
    rows.append(f"total: {len(services)}")
    return "\n".join(rows)
