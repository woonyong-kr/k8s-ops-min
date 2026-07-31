# management·target Deployment 47개 이후, 29.68TB 청구 사용량을 뜯어봤습니다

초기에는 장애 분석 단계를 작은 워커로 나누면 책임, 재시도, 장애 격리가 명확해진다고 판단했습니다. 논리적 경계로는 맞았지만, 그 경계를 거의 그대로 Kubernetes Deployment와 NATS 왕복으로 옮겼습니다. Git과 AWS 청구서을 대조하자 코드 구조가 실행 비용으로 바뀌는 지점이 보였습니다.

이 회고는 개인 기여를 주장하는 문서가 아닙니다. 원본은 5인 팀 아키텍처이고, 통합 controller 는 프로젝트가 끝난 뒤 개인 작업으로 확장한 것입니다. 어디까지가 제 몫인지는 [기여와 근거](source-and-ownership.md)에 있습니다.

## 1. 처음 무엇을 얻으려고 했나

장애 감지부터 복구 검증까지를 typed event로 연결했습니다.

```text
incident.detected
  → evidence.built
  → rca.candidates.planned
  → rca.candidates.evaluated
  → recovery.planned
  → safe_pr.created
  → recovery.verification.updated
```

모든 이벤트는 다음 공통 응답를 공유합니다.

```text
event_id          중복 처리 방지의 식별자
subject           소비자 라우팅과 계약 경계
source            생산 책임
correlation_id    한 장애 흐름 전체 추적
causation_id      직전 원인 이벤트 추적
created_at        생성 시각
payload           도메인 데이터
schema_version    호환 규칙
workspace_id      tenant·권한 경계
```

처리 경계도 코드에 넣었습니다.

- 업무 쓰기, 다음 이벤트 Outbox, 처리 ledger 완료를 같은 DB transaction으로 묶습니다.
- DB commit 뒤 ACK에 실패해 재전달되어도 `(event_id, consumer)` ledger로 업무를 다시 실행하지 않습니다.
- 같은 subject는 순서를 지키고, 서로 다른 subject만 기본 최대 8개까지 병렬 처리합니다.
- handler timeout 30초 `<` NATS ACK wait 60초 `<` processing stale window 순서를 부팅할 때 검증합니다.
- 최대 3회 시도 뒤 DLQ로 종결하고, consumer별 미확인 메시지는 100개로 제한합니다.

이 결정은 “어떤 단계가 실패했는가”를 추적하고 재실행하는 데 유리했습니다. 문제는 논리적 이벤트 경계와 물리적 배포 경계를 동일시한 것입니다.

## 2. 실제로 얼마나 나뉘었나

Git 각 날짜의 마지막 revision에서 `deploy/management/**`·`deploy/target/**`의 배포 문서와 `src/services/**/app.py` entrypoint를 셌습니다.

| 날짜 | Deployment 문서 | `*-worker` entrypoint | 전체 서비스 entrypoint | 선언 replica 합계 |
|---|---:|---:|---:|---:|
| 07-05 | 36 | 27 | 34 | 39 |
| 07-10 | 43 | 30 | 39 | 50 |
| 07-15 | 46 | 32 | 41 | 55 |
| 07-18 | 46 | 32 | 42 | 55 |
| 07-25 | **47** | **32** | **42** | **56** |

이 표는 실행 토폴로지의 상한을 보여주는 Git 증거입니다. 당시 실제 Pod 개수, Pod별 AZ, 재시작 횟수는 보존되지 않았으므로 “56개 Pod가 계속 실행됐다”고 바꿔 말하면 안 됩니다.

CloudTrail에서는 7월 4일 기존 management 1개와 target 2개를 만들고, 7월 18일 management/game/demo 3개로 교체한 기록을 확인했습니다. 교체 시점에는 기존과 신규 클러스터가 잠시 겹쳤습니다.

## 3. AWS 청구서은 무엇을 보여줬나

2026-08-01 Cost Explorer를 다시 조회한 결과입니다.

