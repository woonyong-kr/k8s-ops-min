[← Kyro로 돌아가기](../README.md) · [문서 목록](README.md)

# 이벤트 파이프라인 지속 부하·장애 복구 실험 계획

> 상태: **실행 전 계획**. 이 문서의 이벤트 수·시간·합격 기준은 목표값이며 측정 결과가 아니다.

## 무엇을 증명할 것인가

다음 세 문장을 실제 로그와 DB 대조 결과로 검증한다.

1. 40개 독립 생산자가 지속적으로 이벤트를 만들어도 소비 속도가 생산 속도를 따라간다.
2. DB 변경과 Outbox 기록 사이에는 유실이 없고, 재전달돼도 업무 결과는 한 번만 반영된다.
3. 반복 실패는 3회에서 끝나고 DLQ로 격리되며, 장애 복구 후 적체가 해소된다.

정확한 Transactional Outbox 설명은 다음과 같다.

```text
업무 데이터 변경 + 발행할 이벤트를 Outbox에 기록
                    └─ 같은 PostgreSQL transaction

transaction commit
        ↓
Outbox relay가 NATS에 발행
        ↓
성공한 event_id만 sent 처리
```

DB transaction 안에서 NATS publish까지 수행하는 구조가 아니다.

## 주장 경계

기본 부하 생성기는 하나의 프로세스 안에서 40개의 독립 `source` task를 실행한다. 실제 서비스 이름을 source로 사용하더라도 **40개 서비스 프로세스를 실행한 것은 아니다**.

- 결과 표현: `40개 독립 이벤트 생산자를 모사`
- 사용 금지: `40개 마이크로서비스를 동시에 운영`
- 실제 Golden Path 검증: 장애 감지→근거 수집→원인 판정→복구 흐름의 실제 handler 일부를 별도 실행

이 실험은 로컬 PostgreSQL·NATS·worker의 내구성과 복구를 검증한다. AWS Cross-AZ 비용이나 EKS 운영 처리량으로 확장하지 않는다.

## 현재 코드의 기준값

| 경계 | 현재 기본값 | 검증할 위험 |
|---|---:|---|
| Outbox relay batch | 10건, 순차 publish | burst 이후 drain 속도 부족 |
| consumer fetch | subject당 1건 | 처리량보다 순서 보장 우선 |
| worker 병렬성 | 서로 다른 subject 최대 8개 | 일부 subject 집중 시 병목 |
| max ack pending | consumer당 100건 | 폭주 시 in-flight 제한 |
| handler timeout | 30초 | 장기 실행 handler 종결 |
| NATS ACK wait | 60초 | 처리 중 조기 재전달 방지 |
| processing stale | 90초 | 죽은 claim 재획득 기준 |
| 재시도 | 최대 3회 | 영구 실패 무한 반복 방지 |
| JetStream 보존 | 512MiB 또는 7일 | 큰 payload·장시간 실행 시 오래된 이벤트 제거 |
| duplicate window | 24시간 | relay 재발행 중복 억제 |

## 실험 환경을 고정하는 방법

매 실행마다 다음 정보를 `run-manifest.json`에 저장한다.

```text
run_id
git_commit
dirty_worktree 여부
실행 시각과 timezone
OS / CPU / RAM
Docker / PostgreSQL / NATS 버전
worker·relay 환경 변수
생산자 수와 초당 이벤트 수
payload 크기와 분포
phase별 시작·종료 시각
장애 주입 명령과 실제 시각
```

결과 디렉터리는 benchmark 스킬의 공용 규칙을 따른다.

```text
.ecc/benchmarks/event-pipeline/<run_id>/
├─ run-manifest.json
├─ events-generated.jsonl
├─ metrics-5s.jsonl
├─ fault-timeline.jsonl
├─ final-reconciliation.json
├─ summary.csv
├─ grafana-dashboard.json
└─ charts/
   ├─ 01-grafana-overview.png
   ├─ 02-grafana-outbox-recovery.png
   ├─ 03-grafana-latency-resources.png
   ├─ 04-grafana-payload-size.png
   └─ 05-grafana-reconciliation.png
```

