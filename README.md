# Kyro — 운영 데이터 수집·정규화와 메타데이터 카탈로그 정합성 검증

쿠버네티스에 익숙하지 않은 사용자가 GitOps 기반으로 장애를 스스로 복구할 수 있게 돕는 서비스입니다. 장애가 나면 운영 데이터를 모아 규칙으로 원인을 판정하고, 수정안을 Draft PR로 올린 뒤, 실제로 복구됐는지 확인합니다.

저는 이 서비스가 쓰는 운영 데이터를 맡았습니다. 수집과 정규화 계약을 만들고, 이후 메타데이터 카탈로그와 배치 파이프라인, 정합성 검사, 조회 API를 설계·구현해 등록한 스키마와 실제 데이터가 어긋나는지 검증하는 계층으로 확장하고 있습니다.

크래프톤 정글 12기 최종 프로젝트 · 5인 팀 · 2026.06.22–07.25

---

## 담당 범위

```mermaid
flowchart LR
    subgraph MINE ["이민정 담당"]
        direction TB
        S["네 소스 수집<br/>Kubernetes · Prometheus<br/>Loki · Tempo"] --> N["정규화<br/>+ 완전성 계약"]
        B["일일 배치<br/>Airflow"] --> C["카탈로그<br/>13개 테이블"] --> Q["정합성 검사<br/>SQL 8개"] --> A["조회 API<br/>+ MCP"]
    end
    subgraph TEAM ["팀원 담당"]
        direction TB
        R["원인 판정<br/>규칙 기반"] --> P["수정안<br/>Draft PR"] --> V["복구 확인"]
    end
    N --> R
    V -. "복구 안 됐으면 재수집" .-> S
    N -. "하루 한 번" .-> B
```

원인 판정과 복구 제안, 프론트엔드는 팀원이 맡았습니다. 제품 아키텍처도 팀 설계입니다.

**위쪽은 장애가 나면 도는 실시간 경로**입니다. 네 소스는 응답 형식도 시간 기준도 실패하는 방식도 다릅니다. 하나로 모으면서 각 데이터를 어디까지 믿어도 되는지 함께 넘기는 것이 제 일이었습니다. 원인 판정은 제가 준 데이터로 돌아가기 때문에, 절반만 왔다는 사실을 알리지 않으면 판정도 절반짜리 근거로 결론을 냅니다.

**아래쪽은 하루 한 번 도는 배치 경로**입니다. 실시간 경로는 수집하는 순간의 완전성만 봅니다. 수집이 성공한 뒤에 데이터가 어긋나는 것은 아무도 보지 않고 있었습니다.

이 저장소는 팀 저장소(`[팀 저장소 링크]`)의 사본입니다. 프론트엔드·GitOps 상태·배포 차트를 포함한 전체 코드가 그대로 들어 있고, 제 담당 범위는 위 그림과 아래 각 항목의 `개인`·`팀` 표시를 따릅니다.

---

## 만든 것

### 운영 데이터 수집·정규화
`Python` `수집 계약` — 팀

- 원천 **4종**(Kubernetes API / Prometheus / Loki / Tempo) 수집기 개발 및 공통 구조 정규화
- 수집 결과 **3-state** 완전성 계약 설계 — 빈 응답 **5가지** 원인을 사유 코드로 구분
- provider **4종**에 흩어진 응답 한도 로직을 공통 모듈 **1개**로 추출 (개수 · 직렬화 바이트 이중 상한)
- 장애 근거 로그 네임스페이스 귀속 필터 및 집계 재계산 — 근거 **1,180줄 → 240줄**, 번들 **66.8KB → 13.3KB**

→ [수집 완전성 계약](docs/portfolio/01-collection-contract.md) · [수집 한도 설계](docs/portfolio/02-collection-limits.md) · [근거의 귀속 범위](docs/portfolio/11-evidence-scope.md)

### 메타데이터 카탈로그 모델링
`데이터 모델링` `스키마 이력` — 개인

- PostgreSQL **13개 테이블** 설계 (자산 / 필드 계약 / 스키마 관측 이력 / 리니지 / 실행 이력 / 품질 결과)
- 유일 제약 **8종**으로 멱등 적재 보장
- 등록 계약과 관측 이력 분리 — 버전을 올리지 않은 스키마 변경까지 검출
- 리니지 역추적 (정규화 행 → S3 원본) 및 **7일** 초과 간선 검출

→ [메타데이터 카탈로그](docs/portfolio/04-metadata-catalog.md) · 확인 `make demo-drift`

### 데이터 품질 검증 SQL
`SQL` `PostgreSQL` — 개인

- 검사 SQL **8본** + 조회 SQL **2본** (재귀 CTE / 윈도 함수 / FULL OUTER JOIN / IS DISTINCT FROM)
- 검사 유형 **6종** — 소스 커버리지 / 필수 필드 / 스키마 드리프트 / 최신성 / 리니지 단절 / 실행 정합성
- 관측 **60만 행** 기준 인덱스 설계 — 검사 전체 **424.5ms → 150.0ms**, 드리프트 검사 **110.9ms → 2.5ms**
- 오탐 검증 포함 **15항목** 자동 검증, 단위 테스트 **24종**

→ [검사 SQL 열 개](docs/portfolio/06-sql-quality-checks.md) · 확인 `make catalog-sql` `make catalog-bench`

### 배치 파이프라인
`Airflow` `멱등 재실행` — 개인

