#!/usr/bin/env bash
set -euo pipefail

run_id="${1:?run id is required}"
duration_seconds="${2:-1800}"
interval_seconds="${3:-15}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
destination="${repo_root}/.ecc/benchmarks/payload-experiment/runs/${run_id}/docker-stats.jsonl"
deadline=$(( $(date +%s) + duration_seconds ))

mkdir -p "$(dirname "${destination}")"
while [ "$(date +%s)" -lt "${deadline}" ]; do
  observed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  docker stats --no-stream --format '{{json .}}' \
    $(docker ps --filter name=kyro-payload-benchmark --format '{{.Names}}') \
    | sed "s/^{/{\"observed_at\":\"${observed_at}\",/" >> "${destination}"
  sleep "${interval_seconds}"
done
