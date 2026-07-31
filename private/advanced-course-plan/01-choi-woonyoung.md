# 크래프톤 심화과정 학습 계획서 — 최우녕

## 1. 목표

3개월 뒤에는 Python과 Kubernetes 중심으로 쌓은 운영 시스템 경험을
Java/Spring 기반의 백엔드·플랫폼 설계 역량으로 전환하고자 합니다.

단순히 Spring Boot API를 구현하는 수준을 넘어 다음을 달성하는 것이
목표입니다.

- 트랜잭션, 도메인 이벤트, outbox, 재시도와 멱등성을 설명하고 구현한다.
- 4명이 같은 모듈 경계와 요청 흐름을 이해하도록 아키텍처를 문서화하고
  검증한다.
- 배포·관측·장애 복원까지 포함한 실행 가능한 오픈소스 서비스를 만든다.
- 플랫폼/백엔드/DevOps 직무 면접에서 설계 선택과 실패 경험을 근거로
  설명한다.

## 2. 기존 경험과 보완점

Opsia에서 Kubernetes evidence 수집, RCA, Safe PR, 실시간 전송, GitOps
복구 검증, 배포·운영 경계를 폭넓게 다뤘습니다. 이벤트 계약, outbox,
처리 ledger, retry/DLQ 같은 구조를 실제 제품 흐름에 연결한 것이 강점입니다.

반면 기능과 서비스가 커지면서 핵심 가치 흐름이 여러 모듈에 분산됐고,
Python 생태계의 구현 경험을 Java의 타입·트랜잭션·동시성 모델로 다시
설명할 필요가 있습니다. 이번 과정에서는 더 적은 모듈로 명확한 경계를 만들고,
아키텍처 규칙을 테스트로 지키는 경험을 보완합니다.

## 3. 학습 기술과 우선순위

| 우선순위 | 기술 | 선정 이유 |
|---|---|---|
| 1 | Java 21, Spring Boot, Gradle | Java 백엔드의 기본 실행·구성·테스트 역량 확보 |
| 2 | transaction, domain event, outbox, idempotency | 동기화와 비동기 인덱싱의 데이터 정합성 보장 |
| 3 | Spring Modulith/ArchUnit | 모듈러 모놀리스의 의존 방향을 실행 가능한 규칙으로 관리 |
| 4 | Micrometer/OpenTelemetry | 요청·동기화·검색·AI 지연과 실패를 근거로 분석 |
| 5 | Docker, CI/CD, Testcontainers | 팀원이 같은 환경에서 재현하고 검증할 수 있게 함 |

## 4. 담당 역할

- Java multi-module skeleton과 공통 개발 환경
- 모듈 경계, event/outbox, 공통 오류·관측 규약
- CI, Docker Compose, release gate와 운영 runbook
- 동기화·권한 모듈의 교차 리뷰
- 매주 architecture walkthrough 진행

## 5. 개인 산출물

1. 모듈러 모놀리스, 이벤트, outbox, 동기화 경계를 설명하는 ADR 4편 이상
2. ArchUnit 또는 Spring Modulith 기반 architecture fitness test
3. DB transaction과 outbox가 함께 commit되는 통합 테스트
4. OpenTelemetry trace와 핵심 Micrometer 지표 dashboard
5. CI/CD pipeline과 재현 가능한 Docker Compose
6. 부하 테스트, 장애 주입, backup restore 결과 보고서
7. 배포·장애 대응·rollback 절차를 포함한 운영 runbook
8. `Python event-driven system을 Java로 옮기며 바꾼 것` 기술 블로그

## 6. 기간별 계획

### 1개월 차 — Java 포팅과 공동 아키텍처

- 기존 프로젝트의 핵심 수직 흐름과 계약 고정
- Spring Boot 프로젝트와 테스트·CI 기준선 구성
- transaction/event/outbox spike 및 팀 teach-back
- 원본/Java parity test 완성
- 팀원 모두가 실행 흐름을 설명하도록 architecture walkthrough 진행

### 2개월 차 — 제품 핵심과 운영 기반

- 노트·동기화·권한 모듈 통합 지원
- 자동 저장과 동기화의 관측 지표 정의
- Testcontainers 통합 테스트와 CI gate 구성
- 실패 재시도, 중복 요청, 부분 장애 시나리오 검증

### 3개월 차 — 신뢰성·배포·포트폴리오

- 부하·장애 주입·복원 drill
- 보안·성능 결함 수정과 release candidate 운영
- README, ADR, runbook, 시연 영상 완성
- 아키텍처 발표와 플랫폼/백엔드 면접 자료 정리

## 7. 취업 준비

- 목표 직무: Java 백엔드, 플랫폼 엔지니어, DevOps/SRE
- 8월: 기존 이력서의 Opsia 문장을 문제·제약·판단·검증 결과 중심으로 수정
- 9월: Java/Spring, DB transaction, 네트워크, Kubernetes 기술 면접 주 2회
- 10월: 관심 기업 조사와 지원 시작, 시스템 설계 mock interview 격주 진행
- 11월: 3분/10분/30분 프로젝트 설명, 장애·트레이드오프 사례 3개 완성
- 전 기간: 주 3일 코딩 테스트와 오답 유형 기록