| 항목 | 값 | 의미 |
|---|---:|---|
| Regional transfer 청구 사용량 | **29,683.72GB** | 송·수신 과금 방향 합계 |
| Usage 비용 | **$296.83** | 크레딧 적용 전 사용 비용 |
| Credit | **-$296.83** | 이번 계정의 프로모션 차감 |
| 편도 상당량 | **14,841.86 billed GB** | 사용량÷2, byte·패킷 실측 아님 |
| 최고일 | **07-22, 4,578.58GB / $45.79** | 전체의 15.4% |
| 집중 구간 | **07-20~26, 22,295.89GB** | 전체의 75.1% |

서비스별 분해에서 EC2 - Other는 29,573.84GB(99.63%), Elastic Load Balancing은 109.87GB(0.37%)였습니다. 29,573.84GB와 전체 29,683.72GB의 차이는 재조회 보정이 아니라 같은 기간 ELB 사용량입니다. AWS가 `Estimated=true`로 표시한 기간이므로 조회 날짜와 원본 CSV를 함께 둡니다.

CloudWatch의 노드그룹별 EC2 인터페이스 합계는 전송 방향을 좁혀 줍니다.

| 노드그룹 | Out | In | 읽을 수 있는 신호 |
|---|---:|---:|---|
| management-server | 5,173.35GiB | 5,334.27GiB | management 노드그룹 인터페이스의 양방향 전송량이 큼 |
| battlegrounds-game | 4,895.28GiB | 164.08GiB | 대량 송신 역할 |
| battlegrounds-infra | 331.89GiB | 5,227.56GiB | game 송신과 맞물린 대량 수신 |
| cluster-2-spot | 1,376.37GiB | 1,428.62GiB | 교체 전 target 통신 |
| kubernetes-ops-r6i | 1,199.81GiB | 1,258.12GiB | 교체 전 management 통신 |

`battlegrounds-game Out 4,895.28GiB`와 `battlegrounds-infra In 5,227.56GiB`의 반대 방향 전송량과 시계열이 맞물렸습니다. management도 In/Out이 비슷했습니다. EC2 NetworkIn/Out에는 same-AZ·Cross-AZ·인터넷·제어면 통신이 함께 들어가므로 서비스 경로나 비용 원인을 특정할 수 없습니다.

## 4. “32개 워커가 원인”이라고 쓰지 않는 이유

시간상 상관관계는 있습니다. 배포 문서가 46개로 늘어난 7월 15일 사용량이 1,933.50GB로 뛰었고, 신규 3개 클러스터와 battlegrounds 노드그룹이 운영된 7월 20~26일에 전체의 75.1%가 발생했습니다.

그러나 현재 청구서에는 Pod별 flow log, NATS subject별 payload/throughput, AZ별 Pod 배치 이력이 없습니다. 추가로 분리 검증해야 할 트래픽 가설은 최소 둘입니다.

1. management 내부에서 논리 단계마다 serialize → NATS → consumer → DB를 반복했을 가능성
2. game 노드에서 infra 노드로 게임 데이터면 트래픽을 전달했을 가능성

그래서 문제를 “서비스 숫자” 하나로 축약하면 game→infra 경로를 놓칩니다. 개선 기준도 두 갈래여야 합니다.

## 5. 무엇을 바꿨나

### 관리면: 이벤트 계약은 유지하고 프로세스를 합쳤습니다

후속 controller는 41개 발견 서비스 중 agent 2개를 신뢰 경계 밖에 남기고, management 서비스 39개를 한 프로세스에 조립합니다.

```text
이전
worker A Pod → NATS → worker B Pod → NATS → worker C Pod

변경
controller process
  ├─ worker A task
  ├─ InMemoryEventBus (같은 EventEnvelope 계약)
  ├─ worker B task
  └─ worker C task

별도 유지
target cluster-agent / node-collector → network trust boundary
```

`python src/entrypoints/app.py --check` 결과:

```json
{"agent_services":2,"async_services":4,"controller_services":39,"discovered_services":41,"event_bus_mode":"inprocess","http_services":2,"worker_services":33}
```

