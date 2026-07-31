# Kubernetes 실행 토폴로지와 네트워크 비용 증거 묶음

이 디렉터리는 `서비스를 작게 나누면 책임과 실패를 격리하기 쉽다`는 초기 판단이 실제 AWS 실행 환경에서 어떤 비용을 만들었는지 검증하기 위한 청구서입니다. 이미지에 적힌 숫자는 아래 CSV와 코드에서 다시 계산할 수 있습니다.

## 먼저 읽을 결론

- Git에는 2026-07-25 기준 `deploy/management/**`·`deploy/target/**` 범위의 Kubernetes `Deployment` 문서 47개와 선언 replica 합계 56개가 있었습니다. `src/services/**/app.py` 기준 서비스 entrypoint는 42개, 직계 디렉터리명이 `*-worker`인 entrypoint는 32개였습니다. 실제 가동 Pod 수가 아니라 정적 파일 집계입니다.
- AWS Cost Explorer의 2026-08-01 재조회 결과, 7월 4~27일 `APN2-DataTransfer-Regional-Bytes` 사용 레코드는 **29,683.72GB / $296.83**였습니다. 프로모션 Credit `-$296.83`이 적용되어 순결제액은 거의 0이지만, 크레딧이 없었다면 발생할 사용 비용입니다.
- Cross-AZ 송·수신 양쪽 과금 모델을 가정한 편도 상당량은 **14,841.86 billed GB(약 14.84TB-equivalent)**입니다. 실제 byte·패킷 측정값이 아니라 비용 청구서의 GB 값을 2로 나눈 등가값입니다.
- 7월 20~26일 7일에 22,295.89GB, 전체의 **75.1%**가 집중됐습니다. 최고일은 7월 22일 4,578.58GB / $45.79였습니다.
- CloudWatch EC2 인터페이스 합계에서 `battlegrounds-game`은 Out **4,895.28GiB**, `battlegrounds-infra`는 In **5,227.56GiB**로 방향이 맞물렸고, `management-server`는 Out **5,173.35GiB** / In **5,334.27GiB**로 양방향 통신이 컸습니다.
- 따라서 “32개 워커가 14,841.86 billed GB를 만들었다”는 결론은 증명되지 않습니다. 관리면 내부 이벤트 왕복과 게임→인프라 데이터면 전송이 함께 컸다는 것이 현재 증거가 허용하는 결론입니다.

Cost Explorer의 서비스 분해 결과, 총 29,683.72GB 중 **EC2 - Other가 29,573.84GB(99.63%)**, **Elastic Load Balancing이 109.87GB(0.37%)**였습니다. 29,573.84GB와 전체 사용량의 차이는 재조회 보정분이 아닙니다. 같은 기간의 ELB 사용량입니다. 조회 당시 전체 기간은 `Estimated=true`였습니다.

## 파일

- [`aws-regional-transfer-daily.csv`](raw/aws-regional-transfer-daily.csv): Cost Explorer 일별 Usage 레코드
- [`aws-regional-transfer-by-service.csv`](raw/aws-regional-transfer-by-service.csv): 동일 사용 유형의 EC2 - Other·ELB 서비스 분해
- [`cloudwatch-nodegroup-network.csv`](raw/cloudwatch-nodegroup-network.csv): EC2 Auto Scaling Group별 NetworkIn/Out 합계
- [`cloudwatch-nodegroup-runtime.csv`](raw/cloudwatch-nodegroup-runtime.csv): CPU·packet·가동 인스턴스의 동일 구간 집계
- [`cloudwatch-derived-network-density.csv`](raw/cloudwatch-derived-network-density.csv): 평균 bytes/packet과 GiB/node-hour 파생값
- [`git-topology-timeline.csv`](raw/git-topology-timeline.csv): 날짜별 Git 배포 토폴로지 개수
- [`eks-lifecycle.csv`](raw/eks-lifecycle.csv): CloudTrail에서 복원한 클러스터 수명
- [`event-bus-benchmark.json`](raw/event-bus-benchmark.json): 동일 이벤트 계약의 로컬 전달 비교
- [`airflow-validation.json`](raw/airflow-validation.json): DAG import·정상 실행·동일 날짜 재실행 검증
- [`airflow-dag-run-2026-08-01.csv`](raw/airflow-dag-run-2026-08-01.csv): 실제 Airflow task instance 상태
- [`airflow-bind-mount-failure.csv`](raw/airflow-bind-mount-failure.csv): archive root 삭제로 네 extract task가 재시도 상태가 된 실패 기록
- [`reproduce.md`](reproduce.md): AWS·Git 청구서 재조회와 환산 명령
- [`screenshots/README.md`](screenshots/README.md): 포트폴리오용 16:9 증거판과 Airflow raw DAG graph
- [`aws-console/`](aws-console/): 로그인된 AWS Console에서 직접 캡처한 Cost Explorer·CloudWatch 화면

