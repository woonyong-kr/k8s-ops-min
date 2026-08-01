# 청구서 재조회와 계산 방법

AWS 계정 ID와 리소스 랜덤 suffix가 출력될 수 있으므로 명령 결과를 그대로 공개 저장소에 올리지 않습니다. 아래 쿼리 결과에서 식별자를 제거한 뒤 `raw/` CSV와 대조합니다.

## Cost Explorer

비용 항목을 Credit과 Usage로 나눠 봅니다. `End`는 exclusive입니다.

```bash
aws ce get-cost-and-usage \
  --time-period Start=2026-07-04,End=2026-07-28 \
  --granularity MONTHLY \
  --metrics UnblendedCost UsageQuantity \
  --filter '{"Dimensions":{"Key":"USAGE_TYPE","Values":["APN2-DataTransfer-Regional-Bytes"]}}' \
  --group-by Type=DIMENSION,Key=RECORD_TYPE
```

일별 Usage만 조회합니다.

```bash
aws ce get-cost-and-usage \
  --time-period Start=2026-07-04,End=2026-07-28 \
  --granularity DAILY \
  --metrics UnblendedCost UsageQuantity \
  --filter '{"And":[{"Dimensions":{"Key":"USAGE_TYPE","Values":["APN2-DataTransfer-Regional-Bytes"]}},{"Dimensions":{"Key":"RECORD_TYPE","Values":["Usage"]}}]}' \
  --query 'ResultsByTime[].{date:TimePeriod.Start,gb:Total.UsageQuantity.Amount,cost:Total.UnblendedCost.Amount,estimated:Estimated}'
```

편도 상당량 계산:

```text
29,683.7183586736 billed GB ÷ 2 = 14,841.8591793368 billed GB
표시용 십진 환산: 약 14.84 TB-equivalent
```

AWS가 Regional Transfer를 송신·수신 측에 각각 기록한다는 과금 모델에 따른 등가 환산입니다. Cost Explorer의 GB를 GiB로 간주하지 않으며 VPC Flow Logs의 byte·패킷 합계가 아닙니다.

## CloudWatch EC2

당시 전체 Auto Scaling Group 이름은 `list-metrics`로 복원했습니다.

```bash
aws cloudwatch list-metrics \
  --region ap-northeast-2 \
  --namespace AWS/EC2 \
  --metric-name NetworkOut
```

각 full Auto Scaling Group 이름으로 In/Out 합계를 구했습니다.

```bash
aws cloudwatch get-metric-statistics \
  --region ap-northeast-2 \
  --namespace AWS/EC2 \
  --metric-name NetworkOut \
  --dimensions Name=AutoScalingGroupName,Value="$FULL_ASG_NAME" \
  --start-time 2026-07-04T00:00:00Z \
  --end-time 2026-07-28T00:00:00Z \
  --period 86400 \
  --statistics Sum
```

`NetworkIn`도 같은 방식으로 조회하고 `Sum / 1,073,741,824`로 GiB로 바꿨습니다. CloudWatch retention과 inactive metric 검색 제약 때문에 2026-07-31 에 추출한 원본을 보존했습니다.

같은 구간에서 `NetworkPacketsOut`·`NetworkPacketsIn`은 `Sum`으로 조회했습니다. CPU는 CloudWatch 통계의 `Sum / SampleCount`로 가중 평균을 계산했습니다. `AWS/AutoScaling`의 `GroupInServiceInstances`는 `Sum / 60`으로 node-hour를 계산했습니다.

```text
avg bytes/packet = Network bytes Sum / NetworkPackets Sum
node-hour        = GroupInServiceInstances Sum / 60
GiB/node-hour    = Network GiB / node-hour
```

이 파생값은 EC2 인터페이스의 방향과 밀도를 비교하기 위한 값입니다. 애플리케이션 메시지 크기, 요청 처리량, Cross-AZ 연결값으로 사용하지 않습니다.

## CloudTrail EKS 수명

```bash
aws cloudtrail lookup-events \
  --region ap-northeast-2 \
  --start-time 2026-07-04T00:00:00Z \
  --end-time 2026-07-28T00:00:00Z \
  --lookup-attributes AttributeKey=EventSource,AttributeValue=eks.amazonaws.com
```

`CreateCluster`, `DeleteCluster`, `CreateNodegroup`, `DeleteNodegroup`만 골라 [`eks-lifecycle.csv`](raw/eks-lifecycle.csv)를 만들었습니다.

## Git 토폴로지

각 날짜의 마지막 revision에서 다음 세 값을 별도로 셌습니다.

1. `deploy/management/**`·`deploy/target/**` YAML 중 `kind: Deployment` 수
2. `src/services/**/app.py` entrypoint 수
3. `app.py` 직계 디렉터리명이 `*-worker`인 entrypoint 수

대표 revision과 결과는 [`git-topology-timeline.csv`](raw/git-topology-timeline.csv)에 있습니다. `replicas:` 합계는 정적 manifest의 희망 값이며 실제 Pod metric이 아닙니다.

## 이미지 재생성

```bash
python3 docs/evidence/network-cost/render_evidence.py
for svg in docs/evidence/network-cost/screenshots/*.svg; do
  sips -s format png "$svg" --out "${svg%.svg}.png"
done
```

PNG 생성은 macOS `sips` 기준입니다. SVG가 원본이므로 다른 환경에서는 브라우저나 SVG renderer로 1600×900 PNG를 만들 수 있습니다.