실험마다 새 DB schema와 새 NATS subject prefix를 사용한다. 이전 실행의 stream·DB 행을 섞지 않는다.

## 실제 메트릭 화면 구성

Grafana를 단순 그림 도구가 아니라 Prometheus 원시 metric의 조회 화면으로 사용한다.

```text
load generator ── benchmark /metrics ─┐
gateway ───────── 기존 /metrics ──────┤
NATS exporter ─── stream·consumer ────┤
PostgreSQL exporter ─ DB 상태 ────────┼─ Prometheus ─ Grafana
cAdvisor ───────── container 자원 ────┘
```

로컬 전용 `docker-compose.benchmark.yml`에 다음을 고정한다.

| 컨테이너 | 역할 | 측정 대상 포함 여부 |
|---|---|---|
| PostgreSQL | 업무·Outbox·ledger·DLQ | 포함 |
| NATS JetStream | 이벤트 broker | 포함 |
| producer / relay / worker | 실제 시험 대상 | 포함 |
| Prometheus | 5초 scrape·보존 | 제외, 관측 도구 |
| Grafana | dashboard·캡처 | 제외, 관측 도구 |
| NATS exporter | stream·consumer metric | 제외, 관측 도구 |
| postgres_exporter | DB metric | 제외, 관측 도구 |
| cAdvisor | container CPU·memory·network | 제외, 관측 도구 |

Prometheus와 Grafana가 만드는 트래픽과 CPU는 시험 대상 합계에서 제외한다. 같은 장비를 사용하므로 `docker stats`에는 보이지만 panel과 summary에서 `monitoring` 그룹으로 분리한다.

benchmark exporter에 다음 metric을 추가한다.

```text
benchmark_events_generated_total{run_id,phase,source}
benchmark_events_processed_total{run_id,phase,status}
benchmark_event_serialized_bytes_bucket{run_id,phase}
benchmark_event_e2e_latency_seconds_bucket{run_id,phase}
benchmark_fault_active{run_id,fault}
benchmark_business_effect_total{run_id,result}
```

기존 gateway metric의 `outbox_pending_total`, `outbox_oldest_age_seconds`, `event_dead_letters_open_total`, `event_processing_status_total`도 같은 dashboard에 표시한다. NATS pending·ack pending·redelivery는 exporter에서 읽는다.

Grafana에는 다음 dashboard를 provisioning한다.

| 행 | panel | 표시 방식 |
|---|---|---|
| 1 | generated / processed / loss / duplicate / DLQ | Stat 숫자 5개 |
| 2 | produced/s·processed/s·consumer pending | time series |
| 3 | Outbox pending·oldest age | time series + NATS 중단 annotation |
| 4 | end-to-end p50·p95·p99 | histogram quantile |
| 5 | producer·relay·worker·DB·NATS CPU/RSS/network | time series |
| 6 | payload size별 events/s·p99·RSS peak·DLQ | Table + curve |

Grafana dashboard 변수는 `run_id` 하나만 사용하고, 캡처 URL의 `from`·`to`를 실험 시작·종료 시각으로 고정한다. 캡처는 Playwright로 자동화해 브라우저 viewport와 파일명을 동일하게 유지한다. Grafana image renderer가 안정적으로 동작하면 panel PNG도 함께 저장한다.

## 1시간 통합 실험

유실·중복·DLQ·복구는 정확한 경계에 실패를 주입하면 1시간 안에 의미 있게 검증할 수 있다. 40개 생산자는 평상시 각각 초당 1건, burst에서는 각각 초당 5건을 만든다.

