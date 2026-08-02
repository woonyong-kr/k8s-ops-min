[← Kyro로 돌아가기](../README.md) · [문서 목록](README.md) · [코드·AWS 포렌식](evidence-payload-traffic-forensics.md)

# 운영 데이터 payload 변환·보존·전송 실험 계획

> 상태: **실행 전 사전등록 계획**. 표의 크기·시간은 실험 조건이며 결과가 아니다.

## 결론

이 실험은 유의미하다. 단, “큰 데이터를 얼마나 압축했는가”가 아니라 다음 세 가지를 검증해야 한다.

1. Kubernetes·Prometheus·Loki·Tempo 응답을 정규화하고 제한할 때 전송 크기가 어떻게 변하는가.
2. 크기를 줄인 뒤에도 주입한 장애 신호와 손실 정보가 남는가.
3. Management가 원문은 DB에 두고 NATS에는 참조만 보내 중복 전송·저장을 얼마나 줄이는가.

Agent→Management evidence POST에는 gzip·zstd가 없다. 반면 telemetry source→Agent 응답은 서버가 `Content-Encoding`을 선택하고 `httpx`가 자동 해제할 수 있으므로 실측한다. PostgreSQL TOAST 압축은 별도의 저장 계층 현상으로 구분한다.

## 실제 코드 경로

```text
Kubernetes API / Prometheus / Loki / Tempo
        │ P0a: wire download bytes·Content-Encoding
        │ P0b: 자동 해제 후 response body bytes
        ▼
Cluster Agent provider
        │ P1: parsed raw response
        │ P2: normalized payload before limit
        │ P3: bounded payload + collection_limits
        ▼
EvidenceJobResultRequest
        │ P4: Agent→Management HTTP JSON body bytes
        │     Content-Encoding 없음, result 전체 1MiB 이하
        ▼
Management API / PostgreSQL
        │ P5: provider별 evidence_jobs.result 임시 크기
        │ P6: 집계 evidence_windows.payload 논리·물리 크기
        │     window 확정 transaction에서 임시 result 제거
        ▼
cluster.evidence.received
        │ P7: NATS EventEnvelope bytes
        │     full payload가 아니라 evidence_key + 2KiB 이하 summary
        ▼
evidence-worker / incident-worker
        │ P8: DB에서 복원하고 lineage를 붙인 Evidence bytes
        │ P9: RCA가 사용하는 제한된 EvidenceBundle bytes
        ▼
LLM 보조 경로
        │ P10: 실제 safe prompt bytes·token 수
        ▼
설명 생성
```

P1은 별도의 네트워크 payload가 아니다. 같은 P0b body를 JSON 객체로 파싱한 상태이므로 byte 비교보다 peak RSS와 parse time을 측정한다. `httpx`는 압축 응답을 투명하게 해제할 수 있으므로 P0a는 `response.num_bytes_downloaded`·`Content-Encoding`·`Content-Length`, P0b는 `len(response.content)`로 분리한다.

## 단계별 유의미성 판정

| 단계 | 가치 | 이유 | 수정한 표현 |
|---|---|---|---|
| 원천 응답 크기 | 높음 | 입력 규모의 기준점 | wire download와 decoded `response.content` 분리 |
| Agent가 받은 데이터 | 중간 | P0와 같은 body지만 parse 비용을 확인 | parse time·peak RSS |
| 파싱·정규화·제한 | 매우 높음 | 이민정의 직접 구현과 가장 가까움 | 전후 byte·개수·신호 보존 |
| Evidence payload | 매우 높음 | 1MiB 계약과 잘림 정보 검증 | 최종 직렬화 body byte |
| Agent→Management | 높음 | 실제 애플리케이션 전송량 | HTTP body와 container tx를 분리 |
| source 응답 압축 | 높음 | 서버 선택과 `httpx` 자동 해제를 구분 | wire·decoded byte와 encoding 기록 |
| Management 요청 압축 | 제외 | Agent evidence POST에 압축이 없음 | `Content-Encoding=none` 확인만 기록 |
| DB 저장 | 높음 | JSONB 중복 보존·TOAST 비용 확인 | 논리 JSON과 물리 저장 크기 분리 |
| NATS 전송 | 매우 높음 | claim-check의 실제 효과 확인 | compact event와 inline 반사실 비교 |
| AI 입력 | 높음 | 원문 전체가 LLM에 들어간다는 오해 방지 | EvidenceBundle·실제 prompt 측정 |

