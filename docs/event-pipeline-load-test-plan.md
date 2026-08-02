[← Kyro로 돌아가기](../README.md) · [문서 목록](README.md)

# 이벤트 파이프라인 지속 부하·장애 복구 실험 계획

> 상태: **실행 전 계획**. 이 문서의 이벤트 수·시간·합격 기준은 목표값이며 측정 결과가 아니다.

> 이 문서는 Outbox·NATS 신뢰성의 **후속 실험**이다. Kubernetes·Prometheus·Loki·Tempo의 payload 변환을 검증하는 주 실험은 [운영 데이터 payload 변환·보존·전송 실험 계획](evidence-payload-experiment-plan.md)에서 다룬다.

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

## 연구 질문과 가설

실험 결과는 기능 목록이 아니라 다음 연구 질문에 답하는 형태로 작성한다.

| ID | 연구 질문 | 검증 가설 | 반증 조건 |
|---|---|---|---|
| RQ1 | 40개 logical source의 지속 신호를 밀리지 않고 처리하는가? | 평상시 40/s에서 처리율이 생산율 이상이고 lag가 누적되지 않는다 | consumer·Outbox pending이 시간에 따라 계속 증가 |
| RQ2 | 순간 5배 부하 뒤 정상 상태로 돌아오는가? | 200/s burst 종료 뒤 backlog가 복구 구간 안에 0으로 돌아온다 | 복구 종료 시 pending>0 또는 oldest age 지속 증가 |
| RQ3 | NATS가 중단돼도 DB 변경과 이벤트가 유실되지 않는가? | 중단 중 Outbox에 보존되고 복구 뒤 모든 ID가 처리 또는 예상 DLQ로 종결된다 | 생성 ID가 Outbox·ledger·DLQ 어디에도 없음 |
| RQ4 | 재전달이 업무 중복을 만드는가? | 같은 event_id가 반복 전달돼도 business effect는 1회다 | event_id별 업무 행이 2개 이상 |
| RQ5 | 영구 실패가 무한 재시도되지 않는가? | 세 번째 실패 후 DLQ 한 건으로 종결된다 | attempts≠3, DLQ 누락·중복 또는 계속 재시도 |
| RQ6 | payload 증가가 처리량·지연·자원에 어떤 영향을 주는가? | 크기가 커질수록 처리량은 감소하고 한도 초과는 명시적으로 격리된다 | silent drop, 무한 재시도, 다른 정상 event까지 중단 |

각 질문은 성공·실패 어느 쪽이든 결과가 된다. 가설을 통과시키기 위해 설정값을 실행 도중 바꾸지 않는다.

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
host disk·전원·thermal·background process
Docker CPU·RAM·swap·disk 할당량
Docker / PostgreSQL / NATS tag·digest·실제 버전
worker·relay 환경 변수
생산자 수와 초당 이벤트 수
payload 크기와 분포
random seed와 source 분포
phase별 시작·종료 시각
장애 주입 명령과 실제 시각
유효성 판정과 오염 사유
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

### 호스트 환경표

실행 직전에 자동 수집하고 보고서 본문에 그대로 싣는다. 비어 있는 항목이 있으면 결과를 공개하지 않는다.

| 구분 | 기록 항목 | 값 |
|---|---|---|
| 장비 | 제조사·모델 | 실행 시 수집 |
| CPU | 모델, physical core, logical core | 실행 시 수집 |
| 메모리 | 설치 RAM, Docker 할당 RAM | 실행 시 수집 |
| 저장장치 | 종류, 여유 공간, Docker volume 위치 | 실행 시 수집 |
| OS | macOS/Linux version, kernel, architecture | 실행 시 수집 |
| 전원 | AC 연결, 저전력 모드, sleep 설정 | 실행 시 수집 |
| Docker | Desktop·Engine·Compose version | 실행 시 수집 |
| 가상화 | Docker VM CPU·RAM·swap·disk limit | 실행 시 수집 |
| 시간 | timezone, clock source, 시작·종료 UTC/KST | 실행 시 수집 |
| 외부 부하 | 실행 중인 주요 background process | 실행 시 snapshot |

