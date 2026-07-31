"""ServiceSpec — 서비스 자기선언(명찰).

서비스의 정체성(이름/그룹/종류/배포형태)을 entrypoint 안에서 한 번만 선언함.
    app = App(ServiceSpec(name="diff-worker", group="gitops"))

이 선언(또는 App("이름") 축약형)이 서비스 명부의 단일 출처.
scripts/events.py·tests·manifest 검증은 discovery 가 이 선언을 읽어 명부를 만듦.
서비스 추가 = app.py 생성으로 끝. 별도 목록 파일 수정 없음.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ServiceKind = Literal["worker", "http", "async"]

DEFAULT_WORKLOAD = "Deployment"


@dataclass(frozen=True)
class ServiceSpec:
    """서비스 메타데이터. name 외에는 관례적 기본값을 가짐."""

    name: str
    group: str = ""  # src/services/<group>/<서비스>/ 의 group (미지정 시 경로에서 유추)
    kind: ServiceKind = "worker"  # worker(NATS 구독) | http | async(자체 루프)
    workload: str = DEFAULT_WORKLOAD  # k8s 배포 형태(Deployment/DaemonSet)

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("ServiceSpec.name 은 비어 있을 수 없음")
