#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose_file="${repo_root}/docker-compose.payload-benchmark.yml"
result_root="${repo_root}/.ecc/benchmarks/payload-experiment"
run_id="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
git_sha="$(git -C "${repo_root}" rev-parse HEAD)"
kubeconfig_path="$(mktemp -t kyro-payload-kubeconfig.XXXXXX)"
mounted_kubeconfig="${result_root}/fixtures/target-kubeconfig"
experiment_mode="${EXPERIMENT_MODE:-single}"
monitor_pid=""

cleanup() {
  if [ -n "${monitor_pid}" ]; then
    kill "${monitor_pid}" 2>/dev/null || true
  fi
  rm -f "${kubeconfig_path}"
  rm -f "${mounted_kubeconfig}"
}
trap cleanup EXIT

mkdir -p "${result_root}/fixtures" "${result_root}/runs/${run_id}"

if ! kind get clusters | grep -qx target; then
  echo "kind target cluster is required" >&2
  exit 1
fi
kind get kubeconfig --name target > "${kubeconfig_path}"
install -m 600 "${kubeconfig_path}" "${mounted_kubeconfig}"
KUBECONFIG="${kubeconfig_path}" \
KUBERNETES_FIXTURE_OUT="${result_root}/fixtures/kubernetes-actual.json" \
  python3 "${repo_root}/benchmarks/payload_lab/capture_kubernetes.py"

RUN_ID="${run_id}" GIT_SHA="${git_sha}" \
  docker compose -f "${compose_file}" up -d --build

if [ "${experiment_mode}" = "soak" ]; then
  soak_case="${SOAK_CASE:-baseline}"
  soak_seconds="${SOAK_SECONDS:-1800}"
  soak_interval_seconds="${SOAK_INTERVAL_SECONDS:-30}"
  "${repo_root}/scripts/monitor-payload-containers.sh" "${run_id}" "${soak_seconds}" 15 &
  monitor_pid="$!"
  RUN_ID="${run_id}" GIT_SHA="${git_sha}" \
    docker compose -f "${compose_file}" run --rm \
      -e SOAK_CASE="${soak_case}" \
      -e SOAK_SECONDS="${soak_seconds}" \
      -e SOAK_INTERVAL_SECONDS="${soak_interval_seconds}" \
      benchmark python -m benchmarks.payload_lab.soak
  wait "${monitor_pid}"
  monitor_pid=""
else
  RUN_ID="${run_id}" GIT_SHA="${git_sha}" \
    docker compose -f "${compose_file}" run --rm benchmark \
      python -m benchmarks.payload_lab.runner
fi

docker version --format '{{json .}}' > "${result_root}/runs/${run_id}/docker-version.json"
docker compose -f "${compose_file}" images --format json > "${result_root}/runs/${run_id}/container-images.json"
docker stats --no-stream --format json > "${result_root}/runs/${run_id}/docker-stats.json"
printf '%s\n' "${run_id}" > "${result_root}/LATEST_RUN"

echo "run_id=${run_id}"
echo "grafana=http://localhost:53000/d/kyro-payload/kyro-evidence-payload?var-run_id=${run_id}&from=now-30m&to=now&refresh=5s"
echo "results=${result_root}/runs/${run_id}"
