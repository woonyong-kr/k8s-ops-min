#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/env.sh"

MGMT_CLUSTER="${MGMT_CLUSTER:-}"
MGMT_CONTEXT="${MGMT_CONTEXT:-${MGMT_CLUSTER}}"
DEPLOYMENT="${1:-}"
REPLICAS="${2:-}"
require_env MGMT_CONTEXT

if [[ -z "${DEPLOYMENT}" || -z "${REPLICAS}" ]]; then
  echo "usage: $0 <management-deployment> <replicas>" >&2
  echo "example: $0 rca-worker 3" >&2
  exit 1
fi

kubectl --context "${MGMT_CONTEXT}" -n management scale "deploy/${DEPLOYMENT}" --replicas="${REPLICAS}"
kubectl --context "${MGMT_CONTEXT}" -n management rollout status "deploy/${DEPLOYMENT}" --timeout=120s