노트북은 전원에 연결하고 저전력 모드와 자동 sleep을 끈다. 다른 빌드·동영상 인코딩·대용량 동기화를 중단한다. 이 조치 자체도 `run-manifest.json`에 기록한다.

호스트 전체 자원과 Docker VM에 할당된 자원을 구분한다. 예를 들어 호스트에 32GiB가 있어도 Docker VM 제한이 8GiB라면 이 실험의 직접적인 메모리 상한은 8GiB다. CPU도 호스트 logical core 수와 Docker VM vCPU 수를 각각 기록한다.

### 환경 오염 판정

환경값은 장식용 정보가 아니라 run의 유효성을 판단하는 통제변수다. 정식 실행 전 5분 idle baseline을 수집하고, 실험 중에도 5초 간격으로 호스트와 Docker를 함께 관측한다.

| 요인 | 실행 전 확인 | 실행 중 기록 | 판정 원칙 |
|---|---|---|---|
| CPU | host core·Docker vCPU·전원 모드 | host idle, load average, SUT·monitoring·기타 CPU, thermal pressure | 다른 프로세스의 지속 CPU 점유나 thermal throttling이 있으면 오염 run으로 표시 |
| 메모리 | host RAM·Docker limit·swap limit | host memory pressure, swap 증감, container RSS·cache·OOM | OOM은 FAIL, host swap이나 pressure가 발생하면 자원 제한 결과로 분리 |
| 디스크 | media·filesystem·volume 위치·여유 공간 | read/write bytes, IOPS, latency, fsync/checkpoint, disk busy | 공간 부족·지속 포화·외부 대용량 I/O가 있으면 절대 처리량 비교에서 제외 |
| Docker | Desktop·Engine·Compose, VM CPU/RAM/swap/disk | VM 사용량, container restart·throttle·block I/O | 서로 다른 할당량의 run을 같은 기준선으로 평균내지 않음 |
| NATS | version·digest·JetStream 설정·max payload | store bytes, pending, redelivery, publish/ACK error | 설정 또는 version이 다르면 별도 실험군으로 취급 |
| PostgreSQL | version·digest·pool·WAL·checkpoint 설정 | active connection, transaction, lock, WAL, checkpoint, DB I/O | version·설정·volume이 다르면 별도 실험군으로 취급 |
| 배경 부하 | 상위 CPU·메모리·I/O process snapshot | SUT/monitoring 이외 process의 CPU·RSS·I/O | 30초 이상 지속되는 간섭은 timeline에 남기고 해당 window를 표시 |

오염이 확인됐다고 원본을 삭제하지 않는다. `validity=CONTAMINATED`로 보존하고, 재실행 결과와 나란히 제시한다. 다음 값은 최초 후보 기준이며 장비를 확인한 뒤 smoke 전에 확정하고 결과를 보고 완화하지 않는다.

| 사전 등록할 유효성 기준 | 후보 기준 |
|---|---:|
| 실행 전 저장장치 여유 | 전체의 20% 이상이면서 30GiB 이상 |
| 비의도 background CPU | 60초 이동평균이 host 전체의 10% 미만 |
| thermal pressure | nominal 유지, 30초 이상 warning이면 오염 |
| host swap 증가 | 기준 실험에서 512MiB 미만, 초과 시 자원 제한 결과로 표시 |
| Docker 설정 차이 | 기준값과 하나라도 다르면 비교 run에서 제외 |
| SUT container restart/OOM | restart는 원인별 FAIL, OOM은 자원 한계 FAIL |
| monitoring scrape 누락 | 연속 3회 또는 전체 1% 초과 시 시계열 run 오염 |