전체 해석과 설계 변경은 [아키텍처 비용 회고](../../architecture-cost-postmortem.md)에 있습니다.

## 출처와 재현 조건

AWS 청구서은 개인 식별자, 계정 ID, 인스턴스 ID와 Auto Scaling Group 랜덤 suffix를 제거했습니다. 숫자는 지우지 않았습니다.

Cost Explorer 조회 조건:

```text
기간        2026-07-04 <= usage date < 2026-07-28
Region      ap-northeast-2 사용 유형(APN2)
Usage type  APN2-DataTransfer-Regional-Bytes
Record type Usage
Metric      UsageQuantity, UnblendedCost
재조회      2026-08-01 KST, Estimated=true
```

CloudWatch 조회 조건:

```text
Namespace   AWS/EC2, AWS/AutoScaling
Metrics     NetworkOut·NetworkIn·NetworkPacketsOut·NetworkPacketsIn / Sum
            CPUUtilization / Sum, SampleCount
            GroupInServiceInstances / Sum
Dimension   AutoScalingGroupName
기간        2026-07-04T00:00:00Z ~ 2026-07-28T00:00:00Z
단위 환산   bytes / 1,073,741,824 = GiB
청구서 추출   2026-07-31~08-01 KST
```

`bytes / packets`는 EC2 인터페이스의 방향성 진단값입니다. 애플리케이션 메시지 크기나 payload 크기가 아닙니다. CPU는 CloudWatch `Sum / SampleCount`, node-hour는 `GroupInServiceInstances Sum / 60`으로 계산했습니다. 부분 bucket의 단순 평균을 사용하지 않았지만 결제 청구서의 인스턴스 시간과는 다를 수 있습니다.

## AWS Console 원본 화면

계정 식별자가 있는 상단 header만 잘랐습니다. 그래프·필터·기간·수치는 수정하지 않았습니다.

- [Cost Explorer 일별 비용·사용량](aws-console/cost-explorer-daily-cost-usage-2026-07-04--27.jpg)
- [Cost Explorer 기간 합계](aws-console/cost-explorer-period-total-2026-07-04--27.png)
- [game Out과 infra In](aws-console/cloudwatch-game-out-infra-in-2026-07-04--28-utc.png)
- [management 양방향 트래픽](aws-console/cloudwatch-management-network-2026-07-04--28-utc.png)
- [노드그룹 CPU Average](aws-console/cloudwatch-nodegroup-cpu-average-2026-07-04--28-utc.png)
- [노드그룹 packet Sum](aws-console/cloudwatch-nodegroup-packets-sum-2026-07-04--28-utc.png)
- [가동 인스턴스 Average](aws-console/cloudwatch-nodegroup-inservice-average-2026-07-04--28-utc.png)

로컬 벤치마크 조건:

```text
호스트      Apple M4 / arm64 / macOS 26.5.2
NATS        nats:2.11-alpine, Docker 29.4.0, 127.0.0.1
계약        실제 EventEnvelope JSON, 직렬화 크기 1,393B
부하        1,000 events/round, warm-up 1회, 측정 5회
결과        median, 2026-08-01 00:36 KST
```

재실행:

```bash
docker run --rm -d --name kyro-benchmark-nats -p 127.0.0.1:4223:4222 nats:2.11-alpine -js
NATS_URL=nats://127.0.0.1:4223 make benchmark-event-bus-compare
docker stop kyro-benchmark-nats
```

## 증거가 말하지 못하는 것

- Cost Explorer 사용 유형만으로 어느 Pod·API·이벤트가 몇 GB를 만들었는지 역산할 수 없습니다.
- EC2 `NetworkIn/Out`은 인터페이스 전체 트래픽입니다. Cross-AZ 과금 대상만 분리한 수치가 아닙니다.
- packet 수와 평균 bytes/packet은 네트워크 방향성을 보조할 뿐 요청 수·이벤트 수·처리량을 증명하지 않습니다.
- Git manifest 개수는 실제 Pod 가동 개수와 다릅니다. 실제 replica·재시작·스케줄 위치 기록은 남아 있지 않습니다.
- 로컬 이벤트 버스 벤치마크는 네트워크 전달 계층만 비교합니다. PostgreSQL transaction, 비즈니스 handler, 외부 API가 포함된 end-to-end 처리량이 아닙니다.
- 통합 controller는 코드 조립 검증까지 완료했지만, 같은 부하로 AWS에 재배포해 비용 감소를 관측하지 않았습니다. 따라서 아직 “비용을 몇 % 절감했다”고 쓰지 않습니다.
