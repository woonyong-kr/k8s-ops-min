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

section() {
  printf '\n## %s\n' "$1"
}

run_or_note() {
  local label="$1"
  shift
  section "${label}"
  if ! "$@"; then
    echo "skipped or failed: $*" >&2
  fi
}

context_exists() {
  kubectl config get-contexts "$1" --no-headers 2>/dev/null | awk 'NF { found = 1 } END { exit !found }'
}

namespace_exists() {
  local context="$1"
  local namespace="$2"
  kubectl --context "${context}" get namespace "${namespace}" --request-timeout=10s >/dev/null 2>&1
}

console_handle() {
  local handle="$1"
  handle="${handle#@}"
  printf '@%s' "${handle}"
}

need kubectl

CLUSTER_CONTEXTS="${CLUSTER_CONTEXTS:-}"
if [[ -z "${CLUSTER_CONTEXTS}" ]]; then
  CLUSTER_CONTEXTS="${MGMT_CONTEXT:-} ${TARGET_CONTEXT:-}"
fi
if [[ -z "${CLUSTER_CONTEXTS// }" ]]; then
  CLUSTER_CONTEXTS="cluster-1 cluster-2"
fi

INTERACTION_NAMESPACES="${INTERACTION_NAMESPACES:-management target sandbox default kube-system}"
EVENT_LIMIT="${EVENT_LIMIT:-12}"
EXTERNAL_CONSOLE_CLI="${EXTERNAL_CONSOLE_CLI:-}"
EXTERNAL_CLUSTER_HANDLES="${EXTERNAL_CLUSTER_HANDLES:-}"

if [[ -n "${EXTERNAL_CONSOLE_CLI}" ]] && command -v "${EXTERNAL_CONSOLE_CLI}" >/dev/null 2>&1 && [[ -n "${EXTERNAL_CLUSTER_HANDLES}" ]]; then
  run_or_note "External console clusters" "${EXTERNAL_CONSOLE_CLI}" deployments clusters list
  while IFS= read -r handle; do
    [[ -z "${handle}" ]] && continue
    run_or_note "External console services ${handle}" "${EXTERNAL_CONSOLE_CLI}" deployments services list "$(console_handle "${handle}")"
  done < <(split_words "${EXTERNAL_CLUSTER_HANDLES}")
fi

while IFS= read -r context; do
  [[ -z "${context}" ]] && continue

  section "Context ${context}"
  if ! context_exists "${context}"; then
    echo "context not found: ${context}" >&2
    continue
  fi

  run_or_note "${context} nodes" \
    kubectl --context "${context}" get nodes -o wide --request-timeout=20s

  run_or_note "${context} namespaces" \
    kubectl --context "${context}" get namespaces --request-timeout=20s

  run_or_note "${context} workloads" \
    kubectl --context "${context}" get deployments,statefulsets,daemonsets -A -o wide --request-timeout=20s

  run_or_note "${context} pods" \
    kubectl --context "${context}" get pods -A -o wide --request-timeout=20s

  run_or_note "${context} services and ingress" \
    kubectl --context "${context}" get services,ingress -A -o wide --request-timeout=20s

  if command -v helm >/dev/null 2>&1; then
    run_or_note "${context} Helm releases" \
      helm --kube-context "${context}" list -A
  fi

  while IFS= read -r namespace; do
    [[ -z "${namespace}" ]] && continue
    if namespace_exists "${context}" "${namespace}"; then
      run_or_note "${context}/${namespace} recent events" \
        bash -c 'kubectl --context "$1" -n "$2" get events --sort-by=.lastTimestamp --request-timeout=20s | tail -n "$3"' \
        _ "${context}" "${namespace}" "${EVENT_LIMIT}"
    fi
  done < <(split_words "${INTERACTION_NAMESPACES}")
done < <(split_words "${CLUSTER_CONTEXTS}")
