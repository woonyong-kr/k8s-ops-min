# 크래프톤 심화과정 통합 프로젝트·학습 계획

기준일: 2026-07-29
프로젝트 본 기간: 2026-08-18 ~ 2026-11-17
포트폴리오 정리 권장 기간: 2026-11-18 ~ 2026-11-30
팀: 이민정(팀장), 최우녕, 김희준, 홍윤기

## 1. 결론

심화과정의 공통 목표는 단순히 새 기능을 많이 만드는 것이 아니다.

> 기존 프로젝트에서 검증한 운영·권한·RAG·MCP 경험을 Java/Spring 생태계로
> 옮기고, 네 명 모두가 같은 아키텍처를 설명하고 다른 핵심 영역을 끝까지 책임질
> 수 있는 상태가 된다.

팀 프로젝트는 **Java/Spring Boot를 중심으로 한 로컬 우선(local-first)
마크다운 노트 서비스**로 정의한다. React/Tauri는 사용자 인터페이스와 데스크톱
패키징에만 사용하고, 핵심 도메인 규칙·동기화·검색·권한·RAG·MCP는 Java
애플리케이션이 소유한다.

첫 달에는 기존 프로젝트 전체를 다시 만들지 않는다. 현재 자료상 김희준 계획서에
명시된 `나만무` 프로젝트를 기본 포팅 대상으로 삼되, 저장소 확인 후 **핵심 수직
기능 1개**를 선정한다. 원본 API 계약과 테스트를 먼저 고정하고 같은 계약을
Spring Boot로 통과시키는 방식으로 포팅한다.

`나만무` 저장소를 8월 18일까지 확인하지 못하거나 포팅 적합성이 낮으면 Opsia의
다음 좁은 흐름을 대체 과제로 사용한다.

`증거 입력 → 장애 판정 → 조치 제안 → 감사 이력 조회`

## 2. 제출 자료 분석

### LMS 작성 원칙과의 정합성