SUT 자체가 만든 CPU·메모리·디스크 포화는 오염이 아니라 결과다. 다른 프로그램, 전원 상태 변경, Docker 설정 불일치처럼 실험 밖에서 생긴 간섭만 오염으로 분류한다.

### 자원 계측 항목

| 범위 | 최소 계측값 | 해석 목적 |
|---|---|---|
| host CPU | user·system·idle, load, frequency 또는 thermal state | 파이프라인 병목과 장비 throttling 구분 |
| container CPU | producer·relay·worker·PostgreSQL·NATS별 usage·throttle | 어느 단계가 CPU를 소비하는지 분리 |
| host memory | available, pressure, swap in/out | container 밖의 메모리 간섭 확인 |
| container memory | RSS, cache, working set, limit, OOM | 누수 후보와 Docker limit 영향 확인 |
| host disk | read/write throughput, IOPS, utilization, latency | 로컬 저장장치 포화 확인 |
| PostgreSQL | `pg_stat_database`, WAL bytes, checkpoint, lock, 가능하면 `pg_stat_io` | Outbox·ledger DB 병목 확인 |
| NATS | store bytes, message count, consumer pending, ACK pending, redelivery | broker 보존·소비 병목 확인 |
| network | container별 tx/rx bytes | payload 증가가 내부 전송량에 주는 영향 확인 |

macOS에서 host 지표를 충분히 얻지 못하면 지원되는 항목만 기록하고 `not_observed`로 남긴다. 누락된 값을 0으로 간주하지 않는다.

### 환경 정보 수집 출처

값은 손으로 입력하지 않고 가능하면 다음 출처에서 자동 수집한다.

| 정보 | 수집 출처 |
|---|---|
| macOS·kernel·architecture | `sw_vers`, `uname` |
| CPU·RAM·장비 | `system_profiler`, `sysctl` |
| host memory·swap | `memory_pressure`, `vm_stat`, `sysctl vm.swapusage` |
| host CPU·disk·process | `iostat`, `ps` 주기 수집 |
| Docker 한도·version | `docker info`, Docker Desktop settings, `docker version` |
| container 자원 | Docker stats API·cAdvisor |
| image 불변성 | `docker image inspect`의 RepoDigest·image ID |
| PostgreSQL | `SELECT version()`, `SHOW` 설정 snapshot, `pg_stat_*` |
| NATS | server-reported version, `/varz`, `/jsz`, consumer·stream info |
| 코드·설정 | Git full SHA, diff status, lockfile·compose·benchmark SHA-256 |

macOS의 host CPU와 container CPU는 분모가 다를 수 있다. container CPU는 Docker에 할당한 vCPU 수로 정규화한 값과 원시값을 함께 저장한다.

### 소프트웨어 재현표

| 구분 | 기록 항목 |
|---|---|
| 코드 | repository URL, branch, full commit SHA, dirty 여부 |
| Python | interpreter version, lockfile SHA-256, 주요 package version |
| PostgreSQL | image tag와 digest, server setting, volume 종류 |
| NATS | image tag와 digest, JetStream 설정, max payload |
| Prometheus | image digest, scrape interval, retention |
| Grafana | image digest, dashboard JSON SHA-256 |
| exporter | NATS·PostgreSQL·cAdvisor image digest |
| benchmark | generator·fault·reconcile 파일 SHA-256 |

`latest` image tag는 사용하지 않는다. tag와 digest를 모두 저장한다.

버전 차이의 효과를 주장하려면 commit·workload·Docker 할당량·DB volume 상태를 같게 두고 version만 바꾼 독립 A/B run이 필요하다. 본 실험에서 버전을 단순 기록한 것만으로 PostgreSQL 또는 NATS version이 성능 차이를 만들었다고 결론내리지 않는다.

### 실행 토폴로지

