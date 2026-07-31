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

이 저장소는 팀 저장소([minmings111/Kyro-jungle-final](https://github.com/minmings111/Kyro-jungle-final))의 사본입니다. 프론트엔드·GitOps 상태·배포 차트를 포함한 전체 코드가 그대로 들어 있고, 제 담당 범위는 위 그림과 아래 각 항목의 `개인`·`팀` 표시를 따릅니다.

---

## 만든 것

### 운영 데이터 수집 파이프라인 · 팀

- Kubernetes API / Prometheus / Loki / Tempo 4개 원천 수집기 개발 및 공통 evidence 구조 정규화
- 수집 결과 3-state 완전성 계약 설계 (completed / partial / unavailable + 사유 코드)
- 빈 응답 5가지 원인 구분 (부재 / 권한 없음 / 타임아웃 / 상한 절단 / 원천 무응답)
- provider 4종 공통 응답 한도 모듈 추출 (개수 + 직렬화 바이트 이중 상한, 그룹별 예산 배분)
- 잘림 시 원본 개수와 사유 코드 동반 반환
- 장애 근거 로그 네임스페이스 귀속 필터 및 집계 재계산 (근거 1,180줄 → 240줄, 66.8KB → 13.3KB)
- 노드 지표 수집기(node-collector) 개발

→ [수집 완전성 계약](docs/portfolio/collection-contract.md) · [수집 한도 설계](docs/portfolio/collection-limits.md) · [근거의 귀속 범위](docs/portfolio/evidence-scope.md)

### 배치 파이프라인 및 재처리 · 개인

- Airflow DAG 소스별 독립 수집 / 재시도 / 부분 실패 보존 (1개 실패 시 나머지 3개 적재 + PARTIAL 기록)
- 실행 단위를 DAG 실행과 소스별 수집으로 분리 (부분 실패를 스키마에서 표현 가능하게)
- 멱등 재실행 (같은 논리 날짜 5회 반복에 적재 행 수 불변)
- backfill 시 원천 재조회 없이 보관 원본 재생 (과거 날짜에 현재 값이 적재되는 문제 차단)
- 입력을 수집 결과 소비로 분리 (CollectedSource / FixtureSource 어댑터)
- 재수집 범위 4개 소스 → 실패한 1개
- 고장 주입 시연 3종 (소스 실패 / 스키마 드리프트 / 중복 적재)

→ [배치 설계](docs/portfolio/airflow-pipeline.md) · [검사는 어디서 돌아야 하는가](docs/portfolio/where-checks-run.md) · 확인 `make demo-fail-source`

### 메타데이터 카탈로그 설계 · 개인

- PostgreSQL 13개 테이블 메타데이터 모델 설계
  (자산 / 필드 계약 / 스키마 관측 이력 / 리니지 / 실행 이력 / 품질 결과 / 원본 스냅샷)
- 등록 계약과 관측 이력 분리 (버전을 올리지 않은 스키마 변경까지 검출)
- 유일 제약 11종으로 멱등 적재 보장 (재시도 · backfill 중복 차단)
- 리니지 간선에 확인 시각 저장, 정규화 행 → S3 원본 역추적
- 실시간 3-state와 배치 4-state 상태 어휘 매핑 정의
- 외부 데이터 카탈로그 도구 미도입 결정 (자산 6종 규모에서 운영 비용이 이득을 초과. 자체 카탈로그 구축과의 구분은 [범위 결정](docs/portfolio/scope-decisions.md) 참조)

→ [메타데이터 카탈로그](docs/portfolio/metadata-catalog.md) · [기술 리서치](docs/portfolio/tech-research.md) · 확인 `make demo-drift`

### 데이터 품질 검증 · 개인

- 정합성 검사 SQL 8본 + 조회 SQL 2본 (재귀 CTE / 윈도 함수 / FULL OUTER JOIN / IS DISTINCT FROM)
- 검사 8종 (소스 커버리지 / 필수 필드 누락 / 스키마 드리프트 / 버전 미갱신 변경 / 최신성 SLA / 리니지 단절 / 실행 정합성 / 중복 적재 후보) + 조회 질의 2종
- 통과 · 실패 결과 모두 적재 (검사하지 않은 것과 검사해서 통과한 것을 구분)
- 관측 60만 행 기준 검사 질의 인덱스 설계 (검사 전체 424.5ms → 150.0ms, 드리프트 110.9ms → 2.5ms)
- 관측 470만 행 / 3.4GB 까지 부하 특성 측정 (합계 2,590ms, 병목 질의 1종이 80% 차지, 개선안 1.76배 검증)
- 오탐 검증 포함 15항목 자동 검증 (정상 데이터에서 모든 검사 0행 확인)
- 카탈로그 계층 테스트 70종 (MCP 신뢰 경계 18 · MCP 프로토콜 8 · 조회 API 9 · 결과 적재 키 9 · 그 외 26)

→ [검사 SQL 열 개](docs/portfolio/sql-quality-checks.md) · [측정과 한계](docs/portfolio/load-and-design-limits.md) · 확인 `make catalog-sql` `make catalog-bench`

### 조회 API 및 MCP · 팀 + 개인

- FastAPI 카탈로그 조회 엔드포인트 7종 작성 / 커서 페이지네이션 / 모든 응답에 마지막 실행 상태 부착 (앱 배선 `app.py` · API 테스트 9종 포함)
- ConfigMap · Secret 참조 조회 API (allowlist projection / 카나리 검증 / 경계 조건 16종)
- MCP 읽기 전용 서버 (stdio JSON-RPC · 도구 6종 · 응답 상한 50건 64KB · RFC 8693 토큰 교환과 교환 결과 검증 · 세션 예산 · `principal_sub` 감사 로그)
- MCP 인자 스키마 검증 및 신뢰할 수 없는 입력 표시 (경계 테스트 9종)
- Gateway API 상한값 상수 단일 출처 (91줄, 단독 작성)
- 자연어 → SQL 생성 기능 제외 결정 (생성 질의의 정확성 검증 수단 부재)

→ [조회 API와 MCP](docs/portfolio/catalog-api-mcp.md) · [설정 참조 조회 API](docs/portfolio/config-reference-api.md) · 확인 `make catalog-mcp`

---

## 확인하기

AWS 계정도 실제 클러스터도 필요 없습니다. Docker 만 있으면 됩니다.

**1단계 — 카탈로그** (Docker 필요, 약 3분)

```bash
make catalog-up        # PostgreSQL · MinIO · Airflow 기동
make catalog-run       # 배치 1회 실행
make catalog-verify    # 검증 15항목      → 15/15 통과
make catalog-test      # 카탈로그 테스트   → 70 passed
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
- 관측 470만 행 / 3.4GB 까지 측정했습니다. 그 지점에서 검사 8종 합계 2,590ms 이고 그중 80% 를 중복 적재 검사 하나가 씁니다. 정확성을 위해 전체 스캔을 감수한 결과이며, 2단계 질의로 1.76배 줄어드는 것까지 확인했으나 아직 적용하지 않았습니다. 수억 행 규모는 외삽할 수 없습니다 — [측정과 한계](docs/portfolio/load-and-design-limits.md)
- 관측 데이터에 보존 정책이 없습니다. 상한이 없어 계속 쌓입니다. 계층형 보존과 롤업 설계는 문서에 있으나 구현하지 않았습니다
- 실제로 반복 사용한 사용자가 없습니다. 시연에 성공한 것과 쓰인 것은 다릅니다. 다만 7월 AWS 원장에 로그 수집 64.35GB, LoadBalancer 2,150시간, 공인 IPv4 8,179시간이 남아 있어 한 달 가까이 가동된 것은 확인됩니다 — [청구 원장](docs/evidence/aws-bill-2026-07/README.md)
- 카탈로그가 다루는 것은 이 프로젝트가 만드는 운영 데이터 6종입니다. 외부 업무 시스템 연동은 조사까지만 했습니다
- 배치와 카탈로그는 프로젝트 종료 후 개인 작업입니다. 각 선택의 대안과 배제 사유를 문서에 남겼습니다
- 팀 저장소는 이력 정리를 여러 차례 거쳤습니다. 커밋 수는 기여의 근거가 아닙니다. 파일 단위 blame 과 코드로 확인하는 편이 정확합니다

---

## 문서

읽는 순서를 나눠 두었습니다. 하나만 고른다면 **검사 SQL 열 개**입니다 — 질의마다 왜 그 모양인지, 무엇을 잘못 만들었다가 고쳤는지가 다 들어 있습니다.

**무엇을 만들었나**

- [수집 완전성 계약](docs/portfolio/collection-contract.md) — 원천이 빈 목록을 돌려줄 때, 정말 없는 것인지 못 가져온 것인지를 어떻게 구분했나
- [수집 한도 설계](docs/portfolio/collection-limits.md) — 응답이 한도를 넘을 때 무엇부터 버리고, 버렸다는 사실을 어떻게 남겼나
- [설정 참조 조회 API](docs/portfolio/config-reference-api.md) — Secret 값을 보여주지 않으면서 "이 설정을 누가 쓰는가"에 답하는 방법
- [메타데이터 카탈로그](docs/portfolio/metadata-catalog.md) — 테이블 13개를 등록·실행 이력·관측으로 나눈 이유와 유일 제약 11종
- [배치 파이프라인](docs/portfolio/airflow-pipeline.md) — 네 원천 중 하나만 실패했을 때 나머지를 살리면서 실패를 숨기지 않는 구조
- [조회 API 와 MCP](docs/portfolio/catalog-api-mcp.md) — 모델에게 카탈로그를 열어주되 권한과 응답 크기를 어떻게 묶어 두었나

**어떻게 검증했나**

- [검사 SQL 열 개](docs/portfolio/sql-quality-checks.md) — 질의 하나하나의 설계 근거와, 아무것도 못 잡던 검사를 발견해 고친 기록
- [측정과 한계](docs/portfolio/load-and-design-limits.md) — 관측 470만 행까지 재본 결과와 어느 질의가 먼저 무너지는지
- [AWS 청구 원장 재확인](docs/evidence/aws-bill-2026-07/README.md) — 7월 청구서를 콘솔에서 다시 열어 문서 수치와 대조한 기록

**무엇을 안 만들었나**

- [기술 리서치](docs/portfolio/tech-research.md) — OpenMetadata·DataHub 등을 검토하고 쓰지 않기로 한 근거
- [범위 판단](docs/portfolio/scope-decisions.md) — 만들었다가 걷어낸 것과, 결론이 먼저였던 판단을 남긴 기록
- [검사는 어디서 돌아야 하는가](docs/portfolio/where-checks-run.md) — 배치에 있으면 안 되는 검사를 가려낸 기준

**판단이 바뀐 기록**

- [엔지니어링 로그](docs/portfolio/engineering-log.md) — 처음 생각과 달라진 지점들
- [아키텍처 비용 회고](docs/portfolio/architecture-cost-postmortem.md) — Deployment 47개로 나눈 대가가 청구서에 어떻게 찍혔나

크래프톤 정글 SW-AI Lab 22주 과정을 수료했습니다.
