# Docs

> 기준: 현재 저장소 코드와 반복 점검에서 통과한 `make manifest-check`, `make check` 결과를 문서의 source of truth로 둔다.

이 문서는 문서 루트이자 위키형 진입점이다. 새 문서를 추가하면 이 파일의 문서 목록과 키워드 표에 함께 연결한다.

## 역할별 진입점

- 민정: [Golden Path](./GOLDEN-PATH.md)에서 image pull 장애가 evidence, RCA, Safe PR, verification으로 이어지는 순서를 따라간다.
- 가인: [Project Map](./PROJECT-MAP.md)에서 현재 runtime, route surface, worker composition, provider 경계를 먼저 확인한다.
- 찬빈: [Cleanup Matrix](./CLEANUP-MATRIX.md)에서 command, dashboard, permission, GitOps 정리 우선순위와 삭제 gate를 확인한다.

현재 별도 민정/가인/찬빈 onboarding 문서는 없다. 따라서 전용 onboarding을 추가하기 전까지는 위 세 문서가 역할별 시작점이다.

## 키워드 진입점

| 키워드 | 시작 문서 | 현재 기준 |
|---|---|---|
| command | [Cleanup Matrix](./CLEANUP-MATRIX.md) | 직접 cluster command는 PR-only 방향과 충돌하므로 default 경로에서 제거 대상으로 본다. |
| target | [Project Map](./PROJECT-MAP.md) | `cluster-agent`와 target identity가 evidence 수집의 시작점이다. |
| evidence | [Golden Path](./GOLDEN-PATH.md) | `cluster.evidence.received`에서 RCA 입력이 만들어진다. |
| RCA | [Golden Path](./GOLDEN-PATH.md) | 규칙 기반 후보 계획, 평가, blocked/completed 판정을 source of truth로 둔다. |
| Safe PR | [Golden Path](./GOLDEN-PATH.md) | `safe_pr.requested`부터 `safe_pr.created`까지 source authority와 diff policy를 통과해야 한다. |
| dashboard | [Cleanup Matrix](./CLEANUP-MATRIX.md) | 대형 dashboard projection은 core Golden Path 밖으로 분리한다. |
| permission | [Golden Path](./GOLDEN-PATH.md) | read-only agent, PR-only delivery, source authority 실패 처리가 안전 경계다. |
| Bruno | 이 문서 | 현재 `docs/api` Bruno collection은 없다. 추가되면 `tests/test_bruno_collection.py`가 `.bru` 문법을 검사한다. |
| AWS | [Cleanup Matrix](./CLEANUP-MATRIX.md) | 개인 AWS 배포·운영 경로는 공개 core에서 제거 대상으로 본다. |
| event | [Project Map](./PROJECT-MAP.md) | event body와 subject registry, outbox/ledger/retry/DLQ 구조는 유지 대상이다. |
| provider | [Project Map](./PROJECT-MAP.md) | telemetry와 SCM provider는 실제 adapter와 설정 경계를 문서화한다. |
| worker | [Project Map](./PROJECT-MAP.md) | `src/services/**/app.py`의 handler가 현재 worker 설명의 기준이다. |
| test | [Project Map](./PROJECT-MAP.md) | 반복 점검에서 확인한 gate는 `make manifest-check`와 `make check`다. |
| GitOps | [Golden Path](./GOLDEN-PATH.md) | Opsia는 GitOps reconciler를 대체하지 않고 검토 가능한 source 변경 제안을 만든다. |
| realtime | [Project Map](./PROJECT-MAP.md) | `realtime-gateway`는 browser/agent WebSocket 연결 표면이다. |
| portfolio | [Portfolio Evidence Index](./portfolio/README.md) | 개인 기여, 후속 확장, AWS 비용 회고를 주장 강도와 함께 확인한다. |
| network cost | [AWS·Git Evidence](./evidence/network-cost/README.md) | Cost Explorer·CloudWatch·Git 원장과 이미지의 단위·한계를 함께 확인한다. |

## 문서 목록

- [Project Map](./PROJECT-MAP.md): 현재 runtime, 디렉터리, domain, service, event architecture 지도.
- [Golden Path](./GOLDEN-PATH.md): image pull 장애에서 Safe PR과 후속 evidence 검증까지의 좁은 성공 경로.
- [Cleanup Matrix](./CLEANUP-MATRIX.md): KEEP/LATER/EXPERIMENT/DELETE 분류와 삭제 전 gate.
- [Advanced Course Plan](./advanced-course-plan/README.md): 심화과정 팀 프로젝트·학습 계획과 개인별 제출 문서.
- [Portfolio Evidence Index](./portfolio/README.md): 이민정의 직접 기여, Data Foundation 후속 확장, 아키텍처 비용 회고.
- [AWS·Git Network Evidence](./evidence/network-cost/README.md): Regional Transfer 원본 CSV, 재현 명령, 16:9 증거판.

## 아직 없는 문서 표면

- `docs/api`: Bruno collection이 아직 없다.
- `docs/events.md`: 별도 event 문서는 아직 없으며 현재는 [Project Map](./PROJECT-MAP.md)과 [Golden Path](./GOLDEN-PATH.md)에 분산되어 있다.
- 민정/가인/찬빈 전용 onboarding 문서: 아직 없다.
