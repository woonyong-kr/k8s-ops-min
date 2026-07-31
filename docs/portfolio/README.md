# Portfolio Evidence Index

이 문서는 채용용 주장과 코드 근거를 연결하는 진입점입니다. 원본 팀 프로젝트의 개인 기여, 팀 범위, 종료 후 확장을 섞지 않는 것을 우선합니다.

## 먼저 확인할 문서

- [원본·기여·주장 경계](00-source-and-ownership.md): 이민정 직접 구현, 공동 작업, 팀 범위, 후속 작업 구분
- [Data Foundation 연결](12-data-foundation-fit.md): KRAFTON JD 항목별 직접 근거와 남은 공백
- [아키텍처 비용 회고](13-architecture-cost-postmortem.md): 47개 Deployment 문서와 AWS 29.68TB 사용량을 대조해 실행 경계를 바꾼 과정

## 직접 구현 근거

- [수집 계약과 completeness](01-collection-contract.md)
- [응답 한도와 공정한 잘림](02-collection-limits.md)
- [ConfigMap·Secret 참조 API](03-config-reference-api.md)
- [근거 범위 오염 차단](11-evidence-scope.md)
- [대표 성과에서 제외한 범위](09-scope-decisions.md)
- [판단이 바뀐 지점](10-engineering-log.md)

## 종료 후 Data Foundation 확장

- [메타데이터 카탈로그](04-metadata-catalog.md)
- [Airflow 재검사 파이프라인](05-airflow-pipeline.md)
- [정합성 검사 SQL](06-sql-quality-checks.md)
- [카탈로그 API와 읽기 전용 MCP](07-catalog-api-mcp.md)
- [기술 선택 리서치](08-tech-research.md)

후속 확장은 민정이 코드를 직접 실행·수정·설명하기 전에는 개인 성과로 사용하지 않습니다.
