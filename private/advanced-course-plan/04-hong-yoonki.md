# 크래프톤 심화과정 학습 계획서 — 홍윤기

## 1. 목표

3개월 뒤에는 Nodease에서 다룬 RBAC, workflow, RAG, credential, audit 경험을
Java/Spring Security 기반의 권한·동기화 설계 역량으로 전환하고자 합니다.

기능이 동작하는 것뿐 아니라 다음 보안·데이터 조건을 설명하고 테스트할 수 있는
상태가 목표입니다.

- 사용자와 리소스의 권한을 외부 I/O보다 먼저 검증한다.
- 동기화 재시도와 중복 요청이 데이터를 중복 생성하지 않는다.
- RAG와 MCP가 접근 가능한 노트만 사용한다.
- 쓰기 도구는 승인과 감사 이력 없이 실행되지 않는다.

## 2. 기존 경험과 보완점

Nodease에서 RBAC foundation, 팀 권한, workflow 배포·LLM credential 권한,
조직 switcher와 초대 알림, LLMOps UI, RAG 실행 주체, migration과 demo
흐름을 구현했습니다. client, gateway, workflow engine, shared DB를 함께
다룬 풀스택 경험이 강점입니다.

다만 Python/FastAPI/Celery의 구현을 Java에 그대로 복제하지 않고,
Spring Security, transaction, scheduler와 명시적 모듈 경계로 다시 설계하는
경험이 필요합니다. 이번 과정에서는 넓은 플랫폼보다 노트 접근·동기화·감사라는
좁은 흐름을 깊게 완성합니다.

## 3. 학습 기술과 우선순위

| 우선순위 | 기술 | 선정 이유 |
|---|---|---|
| 1 | Java 21, Spring Boot, Spring Security | Java 기반 인증·인가와 서비스 계층 학습 |
| 2 | resource permission/RBAC | API, RAG, MCP의 동일한 접근 정책 보장 |
| 3 | sync idempotency와 conflict | 재시도·동시 수정에서 데이터 정합성 유지 |
| 4 | audit와 threat modeling | 보안 판단과 쓰기 작업을 추적 가능하게 함 |
| 5 | Testcontainers/보안 통합 테스트 | 실제 DB와 인증 경계의 회귀 방지 |

## 4. 담당 역할

- 로그인과 Spring Security 기본 구성
- 노트·검색·MCP의 리소스 권한 정책
- 백업·동기화 API, idempotency key, conflict 상태
- audit event와 민감정보 redaction
- RAG/MCP 모듈의 보안 교차 리뷰
- 최우녕의 platform/outbox 모듈 교차 리뷰

## 5. 개인 산출물

1. 사용자·노트·공유·MCP 권한 matrix
2. Spring Security 인증과 method/service-level authorization
3. 동기화 protocol, idempotency와 conflict ADR
4. 재시도·중복·동시 수정 Testcontainers 통합 테스트
5. RAG·MCP 접근 제어와 권한 우회 회귀 테스트
6. audit schema, trace 연결, secret/PII redaction 규칙
7. STRIDE 기반 threat model과 수정 결과 보고서
8. `Nodease의 Python 권한 경계를 Spring Security로 옮기며 달라진 점`
   비교 문서와 기술 블로그

## 6. 기간별 계획

### 1개월 차 — Java 보안·동기화 기준선

- 기존 프로젝트의 인증·권한·데이터 경계 확인
- Spring Security와 transaction/JPA 학습 spike
- 권한 matrix와 실패 contract 작성
- 포팅 기능에 service-level authorization과 감사 이력 적용
- 팀 teach-back과 교차 디버깅

### 2개월 차 — 로그인·백업·동기화

- 사용자 인증과 개인 노트 접근 제어
- idempotent backup/sync API
- 충돌 판정과 revision 보존
- 중복·재시도·동시 수정 통합 테스트
- Alpha threat review

### 3개월 차 — RAG/MCP 보안과 검증

- 접근 가능한 노트만 사용하는 retrieval filter
- MCP 실행 주체와 tool permission 연결
- 쓰기 승인, 감사, redaction
- 보안 점검, backup restore, README와 발표 완성

## 7. 취업 준비

- 목표 직무: Java 백엔드, 플랫폼/보안 백엔드, AI workflow 플랫폼
- 8월: Nodease의 RBAC·RAG·workflow 경험을 보안 불변조건 중심으로 재작성
- 9월: Java/Spring Security/transaction/동시성 기술 설명 주 2회
- 10월: 권한 우회·동기화 중복·migration 사례를 면접 STAR 사례로 정리
- 10월부터 플랫폼·AI 백엔드 공고 조사와 지원 시작
- 11월: Nodease와 새 Java 프로젝트의 설계 비교 발표 및 포트폴리오 완성
- 전 기간: 주 3일 코딩 테스트와 격주 상호 기술 면접
