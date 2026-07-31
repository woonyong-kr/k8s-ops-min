#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/env.sh"

MGMT_CLUSTER="${MGMT_CLUSTER:-}"
MGMT_CONTEXT="${MGMT_CONTEXT:-${MGMT_CLUSTER}}"
DEPLOYMENT="${1:-}"
require_env MGMT_CONTEXT

if [[ -z "${DEPLOYMENT}" ]]; then
  echo "usage: $0 <management-deployment>" >&2
  echo "example: $0 rca-worker" >&2
  exit 1
fi

pod="$(kubectl --context "${MGMT_CONTEXT}" -n management get pod \
  -l "app=${DEPLOYMENT}" \
  -o jsonpath='{.items[0].metadata.name}')"

echo "deleting pod ${pod}"
kubectl --context "${MGMT_CONTEXT}" -n management delete pod "${pod}"
kubectl --context "${MGMT_CONTEXT}" -n management rollout status "deploy/${DEPLOYMENT}" --timeout=120s
