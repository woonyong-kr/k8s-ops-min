#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${EXTERNAL_CONSOLE_INSTANCES_ENV_FILE:-${ROOT_DIR}/.env.external-console-instances}"

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

key_var() {
  local key="$1"
  local suffix="$2"
  local normalized
  normalized="$(printf '%s' "${key}" | tr '[:lower:]-' '[:upper:]_')"
  printf 'EXTERNAL_CONSOLE_%s_%s' "${normalized}" "${suffix}"
}

value_for() {
  local key="$1"
  local suffix="$2"
  local var
  var="$(key_var "${key}" "${suffix}")"
  printf '%s' "${!var:-}"
}

console_handle() {
  local handle="$1"
  handle="${handle#@}"
  printf '@%s' "${handle}"
}

run_console() {
  local url="$1"
  local token="$2"
  shift 2
  "${EXTERNAL_CONSOLE_CLI}" deployments --url "${url}" --token "${token}" "$@"
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

EXTERNAL_CONSOLE_CLI="${EXTERNAL_CONSOLE_CLI:-}"
if [[ -z "${EXTERNAL_CONSOLE_CLI}" ]]; then
  echo "missing EXTERNAL_CONSOLE_CLI in ${ENV_FILE}" >&2
  exit 1
fi
need "${EXTERNAL_CONSOLE_CLI}"

EXTERNAL_CONSOLE_INSTANCES="${EXTERNAL_CONSOLE_INSTANCES:-cluster01 cluster02}"
FETCH_KUBECONFIG="${FETCH_KUBECONFIG:-false}"

for key in $(split_words "${EXTERNAL_CONSOLE_INSTANCES}"); do
  name="$(value_for "${key}" NAME)"
  owner="$(value_for "${key}" OWNER)"
  provider="$(value_for "${key}" PROVIDER)"
  region="$(value_for "${key}" REGION)"
  hosting="$(value_for "${key}" HOSTING)"
  size="$(value_for "${key}" SIZE)"
  url="$(value_for "${key}" CONSOLE_URL)"
  token="$(value_for "${key}" CONSOLE_TOKEN)"
  cluster_handles="$(value_for "${key}" CLUSTER_HANDLES)"

  section "External console instance ${name:-${key}}"
  [[ -n "${owner}" ]] && echo "owner: ${owner}"
  [[ -n "${provider}" ]] && echo "provider: ${provider}"
  [[ -n "${region}" ]] && echo "region: ${region}"
  [[ -n "${hosting}" ]] && echo "hosting: ${hosting}"
  [[ -n "${size}" ]] && echo "size: ${size}"

  if [[ -z "${url}" || -z "${token}" ]]; then
    echo "console credentials not configured for ${key}."
    echo "set $(key_var "${key}" CONSOLE_URL) and $(key_var "${key}" CONSOLE_TOKEN) in ${ENV_FILE}"
    continue
  fi

  run_or_note "${name:-${key}} CD clusters" \
    run_console "${url}" "${token}" clusters list
  run_or_note "${name:-${key}} CD providers" \
    run_console "${url}" "${token}" providers list
  run_or_note "${name:-${key}} repositories" \
    run_console "${url}" "${token}" repositories list

  while IFS= read -r handle; do
    [[ -z "${handle}" ]] && continue
    run_or_note "${name:-${key}} services ${handle}" \
      run_console "${url}" "${token}" services list "$(console_handle "${handle}")"
    if [[ "${FETCH_KUBECONFIG}" == "true" ]]; then
      run_or_note "${name:-${key}} kubeconfig ${handle}" \
        run_console "${url}" "${token}" clusters get-credentials "$(console_handle "${handle}")"
    fi
  done < <(split_words "${cluster_handles}")
done
