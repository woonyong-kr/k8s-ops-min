# Evidence payload experiment

Kubernetes API, Prometheus, Loki, Tempo를 같은 5분 시간창에서 수집한 뒤 실제 provider 정규화 코드와 Management claim-check 코드를 통과시킨다.

```bash
scripts/run-payload-experiment.sh
```

30분 기준선과 크기 증폭 실험은 서로 다른 `RUN_ID`로 실행한다.

```bash
RUN_ID=baseline-$(date -u +%Y%m%dT%H%M%SZ) \
EXPERIMENT_MODE=soak SOAK_CASE=baseline \
SOAK_SECONDS=1800 SOAK_INTERVAL_SECONDS=30 \
scripts/run-payload-experiment.sh

RUN_ID=stress-$(date -u +%Y%m%dT%H%M%SZ) \
EXPERIMENT_MODE=soak SOAK_CASE=stress \
SOAK_SECONDS=1800 SOAK_INTERVAL_SECONDS=30 \
scripts/run-payload-experiment.sh
```

실제 Kubernetes API 조회에 쓰는 임시 kubeconfig는 실행 동안에만 결과 volume에 `0600` 권한으로 마운트하고 종료할 때 삭제한다. 증거 아카이브에는 포함하지 않는다.

일회 경계 실험은 다음 경로에 원시 수치와 실행 환경을 남긴다.

- `.ecc/benchmarks/payload-experiment/runs/<run_id>/manifest.json`
- `.ecc/benchmarks/payload-experiment/runs/<run_id>/results.json`
- `.ecc/benchmarks/payload-experiment/runs/<run_id>/results.csv`
- `.ecc/benchmarks/payload-experiment/runs/<run_id>/metrics.prom`

30분 실험은 같은 run 디렉터리에 `progress.json`, `summary.json`, `cycles.json`, `results.json`, `results.csv`, `docker-stats.jsonl`을 남긴다. `summary.json`의 cycle 수가 60인지, 신호 손실과 계약 위반이 0인지 먼저 확인한다.

완료된 30분 시계열을 Grafana와 독립적인 JSON 증거로 저장한다.

```bash
RUN_ID=<완료한-run-id> python3 -m benchmarks.payload_lab.export_prometheus
```

Grafana는 `http://localhost:53000/d/kyro-payload/kyro-evidence-payload`에서 확인한다. 대시보드의 `run_id`가 실행 결과와 같은지 먼저 확인한다. 하단 시계열은 변환 지연, Agent→Management body, 단계별 총 byte, 신호 보존·계약 유효성을 5초 scrape 간격으로 보여준다. 실험 반복 횟수는 그래프의 점 수가 아니라 `progress.json`과 `summary.json`의 30초 cycle로 판정한다.

성공 조건은 네 provider가 모두 포함되고, 고정한 장애 신호가 정규화 뒤에도 남고, provider별 Agent 계약이 1 MiB 상한을 통과하며, 같은 `window_start`로 합쳐지는 것이다. 하나라도 실패하면 러너는 종료 코드 2를 반환한다.