## 연구 질문과 반증 조건

| ID | 질문 | 기대 | 반증·실패 조건 |
|---|---|---|---|
| RQ1 | provider별 입력 증가가 최종 payload에 어떻게 반영되는가 | 상한 전까지 설명 가능한 증가, 이후 bounded | 입력과 무관한 폭증·silent drop |
| RQ2 | 제한 뒤에도 장애 분석 신호가 남는가 | 지정한 truth signal과 손실 metadata 보존 | 핵심 신호 누락 또는 잘림 사실 누락 |
| RQ3 | 모든 provider 결과가 HTTP 1MiB 계약을 지키는가 | 성공 또는 명시적인 비재시도 실패 | 422 뒤 lease 반복·무한 재시도 |
| RQ4 | claim-check가 NATS payload를 원문 크기와 분리하는가 | 원문이 커져도 event는 작은 범위 유지 | NATS message가 원문에 비례해 증가 |
| RQ5 | window 확정 후 불필요한 JSONB 복제가 제거되는가 | provider result 0, window 원문 1개 유지 | 임시 result 영구 잔존·원문 다중 복제 |
| RQ6 | limiter 비용이 입력 크기에 따라 어떻게 증가하는가 | 허용 가능한 parse·limit latency와 RSS | 반복 직렬화로 지연·CPU가 급격히 증가 |
| RQ7 | AI가 받는 데이터가 명시된 경계 안에 있는가 | deterministic bundle과 safe prompt가 bounded | raw log·query·secret이 prompt에 포함 |

## 실험 데이터의 원칙

크기만 줄이면 성공이 아니다. 모든 fixture에는 결과에서 찾아야 하는 정답을 심는다.

| provider | 주입할 truth signal | 반드시 남길 근거 |
|---|---|---|
| Kubernetes | `FailedScheduling`, `OOMKilled`, 특정 namespace·workload UID | symptom·resource identity·원본/반환 개수 |
| Prometheus | 특정 series의 spike·threshold 초과·첫/마지막 point | analysis 결과·series identity·point 잘림 |
| Loki | error severity·장애 패턴·trace ID·비밀정보 | pattern count·trace ID·redaction·line truncation |
| Tempo | error trace·긴 duration·특정 service/span | trace summary·error count·item truncation |

fixture는 고정 seed로 생성하고 JSON SHA-256을 저장한다. 실제 credential·사용자 로그는 사용하지 않는다.

## 1단계 — 결정적 provider 변환 실험

실제 API 모양을 반환하는 로컬 fixture server를 사용한다. 이 단계의 목적은 외부 도구 성능이 아니라 Agent 변환 코드만 격리하는 것이다.

### Kubernetes

| 규모 | Pod | Event | Workload | Service | 특징 |
|---|---:|---:|---:|---:|---|
| K0 | 0 | 0 | 0 | 0 | 성공한 empty |
| K1 | 10 | 10 | 5 | 5 | 소규모 기준 |
| K2 | 100 | 100 | 100 | 50 | 일반 증가 |
| K3 | 500 | 200 | 500 | 300 | 개수 상한 경계 |
| K4 | 1,000 | 400 | 1,000 | 600 | 2배 초과·그룹 보존 |
| K5 | K3와 동일 | 동일 | 동일 | 동일 | 긴 label·annotation으로 byte 한도 자극 |

리소스 종류별 원본 개수, 정규화 후 개수, byte limiter 후 개수, namespace별 대표성, truth signal 보존을 측정한다.

### Prometheus

| 규모 | vector series | matrix series | series당 point | label 크기 |
|---|---:|---:|---:|---:|
| M0 | 0 | 0 | 0 | 기본 |
| M1 | 10 | 10 | 10 | 기본 |
| M2 | 100 | 50 | 40 | 기본 |
| M3 | 250 | 100 | 40 | 코드 상한 경계 |
| M4 | 500 | 200 | 80 | series·point 2배 초과 |
| M5 | 250 | 100 | 40 | 긴 label·고 cardinality |

matrix는 앞·뒤 point를 남기는 edge sampling이 spike의 시점에 어떤 영향을 주는지도 분리한다. spike를 중간에만 넣은 case를 추가해 단순 첫·끝 보존의 한계를 드러낸다.

