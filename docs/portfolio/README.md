# 포트폴리오 증거 인덱스

이 문서는 채용용 주장과 코드 근거를 연결하는 진입점입니다. 원본 팀 프로젝트의 개인 기여, 팀 범위, 종료 후 확장을 섞지 않는 것을 우선합니다.

## 먼저 확인할 문서

- [기여와 근거](source-and-ownership.md): 프로젝트 기간 직접 구현, 팀 범위, 종료 후 실험 구분
- [커밋으로 복원한 판단 변화](development-timeline.md): 실패·리뷰·수정과 검증 근거
- [팀 아키텍처 비용 회고](architecture-cost-postmortem.md): Git 토폴로지와 AWS 원장을 대조한 종료 후 분석

## 직접 구현 근거

- [수집 계약과 completeness](collection-contract.md)
- [응답 한도와 공정한 잘림](collection-limits.md)
- [ConfigMap·Secret 참조 API](config-reference-api.md)
- [근거 범위 오염 차단](evidence-scope.md)
- [대표 성과에서 제외한 범위](scope-decisions.md)
- [판단이 바뀐 지점](engineering-log.md)

## 종료 후 확장

- [메타데이터 카탈로그](metadata-catalog.md)
- [Airflow 재검사 파이프라인](airflow-pipeline.md)
- [정합성 검사 SQL](sql-quality-checks.md)
- [카탈로그 API와 읽기 전용 MCP](catalog-api-mcp.md)
- [기술 선택 리서치](tech-research.md)

이 절의 구현과 수치는 원본 팀 프로젝트의 개인 성과로 사용하지 않습니다. 재현 조건과 남은 한계를 검증하기 위한 별도 확장입니다.

## 지원 자료

- [이력서 초안](../resume.md)
- [Data Foundation 역량 대조](data-foundation-fit.md): 공개 성과표가 아니라 제출 전 공백을 확인하는 내부 점검표
