#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose_file="${repo_root}/docker-compose.payload-benchmark.yml"
result_root="${repo_root}/.ecc/benchmarks/payload-experiment"
run_id="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
git_sha="$(git -C "${repo_root}" rev-parse HEAD)"
kubeconfig_path="$(mktemp -t kyro-payload-kubeconfig.XXXXXX)"

cleanup() {
  rm -f "${kubeconfig_path}"
}
trap cleanup EXIT

mkdir -p "${result_root}/fixtures" "${result_root}/runs/${run_id}"

if ! kind get clusters | grep -qx target; then
  echo "kind target cluster is required" >&2
  exit 1
fi
kind get kubeconfig --name target > "${kubeconfig_path}"
KUBECONFIG="${kubeconfig_path}" \
KUBERNETES_FIXTURE_OUT="${result_root}/fixtures/kubernetes-actual.json" \
  python3 "${repo_root}/benchmarks/payload_lab/capture_kubernetes.py"

RUN_ID="${run_id}" GIT_SHA="${git_sha}" \
  docker compose -f "${compose_file}" up -d --build

RUN_ID="${run_id}" GIT_SHA="${git_sha}" \
  docker compose -f "${compose_file}" run --rm benchmark \
    python -m benchmarks.payload_lab.runner

docker version --format '{{json .}}' > "${result_root}/runs/${run_id}/docker-version.json"
docker compose -f "${compose_file}" images --format json > "${result_root}/runs/${run_id}/container-images.json"
docker stats --no-stream --format json > "${result_root}/runs/${run_id}/docker-stats.json"
printf '%s\n' "${run_id}" > "${result_root}/LATEST_RUN"

echo "run_id=${run_id}"
echo "grafana=http://localhost:53000/d/kyro-payload/kyro-evidence-payload?var-run_id=${run_id}&from=now-15m&to=now"
echo "results=${result_root}/runs/${run_id}"