### Loki

기본 query limit은 query당 20줄이고 line은 4,096자에서 잘린다. Loki에는 현재 provider 공통 byte limiter가 직접 적용되지 않으므로 **query 수 × 줄 수 × 줄 길이**가 핵심이다.

| 규모 | query 수 | query당 line | line 길이 | 목적 |
|---|---:|---:|---:|---|
| L0 | 1 | 0 | 0 | 성공한 empty |
| L1 | 1 | 20 | 128B | 기준 |
| L2 | 4 | 20 | 1KiB | 여러 query 합산 |
| L3 | 8 | 20 | 4KiB | HTTP 상한 근접 |
| L4 | 16 | 20 | 8KiB | line truncate 뒤에도 전체 1MiB 초과 가능성 |
| L5 | 4 | 20 | 4KiB | secret·trace ID·패턴 혼합 |

L4가 422를 만들고 job lease 재시도로 이어진다면 실제 결함이다. Loki build_response에 공통 byte limiter를 적용하고 non-retryable payload error를 분류한 뒤 같은 fixture로 재검증한다.

### Tempo

| 규모 | query 수 | trace 수/query | trace 원본 크기 | 목적 |
|---|---:|---:|---:|---|
| T0 | 1 | 0 | 0 | 성공한 empty |
| T1 | 1 | 10 | 1KiB | 기준 |
| T2 | 1 | 20 | 32KiB | query limit·중간 크기 |
| T3 | 4 | 20 | 64KiB | trace item 경계 |
| T4 | 4 | 20 | 128KiB | trace summary fallback·provider byte limit |
| T5 | 1 | 20 | 중첩 list 40개·문자열 2KiB | nested·string truncation |

## 2단계 — 실제 도구 통합 실험

fixture 결과만으로 실제 Prometheus·Loki·Tempo API 동작을 일반화하지 않는다. 로컬 `kind` 또는 `k3d`에 다음을 올리고 대표 3점만 확인한다.

```text
Kubernetes cluster
├─ Prometheus
├─ Loki
├─ Tempo
├─ OTel Collector
├─ deterministic workload generator
├─ Cluster Agent
└─ Management + PostgreSQL + NATS
```

대표점은 각 provider의 `small`, `limit boundary`, `over-limit`이다. 부하 생성기는 metric series, 구조화 로그, trace와 Kubernetes workload를 같은 `scenario_id`로 묶어 원천 간 상관관계를 검증한다.

실제 도구 단계에서 측정한다.

- provider HTTP wire download와 자동 해제 후 response body
- Agent parse·normalize·limit 시간
- Agent·Management CPU와 RSS
- Agent container tx/rx
- EvidenceJobResultRequest body
- Management 응답 status와 retry
- PostgreSQL JSON 논리·물리 크기
- NATS 실제 message byte
- RCA EvidenceBundle과 실제 LLM prompt byte·token

## 3단계 — 코드 변경 전후 비교

동일 fixture를 다음 좁은 commit 경계에서 실행한다. 여러 기능이 섞인 임의 브랜치를 비교하지 않는다.

| 비교 | before | after | 개인 기여 해석 |
|---|---|---|---|
| provider byte limiter | `7cc428e4^` | `7cc428e4` | 이민정 직접 구현 |
| 첫 claim-check | `b37ea3998^` | `b37ea3998` | 팀 아키텍처, 개인 단독 성과로 쓰지 않음 |
| evidence.built claim-check | `c02caa528^` | `c02caa528` | 팀 아키텍처 |
| provider result 정리 | `b30ee0ea9^` | `b30ee0ea9` | 팀 저장 구조 개선 |

before·after는 별도 Git worktree, 별도 DB schema와 NATS stream에서 실행한다. byte 차이는 같은 fixture hash일 때만 비교한다.

## 확인된 개선 후보

### 공통 limiter의 반복 직렬화

현재 `limit_payload_size`는 다음 작업을 반복한다.

1. 전체 payload를 `json.dumps`해 한도 초과 여부 확인
2. 축소 후보 list마다 다시 `json.dumps`해 가장 큰 목록 선택
3. 선택한 목록을 절반으로 줄인 뒤 처음부터 반복