```text
benchmark-producer 1 process / 40 async source
        ↓ PostgreSQL transaction
PostgreSQL: business_effect + outbox
        ↓ outbox-relay 1 process
NATS JetStream 1 node
        ↓ pull consumer
benchmark-worker 1 process / subject concurrency 8
        ↓
event_processing ledger + business_effect

별도 monitoring network
Prometheus + Grafana + exporters + cAdvisor
```

모든 SUT 컨테이너는 같은 로컬 Docker host에 있다. 따라서 네트워크 결과는 Docker bridge를 포함하지만 Kubernetes CNI·EKS control plane·Cross-AZ를 포함하지 않는다.

### 실행 설정표

코드 기본값을 믿고 문서에 손으로 옮기지 않는다. 실행 프로세스가 실제로 읽은 값을 시작 로그와 manifest에 출력한다.

| 설정 | 기준값 | 결과 보고 시 확인 |
|---|---:|---|
| producer 수 | 40 | 실제 기동 source 목록 |
| steady rate | 40/s | 실제 generated/s |
| burst rate | 200/s | 실제 generated/s |
| Outbox batch | 10 | 실행 환경 변수와 시작 로그 |
| relay interval | 실행값 | 실행 환경 변수 |
| publish timeout | 10초 | 실행 환경 변수 |
| fetch batch | 1 | 실행 환경 변수 |
| max concurrency | 8 | 실행 환경 변수 |
| handler timeout | 30초 | 실행 환경 변수 |
| ACK wait | 60초 | NATS consumer config |
| processing stale | 90초 | 실행 환경 변수 |
| max attempts | 3 | 실행 환경 변수 |
| max ack pending | 100 | NATS consumer config |
| stream max bytes | 512MiB | NATS stream info |
| stream max age | 7일 | NATS stream info |
| DB pool | 실행값 | engine config·runtime snapshot |
| Prometheus scrape | 5초 | Prometheus target config |

### workload 정의

- 부하 생성은 처리 완료를 기다리지 않는 **open-loop** 방식으로 한다. closed-loop는 시스템이 느려질수록 입력도 줄어 병목을 숨긴다.
- 40개 source마다 독립 sequence를 사용하고, `run_id/source/sequence`에서 결정적 event_id를 만든다.
- random 분기가 필요하면 seed를 manifest에 고정한다.
- created timestamp는 UTC wall clock, 프로세스 내부 구간 시간은 monotonic clock으로 측정한다.
- 기본 payload는 실제 장애 이벤트 계약에서 비밀정보를 제거한 표본으로 만들고 serialized p50·p95를 먼저 기록한다.
- subject 분포는 평상시 균등 분포를 사용한다. burst 5분은 앞 2분 30초 균등, 뒤 2분 30초는 전체의 80%를 한 subject에 보내 순차 처리 hotspot을 확인한다.

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
| 15:00 | burst | 5분 | 200/s | 60,000 | 균등 2분 30초 + 80% single-subject hotspot 2분 30초 |
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

## 2시간을 사용할 때의 선택

기본 제출 증거는 1시간 통합 실험이다. 추가 1시간은 첫 run 종료 전에 목적을 선택한다.

- 첫 1시간에 RSS·지연·lag의 시간 증가가 보이면 40/s steady를 60분 연속 실행해 추세를 확인한다.
- 첫 1시간이 안정적이고 재현성을 확인하려면 모든 상태를 초기화하고 같은 1시간 실험을 독립적으로 한 번 더 실행한다.
- 신뢰성은 통과했지만 자원 제약에 따른 병목을 탐색하려면 S0~S4 자원 민감도 실험을 실행한다.
- 첫 1시간에 기능 결함이 나오면 설정을 조정해 억지로 통과시키지 않고 종료한다. 원인 수정은 새 commit과 새 run으로 검증한다.

연속 steady를 선택할 때는 payload sweep이나 추가 장애를 섞지 않는다. 이 구간은 메모리·지연·lag의 시간 추세만 본다.

