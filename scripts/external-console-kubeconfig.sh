#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${EXTERNAL_CONSOLE_ENV_FILE:-${ROOT_DIR}/.env.external-console}"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

split_words() {
  tr ',\n\t' '   ' <<< "${1:-}" | xargs -n1 2>/dev/null || true
}

console_handle() {
  local handle="$1"
  handle="${handle#@}"
  printf '@%s' "${handle}"
}

need kubectl

EXTERNAL_CONSOLE_CLI="${EXTERNAL_CONSOLE_CLI:-}"
EXTERNAL_CONSOLE_URL="${EXTERNAL_CONSOLE_URL:-}"
EXTERNAL_CONSOLE_TOKEN="${EXTERNAL_CONSOLE_TOKEN:-}"
EXTERNAL_CLUSTER_HANDLES="${EXTERNAL_CLUSTER_HANDLES:-}"
CLUSTER_CONTEXTS="${CLUSTER_CONTEXTS:-}"
if [[ -z "${CLUSTER_CONTEXTS}" ]]; then
  CLUSTER_CONTEXTS="${MGMT_CONTEXT:-} ${TARGET_CONTEXT:-}"
fi
if [[ -z "${CLUSTER_CONTEXTS// }" ]]; then
  CLUSTER_CONTEXTS="cluster-1 cluster-2"
fi

if [[ -n "${EXTERNAL_CONSOLE_URL}" && -n "${EXTERNAL_CONSOLE_TOKEN}" ]]; then
  if [[ -z "${EXTERNAL_CONSOLE_CLI}" ]]; then
    echo "missing EXTERNAL_CONSOLE_CLI for external console login" >&2
    exit 1
  fi
  need "${EXTERNAL_CONSOLE_CLI}"
  echo "==> logging in to external console"
  "${EXTERNAL_CONSOLE_CLI}" deployments login --url "${EXTERNAL_CONSOLE_URL}" --token "${EXTERNAL_CONSOLE_TOKEN}"
else
  echo "==> skipping external console login; set EXTERNAL_CONSOLE_URL and EXTERNAL_CONSOLE_TOKEN in ${ENV_FILE}"
fi

if [[ -n "${EXTERNAL_CLUSTER_HANDLES}" ]]; then
  if [[ -z "${EXTERNAL_CONSOLE_CLI}" ]]; then
    echo "missing EXTERNAL_CONSOLE_CLI for external cluster handles" >&2
    exit 1
  fi
  need "${EXTERNAL_CONSOLE_CLI}"
  echo "==> fetching kubeconfig entries from external console"
  while IFS= read -r handle; do
    [[ -z "${handle}" ]] && continue
    "${EXTERNAL_CONSOLE_CLI}" deployments clusters get-credentials "$(console_handle "${handle}")"
  done < <(split_words "${EXTERNAL_CLUSTER_HANDLES}")
else
  echo "==> EXTERNAL_CLUSTER_HANDLES is empty; using existing kubeconfig only"
fi

echo
echo "==> kube contexts"
kubectl config get-contexts

echo
echo "==> validating configured contexts"
status=0
while IFS= read -r context; do
  [[ -z "${context}" ]] && continue
  printf '%s: ' "${context}"
  if kubectl --context "${context}" get namespace default --request-timeout=10s >/dev/null 2>&1; then
    echo "ok"
  else
    echo "unreachable"
    status=1
  fi
done < <(split_words "${CLUSTER_CONTEXTS}")
exit "${status}"