| 경과 | 단계 | 시간 | 총 생산률 | 예상 이벤트 | 수행 내용 |
|---:|---|---:|---:|---:|---|
| 00:00 | 준비 | 5분 | 40/s | 12,000 | 연결·캐시 안정화, 결과 통계에서 제외 |
| 05:00 | 평상시 | 10분 | 40/s | 24,000 | 정상 생산·relay·consume 기준선 |
| 15:00 | burst | 5분 | 200/s | 60,000 | 최대 backlog·p95/p99 확인 |
| 20:00 | NATS 중단 | 3분 | 40/s | 7,200 | DB write·Outbox 유지, publish만 차단 |
| 23:00 | 복구 | 7분 | 40/s | 16,800 | 신규 유입과 7,200건 backlog 동시 처리 |
| 30:00 | 복구 후 정상 | 10분 | 40/s | 24,000 | lag·Outbox가 다시 안정되는지 확인 |
| 40:00 | 원자성·멱등·DLQ | 8분 | 시나리오 입력 | 수백 건 | transaction·ACK 경계 failpoint |
| 48:00 | payload 크기 sweep | 8분 | 크기별 batch | 수천 건 | 1KiB~1.1MiB 경계 측정 |
| 56:00 | drain·대조·캡처 | 4분 | 0/s | 0 | Outbox 0 확인, SQL 대조, Grafana 저장 |

- warm-up 제외 기본 이벤트: 약 132,000건 + 장애·payload 시나리오 입력
- 복구 목표: NATS 복구 후 7분 안에 Outbox pending 0
- 복구 중 소비 처리량은 신규 유입 40/s보다 커야 한다.
- 1시간 결과로 장기 메모리 누수를 해결했다고 주장하지 않는다.

7분 안에 적체가 해소되지 않으면 실패를 숨기지 않고 Outbox batch 10건·순차 publish·DB claim 비용을 병목 후보로 기록한다.

## 조건부 2시간 연장

기본 제출 증거는 1시간 통합 실험이다. 다음 중 하나가 발생할 때만 총 2시간까지 연장한다.

- 복구 후에도 consumer pending 또는 Outbox pending이 0으로 돌아오지 않음
- 첫 10분과 마지막 10분 사이에 p95 지연 또는 RSS가 계속 상승
- 1시간 결과에서 처리율이 생산율과 너무 가까워 안정 여유를 판단하기 어려움
- CTO 제출 문장에 장기 안정성을 포함하려는 경우

연장 구간은 60분 동안 40/s를 유지하고, payload sweep이나 추가 장애를 섞지 않는다. 이 구간은 메모리·지연·lag의 시간 추세만 본다.

연장 합격 기준:

- 마지막 20분 p95 처리 지연이 연장 첫 20분보다 20% 넘게 증가하지 않음
- 마지막 20분 RSS 중앙값이 연장 첫 20분보다 15% 넘게 증가하지 않음
- consumer pending이 시간에 따라 계속 증가하지 않음
- 종료 시 생성 ID 유실 0, 업무 중복 0, Outbox pending 0

20%·15%는 운영 SLA가 아니라 첫 로컬 회귀 기준이다. 첫 실행 결과와 장비 사양을 고정한 뒤 다음 commit 비교 기준으로 사용한다.

## 원자성·멱등·DLQ 장애 주입

시간을 오래 돌리는 것만으로 원자성과 멱등성을 증명할 수 없다. 40~48분 구간에 transaction과 ACK 경계 실패를 직접 넣는다.

| 장애 지점 | 주입 방법 | 반드시 남아야 하는 결과 |
|---|---|---|
| 업무 write 뒤 commit 전 | benchmark 전용 handler가 예외 발생 | 업무 0건, Outbox 0건 |
| DB commit 뒤 NATS 발행 전 | NATS 중단 | 업무 1건, Outbox pending 1건, 복구 후 처리 1건 |
| NATS publish 뒤 sent 표시 전 | relay를 첫 publish 직후 종료 | 재발행 가능, 최종 업무 반영 1건 |
| handler commit 뒤 ACK 전 | benchmark message wrapper가 첫 ACK 실패 | redelivery≥1, 업무 반영 1건 |
| 항상 실패하는 handler | 동일 이벤트를 재전달 | attempts=3, DLQ=1, 업무 반영 0건 |
| 같은 event_id 반복 입력 | 100개 ID를 각각 10번 전달 | unique 업무 반영 100건 |

임의 시점에 프로세스를 죽이는 방식과 함께, 재현 가능한 benchmark 전용 failpoint를 둔다. production handler의 동작을 바꾸는 환경 변수는 추가하지 않는다.