연속 실행의 첫 로컬 회귀 기준:

- 마지막 20분 p95 처리 지연이 연장 첫 20분보다 20% 넘게 증가하지 않음
- 마지막 20분 RSS 중앙값이 연장 첫 20분보다 15% 넘게 증가하지 않음
- consumer pending이 시간에 따라 계속 증가하지 않음
- 종료 시 생성 ID 유실 0, 업무 중복 0, Outbox pending 0

20%·15%는 운영 SLA가 아니다. 첫 장비·commit·Docker 할당량에서 후속 변경을 비교하기 위한 회귀 기준이며, 다른 환경에 일반화하지 않는다.

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

## 측정값의 정의

이 정의를 실행 전에 고정하고 결과를 본 뒤 바꾸지 않는다.

| 측정값 | 계산 정의 |
|---|---|
| generated unique | generator가 생성 완료 로그를 남긴 distinct event_id |
| staged unique | Outbox에 존재하는 distinct event_id |
| processed unique | ledger status=`processed`인 distinct event_id |
| expected DLQ | 사전에 failpoint 대상으로 지정한 distinct event_id |
| loss | generated accepted − processed − expected DLQ − final pending |
| duplicate delivery | event_id별 delivery attempt−1 합계 |
| duplicate business effect | event_id별 업무 행 수가 1을 초과한 행의 초과분 |
| throughput | 5초 window에서 terminal 상태로 전환된 event 수 ÷ 5 |
| end-to-end latency | event created_at부터 processed 또는 DLQ terminal_at까지 |
| processing latency | handler 시작부터 DB transaction commit까지 |
| recovery time | NATS 복구 시각부터 Outbox pending=0이 3회 연속 scrape될 때까지 |
| max backlog | 실험 중 Outbox pending의 최댓값 |
| consumer lag | NATS pending + ack pending, redelivery는 별도 표시 |
| payload 처리량 | payload 크기별 terminal event / 측정 wall time |
| memory drift | 첫 안정 window와 마지막 안정 window의 RSS 중앙값 차이 |

NATS 중단 중 생성 시도 자체가 DB transaction 전에 실패한 경우는 `generated accepted`에 넣지 않는다. 어느 모집단을 분모로 삼았는지 결과표에 함께 쓴다.

## 반복과 통계

1시간 제한과 논문 수준의 반복성은 같은 요구가 아니다.

### 1시간 실행에서 가능한 주장

- deterministic ID 대조를 통한 유실·중복 여부
- 정해진 failpoint의 transaction·ACK·DLQ 결과
- 해당 장비·commit·설정에서 관측한 처리량·p50·p95·p99
- payload 크기 그룹별 3회 반복의 중앙값과 범위

한 번의 1시간 시계열만으로 일반적인 장기 안정성이나 통계적 재현성을 주장하지 않는다.

### 최대 2시간을 사용할 경우

세 가지 중 하나를 실행 전에 선택한다.

| 선택 | 구성 | 얻는 것 | 얻지 못하는 것 |
|---|---|---|---|
| A. 연속 2시간 | 1시간 통합 + 1시간 steady | 메모리·지연·lag 장기 추세 | 독립 반복 |
| B. 독립 2회 | 1시간 통합을 reset 후 2회 | 재현 여부와 run 간 범위 | 2시간 연속 soak |
| C. 자원 민감도 | 1시간 통합 + S0~S4 12분씩 | CPU·RAM·disk·background 영향 후보 | 독립 반복·장기 추세 |

CTO 제출 목적에는 기본적으로 **B. 독립 2회**를 우선한다. 같은 결과가 두 번 재현되는지가 한 번 오래 돌리는 것보다 방어하기 쉽다. 메모리 증가가 관측되면 A, 환경 제약에 따른 병목을 별도 질문으로 다룰 때만 C를 선택한다.

통계 표기 규칙:

