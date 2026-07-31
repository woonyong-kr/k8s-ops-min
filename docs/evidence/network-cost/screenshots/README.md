# 증거판 이미지

SVG 원본은 `python docs/evidence/network-cost/render_evidence.py`로 다시 생성합니다. PNG는 macOS `sips`로 1600×900으로 변환한 배포용 사본입니다. `05-airflow-dag.png`는 Airflow가 직접 렌더링한 825×89 raw graph라서 예외입니다.

- `01-aws-regional-transfer`: 일별 Regional Transfer와 집중 구간
- `02-cloudwatch-nodegroup-direction`: 노드그룹별 송수신 방향
- `03-architecture-before-after`: 논리 이벤트와 물리 배포 경계 변경
- `04-event-bus-benchmark`: 동일 이벤트 계약의 로컬 전달 비교
- `05-airflow-dag.png`: Airflow `dags show`가 직접 렌더링한 raw task graph
- `06-event-contract-and-limits`: 이벤트 공통 필드, 재시도·backpressure 기본값, 보존하지 못한 운영 지표
- `07-airflow-failure-to-proof`: bind-mount 실패 원인, inode를 유지한 수정, 7/7·5→5·15/15 재검증

모든 이미지 하단의 출처·범위 문구를 자르지 않고 사용해야 합니다.