## payload 크기별 처리

큰 payload는 48~56분 구간의 작은 batch로만 보낸다. 1시간 내내 큰 payload를 보내면 JetStream 512MiB 보존 한계가 먼저 개입해 순수한 크기 영향을 볼 수 없다.

`payload_bytes`는 blob 길이가 아니라 **EventEnvelope 직렬화 이후 최종 byte**를 뜻한다. 생성기는 envelope overhead를 포함해 목표 크기에 맞춘다.

| 최종 event 크기 | 회당 이벤트 | 반복 | 목적 | 예상 판정 |
|---:|---:|---:|---|---|
| 실제 표본 p50 | 200 | 3 | 대표 장애 신호 | 기준 처리량 |
| 1KiB | 200 | 3 | 현재 microbenchmark와 비교 | 정상 |
| 16KiB | 100 | 3 | 상세 metadata | 정상, 지연 변화 기록 |
| 64KiB | 50 | 3 | 큰 요약·근거 | 정상, DB/NATS 증가 기록 |
| 256KiB | 20 | 3 | stress | 정상 여부와 p99 기록 |
| 900KiB | 5 | 3 | NATS 기본 한도 근접 | 성공 여부를 환경값과 함께 기록 |
| 1.1MiB | 3 | 1 | 한도 초과 | `MaxPayloadError`, Outbox DLQ 3건 예상 |

각 크기 그룹은 총 저장량을 제한하고, 결과를 저장한 뒤 전용 subject를 비운다. 크기별로 다음 표를 자동 생성한다.

| serialized bytes | publish p50/p95/p99 | consume p50/p95/p99 | events/s | max Outbox | max consumer pending | RSS peak | DLQ |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 측정값 | 측정값 | 측정값 | 측정값 | 측정값 | 측정값 | 측정값 | 측정값 |

## 5초마다 수집할 지표

| 계층 | 지표 | 출처 |
|---|---|---|
| 생산 | generated total, generated/s, serialized bytes | load generator |
| Outbox | pending, oldest age, sent total | PostgreSQL |
| NATS | stream messages/bytes, consumer pending, ack pending, redelivered | JetStream API |
| 처리 | processed/retrying/dead-lettered, processing duration | PostgreSQL ledger |
| 정합성 | unique generated, unique processed, duplicate business rows | reconciliation SQL |
| 자원 | producer·relay·worker RSS/CPU, PostgreSQL·NATS container 사용량 | process sampler, Docker stats |
| 장애 | start/end, target, command, expected effect | fault injector |

로그에는 모든 payload를 복사하지 않는다. 다음 식별자와 크기만 남긴다.

```text
run_id
phase
producer_id
event_id
correlation_id
subject
serialized_bytes
created_at
staged_at
published_at
processing_started_at
processed_at
attempt
status
```

## 최종 정합성 판정

실험이 끝나면 로그 눈대중이 아니라 ID 집합과 SQL로 대조한다.

```text
생성 unique ID
  = Outbox에 기록된 unique ID

정상 시나리오 생성 ID
  = PROCESSED ID ∪ 예상 DLQ ID

PROCESSED ID ∩ DLQ ID
  = 공집합

business effect row / event_id
  = 정확히 1

종료 시 unsent Outbox
  = 0
```

`0건 유실`은 위 집합 대조가 모두 통과했을 때만 사용한다. 로그 줄 수와 DB 행 수만 비슷한 것은 통과가 아니다.

## 로그와 이미지를 어떻게 남길 것인가

둘 다 필요하지만 역할이 다르다.

- JSONL·SQL 결과: 검증 가능한 원본 증거
- CSV·summary JSON: 계산 결과
- PNG: CTO가 한눈에 판단할 수 있는 설명 자료

스크롤 중인 terminal을 주 증거로 촬영하지 않는다. `metrics-5s.jsonl`과 `final-reconciliation.json`에서 같은 `run_id`의 그래프를 자동 생성한다.

필수 이미지:

