#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/env.sh"

MGMT_CLUSTER="${MGMT_CLUSTER:-}"
TARGET_CLUSTER="${TARGET_CLUSTER:-}"
MGMT_CONTEXT="${MGMT_CONTEXT:-${MGMT_CLUSTER}}"
TARGET_CONTEXT="${TARGET_CONTEXT:-${TARGET_CLUSTER}}"
BASE_URL="${BASE_URL:-}"

require_env MGMT_CONTEXT
require_env TARGET_CONTEXT

echo "==> management pods"
kubectl --context "${MGMT_CONTEXT}" -n management get pods -o wide

echo
echo "==> target pods"
kubectl --context "${TARGET_CONTEXT}" -n target get pods -o wide

echo
echo "==> console/api health"
if [ -n "${BASE_URL}" ]; then
  if ! curl -fsS "${BASE_URL%/}/api/healthz"; then
    curl -fsS "${BASE_URL%/}/healthz"
  fi
else
  echo "BASE_URL not set; skipping console/api health"
fi
echo
