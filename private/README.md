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
| portfolio | [Portfolio Evidence Index](../README.md) | 개인 기여, 후속 확장, AWS 비용 회고를 주장 강도와 함께 확인한다. |
| network cost | [AWS·Git Evidence](../docs/evidence/network-cost/README.md) | Cost Explorer·CloudWatch·Git 원장과 이미지의 단위·한계를 함께 확인한다. |
| AWS bill | [2026년 7월 AWS 청구 원장](../docs/evidence/aws-bill-2026-07/README.md) | 서비스별 시간 요금·데이터 처리 비용·크레딧 상계를 콘솔 화면으로 확인한다. |
| quality check placement | [검사는 어디서 돌아야 하는가](../docs/where-checks-run.md) | 수집 시점·실행 직후·주기 실행·배치로 검사 위치를 분리한다. |

## 문서 목록

- [Project Map](./PROJECT-MAP.md): 현재 runtime, 디렉터리, domain, service, event architecture 지도.
- [Golden Path](./GOLDEN-PATH.md): image pull 장애에서 Safe PR과 후속 evidence 검증까지의 좁은 성공 경로.
- [Cleanup Matrix](./CLEANUP-MATRIX.md): KEEP/LATER/EXPERIMENT/DELETE 분류와 삭제 전 gate.
- [Advanced Course Plan](./advanced-course-plan/README.md): 심화과정 팀 프로젝트·학습 계획과 개인별 제출 문서.
- [Portfolio Evidence Index](../README.md): 직접 기여, 종료 후 확장, 아키텍처 비용 회고.
- [Resume Draft](./resume.md): 확인된 프로젝트 근거만 반영한 지원 이력서 초안.
- [Kyro 카탈로그 학습 가이드](./kyro-학습-가이드.md): 카탈로그 계층을 직접 설명하고 재현하기 위한 단계별 학습 문서.
- [Development Timeline](development-timeline.md): 실패·리뷰·수정 과정을 커밋으로 복원한 기록.
- [AWS·Git Network Evidence](../docs/evidence/network-cost/README.md): Regional Transfer 원본 CSV, 재현 명령, 16:9 증거판.
- [2026년 7월 AWS 청구 원장](../docs/evidence/aws-bill-2026-07/README.md): 서비스 요금·점유 시간·크레딧 상계 콘솔 근거.
- [검사는 어디서 돌아야 하는가](../docs/where-checks-run.md): 정합성 검사 8종의 실행 위치와 이동 계획.

## 아직 없는 문서 표면

- `docs/api`: Bruno collection이 아직 없다.
- `docs/events.md`: 별도 event 문서는 아직 없으며 현재는 [Project Map](./PROJECT-MAP.md)과 [Golden Path](./GOLDEN-PATH.md)에 분산되어 있다.
- 민정/가인/찬빈 전용 onboarding 문서: 아직 없다.

---

## 이 폴더에 대해

여기는 **내부 작업 문서**입니다. 외부 공개용 문서는 [`docs/`](../docs/README.md)에 있습니다.

정리가 끝나면 이 폴더는 통째로 지울 수 있습니다. 지금 남겨 두는 이유는 판단 근거를 잃지 않기 위해서입니다.

- [카탈로그 구현 계획과 진행 상태](catalog-implementation-plan.md) — 무엇이 완료·부분·계획인가
- [Data Foundation 연결](data-foundation-fit.md) — 채용 공고 항목과 실제 작업의 대응
- [근거의 적용 범위](evidence-scope.md) — 로그를 무엇으로 걸렀나
- [이력서 문장과 표현 규칙](resume-sentences.md) — 안전한 문장, 아직 쓰면 안 되는 문장, 무엇을 어떻게 부를지
- [커밋으로 복원한 판단 변화](development-timeline.md)
- [포트폴리오 문서 색인 (구버전)](portfolio-README.md) — `docs/README.md` 로 대체됨