1. **처리량과 lag** — produced/s, processed/s, consumer pending
2. **장애와 복구** — NATS 중단 음영, Outbox pending·oldest age 상승과 0 복귀
3. **지연과 메모리** — p50/p95/p99, RSS의 첫 10분·마지막 10분 비교
4. **payload 크기 곡선** — event byte 증가에 따른 events/s·p99·RSS
5. **정합성 결과** — generated/processed/DLQ/pending/duplicate 표

이미지 하단에는 반드시 다음을 넣는다.

```text
run_id · commit SHA · 장비 · 지속시간 · 생산률 · payload 크기 · 측정 시각
```

terminal 캡처는 최종 reconciliation 명령의 `PASS/FAIL` 표 한 장만 보조 자료로 사용한다. Grafana 캡처에는 실제 metric query와 `run_id`가 연결돼 있어야 하며, 이미지만 남기고 Prometheus snapshot·JSONL·SQL 결과를 버리지 않는다.

## 실행 순서

구현 검증용 smoke는 정식 1시간에 포함하지 않고 사전에 5분만 실행한다. smoke가 통과해야 정식 실험을 시작한다.

```text
1. 전용 PostgreSQL·NATS·Prometheus·Grafana·exporter 시작
2. schema·stream·Grafana dashboard provisioning 확인
3. 사전 5분 smoke 후 결과 폐기
4. 새 run_id와 빈 schema로 정식 1시간 시작
5. 평상시→burst→NATS 중단→복구 순서 실행
6. transaction·ACK·DLQ failpoint 실행
7. payload size sweep 실행
8. producer 중단 후 Outbox·consumer drain
9. reconciliation SQL과 ID 집합 대조
10. Grafana 전체·panel PNG 자동 캡처
11. raw JSONL·Prometheus snapshot·summary hash 기록
12. 조건에 해당하면 60분 steady extension
```

## 구현해야 할 도구

| 파일 | 책임 |
|---|---|
| `benchmarks/event_pipeline_load.py` | 40 source, rate, payload 크기, phase 실행 |
| `benchmarks/event_pipeline_faults.py` | NATS/worker 중단과 재현 가능한 failpoint |
| `benchmarks/event_pipeline_sample.py` | 5초 metric·process resource 수집 |
| `benchmarks/event_pipeline_reconcile.py` | ID 집합·DB·DLQ 최종 판정 |
| `benchmarks/event_pipeline_exporter.py` | generator·reconciliation Prometheus metric 노출 |
| `benchmarks/event_pipeline_report.py` | JSONL·Prometheus를 CSV·요약으로 변환 |
| `benchmarks/capture_grafana.py` | 고정 run_id·시간 범위 Grafana PNG 캡처 |
| `docker-compose.benchmark.yml` | PostgreSQL·NATS·Prometheus·Grafana·exporter 고정 version |
| `benchmarks/grafana/event-pipeline.json` | dashboard provisioning |
| `benchmarks/prometheus/prometheus.yml` | 5초 scrape와 target 구성 |

계획서만으로 이력서 수치를 만들지 않는다. 위 도구 구현, smoke, 전체 실행, reconciliation 통과 후 결과 문서로 교체한다.

## 결과 문장 템플릿

모든 합격 조건을 통과한 뒤에만 숫자를 채운다.

> 40개 독립 생산자를 모사한 1시간 복합 부하에서 **{unique_events}건**을 처리했습니다. NATS 중단과 ACK 실패를 주입했지만 ID·DB 대조 결과 유실 **0건**, 업무 중복 **0건**이었고, 반복 실패 이벤트는 **3회** 후 DLQ로 격리됐습니다. 복구 후 Outbox **{max_backlog}건**을 **{recovery_minutes}분** 안에 해소했습니다.

payload 결과는 별도 문장으로 분리한다.

> 직렬화 event 크기를 **{min_size}~{max_size}**로 늘려 처리량·p99·메모리를 비교했고, **{boundary}**에서 한도 동작을 확인했습니다. 한도 초과 이벤트는 재시도 루프에 남기지 않고 DLQ로 격리했습니다.
