# 크래프톤 심화과정 학습 계획서 — 이민정

## 1. 목표

3개월 뒤에는 팀장으로서 기능을 나누는 데 그치지 않고, 사용자 흐름과
아키텍처·API 계약을 연결해 팀의 결과물을 하나의 제품으로 완성할 수 있는
개발자가 되고자 합니다.

Opsia에서 쌓은 Kubernetes 관측 데이터, MCP, API와 프론트 연결 경험을
Java/Spring 기반 서비스와 크로스 플랫폼 클라이언트 통합 역량으로 확장합니다.

## 2. 기존 경험과 보완점

Opsia에서 Kubernetes metadata와 evidence, Prometheus/Loki/Tempo,
내부 MCP의 권한·필터 경계, inventory API, React 화면과 상태 흐름을
구현했습니다. 백엔드와 프론트 사이의 계약 문제를 실제 기능으로 연결한 것이
강점입니다.

이번 과정에서는 큰 기능을 빠르게 추가하는 것보다 사용자 흐름, 실패 상태,
API contract, E2E 검증을 먼저 정의하고 팀이 같은 우선순위로 움직이게 하는
능력을 보완합니다.

## 3. 학습 기술과 우선순위

| 우선순위 | 기술 | 선정 이유 |
|---|---|---|
| 1 | Java/Spring Boot API와 validation | 클라이언트와 서버의 명시적 계약 이해 |
| 2 | React/TypeScript/Tauri 상태 구조 | 편집·자동 저장·오프라인 상태를 예측 가능하게 관리 |
| 3 | API contract/OpenAPI | 팀 병렬 작업의 통합 비용 감소 |
| 4 | Playwright와 통합 테스트 | 사용자 관점의 회귀 방지 |
| 5 | conflict UX와 사용자 테스트 | 동기화 실패를 숨기지 않고 해결 가능한 제품 경험 제공 |

## 4. 담당 역할

- 팀 리더와 제품 통합 책임
- 노트 작성·자동 저장·오프라인·충돌 사용자 흐름 정의
- React/Tauri client architecture와 Java API adapter
- OpenAPI contract와 프론트 타입·상태 정합성
- E2E 시나리오와 사용자 테스트
- 팀 결정·위험·의존성 기록과 주간 데모 운영

## 5. 개인 산출물

1. 사용자 journey, wireframe, 실패 상태를 포함한 product flow 문서
2. 노트 editor, 자동 저장, 오프라인 queue, conflict UI 구현
3. Java API와 클라이언트 사이의 OpenAPI contract 및 adapter
4. 핵심 사용자 흐름 Playwright E2E suite
5. 동기화 실패·권한 거절·RAG citation UI 상태 명세
6. 5명 이상 사용자 테스트와 문제 우선순위 보고서
7. 팀 decision log, 위험 register, 주간 demo 기록
8. `관측 가능한 실패 상태를 사용자 경험으로 바꾸는 방법` 기술 블로그

## 6. 기간별 계획

### 1개월 차 — Java 계약 이해와 팀 합 맞추기

- 포팅할 기능의 사용자 흐름과 API fixture 작성
- Spring controller/application/domain 경계 학습
- client adapter와 E2E spike
- 팀 architecture·용어·Definition of Done 합의
- 주 담당자가 아닌 팀원이 기능을 설명하는 교차 walkthrough 운영

### 2개월 차 — 편집 경험과 통합

- 마크다운 편집, 자동 저장, 로컬 상태 구현
- 폴더·태그·내부 링크와 검색 UI
- 로그인·백업·동기화 상태 연결
- Alpha 사용자 테스트와 P0/P1 결함 정리

### 3개월 차 — AI/MCP UX와 제품 완성

- RAG citation과 권한 거절 UI
- MCP 쓰기 승인·감사 이력 사용자 흐름
- E2E와 release checklist 완성
- 시연·발표·README와 포트폴리오 통합

## 7. 취업 준비

- 목표 직무: Java 백엔드, 풀스택, 플랫폼 제품 개발
- 8월: Opsia의 evidence/MCP/프론트 통합 경험을 이력서 사례로 재작성
- 9월: Java/Spring/API/DB와 React 상태 관리 기술 설명 주 2회
- 10월: 팀장 경험을 일정 관리가 아닌 의사결정·위험 해결 사례로 정리
- 10월부터 관심 기업과 공고를 매주 5개 조사하고 적합한 공고에 지원
- 11월: 사용자 문제→설계→실패→검증 결과의 프로젝트 발표 3종 준비
- 전 기간: 주 3일 코딩 테스트와 격주 상호 기술 면접