합친 기준은 “작은 서비스가 나쁘다”가 아닙니다.

- 같은 릴리스 주기, 같은 DB, 같은 장애 영역, 같은 팀이 소유하고 네트워크 격리가 필요 없으면 한 프로세스
- target cluster 권한처럼 trust boundary가 다르거나 독립 확장이 필요한 경우 별도 프로세스
- 도메인 모듈과 typed event 계약은 유지해 다시 분리할 수 있게 함

### 데이터면: 같은 AZ 우선과 집계 전송이 필요합니다

game→infra는 프로세스 통합으로 해결할 문제가 아닙니다. 다음 검증이 남았습니다.

- topology-aware routing 또는 노드 affinity로 생산자·수신자를 같은 AZ에 우선 배치
- raw event를 매번 보내지 않고 시간창별 집계·압축 전송
- 큰 telemetry는 이벤트 payload에 싣지 않고 object reference와 checksum만 전달
- AZ별 bytes, subject별 messages/bytes, payload p50/p95/p99를 함께 계측
- 일일 Regional-Bytes 예산과 이상 증가율 경보를 배포 게이트에 포함

## 6. 이벤트를 얼마나 감당하도록 설계했나

코드에 있는 경계와 측정된 처리량을 구분합니다.

| 층 | 코드상의 경계 | 의미 |
|---|---:|---|
| JetStream 보존 | 512MiB 또는 7일 | 둘 중 먼저 도달한 한계에서 오래된 이벤트 제거 |
| 중복 publish window | 24시간 | 같은 Nats-Msg-Id 재발행 억제 |
| consumer 미확인 상한 | 100건 | 폭주 시 in-flight 메모리·동시 작업 제한 |
| subject fetch batch | 기본 1건 | 같은 subject 순차성 우선 |
| worker 병렬성 | 최대 8 subject | 서로 다른 subject만 병렬 |
| handler timeout | 30초 | hang 상한 |
| ACK wait | 60초 | 정상 handler 처리 중 재전달 방지 |
| 최대 시도 | 3회 | 소진 시 DLQ |
| Outbox relay | 10건/회, 순차 publish | DB lock·메모리 피크 제한 |

직렬화 1,393B 이벤트만 있다고 단순 계산하면 512MiB는 약 38.5만 건입니다. JetStream 인덱스·subject·storage overhead와 실제 payload 분포를 빼지 않은 이론 상한이므로 용량 보장으로 쓰지 않습니다.

더 중요한 사실은 원본 배포의 **실제 event/s, payload p95, consumer lag 이력이 보존되지 않았다는 점**입니다. AWS 29.68TB를 이벤트 수로 나누는 것도 불가능합니다. 이 결손 때문에 “초당 N건을 운영에서 처리했다”고는 말할 수 없습니다.

## 7. 로컬에서 무엇을 검증했나

같은 `EventEnvelope`와 1,393B 직렬화 크기로, 1,000건을 5회 전달했습니다. warm-up은 결과에서 제외했습니다.

| 전달 방식 | median batch | 처리량 | 범위 |
|---|---:|---:|---|
| InMemoryEventBus | **12.182ms** | **82,088 events/s** | 같은 프로세스 |
| NATS JetStream | **373.955ms** | **2,674 events/s** | localhost Docker |
| 차이 | **96.74% 감소** | **30.7배** | 전송 계층만 |

이 실험은 같은 프로세스 안의 논리 단계를 NATS로 왕복시킬 이유가 있는지 판단하는 microbenchmark입니다. PostgreSQL, handler, 외부 API, Kubernetes CNI, Cross-AZ는 포함하지 않았습니다. 그래서 “전체 시스템이 82K events/s를 처리한다” 또는 “AWS 비용이 96.74% 줄었다”고 쓰면 안 됩니다.

재현 코드는 [`benchmarks/event_bus_transport.py`](../benchmarks/event_bus_transport.py)에 있으며, 청구서은 [증거 묶음](evidence/network-cost/README.md)에 있습니다.

