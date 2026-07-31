# 이민정

Data Foundation 엔지니어 지원  
[GitHub](https://github.com/minmings111)

## About

수집된 값보다 먼저, 그 값이 어디서·언제·어떤 범위에서 만들어졌는지 확인합니다.

해양생물 연구에서는 서로 다른 채집 조건의 플랑크톤 데이터를 비교하며 결과의 신뢰 조건을 검토했습니다. 개발로 전환한 뒤에는 같은 기준을 운영 데이터에 적용했습니다. Kubernetes API·Prometheus·Loki·Tempo의 응답에 공통 신뢰도 계약을 적용하고, 실제 데이터 부재·부분 수집·수집 불가를 구분했습니다.

출처와 한계를 설명할 수 있는 데이터를 API와 AI가 안전하게 활용하도록 만드는 데이터 엔지니어를 지향합니다.

## Project

### Kyro — 수집 실패를 ‘데이터 없음’으로 오판하지 않는 Kubernetes 장애 분석

크래프톤 정글 12기 최종 프로젝트 · 5명 · 2026.06.22–07.25  
담당: 운영 데이터 수집·정규화 계약, inventory 정합성, FastAPI·PostgreSQL

- Kubernetes API·Prometheus·Loki·Tempo의 기존 수집 경로에 출처·대상·시간·수집 상태·잘림 여부를 포함한 공통 계약을 적용했습니다.
- 실제 부재·부분 수집·수집 불가를 `completed`·`partial`·`unavailable`로 구분하고, 불완전한 수집 결과가 삭제 판정의 근거가 되는 경로를 차단했습니다.
- Deployment의 ConfigMap·Secret 참조 관계만 반환하는 FastAPI를 구현했습니다. 원문 값 비노출·부분 관측·입력 상한을 16개 경계 테스트로 검증했습니다.
- 사용자별 노드 별칭 기능의 PostgreSQL schema, Alembic migration, repository, FastAPI read/write API와 화면을 한 기능 단위로 연결했습니다.

**판단과 개선**

- 원천별 수집기에 반복하던 응답 제한 로직을 공통 모듈로 분리했습니다. 초기 기능 커밋 39분 뒤 참조 수·문자열 길이·reason code 수의 상한과 테스트를 보강했습니다.

원인 판정, GitOps 복구, 프론트엔드와 EKS 배포는 5인 팀의 공동 결과입니다.

[코드·커밋·테스트 근거](portfolio/00-source-and-ownership.md)

## Skills

- **Language:** Python
- **Backend:** FastAPI, Pydantic, SQLAlchemy, Alembic
- **Database:** PostgreSQL
- **Data & Observability:** Kubernetes API, Prometheus, Loki, Tempo, OpenTelemetry
- **Infrastructure:** Docker, Helm, AWS EKS 팀 배포 환경
- **AI Integration:** MCP
- **Collaboration:** Git, GitHub, PR, Code Review

## Education

### 크래프톤 정글 12기

- 컴퓨터과학, 운영체제, 네트워크, 자료구조, 팀 기반 제품 개발
- 5인 팀 Kubernetes 장애 분석 프로젝트

## Research Background

- 해양생물 연구에서 플랑크톤 데이터의 채집 조건과 비교 기준을 검토했습니다.
- 서로 다른 조건에서 수집된 결과를 비교하며 값보다 생성 맥락과 재현 가능성을 먼저 확인하는 기준을 익혔습니다.
