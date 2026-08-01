# 이력서 문장 초안 (내부)

외부 문서에서 옮겨 온 것입니다. 지원서 작성용이며 저장소 문서에는 두지 않습니다.

출처: `docs/architecture-cost-postmortem.md`

## 8. 이력서에 쓸 문장

현재 증거만으로 안전한 문장:

> Git 배포 이력과 AWS Cost Explorer·CloudWatch를 대조해 management·target manifest의 Deployment 문서 47개와 29.68TB Regional Data Transfer 청구 사용량을 추적했습니다. 관리면 5,173.35GiB out/5,334.27GiB in과 게임→인프라 4,895.28GiB out/5,227.56GiB in을 분리했습니다. 논리적 이벤트 경계는 유지하고 39개 관리 서비스를 단일 controller와 프로세스 내부 이벤트 전달로 조립했습니다. 동일 1,393B 이벤트 1,000건의 로컬 실험에서 전달 완료시간은 373.955ms에서 12.182ms로 줄었습니다. 이 값은 종료 후 전송 계층 실험이며 AWS 비용 절감 결과가 아닙니다.

한 줄형:

> management·target manifest의 Deployment 문서 47개와 AWS 29.68TB Regional Transfer 청구 내역을 대조해 관리면·데이터면 트래픽을 분리했습니다. 종료 후 39개 관리 서비스를 in-process controller로 조립하고 로컬 이벤트 전달 시간을 373.955ms→12.182ms로 검증했습니다.

아직 쓰면 안 되는 문장:

- “32개 워커 때문에 편도 14,841.86 billed GB가 발생했다” — Pod별 귀속이 없음
- “AWS 비용을 96.74% 절감했다” — 재배포 후 비용 비교가 없음
- “82K events/s를 처리하는 시스템” — 전송 microbenchmark일 뿐 end-to-end가 아님
- “프로젝트 기간에 39개 서비스를 통합했다” — controller는 종료 후 확장

지원서에서는 이 사건을 팀 시스템의 아키텍처 회고로 설명하고, 프로젝트 기간의 직접 구현인 수집 계약·부분 실패·잘림 메타데이터와 분리합니다.


## 9. 다음 측정이 완료돼야 닫히는 주장

1. 기존 NATS 모드와 통합 controller 모드를 같은 fixture·같은 24시간 부하로 배포
2. subject별 publish bytes/events, consumer lag p95, handler duration p95 수집
3. AZ별 Pod 배치와 VPC Flow Logs로 Cross-AZ tuple 집계
4. Cost Explorer가 확정된 뒤 daily Regional-Bytes 비교
5. 장애 격리 회귀: 한 handler 실패가 controller 전체를 종료하지 않는지 검증
6. 메모리·CPU p95와 재시작 횟수 비교

이 여섯 항목을 통과해야 비용 감소와 운영 용량을 이력서의 “결과”로 승격할 수 있습니다.


---

출처: `docs/source-and-ownership.md`

## 이력서에 지금 쓸 수 있는 세 문장

- Kubernetes API·Prometheus·Loki·Tempo의 서로 다른 응답을 공통 evidence 계약으로 정규화하고, 개수·크기 상한과 잘림 상태를 함께 전달했다.
- 불완전하거나 잘린 inventory snapshot이 실제 삭제 근거로 쓰이지 않도록 수집 범위와 삭제 권위를 분리했다.
- Deployment의 ConfigMap·Secret 참조 관계만 반환하는 FastAPI를 구현하고, 원문 값 비노출과 비정상적으로 큰 입력의 경계를 테스트했다.

Airflow·데이터 카탈로그·카탈로그 MCP 는 위 승격 조건을 통과했으므로 기술 목록에 넣되, **팀 프로젝트 성과와 섞지 않고 프로젝트 종료 후 개인 작업으로 따로 적습니다.**

---

# 표현 규칙 — 무엇을 어떻게 부를 것인가

기술 문서 본문에 섞여 있던 문장들입니다. 문서는 시스템을 설명하는 자리이고
이력서 표현을 정하는 자리가 아니라, 규칙만 여기로 옮겼습니다.

## 수집 완전성 (`docs/source-and-ownership.md`)

**쓰지 않는다**: "삭제 사고를 해결했다"
**쓴다**: "불완전한 수집 결과가 삭제 근거로 쓰일 수 있는 경로를 차단했다"

이유 — 실제 사용자 데이터가 삭제됐다는 운영 사고는 Git 만으로 증명되지 않습니다.
막은 것은 사고가 아니라 사고로 이어질 수 있는 경로입니다.

## Airflow (`docs/airflow-pipeline.md`)

**아직 쓰지 않는다**: "Airflow 로 운영 파이프라인을 다뤘다"

이유 — 스케줄러를 띄우고 DAG 를 돌린 것과 운영한 것은 다릅니다.
해당 문서의 남은 조건이 끝나기 전까지는 "구현했다"까지만 말합니다.

## 이벤트 처리량 (`docs/architecture-cost-postmortem.md`)

**쓰지 않는다**: "초당 N건을 운영에서 처리했다"

이유 — 원본 배포의 실제 event/s, payload p95, consumer lag 이력이 보존되지
않았습니다. 로컬 전송 실험값은 있지만 그건 운영 처리량이 아닙니다.

## MCP (`docs/catalog-api-mcp.md`)

**쓴다**: "MCP 서버를 구현하고 권한·입력 경계를 테스트했다"
**쓰지 않는다**: "AI 장애 분석을 MCP 로 완성했다", 사용 효과에 대한 주장

이유 — 최종 수용 조건과 사용자 검증 범위에서 제외됐습니다.

## 팀 성과와 개인 작업

Airflow · 데이터 카탈로그 · 카탈로그 MCP 는 프로젝트 종료 후 개인 작업입니다.
팀 프로젝트 성과와 섞어 세지 않습니다. 규칙 기반 원인 판정, Draft PR 생성,
배포 후 재확인, 프론트엔드, EKS 배포는 팀 성과로만 말합니다.