- event latency: phase별 p50·p95·p99와 최대값
- payload group 3회: 중앙값, 최솟값, 최댓값
- 독립 2회: 두 run을 모두 표시하고 median·range만 사용
- 95% 신뢰구간은 독립 run 3회 이상일 때만 계산
- 5초 시계열 점을 독립 표본으로 간주해 가짜 신뢰구간을 만들지 않음
- 서로 다른 장비에서 나온 수치를 한 평균으로 합치지 않음

## 자원 민감도 실험

CPU·메모리·디스크·배경 부하가 결과에 미치는 영향은 본 1시간 run의 변동만 보고 추정하지 않는다. 본 실험과 같은 commit·payload·rate·version을 유지하고 **한 요인만 바꾸는** 짧은 탐색 실험으로 분리한다.

각 탐색 run은 12분이다.

```text
0~2분   warm-up, 결과 제외
2~6분   steady 40/s
6~8분   burst 200/s: 균등 60초 + hotspot 60초
8~12분  producer 중지, drain·reconciliation
```

| 실험군 | 변경하는 한 요인 | 고정하는 값 | 확인할 차이 |
|---|---|---|---|
| S0 | 없음, 별도 12분 기준 run | commit·version·payload·Docker limit | 비교 기준 |
| S1 | Docker vCPU를 기준의 50%로 제한 | RAM·disk·version | processed/s, p99, recovery, CPU throttle |
| S2 | Docker RAM을 기준의 50%로 제한 | CPU·disk·version | RSS, cache, swap/OOM, p99 |
| S3 | 별도 volume에 통제된 disk I/O 부하 | CPU·RAM·version | PostgreSQL latency, WAL/checkpoint, Outbox age |
| S4 | SUT 밖의 통제된 CPU noisy neighbor | Docker limit·disk·version | host idle, scheduling 지연, p99 |

본 1시간 + S0~S4 각 12분은 총 2시간이다. 이 다섯 run은 병목에 대한 **탐색적 민감도 결과**이며 실험군당 한 번뿐이므로 일반적인 인과 효과나 신뢰구간을 주장하지 않는다. 실행 순서는 seed로 무작위화하고 각 run 뒤 DB·NATS·volume을 초기화한다. 특정 요인의 영향이 명확하면 해당 비교만 독립 3회 이상 반복해 별도 보고서로 승격한다.

PostgreSQL·NATS version 비교는 12분 민감도 실험에 섞지 않는다. schema·broker 동작·default 설정까지 달라질 수 있으므로 다음 조건을 갖춘 별도 호환성·성능 연구로 취급한다.

- version별 빈 volume과 동일한 초기 데이터
- 동일 image 외 설정·commit·workload
- version별 독립 run 최소 3회
- migration·기능 호환성 결과와 처리량 결과 분리
- image tag뿐 아니라 digest와 실제 server-reported version 대조

자원 민감도 표에는 절대값과 함께 기준 대비 변화율을 싣는다.

| group | Docker CPU/RAM | interference | processed/s | p99 | recovery | RSS peak | disk latency | 기준 대비 변화 |
|---|---|---|---:|---:|---:|---:|---:|---|
| S0 | 실행값 | none | 결과 | 결과 | 결과 | 결과 | 결과 | 기준 |
| S1 | CPU 50% | none | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 |
| S2 | RAM 50% | none | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 |
| S3 | 기준 | disk | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 |
| S4 | 기준 | CPU | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 |

비교식은 `(실험군 − S0) / S0 × 100`으로 고정한다. 지연·recovery·자원 사용량은 증가가 악화이고 처리량은 감소가 악화이므로 표에 방향을 명시한다.

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

## 최종 보고서 구성

실행이 끝나면 아래 목차로 별도 결과 보고서를 만든다. 계획과 결과를 같은 문서에서 덮어쓰지 않는다.

제출 자료는 하나의 긴 문서가 아니라 다음 묶음으로 만든다.

