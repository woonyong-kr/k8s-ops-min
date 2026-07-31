# 크래프톤 심화과정 학습 계획서 — 김희준

## 1. 목표

3개월 뒤에는 기존 프로젝트의 핵심 기능을 Spring Boot로 포팅한 경험과,
실제 사용 가능한 마크다운 노트 서비스의 도메인·검색·RAG·MCP를 직접
설계하고 검증한 포트폴리오를 갖추고자 합니다.

기술을 많이 사용했다는 설명보다 다음을 증명하는 것이 목표입니다.

- 원본 동작을 테스트로 고정하고 Java로 안전하게 포팅할 수 있다.
- JPA transaction과 migration을 포함한 노트 도메인을 설계할 수 있다.
- RAG 검색 품질을 고정 dataset과 지표로 비교할 수 있다.
- MCP 도구의 읽기·쓰기·승인·권한 차이를 설명할 수 있다.

## 2. 기존 계획의 보완 방향

기존 계획은 Spring Boot, Next.js, RAG, MCP와 구체적인 주 단위 일정이 있는
점이 좋습니다. 다만 Java가 마지막 한 주의 로그인·백업 API로 한정돼 있고,
`1개월+2개월`, `8주`, `8/18~11/30`이 서로 맞지 않았습니다.

본 계획에서는 8/18~11/17을 3개월 본 기간으로 고정하고, 첫 달부터 Java를
핵심 학습 대상으로 둡니다. 11/18~11/30은 프로젝트 기간이 아니라 이력서와
포트폴리오 정리 기간으로 분리합니다.

현재 문서에는 기존 `나만무` 저장소 주소가 없습니다. 저장소를 확보한 뒤
포팅 범위와 현재 기술 스택을 확정하며, 확인 전에는 코드 분석을 완료했다고
표현하지 않습니다.

## 3. 학습 기술과 우선순위

| 우선순위 | 기술 | 선정 이유 |
|---|---|---|
| 1 | Java 21, Spring Boot, JUnit | Java 백엔드 기본기와 테스트 가능한 계층 설계 |
| 2 | JPA, PostgreSQL, migration | 노트·revision·검색 metadata의 정합성 보장 |
| 3 | 검색, RAG, Spring AI | 단순 LLM 호출이 아닌 근거 기반 검색과 평가 |
| 4 | MCP server | 노트 기능을 표준 도구·리소스로 재사용 |
| 5 | Next.js/React/Tauri | 팀 클라이언트 구현을 이해하고 담당 기능과 연결 |

## 4. 담당 역할

- 기존 `나만무` 핵심 기능의 Java 포팅과 parity matrix
- 노트·folder·tag·link·revision 도메인
- PostgreSQL/JPA schema와 migration
- FTS baseline, RAG indexing/retrieval/citation
- Spring AI 기반 MCP 읽기 도구와 쓰기 승인 prototype
- 이민정의 client 모듈 교차 리뷰

## 5. 개인 산출물

1. `PORTING-SCOPE.md`와 원본/Java 동작 parity matrix
2. 원본 characterization test와 Spring Boot contract test
3. 노트 도메인 모델, ERD, JPA repository와 migration
4. 전문 검색 baseline과 100개 내외의 고정 검색/RAG 평가 dataset
5. Recall@5 또는 MRR, 응답 지연, citation 정확성 비교 보고서
6. 노트 조회·검색 MCP tool/resource
7. 쓰기 도구의 사용자 승인·권한·감사 prototype
8. Java/Spring 학습 기록과 `RAG를 기능이 아니라 평가 가능한 시스템으로 만든 과정`
   기술 블로그

## 6. 기간별 계획

### 1개월 차 — 기존 프로젝트 Java 포팅

- 저장소와 기준 commit, 핵심 사용자 흐름 확정
- 원본 API/DB 동작을 fixture와 테스트로 고정
- Java/Spring/JPA/JUnit 집중 학습과 예제 구현
- 핵심 수직 기능 포팅 및 parity test
- 포팅 과정의 유지/변경/제외 판단 문서화

### 2개월 차 — 노트 도메인과 검색

- 노트 CRUD, revision, folder, tag, 내부 링크 구현
- PostgreSQL migration과 실 DB 통합 테스트
- 전문 검색과 정답 dataset 작성
- 클라이언트·동기화 모듈과 Alpha 통합

### 3개월 차 — RAG·MCP·포트폴리오

- 권한 범위를 지키는 RAG와 citation 구현
- 검색 품질·응답 속도 평가와 개선
- MCP 읽기 도구, 쓰기 승인 prototype
- README, 기술 블로그, 시연 영상, 발표 자료 완성

## 7. 취업 준비

- 목표 직무: Java/Spring 백엔드, AI 응용 백엔드
- 8월: 기존 이력서와 포트폴리오를 Java 전환 목표에 맞게 재작성
- 9월: Java, Spring, JPA, transaction, DB index 기술 설명 주 2회
- 10월: RAG 평가·MCP 권한을 중심으로 프로젝트 문장 완성, 지원 시작
- 11월: 포팅 트레이드오프와 검색 품질 개선 사례를 면접 답변으로 정리
- 전 기간: 주 3일 코딩 테스트, 격주 상호 면접, 월 1회 이력서 리뷰