따라서 “델타 계산으로 반복 직렬화를 제거했다”는 문장은 현재 코드로 증명되지 않는다. 먼저 payload 크기별 호출 횟수·CPU time·peak RSS를 계측한다. 이후 cached byte estimate 또는 한 번의 budget allocation으로 개선하고, 같은 fixture에서 다음을 비교한다.

| 지표 | before | after | 판정 |
|---|---:|---:|---|
| 전체 JSON 직렬화 횟수 | 측정 | 측정 | 감소 여부 |
| limiter p50/p95 | 측정 | 측정 | 변화율 |
| peak RSS | 측정 | 측정 | 변화율 |
| 최종 byte | 측정 | 측정 | 한도 이하 동일 |
| returned item·truth signal | 측정 | 측정 | 의미 보존 동일 |

속도만 빨라지고 반환 집합이나 `original_count`가 달라지면 회귀다.

### Loki 전체 byte 상한

line별 상한과 query별 20줄 제한이 있어도 query 수가 늘면 provider result 전체가 1MiB를 넘을 수 있다. L3·L4에서 실제 경계를 찾고, 문제가 재현될 때만 Loki용 전체 byte limiter를 구현한다.

### gzip 후보

Agent→Management 요청 압축은 없다. 정상 분포의 p95 HTTP body가 작다면 gzip은 복잡성만 늘린다. p95가 256KiB 이상이고 네트워크가 병목으로 확인될 때만 별도 A/B를 연다.

```text
동일 JSON body
├─ identity: body byte, latency, CPU
└─ gzip: compressed byte, compress/decompress latency, CPU
```

FastAPI request decompression과 최대 압축 해제 크기 보호까지 구현하지 않았다면 “gzip 적용”이라고 쓰지 않는다.

## 실험 환경

### 호스트와 Docker

| 항목 | 기록값 |
|---|---|
| 장비·OS·kernel·architecture | 실행 시 자동 수집 |
| CPU model·physical/logical core | 실행 시 자동 수집 |
| host RAM·memory pressure·swap | 실행 전·중 수집 |
| disk media·filesystem·free·volume 위치 | 실행 시 자동 수집 |
| Docker Desktop·Engine·Compose | tag가 아닌 실제 version |
| Docker VM vCPU·RAM·swap·disk | host 자원과 분리 기록 |
| PostgreSQL·NATS·Prometheus·Loki·Tempo | image tag·digest·server version |
| 전원·저전력·sleep·thermal | 실행 전 고정 |
| background process | 5초 간격 CPU·RSS·I/O 상위 process |

실험 전 5분 idle baseline을 남긴다. 외부 process CPU의 60초 평균이 host 전체의 10% 이상이거나 thermal warning이 30초 이상 지속되면 `CONTAMINATED`로 표시한다. SUT가 만든 포화는 오염이 아니라 결과다.

### 고정해야 하는 설정

- Agent image digest와 Git SHA
- provider query·range·step·query count
- Loki·Tempo query limit
- provider worker count와 collection interval
- HTTP timeout·connection reuse
- PostgreSQL JSONB·TOAST 설정과 빈 volume
- NATS max payload·stream 설정
- fixture seed·hash
- Docker CPU·RAM 할당량

## 측정값 정의

| 지표 | 정의 |
|---|---|
| source wire bytes | `response.num_bytes_downloaded`, `Content-Length`, `Content-Encoding` |
| decoded raw bytes | 자동 해제 후 `len(response.content)` |
| normalized bytes | limit 전 normalized object의 compact JSON UTF-8 byte |
| bounded bytes | limit 후 provider result의 compact JSON UTF-8 byte |
| HTTP body bytes | 실제 `EvidenceJobResultRequest` request content byte |
| logical DB bytes | `octet_length(payload::text)` |
| physical JSONB bytes | `pg_column_size(payload)` |
| NATS bytes | 실제 publish 직전 `json.dumps(evt.to_dict()).encode()` 길이 |
| normalization ratio | bounded bytes / decoded raw bytes |
| NATS isolation ratio | compact NATS bytes / inline EventEnvelope bytes |
| truth retention | 보존된 필수 signal / 주입한 필수 signal |
| truncation disclosure | 잘린 collection 중 원본·반환 개수가 기록된 비율 |
| transform latency | response 수신 완료부터 bounded payload 생성 완료까지 |
| end-to-end latency | provider 요청 시작부터 Management accepted까지 |