- 소스별 독립 수집·재시도 — **1개** 실패 시 나머지 **3개** 적재 + `PARTIAL` 기록
- 재수집 대상 **4개 → 1개**
- 같은 논리 날짜 **5회** 재실행에 적재 행 수 불변
- backfill 시 원천 재조회 없이 보관본 재생, 입력을 수집 결과 소비로 분리

→ [배치 설계](docs/portfolio/05-airflow-pipeline.md) · [검사는 어디서 돌아야 하는가](docs/portfolio/15-where-checks-run.md) · 확인 `make demo-fail-source`

### 조회 API·MCP 서버
`FastAPI` `MCP` — 팀 · 개인

- FastAPI 엔드포인트 **7종** / 커서 페이지네이션 / 모든 응답에 마지막 실행 상태 부착
- ConfigMap·Secret 참조 조회 API **681줄** — allowlist projection, 경계 조건 **16종**
- MCP 읽기 전용 도구 **6종** — 응답 상한 **50건 · 64KB**, 절단 사실과 원본 개수 반환
- Gateway API 상한값 상수 **91줄** 단일 출처 (단독 작성)

→ [조회 API와 MCP](docs/portfolio/07-catalog-api-mcp.md) · [설정 참조 조회 API](docs/portfolio/03-config-reference-api.md) · 확인 `make catalog-mcp`

---

---

## 확인하기

AWS 계정도 실제 클러스터도 필요 없습니다. Docker 만 있으면 됩니다.

**1단계 — 카탈로그** (Docker 필요, 약 3분)

```bash
make catalog-up        # PostgreSQL · MinIO · Airflow 기동
make catalog-run       # 배치 1회 실행
make catalog-verify    # 검증 15항목      → 15/15 통과
make catalog-test      # 단위 테스트       → 15 passed
make catalog-bench     # 인덱스 전후 비교   → 424.5ms → 150.0ms
```

**2단계 — 고장 시연** (약 2분)

```bash
make demo-fail-source  # Loki 를 끊고 배치     → PARTIAL 기록, 나머지 3개 적재
make demo-drift        # 원천 필드 타입 변경   → 스키마 드리프트 검출
make demo-duplicate    # 같은 날짜 두 번 적재  → 중복 적재 후보 검출
```

정상 데이터로만 시험하면 무엇이든 통과한다고 답하는 검사도 통과합니다. 그래서 일부러 고장 내는 명령을 함께 뒀습니다.

---

## 한계

- 조회 API 가 Secret 값을 반환하지 않을 뿐, 스냅샷 저장소와 S3 원본에는 값이 남습니다
- 부하 한계를 재지 않았습니다. 응답 상한은 상한 계약이지 처리량 근거가 아닙니다
- 실제로 반복 사용한 사용자가 없습니다. 시연에 성공한 것과 쓰인 것은 다릅니다
- 카탈로그가 다루는 것은 이 프로젝트가 만드는 운영 데이터 6종입니다. 외부 업무 시스템 연동은 조사까지만 했습니다
- 배치와 카탈로그는 프로젝트 종료 후 개인 작업이며 AI 코딩 도구를 함께 썼습니다. 설계 판단과 검증은 직접 했고, 각 선택의 대안과 배제 사유를 문서에 남겼습니다
- 팀 저장소는 이력 정리를 여러 차례 거쳤습니다. 커밋 수는 기여의 근거가 아닙니다. 파일 단위 blame 과 코드로 확인하는 편이 정확합니다

---

## 문서

| | |
|---|---|
| [수집 완전성 계약](docs/portfolio/01-collection-contract.md) | 빈 목록 다섯 가지를 어떻게 나눴나 |
| [수집 한도 설계](docs/portfolio/02-collection-limits.md) | 어떤 순서로 잘랐나 |
| [설정 참조 조회 API](docs/portfolio/03-config-reference-api.md) | 열거에서 카나리로 |
| [메타데이터 카탈로그](docs/portfolio/04-metadata-catalog.md) | 13개 테이블과 검사 6종 |
| [배치 파이프라인](docs/portfolio/05-airflow-pipeline.md) | 부분 실패를 어떻게 보존하나 |
| [검사 SQL 열 개](docs/portfolio/06-sql-quality-checks.md) | 질의별 설계와 측정 |
| [조회 API 와 MCP](docs/portfolio/07-catalog-api-mcp.md) | 응답 경계와 권한 |
| [기술 리서치](docs/portfolio/08-tech-research.md) | 쓰지 않기로 한 것들 |
| [범위 판단](docs/portfolio/09-scope-decisions.md) | 만들었지만 걷어낸 것 |
| [엔지니어링 로그](docs/portfolio/10-engineering-log.md) | 판단이 바뀐 지점 |
| [근거의 귀속 범위](docs/portfolio/11-evidence-scope.md) | 로그를 무엇으로 걸렀나 |
| [카탈로그 구현 계획과 진행 상태](docs/portfolio/12a-catalog-implementation-plan.md) | 무엇이 완료·부분·계획인가 |
| [AWS 청구 원장 재확인](docs/evidence/aws-bill-2026-07/) | 7월 청구서 콘솔 캡처와 대조 |
| [검사는 어디서 돌아야 하는가](docs/portfolio/15-where-checks-run.md) | 무엇만 배치여야 하는지와 이동 계획 |

크래프톤 정글 SW-AI Lab 22주 과정을 수료했습니다.