| 산출물 | 답하는 질문 | 현재 실행 범위 |
|---|---|---|
| 신뢰성·복구 본 보고서 | 유실·업무 중복 없이 장애를 견디고 적체를 해소하는가 | 1시간 통합 실험 |
| 자원 민감도 부록 | CPU·RAM·disk·background 간섭에서 병목이 어떻게 달라지는가 | 선택 시 S0~S4, 총 1시간 |
| 실패→수정 비교 기록 | 최초 실패 원인과 code·설정 변경이 결과를 어떻게 바꿨는가 | 실패 run과 새 commit run 대조 |
| 재현 패키지 | 다른 사람이 동일 조건을 복원할 수 있는가 | manifest·raw·SQL·hash·dashboard |
| version 호환성 보고서 | PostgreSQL·NATS version 차이가 기능·성능에 주는 영향은 무엇인가 | 이번 2시간 범위 밖, 별도 반복 필요 |

README에는 통과한 핵심 결과와 한계만 요약한다. CTO가 숫자를 누르면 본 보고서→raw hash→실행 코드까지 역추적할 수 있게 연결한다.

```text
1. 초록
2. 문제 정의와 연구 질문
3. 시스템과 신뢰성 계약
4. 실험 환경
5. workload와 장애 주입 방법
6. 측정 지표와 분석 방법
7. 결과
   7.1 평상시 처리량
   7.2 균등·hotspot burst
   7.3 NATS 중단과 Outbox 복구
   7.4 멱등·DLQ·transaction 경계
   7.5 payload 크기 민감도
   7.6 CPU·메모리·disk·network
   7.7 자원 제한·background interference 탐색
8. 실패 원인과 후속 수정
9. 타당성 위협과 한계
10. 결론
부록 A. 실행 명령·설정
부록 B. 원시 파일 hash
부록 C. reconciliation SQL
```

### 표 1 — 실험 환경

| 항목 | 실행값 |
|---|---|
| Host model / OS / kernel / architecture | 결과 입력 |
| CPU model / physical·logical core | 결과 입력 |
| Host RAM / 실행 전 memory pressure·swap | 결과 입력 |
| Disk media / filesystem / free / volume 위치 | 결과 입력 |
| 전원·저전력·sleep / thermal state | 결과 입력 |
| Docker Desktop·Engine·Compose | 결과 입력 |
| Docker VM CPU / RAM / swap / disk | 결과 입력 |
| PostgreSQL tag / digest / runtime version / 설정 | 결과 입력 |
| NATS tag / digest / runtime version / JetStream 설정 | 결과 입력 |
| background process / idle baseline / validity | 결과 입력 |
| Git SHA / dirty | 결과 입력 |
| Prometheus / Grafana / exporter digest | 결과 입력 |
| 실험 시작·종료 / run_id | 결과 입력 |

### 표 2 — 단계별 성능 결과

| phase | generated | processed | produced/s | processed/s | p50 | p95 | p99 | max Outbox | max consumer pending |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| steady | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 |
| burst-uniform | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 |
| burst-hotspot | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 |
| NATS-down | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 |
| recovery | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 |

### 표 3 — 신뢰성 판정

| 항목 | 기대 | 실제 | 판정 |
|---|---:|---:|---|
| generated accepted | 기준 집합 | 결과 | — |
| processed unique | 계산값 | 결과 | PASS/FAIL |
| expected DLQ | 사전 지정 | 결과 | PASS/FAIL |
| final pending | 0 | 결과 | PASS/FAIL |
| loss | 0 | 결과 | PASS/FAIL |
| duplicate business effect | 0 | 결과 | PASS/FAIL |
| permanent failure attempts | 3 | 결과 | PASS/FAIL |
| recovery time | ≤7분 | 결과 | PASS/FAIL |

### 표 4 — payload 민감도