`normalization ratio < 1` 자체는 성공 기준이 아니다. 구조화 metadata가 추가되어 크기가 늘어도 신뢰도와 사용성이 높아질 수 있다.

## 반복과 분석

- byte·개수·truth signal은 fixture가 같으면 결정적이므로 hash가 같은 결과 1개를 기준으로 사용한다.
- latency·CPU·RSS는 process를 초기화한 독립 run 5회를 실행한다.
- 각 run 내부 첫 요청은 warm-up으로 제외한다.
- 결과는 median, min, max와 개별 run을 모두 표시한다.
- 5회로 일반적인 95% 신뢰구간을 주장하지 않는다.
- provider 간 byte를 평균내지 않고 각각 제시한다.

## 최대 2시간 실행 순서

| 경과 | 구간 | 내용 |
|---:|---:|---|
| 00~10분 | 환경·smoke | manifest, idle baseline, endpoint·metric 확인 |
| 10~40분 | provider fixture | K/M/L/T 전 규모의 결정적 byte·signal 검증 |
| 40~75분 | 실제 도구 | small·boundary·over-limit end-to-end |
| 75~100분 | commit A/B | limiter·claim-check·result cleanup 비교 |
| 100~112분 | limiter profile | 반복 직렬화 횟수·CPU·RSS 측정 |
| 112~120분 | 대조 | DB·NATS·raw hash·PASS/FAIL 확정 |

첫 smoke에서 fixture와 실제 provider 계약이 맞지 않으면 시간을 채우지 않고 중단한다. 실패를 수정한 run은 새 commit·새 run_id로 다시 시작한다.

## 결과표

### Provider 변환

| provider | case | raw byte | normalized byte | bounded byte | HTTP byte | retained/original | truth retention | transform p95 | RSS peak |
|---|---|---:|---:|---:|---:|---|---:|---:|---:|
| 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 |

### Management 저장·이벤트

| case | window logical | window physical | job result after finalize | compact NATS | inline counterfactual | isolation ratio | bundle byte | prompt token |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 | 결과 |

### 실패 경계

| case | expected | HTTP | retry | collection status | data loss disclosed | verdict |
|---|---|---:|---:|---|---|---|
| Loki over-limit | bounded 또는 명시적 실패 | 결과 | 결과 | 결과 | 결과 | PASS/FAIL |
| provider empty | completed/no signal | 결과 | 결과 | 결과 | n/a | PASS/FAIL |
| provider failure | unavailable/reason | 결과 | 결과 | 결과 | n/a | PASS/FAIL |

## 보고서가 말할 수 있는 것

모든 검증을 통과한 뒤 실제 숫자를 채운다.

> 동일한 Kubernetes·Prometheus·Loki·Tempo fixture를 사용해 원천 응답부터 Agent 정규화, 1MiB HTTP 경계, PostgreSQL 저장, NATS claim-check, RCA 입력까지 byte와 신호 보존을 대조했습니다. `{provider}`의 `{input}`을 `{bounded}`로 제한하면서 주입한 장애 신호 `{retained}/{total}`개와 잘림 metadata를 보존했고, `{full}` 크기의 원문을 NATS `{compact}` 참조 이벤트로 분리했습니다.

이 실험만으로 말할 수 없는 것:

- 과거 AWS 29.68TB 중 Agent payload의 비율
- EKS에서의 최대 처리량·Cross-AZ 비용
- gzip 절감률
- 작은 payload가 더 정확한 RCA를 보장한다는 주장
- 팀 claim-check 전체를 이민정 개인 구현으로 표현하는 것

## 실행 전에 구현할 계측

| 위치 | 추가할 계측 |
|---|---|
| provider HTTP client | wire·decoded response byte, Content-Encoding, parse time |
| provider normalize/limit | before·after byte, item count, truth 결과, limiter 호출 횟수 |
| Management HTTP client | request content byte·status·duration |
| Management repository | logical·physical JSONB, finalize 후 job result |
| NATS publisher | subject별 serialized byte |
| EvidenceBuilder·Bundle | 단계별 serialized byte |
| LLM client 직전 | safe prompt byte·token, 원문 포함 금지 검사 |

metric label에는 provider·case·status만 사용한다. resource UID·query·trace ID는 raw 결과에 두고 Prometheus label에 넣지 않는다.