[학습 계획서 작성 가이드](https://jungle-lms.krafton.com/learning/804)는 다음
여섯 가지를 요구한다.

1. 3개월 뒤의 상태를 먼저 정의한다.
2. 기술을 선택한 이유를 설명한다.
3. 모든 기술을 같은 깊이로 다루지 말고 우선순위를 정한다.
4. GitHub 프로젝트, 발표, 기술 블로그처럼 검증 가능한 결과물을 계획한다.
5. 월별 목표를 구체적으로 정한다.
6. 이력서, 기술 면접, 코딩 테스트를 포함한 취업 계획을 작성한다.

현재 [김희준 계획서](https://docs.google.com/document/d/13Kc03h07MkzhH06TglXS7l3xOFxrW6b1-k75c0iO0VA/edit?tab=t.0)는
프로젝트 주제와 기술 후보, 주 단위 일정을 이미 포함한 점이 좋다. 다만 제출 전
다음 항목을 바로잡아야 한다.

- `1개월 포팅 + 2개월 팀 프로젝트`, `기간 8주`, `8/18~11/30`이 서로
  일치하지 않는다.
- Java가 핵심 목표인데 기존 일정에서는 11월의 로그인·백업 API 한 주에만
  등장한다.
- Next.js, Tauri, SQLite, Spring Boot, RAG, MCP를 모두 같은 우선순위로
  제시해 핵심 학습 목표가 흐려진다.
- 첫 달의 공동 아키텍처 설계, 기술 분담 학습, 교차 설명, 통합 기준이 없다.
- 팀 산출물과 김희준 산출물만 있고 나머지 세 사람의 개인 산출물이 없다.
- 검색 품질, 동기화 충돌, 권한, MCP 쓰기 승인 등 성공 기준이 없다.
- LMS가 요구하는 취업 준비 일정이 빠져 있다.

본 문서는 이 문제를 해결해 3개월 본 프로젝트와 2주 이내의 후속 포트폴리오
정리 기간을 분리한다.

## 3. 기존 프로젝트 심층 분석

### 3.1 Opsia — 최우녕·이민정

저장소: [Jungle-303-04/final](https://github.com/Jungle-303-04/final)
내부 구조 문서: [Project Map](https://github.com/Jungle-303-04/final/blob/dev/docs/PROJECT-MAP.md)

#### 어떤 프로젝트인가

Opsia는 Kubernetes에서 수집한 증거를 장애 규칙과 대조하고, 사람이 검토할 수
있는 GitOps 복구 PR을 만든 뒤, 배포 이후의 새 증거로 실제 복구 여부를
검증하는 운영 제어면이다.

핵심 흐름은 다음과 같다.

`Kubernetes/Prometheus/Loki/Tempo 증거 → 사건 및 RCA → 안전한 소스 변경 제안
→ SCM PR → 외부 GitOps 배포 → 후속 증거 검증`

Python 3.13, FastAPI, SQLAlchemy/PostgreSQL, React/Vite, Kubernetes, Helm을
사용한다. 이벤트 계약, correlation/causation, DB outbox, 처리 ledger,
retry와 dead letter 경계를 갖는다.

현재 저장소 실측 규모는 다음과 같다.

- 추적 파일 1,636개
- Python 749개, TypeScript/TSX 385개
- 도메인 디렉터리 44개, 서비스 디렉터리 44개
- Python 테스트 73개, 프론트 테스트 55개
- Alembic migration 49개

#### 강점

- 직접 클러스터를 수정하지 않고 PR을 통해 변경을 검토하게 하는 안전 경계가
  분명하다.
- 원인 판정과 복구 완료를 별도 단계로 두어 “PR 생성=복구 완료”로 오인하지
  않는다.
- event body, subject registry, outbox, ledger, retry, DLQ가 있어 상태 전이와
  장애 복구를 학습하기 좋다.
- read-only agent, source authority, patch 허용 범위, 감사 이력 등 운영
  시스템의 실패 방식을 제품 계약으로 다룬다.
- 이민정은 Kubernetes evidence, Loki/Tempo/Prometheus, MCP 권한 경계,
  API·프론트 연결을 폭넓게 구현했다.
- 최우녕은 RCA, Safe PR, 실시간 전송, 배포·복구, 인프라와 제품 통합을
  장기간 주도했다.

#### 한계와 Java 전환 시 주의점

- 논리적 서비스와 도메인이 40개를 넘어 첫 학습자가 핵심 흐름을 찾기 어렵다.
- 기본 배포는 다수 서비스가 한 Controller 프로세스에 합쳐지므로, 디렉터리
  수만큼 마이크로서비스 이점이 생기지는 않는다.
- 핵심 RCA 외에 dashboard, command, 비용, AI, 배포 실험이 한 저장소에
  누적돼 제품 경계가 넓다.
- 생성 산출물, 과거 제품명, 개인 배포 설정, 큰 프론트 단일 파일 등 정리
  부채가 남아 있다.

따라서 Java 포팅에서 44개 서비스를 그대로 옮기면 안 된다. **모듈러
모놀리스 + 한 개 수직 흐름**으로 시작하고, 이벤트·outbox·감사 계약만
재사용한다.

### 3.2 Nodease — 홍윤기

저장소: [yoonki1207/nodease](https://github.com/yoonki1207/nodease)
제품 요구사항: [PRD](https://github.com/yoonki1207/nodease/blob/dev/docs/PRD.md)
아키텍처: [Architecture](https://github.com/yoonki1207/nodease/blob/dev/docs/architecture.md)

#### 어떤 프로젝트인가

Nodease는 기업 내부 사용자가 자연어와 시각적 그래프로 AI workflow를 만들고,
조직·팀 권한이 허용한 지식을 RAG로 사용하며, 실행 비용·감사·trace를 함께
운영하는 AI Workflow/LLMOps 플랫폼이다.

주요 구성은 다음과 같다.

- Next.js 16/React 19 기반 workflow editor와 관리 UI
- FastAPI Gateway와 SQLAlchemy
- Celery/Redis 기반 workflow engine과 log worker
- PostgreSQL/pgvector 기반 업무 데이터와 RAG
- NSJail 기반 Python 코드 실행 격리
- Docker Compose와 provider-neutral Helm

저장소 실측 규모는 다음과 같다.

- 추적 파일 2,750개, 커밋 1,987개
- Python 1,442개, TSX 311개, TypeScript 176개
- 기능 문서군 19개, ADR 72개
- Python·TypeScript 테스트 파일 약 712개
- 데이터 모델 문서 기준 핵심 관계와 함께 103개 테이블 inventory 관리

#### 홍윤기의 확인된 기여

동일 이메일로 집계되는 `yoonki1207`·`홍윤기` 커밋은 약 161개다. 커밋 이력은
다음 경험을 보여 준다.

- RBAC foundation, 팀 권한, workflow 배포 권한, LLM credential 권한
- 조직 switcher, 초대 알림, 조직 범위 API
- LLMOps UI와 가격 관리자 권한
- RAG 실행 주체와 public-only 검색 경계
- Agent Builder와 workflow editor 안정화
- Alembic head 충돌, demo seed와 migration 정리
- client, gateway, workflow engine, shared DB를 넘나드는 풀스택 통합

#### 강점

- 조직·팀·사용자·리소스 권한을 실행 경로까지 연결한다.
- workflow, RAG, audit, trace, 비용을 하나의 제품 맥락으로 묶는다.
- PRD, 기능별 요구사항/API/component/test case, ADR이 구현과 함께 존재한다.
- credential, secret, 외부 I/O, 비동기 실행을 fail-closed 방식으로 다루는
  규칙이 강하다.
- RAG를 단순 데모가 아니라 권한, citation, 평가, 비용과 연결한다.

#### 한계와 Java 전환 시 주의점

- 기존 Moduly를 확장한 대형 monorepo라 제품명과 과거 구조가 일부 남아 있다.
- Next.js, FastAPI, Celery, Redis, pgvector, NSJail, Helm을 한 번에 이해해야
  해 학습 범위가 넓다.
- 많은 테이블과 보호 리소스 상태 전이는 3개월 프로젝트가 그대로 모방하기엔
  과도하다.
- Java에서 Python의 Celery 계층을 그대로 흉내 내기보다 Spring의 트랜잭션,
  scheduler와 bounded background worker로 재설계해야 한다.

새 프로젝트에는 Nodease 전체가 아니라 **권한 확인 → RAG/MCP 실행 → 감사
기록**이라는 안전 경계를 가져온다.

### 3.3 김희준의 기존 `나만무`

제공된 Google 문서에는 `나만무 프로젝트를 Spring framework로 변환 및
폴리싱`한다는 목표만 있고 저장소 주소, 기능 목록, 기술 스택, 실행 방법은 없다.
따라서 코드 수준의 심층 분석을 했다고 주장할 수 없다.

8월 18일 이전에 다음 자료를 확보해야 한다.

- 저장소 URL과 포팅 기준 commit
- 현재 실행 방법과 환경 변수 목록
- 핵심 사용자 흐름 1개
- API 명세 또는 실제 요청/응답 fixture
- DB schema와 migration
- 현재 통과하는 테스트와 알려진 결함

자료 확보 후 `PORTING-SCOPE.md`에 유지/변경/제외 대상을 기록한다. 자료가
없으면 Opsia의 좁은 흐름을 포팅 대상으로 사용한다.

## 4. 새 프로젝트 정의

### 프로젝트 가칭

**OpenNote** — 로컬 우선 마크다운 지식 노트와 권한 기반 AI 검색

### 해결하려는 문제

- 사용자는 네트워크가 없어도 노트를 작성하고 검색할 수 있어야 한다.
- 여러 기기에서 로그인하면 변경 사항을 안전하게 백업·동기화할 수 있어야 한다.
- 내부 링크, 태그, 전문 검색으로 지식을 연결할 수 있어야 한다.
- RAG 답변은 사용자가 접근 가능한 노트만 근거로 사용하고 citation을
  제공해야 한다.
- MCP의 읽기 도구와 쓰기 도구는 구분되고, 쓰기에는 사용자 승인과 감사
  이력이 필요하다.

### 3개월 뒤 팀 상태

- 네 명 모두 Java/Spring 애플리케이션의 요청, 트랜잭션, 영속화, 비동기 작업,
  보안, 배포 흐름을 그림 없이 설명할 수 있다.
- 각자 한 개 핵심 모듈을 설계·구현·테스트·문서화하고, 다른 사람의 모듈
  하나를 대신 디버깅할 수 있다.
- 설치 가능한 클라이언트와 배포된 서버, 공개 GitHub, 재현 가능한 데모,
  아키텍처 문서와 개인 포트폴리오가 남는다.

### 범위

#### 반드시 구현

- 마크다운 노트 CRUD, 자동 저장, 폴더·태그, 내부 링크
- 로컬 저장과 서버 백업·동기화
- 로그인, 개인 노트 접근 제어
- 전문 검색
- 변경 이력과 감사 로그
- RAG 검색과 citation
- MCP 읽기 도구
- 핵심 흐름 단위·통합·E2E 테스트
- Docker 기반 재현 가능한 실행과 CI

#### 시간이 남으면 구현

- 노트 공유와 세분화된 권한
- MCP 쓰기 도구와 사용자 승인
- 벡터 검색과 hybrid ranking 고도화
- 다중 기기 충돌 해결 UI

#### 명시적으로 제외

- 실시간 Google Docs 수준 공동 편집
- 자체 LLM 학습
- 범용 workflow builder
- Kubernetes 운영을 제품 필수 조건으로 만들기
- 초기 단계의 마이크로서비스 분리

## 5. 권장 아키텍처

### 원칙

1. Spring Boot 모듈러 모놀리스로 시작한다.
2. 도메인 규칙은 controller, ORM, AI SDK에 종속시키지 않는다.
3. 동기화·인덱싱·MCP 쓰기는 멱등성과 감사 이력을 먼저 설계한다.
4. RAG와 MCP는 노트 앱이 안정된 뒤 붙이는 어댑터다.
5. 모든 핵심 모듈에 주 담당자와 교차 리뷰어를 둔다.

### 기술 스택

| 영역 | 선택 | 이유 |
|---|---|---|
| 언어 | Java 21 LTS | 안정된 LTS 기준으로 Java 기본기와 동시성·타입 설계를 학습 |
| 백엔드 | Spring Boot 4.x, Gradle | 현재 Spring 생태계와 Java 애플리케이션 구성 학습 |
| 구조 | Spring Modulith 적용 검토 | 모듈 경계와 이벤트를 검증하되 배포는 하나로 유지 |
| API | Spring MVC, OpenAPI | 명시적인 클라이언트 계약과 검증 가능한 API |
| 영속화 | PostgreSQL, JPA | 서버의 동기화·검색·권한 데이터 관리 |
| 로컬 저장 | SQLite | 오프라인 노트와 자동 저장 |
| 클라이언트 | React/TypeScript + Tauri | 기존 역량을 활용하되 비즈니스 규칙은 Tauri/Rust 계층에 두지 않음 |
| 검색·AI | PostgreSQL FTS 우선, pgvector/Spring AI 후속 | 검색 baseline을 먼저 만들고 RAG 개선을 측정 |
| MCP | Spring AI MCP | Java 서버에서 tool/resource 계약을 구현 |
| 보안 | Spring Security | 인증, 리소스 접근, MCP 실행 주체 검증 |
| 관측 | Micrometer/OpenTelemetry | 요청·동기화·검색·AI 지연과 오류 추적 |
| 테스트 | JUnit 5, Testcontainers, ArchUnit, Playwright | 단위·실 DB 통합·구조·사용자 흐름을 분리 검증 |
| 배포 | Docker Compose, CI | 3개월 안에 재현성과 운영 경험에 집중 |

Spring Boot 4.1은 Java 17 이상을 요구하고 Java 26까지 호환된다. Java 21은
학습 환경의 안정된 LTS 기준으로 선택하며, 팀 환경에서 Java 25를 표준으로
정하면 첫 주 ADR로 변경할 수 있다. Spring AI는 RAG, 평가, MCP client/server
통합을 제공하지만, MCP 보안 일부는 아직 변화 가능성이 있으므로 핵심 권한
검사는 자체 Spring Security 경계에서 유지한다.

### 논리 모듈

| 모듈 | 책임 | 주 담당 | 교차 리뷰 |
|---|---|---|---|
| `notes` | 노트, 폴더, 태그, 내부 링크, revision | 김희준 | 이민정 |
| `client` | 편집기, 자동 저장, 오프라인 상태, 충돌 UX | 이민정 | 김희준 |
| `identity-access` | 로그인, 리소스 권한, 공유 | 홍윤기 | 최우녕 |
| `sync-backup` | 변경 전송, 멱등성, 충돌, 복원 | 홍윤기 | 최우녕 |
| `search-ai` | FTS, indexing, RAG, citation, 평가 | 김희준 | 홍윤기 |
| `mcp` | 읽기/쓰기 도구, 승인, 실행 주체 전달 | 김희준 | 홍윤기 |
| `platform` | 모듈 경계, outbox, CI/CD, 관측, 배포 | 최우녕 | 이민정 |
| `quality` | 공통 테스트 전략, 릴리스 gate | 최우녕 | 전원 순환 |

## 6. 첫 달 운영 방식

### 1주차 — 원본 계약과 아키텍처 기준선

- 포팅할 기존 프로젝트와 기준 commit 확정
- 핵심 사용자 흐름 1개 선정
- 원본 API/DB/event 계약과 characterization test 작성
- Java multi-module skeleton, CI, 코딩 규칙 구성
- context map, sequence diagram, ERD 초안
- ADR-001: 모듈러 모놀리스
- ADR-002: 로컬 저장과 서버 동기화 경계
- Definition of Done과 PR 리뷰 규칙 합의

### 2주차 — 분담 학습과 작은 spike

- 최우녕: Spring transaction, domain event, outbox, observability
- 이민정: Java API 계약, React/Tauri adapter, E2E와 충돌 UX
- 김희준: JPA, migration, SQLite/PostgreSQL, 검색/RAG baseline
- 홍윤기: Spring Security, resource permission, sync idempotency, audit
- 각자 30분 teach-back과 1개 실행 예제 제출
- 다른 팀원이 설명을 재현하지 못하면 학습 완료로 보지 않음

### 3주차 — 수직 기능 포팅

- 원본 요청/응답 fixture를 Java contract test로 실행
- controller → application service → domain → repository 전체 흐름 구현
- 정상·권한 실패·중복 요청·DB 실패 경계 테스트
- 원본과 Java 결과를 parity matrix로 비교

### 4주차 — 통합과 공동 이해

- 네 명의 branch를 한 개 실행 가능한 baseline으로 통합
- 주 담당자가 아닌 사람이 모듈 실행·디버깅
- architecture fitness test와 Testcontainers 통합 테스트
- 포팅 회고: 그대로 옮긴 것/Java에 맞게 바꾼 것/버린 것
- 다음 2개월 기능 backlog와 위험 목록 확정

## 7. 전체 일정

| 기간 | 팀 목표 | 종료 조건 |
|---|---|---|
| 8/18~8/23 | 포팅 범위·계약·아키텍처 기준선 | 기준 commit, contract fixture, ADR 2개, 실행 skeleton |
| 8/24~8/30 | 분담 학습과 spike | 개인 예제 4개, teach-back 4회, 공통 glossary |
| 8/31~9/6 | Java 수직 기능 포팅 | 원본/Java parity test 통과 |
| 9/7~9/13 | 통합·교차 디버깅 | 통합 데모, porting report, architecture v1 |
| 9/14~9/20 | 노트 도메인·클라이언트 뼈대 | CRUD 한 흐름 E2E |
| 9/21~9/27 | 자동 저장·revision·로컬 저장 | 재시작 후 데이터 보존, 실패 복구 테스트 |
| 9/28~10/4 | 폴더·태그·내부 링크·FTS | 검색 baseline과 정답 dataset |
| 10/5~10/11 | 로그인·백업·동기화 | 중복 요청/재시도/충돌 통합 테스트 |
| 10/12~10/18 | Alpha 통합·사용자 테스트 | 5명 이상 테스트, P0/P1 결함 정리 |
| 10/19~10/25 | RAG·citation·평가 | 접근 가능한 노트만 검색, 품질 보고서 |
| 10/26~11/1 | MCP와 감사 | 읽기 도구, 쓰기 승인 prototype, 감사 추적 |
| 11/2~11/8 | 성능·보안·복원 | 부하·권한·backup restore 보고서 |
| 11/9~11/15 | 안정화·문서·오픈소스 | README, ADR, API, 운영 runbook, release candidate |
| 11/16~11/17 | 최종 검증·발표 | 태그 release, 시연 영상, 발표와 회고 |
| 11/18~11/30 | 후속 취업 자료 정리 | 개인 이력서, 포트폴리오, 기술 블로그 |

## 8. 공통 성공 기준

### 제품

- 설치 후 10분 안에 로컬 demo를 실행할 수 있다.
- 네트워크 없이 노트 작성·조회·검색이 가능하다.
- 재시작과 자동 저장 실패 후에도 확정된 노트를 잃지 않는다.
- 같은 동기화 요청을 재전송해도 중복 revision이 생기지 않는다.
- 권한 없는 사용자의 노트가 API, RAG, MCP 결과에 포함되지 않는다.
- RAG 답변에는 사용한 노트의 citation이 포함된다.
- MCP 쓰기 작업은 사용자 승인과 감사 이벤트 없이 실행되지 않는다.
- 백업을 새 환경에 복원하는 drill을 통과한다.

### 품질

- 핵심 수직 흐름의 단위·실 DB 통합·E2E 테스트가 CI에서 실행된다.
- 모듈 간 금지 의존성을 ArchUnit 또는 Modulith test로 막는다.
- 검색 품질은 고정 dataset의 Recall@5 또는 MRR로 매 release 비교한다.
- 성능 목표는 2개월 차 baseline 측정 후 ADR로 확정하며, 숫자를 근거 없이
  신청서에 약속하지 않는다.
- 알려진 제한, 실패 복구, secret 관리 방법을 runbook에 기록한다.

### 협업·학습

- 모든 핵심 모듈은 주 담당자 외 1명이 실행하고 설명할 수 있다.
- 중요한 결정은 ADR로 남기며, 최소 1명의 반대 관점 검토를 받는다.
- 매주 1회 architecture walkthrough와 장애 재현 세션을 진행한다.
- 개인별로 구현 PR, 테스트, 문서, 발표가 모두 남는다.

## 9. 개인별 차별화된 최종 산출물

| 이름 | 성장 목표 | 고유 산출물 |
|---|---|---|
| 최우녕 | Python/Kubernetes 운영 경험을 Java 플랫폼 설계 역량으로 전환 | 모듈 경계 ADR, outbox/관측 구현, CI/CD, 부하·복원 보고서, 운영 runbook |
| 이민정 | 관측·MCP·풀스택 경험을 사용자 흐름과 통합 리딩 역량으로 전환 | 편집기·오프라인 상태 구조, API contract, 충돌 UX, E2E, 사용자 테스트 보고서, 팀 의사결정 로그 |
| 김희준 | Java/Spring 백엔드와 AI 검색을 포트폴리오 핵심으로 확보 | 포팅 parity matrix, note domain/JPA, 검색 dataset, RAG 평가, Java MCP server, 학습 기록 |
| 홍윤기 | Nodease의 RBAC·workflow 경험을 Java 보안·동기화 설계로 전환 | Spring Security/RBAC, 동기화 멱등성, audit/threat model, 권한 테스트 matrix, Nodease-Java 비교 문서 |

개인 제출용 문서는 같은 디렉터리의 다음 파일에 분리했다.

- [4인 통합 제출본](./심화과정-통합-제출본.md)
- [공통 학습 및 프로젝트 계획](./공통-학습-계획.md)
- [최우녕](./01-choi-woonyoung.md)
- [이민정](./02-lee-minjeong.md)
- [김희준](./03-kim-heejun.md)
- [홍윤기](./04-hong-yoonki.md)

## 10. 공통 취업 준비 계획

### 1개월 차

- 기존 이력서에서 “무엇을 만들었다”를 “어떤 제약에서 어떤 판단을 했고 어떤
  결과를 검증했다”로 수정
- 개인별 지원 직무 2종과 기업 기준 작성
- 주 2회 Java/Spring/DB/네트워크 기술 설명 녹화
- 주 3일 코딩 테스트 풀이와 오답 분류

### 2개월 차

- Alpha 데모를 기준으로 이력서 프로젝트 문장 1차 완성
- 격주 상호 기술 면접
- 매주 관심 기업 5곳 조사, 그중 적합한 공고에 지원 시작
- 개인 기술 블로그 1편 발행

### 3개월 차

- 프로젝트 발표를 3분·10분·30분 버전으로 준비
- 장애·트레이드오프·실패 경험 질문에 답할 수 있는 사례 3개 정리
- GitHub README, 포트폴리오, 이력서의 기술 설명을 동일한 사실로 맞춤
- 실제 공고에 주 5건 이상 지원하고 질문·탈락 원인을 회고

## 11. 자료와 기술 선택 근거

- [크래프톤 Jungle LMS 학습 계획서 가이드](https://jungle-lms.krafton.com/learning/804)
- [김희준 기존 계획서](https://docs.google.com/document/d/13Kc03h07MkzhH06TglXS7l3xOFxrW6b1-k75c0iO0VA/edit?tab=t.0)
- [Opsia 저장소](https://github.com/Jungle-303-04/final)
- [Nodease 저장소](https://github.com/yoonki1207/nodease)
- [Spring Boot 시스템 요구사항](https://docs.spring.io/spring-boot/system-requirements.html)
- [Spring AI 개요](https://docs.spring.io/spring-ai/reference/index.html)
- [Spring AI MCP annotations](https://docs.spring.io/spring-ai/reference/api/mcp/mcp-annotations-overview.html)
- [MCP authorization specification](https://modelcontextprotocol.io/specification/2025-03-26/basic/authorization)

## 12. 확인이 필요한 한 가지

김희준의 `나만무` 저장소 URL이 확보되면 첫 달 포팅 대상을 최종 확정해야 한다.
그 전까지 이 문서의 포팅 분석은 프로젝트 코드에 대한 분석이 아니라 신청서에
적힌 계획에 대한 분석이다.
