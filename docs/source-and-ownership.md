[← Kyro로 돌아가기](../README.md)

# 기여와 근거

코드가 존재하는 것, 팀이 만든 것, 프로젝트 기간에 직접 구현한 것, 종료 후 확장한 것은 서로 다른 주장입니다. 이 문서는 공개 문장을 원본 Git 이력과 대조합니다.

## 기준 저장소

- 원본 팀 저장소: [`minmings111/Kyro-jungle-final`](https://github.com/minmings111/Kyro-jungle-final)
- 이 작업본: [`woonyong-kr/k8s-ops-min`](https://github.com/woonyong-kr/k8s-ops-min)
- 비교 시점: 2026-08-01
- 원본 파일: 1,644개, 작업본 파일: 1,725개
- 원본과 byte 단위로 같은 파일: 1,629개
- 공통 경로에서 수정한 파일: 15개
- 이 작업본에만 있는 후속 파일: 81개
- 원본에만 있고 작업본에서 사라진 파일: 0개

`.git`, cache, 실행 중 생성되는 `.catalog-archive`, 정리 대기 디렉터리는 비교에서 제외하고 SHA-256으로 내용을 대조했습니다. 공개 저장소 정리 때문에 legacy ECR account ID만 placeholder로 바꾼 배포 문서도 수정 파일에 포함됩니다. 개수는 기여도의 근거가 아니라 원본 보존 여부를 확인하는 값입니다.

원본 저장소에는 이력 정리와 rebase로 중복된 커밋이 있습니다. 그래서 커밋 수나 총 변경 줄 수는 성과로 사용하지 않습니다. 현재 파일의 blame, 대표 커밋의 diff, 실행 가능한 테스트를 함께 봅니다.

## 직접 구현으로 확인한 범위

### 네 원천의 증거 수집과 공통 한도

| 현재 파일 | 원본 blame에서 직접 작성 | 확인할 내용 |
|---|---:|---|
| `collection_limits.py` | 157 / 157줄 | 공통 개수·크기 상한과 잘림 메타데이터 |
| `metadata_providers.py` | 559 / 613줄 | Kubernetes workload metadata 정규화 |
| `loki_providers.py` | 334 / 447줄 | 로그 구조화·요약·범위 제한 |
| `prometheus_providers.py` | 136 / 366줄 | metric evidence 정규화·상한 적용 |
| `tempo_providers.py` | 89 / 215줄 | trace 검색 결과 축약·상한 적용 |

대표 이력:

- [`7cc428e4f`](https://github.com/minmings111/Kyro-jungle-final/commit/7cc428e4f74a6c8b8fc0759616dc5632f5abbf8f): 공통 `collection_limits.py`를 만들고 Kubernetes·Prometheus에 적용
- [`f533762a5`](https://github.com/minmings111/Kyro-jungle-final/commit/f533762a55f9b0e6e9c560cc13b057d6b0bb0d5e): 한도 계약과 경계 테스트 보강
- [`d42d8c019`](https://github.com/minmings111/Kyro-jungle-final/commit/d42d8c01972cdc5501dae8a492701ce6471ae92f): 같은 계약을 Loki·Tempo까지 확장

이 근거로 말할 수 있는 것은 “네 시스템 전체를 혼자 처음부터 만들었다”가 아닙니다. 각 provider의 기존 팀 코드 위에서 수집·정규화·축약 계약을 직접 구현하고 공통화했다는 것입니다.

### 불완전한 스냅샷의 삭제 오인 방지

- `src/domains/inventory/coverage.py`: 원본 blame 295 / 295줄
- `tests/test_inventory_coverage.py`: 원본 blame 384 / 416줄
- 대표 커밋 [`d29d3c429`](https://github.com/minmings111/Kyro-jungle-final/commit/d29d3c42963335756cf14212f05533e9ea54e57b): 9개 파일, +1,068/-64

이 커밋은 원천별 수집 범위와 잘림 상태를 계산하고, 불완전한 스냅샷이 삭제 권위로 사용되지 않게 만듭니다. 실제 사용자 데이터가 삭제됐다는 운영 사고는 Git만으로 증명되지 않습니다. 그래서 “삭제 사고를 해결했다”가 아니라 “불완전한 수집 결과가 삭제 근거로 쓰일 수 있는 경로를 차단했다”고 적습니다.

### ConfigMap·Secret 참조 조회 API

- `src/domains/inventory/config_references.py`: 원본 blame 681 / 681줄
- `tests/test_config_references.py`: 원본 blame 624 / 624줄
- 대표 커밋 [`05c60fdd9`](https://github.com/minmings111/Kyro-jungle-final/commit/05c60fdd9bfd4a6c42f59cbcb33b22d037dd5577): API·응답 계약·테스트, +1,263/-1
- 초기 기능 커밋 39분 뒤 보강 [`6c082d12a`](https://github.com/minmings111/Kyro-jungle-final/commit/6c082d12af40bc4c97bb08df503434b17d4fb860): 입력·응답 상한과 경계 테스트, +168/-15

이 API는 저장된 manifest 전체를 반환하지 않고 Deployment가 참조하는 ConfigMap·Secret의 식별자와 사용 위치만 새 응답 모델로 만듭니다. Secret 값과 평문 환경변수는 응답에 포함되지 않는 테스트가 있습니다.

현재 구현은 `secretKeyRef.key` 같은 참조 키 이름은 허용합니다. 그래서 “Secret 값은 반환하지 않는다”는 말은 가능하지만 “Secret과 관련된 이름을 전혀 노출하지 않는다”는 말은 사실이 아닙니다. 저장소 원본에도 값이 남아 있으므로 수집 시점 비밀정보 제거 경험으로 확대해서는 안 됩니다.

### FastAPI·PostgreSQL의 세로 구현

대표 커밋 [`e5de71d49`](https://github.com/minmings111/Kyro-jungle-final/commit/e5de71d49): 사용자별 노드 별칭 기능을 위해 Alembic migration, SQLAlchemy model·repository, FastAPI router·계약, 프론트 연결, 테스트를 함께 구현했습니다.

그래서 “FastAPI를 사용해 봤다”보다 다음 주장이 정확합니다.

> 사용자별 노드 별칭을 저장하는 PostgreSQL 스키마와 migration을 설계하고, repository와 FastAPI read/write API, 화면 연결, 테스트까지 한 기능 단위로 구현했다.

### MCP 구현과 대표 성과 제외

- `src/services/mcp/internal_control/tools.py`: 원본 blame 4,864 / 4,864줄
- 전체 등록 도구: 75개
- 시작 커밋 [`bc36959e0`](https://github.com/minmings111/Kyro-jungle-final/commit/bc36959e072c054c3b8d1215f31a6c6630ed632a): 읽기 도구·서버·API client·테스트
- gateway 연결 [`ab3fbf3e7`](https://github.com/minmings111/Kyro-jungle-final/commit/ab3fbf3e7fbeb0cbcaea45e3fcd5a5ed08e165b0)
- AI runtime 연결 [`0071a9095`](https://github.com/minmings111/Kyro-jungle-final/commit/0071a9095601f6f1dc193e27a79c6d7dee9285fa)

구현과 연결 이력은 있습니다. 그러나 최종 Golden Path의 수용 조건과 실제 사용자 검증 범위에서는 제외됐습니다. “MCP 서버를 구현하고 권한·입력 경계를 테스트했다”는 말은 가능하지만, “AI 장애 분석을 MCP로 완성했다”거나 사용 효과를 주장해서는 안 됩니다.

## 팀 성과로만 말할 범위

- 규칙 기반 원인 판정 전체
- GitHub Draft PR 생성과 GitOps 복구 흐름 전체
- 배포 후 재확인 전체
- 프론트엔드 전체
- EKS 배포 전체

일부 연결·화면·배포 설정의 수정 이력이 있어도 위 기능의 전체 소유자로 말하지 않습니다. “5인 팀이 구현했고, 그중 수집·정규화 계층을 담당했다”가 기준입니다.

## 후속 확장: 아직 개인 성과로 쓰지 않을 범위

다음은 이 작업본에만 있고 원본 팀 저장소에는 없는 후속 파일에 포함됩니다.

- `src/domains/datacatalog/`
- `dags/catalog_reconciliation_daily.py`
- `sql/checks/`
- `fixtures/catalog/`
- `src/services/catalog_mcp/`
- `tests/catalog/`

이 코드는 프로젝트 종료 후 개인 작업입니다. 팀 프로젝트 이력에는 없으므로 팀 성과와 섞어 세지 않습니다.

개인 성과로 세기 전에 다음을 통과하기로 정했습니다. 현재 상태를 함께 적습니다.

| | 조건 | 상태 |
|---|---|---|
| 1 | 정상·부분 실패·드리프트 시나리오를 로컬에서 직접 재현 | 완료 — `make demo-fail-source` `make demo-drift` `make demo-duplicate` |
| 2 | 배치가 원천을 중복 조회하는지 확인하고 수정과 테스트를 남김 | 완료 — 입력을 `CollectedSource`/`FixtureSource` 어댑터로 분리, 테스트 9종 |
| 3 | 같은 논리 날짜 재실행·일부 원천 실패·downstream 실패의 결과를 SQL로 설명 | 완료 — `make catalog-verify` 15항목 |
| 4 | 등록 스키마와 관측 스키마의 양방향 차이를 직접 설명 | 완료 — `schema_drift.sql` FULL OUTER JOIN |
| 5 | 카탈로그 조회 router 를 앱에 연결하고 API 테스트 추가 | 완료 — `app.py` 에서 `dependency_overrides` 로 접속을 주입하고 실제 DB 로 API 를 테스트 |
| 6 | 판단과 수정 과정이 변경 이력에 남음 | 완료 — [엔지니어링 로그](engineering-log.md)와 커밋 메시지 |

여섯 조건을 모두 통과했습니다. **이건 "개인 작업으로 세도 되는가"의 기준이고, "Airflow 운영 경험이라고 불러도 되는가"는 별개입니다.** 뒤쪽 기준은 아직 통과하지 않았고 남은 항목을 [배치 파이프라인 문서의 완료 조건](airflow-pipeline.md#완료-조건)에 적어 뒀습니다 — 스케줄러 위에서 실패 시나리오를 실제로 돌려 보는 것이 핵심입니다.

통과가 곧 운영도 아닙니다. 실제 사용자가 이 API 를 호출한 적이 없고, 인가는 MCP 가 올바른 토큰을 보내는 데까지만 검증되어 있습니다. API 가 그 토큰으로 권한을 판정하는 경로는 아직 없습니다.

## 현재 검증 결과

`수집`은 pytest 가 모은 개수이고 `passed`는 실제로 통과한 개수입니다. 나란히 놓으면 앞의 숫자도 통과 수로 읽히므로 구분해 적습니다.

```text
저장소 전체 테스트: 621종 수집 (전량 실행은 하지 않음)
카탈로그 계층: 124 passed
PostgreSQL catalog verification: 15/15 passed
Airflow DAG import errors: 0
Airflow normal dags test: SUCCESS (7 task instances)
```

전체 단위·문서 게이트, PostgreSQL 기반 멱등성·부분 실패·드리프트 검증, 정상 Airflow DAG 실행까지 확인했습니다. 실제 Airflow task 재시도 소진, MinIO 객체 저장, 운영 부하는 아직 검증하지 않았으므로 완료 범위로 확대하지 않습니다.

---

[다음: 빈 결과와 실패를 구분하기 →](collection-contract.md)
