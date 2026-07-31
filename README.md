# Kyro — 수집 실패를 ‘데이터 없음’으로 오판하지 않는 장애 분석

Kubernetes API·Prometheus·Loki·Tempo의 원천별 응답은 유지하고, 출처·대상·시간·수집 상태·잘림 여부를 공통 계약으로 붙였습니다. 불완전한 수집 결과는 삭제 판정의 근거에서 제외했습니다.

**크래프톤 정글 12기 최종 프로젝트 · 5명 · 2026.06.22–07.25**
**담당:** 운영 데이터 수집·정규화 계약, inventory 정합성, FastAPI 조회 API

> 데이터를 많이 모으는 것보다, 어디까지 믿어도 되는지를 함께 전달했습니다.

## 문제와 결정

| 문제 | 결정 | 검증 가능한 결과 |
|---|---|---|
| 빈 배열만으로 실제 부재와 수집 실패를 구분할 수 없음 | `completed`·`partial`·`unavailable` 상태와 reason code를 응답에 포함 | 불완전한 결과의 삭제 권한을 코드에서 차단 |
| 응답을 앞에서부터 자르면 특정 namespace가 사라짐 | 개수·byte 이중 상한과 그룹별 배분을 공통 모듈로 적용 | Kubernetes·Prometheus·Loki·Tempo 4개 수집기에 같은 계약 적용 |
| 배포 설정 원문 전체 조회는 설정값 노출 위험이 큼 | ConfigMap·Secret의 식별자와 사용 위치만 허용 필드 방식으로 투영 | 원문 값 비노출·부분 관측·입력 상한을 16개 경계 테스트로 검증 |

```text
Kubernetes API / Prometheus / Loki / Tempo
                    ↓
       원본별 payload + 공통 수집 맥락
       source · resource · namespace · time
                    ↓
        status · reason · count · bytes
                    ↓
       정합성 판정 / FastAPI / 원인 분석
```

원천별 응답을 억지로 하나의 표로 평탄화하지 않았습니다. 원천별 의미는 보존하고, 모든 소비자가 신뢰 범위를 판단하는 데 필요한 메타데이터만 공통화했습니다.

## 직접 구현

### 1. 불완전한 리소스 목록의 삭제 오판 차단

수집 결과가 잘렸거나 일부 API를 조회하지 못했으면 `delete_authoritative=false`로 판정합니다. 화면이 상태 필드를 놓치더라도 되돌리기 어려운 삭제 경로는 데이터 계층에서 막았습니다.

- [설계와 실패 조건](docs/portfolio/01-collection-contract.md)
- [판정 코드](src/domains/inventory/coverage.py)
- [테스트](tests/test_inventory_coverage.py)

### 2. 잘림을 숨기지 않는 공통 수집 한도

처음에는 원천별 수집기마다 한도 로직을 복제했습니다. 2026-07-11 04:28–06:01, 네 커밋에 걸쳐 공통 모듈을 추출하고 네 수집기로 확장했습니다. 반환 개수와 직렬화 byte를 함께 제한하고, 원본·반환 개수와 잘림 사유를 남겼습니다.

- [설계와 경계](docs/portfolio/02-collection-limits.md)
- [공통 모듈](src/services/target/cluster-agent/providers/collection_limits.py)
- [변경 이력](docs/portfolio/14-development-timeline.md)

### 3. Secret 값 대신 참조 관계를 반환하는 FastAPI

Deployment가 참조하는 ConfigMap·Secret의 이름과 사용 위치만 새 응답 모델에 채웠습니다. PostgreSQL에 저장된 특정 시점의 수집 결과를 조회하는 FastAPI 계약과 16개 경계 테스트를 구현했습니다.

- [API 설계와 한계](docs/portfolio/03-config-reference-api.md)
- [projection](src/domains/inventory/config_references.py)
- [16개 경계 테스트](tests/test_config_references.py)

## 실패 후 바꾼 기준

| 처음의 문제 | 변경 | 남은 한계 |
|---|---|---|
| 가짜 telemetry가 수집 실패를 정상처럼 보이게 함 | 제품 경로에서 fixture와 raw payload 제거 | 저장된 snapshot의 민감값은 별도 정리 필요 |
| provider마다 같은 한도 로직을 반복 | 공통 계약을 먼저 테스트하고 4개 provider에 적용 | 원천별 최적 한도는 운영 부하로 조정하지 못함 |
| 기능 테스트 뒤 입력·응답 경계가 빠짐 | 초기 기능 커밋 39분 뒤 참조 수·문자열·reason code 상한 보강 | 상한 이후 결과를 조회할 페이지네이션 없음 |

[커밋으로 복원한 전체 판단 기록](docs/portfolio/14-development-timeline.md)

## 기여와 검증 경계

- **직접 구현:** 수집·정규화 계약, inventory coverage, ConfigMap·Secret 참조 API, PostgreSQL migration·repository·FastAPI 기능
- **5인 팀 구현:** 원인 판정, GitOps 복구, 프론트엔드, EKS 배포
- **종료 후 별도 검증:** Airflow·카탈로그, AWS 원장 분석, controller·이벤트 전달 실험

종료 후 검증 수치는 개인 구현 성과로 사용하지 않습니다. [파일·커밋·테스트별 기여 근거](docs/portfolio/00-source-and-ownership.md)를 공개합니다.

## 실행

```bash
uv sync --all-groups
make test
make portfolio-verify
```

전체 작업본 기준 품질 게이트와 후속 실험은 [검증 인덱스](docs/portfolio/README.md)에 분리했습니다.

## 한계

- `partial`을 강제하는 소비자는 삭제 경로뿐입니다. 운영 화면과 원인 분석은 상태를 누락할 수 있습니다.
- ConfigMap·Secret API는 값을 반환하지 않지만 upstream snapshot에는 원문이 남습니다.
- 운영 트래픽의 provider별 p95와 실패율을 보존하지 않아 수집 용량을 주장할 수 없습니다.
- 팀 EKS 배포와 시연까지 완료했지만 반복 사용자를 확보한 서비스는 아닙니다.

## 더 보기

- [포트폴리오 증거 인덱스](docs/portfolio/README.md)
- [팀 아키텍처 종료 후 회고와 AWS 원장](docs/portfolio/13-architecture-cost-postmortem.md)
