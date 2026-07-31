#!/usr/bin/env bash
set -euo pipefail

MGMT_CLUSTER="${MGMT_CLUSTER:-management}"
TARGET_CLUSTER="${TARGET_CLUSTER:-target}"

if kind get clusters | grep -qx "${TARGET_CLUSTER}"; then
  kind delete cluster --name "${TARGET_CLUSTER}"
else
  echo "target cluster not found: ${TARGET_CLUSTER}"
fi

if kind get clusters | grep -qx "${MGMT_CLUSTER}"; then
  kind delete cluster --name "${MGMT_CLUSTER}"
else
  echo "management cluster not found: ${MGMT_CLUSTER}"
fi

