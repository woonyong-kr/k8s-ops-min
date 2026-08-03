[← 문서 목록](README.md) · [네트워크 비용 증거](evidence/network-cost/README.md)

# 운영 데이터 payload와 AWS 트래픽 포렌식

**결론** — 코드로 증명되는 payload 경로와 CloudWatch가 측정한 노드 트래픽은 서로 다른 층의 증거입니다. Agent 요청은 1MiB 이하 JSON이고, Management는 원문을 한 번 저장한 뒤 NATS에 참조키와 2KB 이하 요약만 보냅니다. 반면 2026년 7월 CloudWatch에는 Pod·flow 단위 관측이 없어 EC2 `NetworkIn/Out`을 Agent payload로 귀속할 수 없습니다.

이 문서는 프로젝트 종료 시점의 원본 팀 저장소 [`Jungle-303-04/final`](https://github.com/Jungle-303-04/final)과 당시 AWS 원장을 기준으로 작성했습니다. 이 저장소의 후속 개선 코드는 원인 설명에 사용하지 않았습니다.

## 1. 증거 등급

| 등급 | 이 문서에서의 의미 |
|---|---|
| 증명 | 코드, 테스트, GitHub Deployment, CloudTrail 또는 CloudWatch 원시 지표가 직접 확인함 |
| 강한 추론 | 시간과 구조가 맞지만 Pod·flow 단위 원장이 없어 원인을 하나로 확정하지 못함 |
| 확인 불가 | 당시 관측을 다시 만들 수 없어 포트폴리오 주장으로 사용하지 않음 |

## 2. 실제 payload 경로

```text
Management provider job scheduler
        │ provider별 lease
        ▼
Cluster Agent
  ├─ Kubernetes API   ── 리소스·메타데이터
  ├─ Prometheus       ── metric query result
  ├─ Loki             ── log stream
  └─ Tempo            ── trace search result
        │ 수집·정규화·상한 적용
        │ HTTP JSON POST, gzip 없음, 요청 전체 1MiB 이하
        ▼
Management API
  ├─ provider별 상태와 실제 payload를 보수적으로 대조
  ├─ full evidence → evidence_windows JSONB 1회 저장
  ├─ provider 임시 result → 같은 transaction에서 제거
  └─ NATS event → evidence_key + collection_status + 2KB 이하 summary
        ▼
RCA worker / API / 운영 화면
```

### 원천별 변환

| 원천 | 조회 | Agent 안에서 남기는 형태 | 상한·손실 표시 |
|---|---|---|---|
| Kubernetes API | Pod, Event, Node, Workload, Service 등 | 리소스 목록과 관계·관측 메타데이터 | 종류별 개수 상한 뒤 공통 byte limiter, `truncated`와 원본·반환 개수 |
| Prometheus | instant/range query | query 이름별 vector sample 또는 matrix series | provider byte limiter |
| Loki | `query_range` | redaction된 line, severity, 장애 패턴, trace id | query/line/matched-entry 상한과 최종 1MiB 요청 검증 |
| Tempo | trace search | 큰 중첩 필드를 줄인 trace 요약 | 문자열·목록·trace별 64KiB 상한과 provider byte limiter |

Agent가 Management에 보내는 evidence 요청은 [`MAX_EVIDENCE_PAYLOAD_BYTES = 1_048_576`](https://github.com/Jungle-303-04/final/blob/d70daef8c15033e12485d1415b76bf84027be400/src/packages/contracts/gateway/requests.py#L69-L72)으로 검증됩니다. provider는 envelope 여유 64KiB를 남긴 983,040B 안에서 가장 큰 축소 가능 목록을 반복해서 줄입니다. Agent HTTP client는 `httpx`의 `json=`으로 POST하며 evidence 경로에 `Content-Encoding`, gzip, zstd는 없습니다.

Management의 축약은 네트워크 압축이 아닙니다. [`compact_cluster_evidence_payload`](https://github.com/Jungle-303-04/final/blob/d70daef8c15033e12485d1415b76bf84027be400/src/domains/rca/events.py#L67-L96)는 원문 대신 `evidence_key`, `payload_size`, `collection_status`, 2KiB 이하 summary를 이벤트에 싣습니다. 원문은 [`evidence_windows`](https://github.com/Jungle-303-04/final/blob/d70daef8c15033e12485d1415b76bf84027be400/src/domains/target/repository.py#L1166-L1232)에 저장하고 provider job의 임시 JSONB는 같은 transaction에서 비웁니다.

## 3. “Provider 실패와 no signal을 구분한다”의 정확한 뜻

정확한 표현은 다음입니다.

> 쿼리는 성공했지만 값이 없는 경우와, 권한·타임아웃 등으로 조회 자체가 실패한 경우를 provider별 상태와 reason code로 분리해 RCA가 데이터 부재를 근거 수집 실패로 오인하지 않도록 했습니다.

| 관측 | payload 예 | 상태 | RCA가 읽어야 할 뜻 |
|---|---|---|---|
| 쿼리 성공, 결과 0건 | `results.up.samples=[]` | `completed` | 확인한 범위에 signal이 없음 |
| 일부 쿼리만 성공 | 성공 결과 + 실패 수 | `partial` | 남은 근거만 사용하고 누락을 표시 |
| 모든 쿼리 실패 | `results={}` + 실패 수 | `unavailable` / `provider_query_failed` | signal 유무를 판단할 수 없음 |
| 실행할 쿼리 없음 | query count 0 | `not_queried` / `no_queries_configured` | 조회 자체를 하지 않음 |
| 구형 Agent의 빈 envelope | `results={}`만 존재 | `unavailable` / `no_provider_results` | 성공으로 승격하지 않음 |

테스트는 [모든 query 실패](https://github.com/Jungle-303-04/final/blob/d70daef8c15033e12485d1415b76bf84027be400/tests/test_evidence_collection_status.py#L210-L244), [구형 빈 envelope](https://github.com/Jungle-303-04/final/blob/d70daef8c15033e12485d1415b76bf84027be400/tests/test_evidence_collection_status.py#L322-L340), [성공한 empty vector](https://github.com/Jungle-303-04/final/blob/d70daef8c15033e12485d1415b76bf84027be400/tests/test_evidence_collection_status.py#L343-L360)을 별도로 고정합니다.

## 4. AWS에서 증명된 사실

### 클러스터 구성

CloudTrail `CreateNodegroup` 기록은 다음을 확인합니다.

| 시각 UTC | nodegroup | 초기 desired | instance type | 역할 |
|---|---|---:|---|---|
| 07-04 17:49 | cluster-1-ng | 2 | t3.large | target predecessor |
| 07-04 18:02 | cluster-2-ng | 2 | t3.large | target predecessor |
| 07-07 18:52 | cluster-1-spot | 1 | t3.large, t3a.large, m5.large | 교체 target |
| 07-07 19:21 | cluster-2-spot | 1 | t3.large, t3a.large, m5.large | 교체 target |

원본 [`aws-up.sh`](https://github.com/Jungle-303-04/final/blob/b03cbd750fe36b2e9111dd03609aa867bc630331/scripts/aws-up.sh)는 두 target에 같은 instance type·노드 수·등록 함수를 적용합니다. 둘 다 Agent와 telemetry 설치 대상입니다. 따라서 `cluster-1`을 “아무것도 설치하지 않은 빈 클러스터”라고 부를 수 없습니다.

CloudTrail에는 07-14 18:56 UTC에 `cluster-2-spot`을 `min=1, max=3, desired=3`으로 바꾼 `UpdateNodegroupConfig`가 있습니다. CloudWatch `GroupInServiceInstances`도 07-15 04:00 KST부터 3대를 유지합니다.

### lower-activity reference와 project-recorded game cluster

동일 생존 구간을 node-hour로 보정한 결과입니다.

| 구간 | Out | In | node-hour | 양방향 GiB/node-hour | CPU 평균 |
|---|---:|---:|---:|---:|---:|
| cluster-1-spot | 142.30GiB | 223.83GiB | 516.57 | **0.709** | 11.10% |
| cluster-2-spot | 1,376.37GiB | 1,428.62GiB | 439.63 | **6.380** | 18.48% |

비율은 약 9.0배지만 원인 비율은 아닙니다. `cluster-2`의 총량 중 07-15 UTC 약 1,618.94GiB, 07-16 UTC 약 1,067.16GiB가 집중됐습니다. 시간당 양방향 전송은 07-15 05:00 KST 12.16GiB에서 증가해 10:00 132.50GiB, 11:00 145.82GiB였습니다. 50~70GiB 범위에 든 36개 1시간 bucket의 평균은 60.04GiB/h입니다. 07-17 03:00 KST에는 1.00GiB/h로 떨어졌지만 노드는 전후 모두 3대였으므로 급감은 node scale-in으로 설명되지 않습니다.

원본 프로젝트 문서는 07-15 05:36 KST에 `cluster-2`의 서버 3대와 게임 Pod, 20~100 bot 부하, HPA 확장을 실측했다고 기록합니다. [해당 commit](https://github.com/Jungle-303-04/final/commit/2870929)은 당시 팀이 남긴 측정 기록입니다. CloudWatch는 같은 구간의 3대 운전과 네트워크 증가를 독립적으로 확인하지만 Pod identity는 남기지 않았습니다. 따라서 문서에서는 `cluster-2`를 **project-recorded game/load cluster**로 부르고, “AWS가 게임 Pod를 직접 확인했다”고 확장하지 않습니다.

### Git·배포·AWS 통합 타임라인

| 시각 KST | 확인된 사건 | 증거가 허용하는 결론 | 허용하지 않는 결론 |
|---|---|---|---|
| 07-05 02:47 | `aws-up.sh` commit | 두 target을 같은 node type·등록 함수로 만들도록 코드가 구성됨 | 스크립트 전체가 성공해 Agent까지 설치됨 |
| 07-05 02:49 / 03:02 | `cluster-1-ng` / `cluster-2-ng` 생성 | CloudTrail이 실제 nodegroup 생성을 확인 | 그 위 Pod 종류와 실행 상태 |
| 07-08 03:52 / 04:21 | `cluster-1-spot` / `cluster-2-spot` 생성 | 후속 spot nodegroup이 실제 존재함 | 두 클러스터 workload가 동일함 |
| 07-15 02:39 | `a47826ed` commit | node별 kubelet `/stats/summary` 1초 조회 코드가 저장소에 추가됨 | 그 코드가 target에서 실행됨 |
| 07-15 02:45~02:51 | GitHub Deployment success | 해당 SHA의 service image와 management namespace 38개 Deployment rollout 성공 | 외부 `cluster-2` Agent가 같은 digest로 교체됨 |
| 07-15 03:56 | `cluster-2-spot` desired 3 변경 | CloudTrail이 1→3 scaling 요청을 확인 | 트래픽 증가 원인이 scaling임 |
| 07-15 04:00 | in-service 3대 | CloudWatch가 실제 운전 노드 3대를 확인 | 각 노드의 Pod identity |
| 07-15 05:00~11:00 | 12.16→145.82GiB/h | EC2 양방향 네트워크가 급증함 | Agent·게임·EKS 제어 트래픽별 byte 비중 |
| 07-15 05:36 | 게임 부하 실측 기록 | 팀 기록상 `cluster-2`에서 20~100 bot·HPA 실험을 수행함 | AWS 원장이 Pod별 실험을 독립 검증함 |
| 07-17 03:00 | 1.00GiB/h로 급감, 노드 3대 | node scale-in 없이 전송량이 급감함 | 어떤 프로세스 종료가 급감을 만들었는지 |

즉 Git 배포 시점은 **원인 후보를 좁히는 증거**입니다. 외부 Agent rollout과 Pod/flow byte가 빠진 상태에서는 애플리케이션별 인과를 완성하는 증거가 아닙니다.

### 1초 자원 수집과 시간 상관관계

[`a47826ed`](https://github.com/Jungle-303-04/final/commit/a47826ed4becdf837fbe3b0ac9a79f9cc177f17d)는 Pod가 200개 미만이면 매초 node별 `/stats/summary`를 조회하고 변경된 Pod를 WebSocket으로 보내는 코드를 추가했습니다. GitHub Deployment 원장은 이 SHA의 service image가 07-15 02:45~02:51 KST에 성공 배포됐음을 보여주고, [Actions 로그](https://github.com/Jungle-303-04/final/actions/runs/29355081158/job/87160565202)는 management의 `cluster-agent` rollout 성공을 남깁니다.

여기서 증명되는 범위는 **management 배포**까지입니다. 외부 `cluster-2` Agent가 같은 digest로 교체됐다는 rollout 원장은 보존되지 않았습니다. 네트워크 상승 시각과 코드·게임 부하 시각은 가설을 만들기에 충분하지만, 2.69TiB를 kubelet 수집 또는 게임 트래픽 한쪽에 귀속할 수는 없습니다.

## 5. CloudWatch가 답하지 못하는 질문

당시 계정에는 다음 원장이 없습니다.

- Container Insights Pod/namespace network metric
- VPC Flow Logs 또는 CNI flow log
- Agent request/response byte counter와 provider별 payload histogram
- NATS subject별 message/byte counter의 보존 시계열
- 외부 target Agent의 image digest·rollout audit

EC2 `NetworkIn/Out`은 같은 AZ, 다른 AZ, 인터넷, EKS control plane, 게임 data plane을 모두 합칩니다. 그러므로 다음 문장은 사용하지 않습니다.

- “32개 worker가 14.84TB를 만들었다.”
- “cluster-2와 cluster-1의 차이가 Agent payload다.”
- “Management가 payload를 압축해 몇 % 줄였다.”
- “1MiB 상한까지 매 요청을 사용했다.”

## 6. 재발 방지를 위한 계측 계약

다음 배포에서는 원인을 추측하지 않도록 계측을 먼저 넣습니다.

```text
provider_request_bytes{provider}
provider_response_bytes{provider,status}
agent_management_payload_bytes{direction,endpoint}
evidence_payload_bytes{provider,truncated}
evidence_jobs_total{provider,status,reason_code}
nats_messages_total{subject}
nats_payload_bytes{subject}
```

- p50/p95/p99와 count를 함께 기록해 평균값 착시를 막습니다.
- cluster, namespace, provider까지만 label로 쓰고 resource UID는 trace/log에 둬 cardinality를 제한합니다.
- VPC Flow Logs 또는 CNI 관측으로 source/destination/AZ를 분리합니다.
- Agent image digest, collection interval, query count를 evidence metadata에 남깁니다.
- 같은 cluster에서 기능 off/on 구간을 만들고 node-hour, bytes, packets, CPU의 차분을 비교합니다.

## 7. 이력서에서 사용할 수 있는 문장

**코드로 방어 가능한 문장**

> Kubernetes·Prometheus·Loki·Tempo의 운영 데이터를 공통 evidence 구조로 정규화하고, 쿼리 성공 후 빈 결과와 수집 실패를 상태·사유 코드로 분리했습니다. 요청 전체를 1MiB 이하로 제한하고 잘림 여부와 원본 개수를 보존해 RCA가 근거의 신뢰 범위를 판단하도록 했습니다.

**AWS 회고 문장**

> CloudWatch와 Git 배포 이력을 대조해 특정 target cluster에서 50~70GiB 범위의 1시간 bucket 36개가 평균 60.04GiB/h였고, 이후 노드 수 변화 없이 1.00GiB/h로 급감한 사실을 확인했습니다. Pod·flow 원장이 없어 원인을 단정하지 않고, 다음 배포의 provider별 byte·status·AZ 계측 계약으로 전환했습니다.

두 번째 문장은 트래픽을 개인 구현의 성과로 포장하지 않습니다. 문제를 어디까지 증명했고 무엇이 없어 단정하지 않았는지를 보여주는 회고입니다.

## 8. 개인 기여 경계

Git author 기준으로 이민정은 [`7cc428e4`](https://github.com/Jungle-303-04/final/commit/7cc428e4)에서 공통 payload byte limiter와 관련 테스트를 만들었고, `f533762a`, `d7e5bf78`, `57ed4f78`에서 evidence·metadata 경계를 보강했습니다. 최종 provider collection status 구현은 팀 commit `17b35eb3`입니다.

따라서 개인 이력에는 “payload 한도·evidence/metadata 수집 경로 구현”을 직접 기여로 쓰고, 최종 상태 계약은 “팀의 상태 계약을 연동·검증했다”는 범위에서만 사용합니다. 팀 기능 전체를 단독 설계로 쓰지 않습니다.

## 9. 원시 자료

- [nodegroup 누적 NetworkIn/Out](evidence/network-cost/raw/cloudwatch-nodegroup-network.csv)
- [CPU·packet·node-hour](evidence/network-cost/raw/cloudwatch-nodegroup-runtime.csv)
- [target reference 비교](evidence/network-cost/raw/cloudwatch-target-reference.csv)
- [target 시간별 NetworkIn/Out·node count](evidence/network-cost/raw/cloudwatch-target-hourly-2026-07-14--18.csv)
- [Git·배포·AWS 통합 타임라인](evidence/network-cost/raw/git-aws-target-timeline.csv)
- [CloudWatch target 시간별 network 원본 그래프](evidence/network-cost/aws-console/cloudwatch-target-hourly-network-2026-07-14--18-kst.png)
- [CloudWatch target in-service node 원본 그래프](evidence/network-cost/aws-console/cloudwatch-target-hourly-nodes-2026-07-14--18-kst.png)
- [EKS 생명주기](evidence/network-cost/raw/eks-lifecycle.csv)
- [AWS Console 원본 캡처](evidence/network-cost/aws-console/)
- [재현 명령](evidence/network-cost/reproduce.md)