| serialized size | 반복 | events/s median [min,max] | p95 | p99 | RSS peak | NATS bytes | DLQ | 판정 |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 실제 p50 | 3 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 |
| 1KiB | 3 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 |
| 16KiB | 3 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 |
| 64KiB | 3 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 |
| 256KiB | 3 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 |
| 900KiB | 3 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 |
| 1.1MiB | 1 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 |

### 표 5 — 장애 주입 결과

| failpoint | 입력 | attempt | business rows | Outbox | redelivery | DLQ | 결론 |
|---|---:|---:|---:|---:|---:|---:|---|
| before commit | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 |
| after commit / before publish | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 |
| after publish / before sent | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 |
| after handler commit / before ACK | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 |
| permanent handler failure | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 |

### 결과가 실패해도 만들 수 있는 결론

- steady부터 lag가 증가: 현재 설정의 지속 처리 한계가 40/s 미만
- 균등 burst는 복구되지만 hotspot은 실패: subject 순차 처리 병목
- NATS 복구 뒤 Outbox가 느리게 감소: relay batch 10·순차 publish 병목
- redelivery에서 업무 행이 중복: ledger 또는 transaction 경계 결함
- 1.1MiB가 DLQ가 아니라 무한 재시도: non-retryable 분류 결함
- RSS만 지속 증가: payload retention·JSON 객체·client buffer 누수 후보

실패 결과도 원인과 다음 변경을 연결하면 포트폴리오 근거가 된다. 실패한 최초 run과 수정 후 run을 모두 보존한다.

## 타당성 위협과 한계

### 내부 타당성

- Prometheus scrape와 cAdvisor가 같은 host 자원을 사용한다.
- Docker Desktop VM의 CPU scheduling과 filesystem cache가 run마다 달라질 수 있다.
- PostgreSQL·NATS warm cache가 첫 구간을 유리하게 만들 수 있다.
- fault injector 시각과 metric scrape 시각 사이에 최대 5초 오차가 있다.

대응: monitoring 자원을 별도 그룹으로 표시하고, warm-up을 제외하며, annotation 실제 시각을 JSONL에 남긴다.

### 구성 타당성

- 40 logical source는 40개 서비스 프로세스가 아니다.
- synthetic event는 실제 장애 신호의 전체 분포를 대표하지 않는다.
- generic benchmark handler는 모든 실제 서비스의 DB·외부 API 비용을 포함하지 않는다.

대응: 인프라 신뢰성 실험과 실제 Golden Path handler 실험을 분리해 보고한다.

### 외부 타당성

- 로컬 Docker 결과를 EKS·Cross-AZ·AWS 비용으로 일반화할 수 없다.
- 단일 NATS·PostgreSQL이므로 HA failover를 검증하지 않는다.
- 한 장비 결과로 다른 CPU·disk의 절대 처리량을 보장하지 않는다.

### 결론 타당성

- 1회 1시간 run은 통계적 재현성을 제공하지 않는다.
- 수십만 event가 있어도 같은 run의 event는 독립 표본이 아니다.
- p99는 표본 수와 phase 길이에 민감하다.

대응: 가능하면 독립 2회, 엄격한 신뢰구간이 필요하면 독립 3회 이상 실행한다.

## 재현성과 원본 보존

- 계획서 commit과 실행 대상 commit을 모두 기록한다.
- raw JSONL·Prometheus snapshot·SQL 결과는 실행 후 수정하지 않는다.
- 각 파일 SHA-256을 `artifact-manifest.sha256`에 저장한다.
- 결과 보고서의 모든 숫자는 원본 파일과 계산식을 역추적할 수 있어야 한다.
- 최초 실패 run도 삭제하지 않고 `verdict=FAIL`로 보존한다.
- 실제 사용자·비밀정보는 payload에 넣지 않는다.

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
12. 목적에 따라 60분 steady, 독립 1시간 재현, S0~S4 민감도 중 하나 선택
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
