# Evidence payload experiment

Kubernetes API, Prometheus, Loki, Tempo를 같은 5분 시간창에서 수집한 뒤 실제 provider 정규화 코드와 Management claim-check 코드를 통과시킨다.

```bash
scripts/run-payload-experiment.sh
```

실험이 성공하면 다음 경로에 원시 수치와 실행 환경이 남는다.

- `.ecc/benchmarks/payload-experiment/runs/<run_id>/manifest.json`
- `.ecc/benchmarks/payload-experiment/runs/<run_id>/results.json`
- `.ecc/benchmarks/payload-experiment/runs/<run_id>/results.csv`
- `.ecc/benchmarks/payload-experiment/runs/<run_id>/metrics.prom`

Grafana는 `http://localhost:53000/d/kyro-payload/kyro-evidence-payload`에서 확인한다. 대시보드의 `run_id`가 실행 결과와 같은지 먼저 확인한다.

성공 조건은 네 provider가 모두 포함되고, 고정한 장애 신호가 정규화 뒤에도 남고, provider별 Agent 계약이 1 MiB 상한을 통과하며, 같은 `window_start`로 합쳐지는 것이다. 하나라도 실패하면 러너는 종료 코드 2를 반환한다.
