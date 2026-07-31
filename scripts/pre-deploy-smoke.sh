#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/env.sh"
source "${SCRIPT_DIR}/lib/cluster-curl.sh"

BASE_URL="${BASE_URL:-}"
MGMT_CONTEXT="${MGMT_CONTEXT:-}"
MGMT_NS="${MGMT_NS:-management}"
PRE_DEPLOY_HEALTH_MAX_ATTEMPTS="${PRE_DEPLOY_HEALTH_MAX_ATTEMPTS:-7}"
PRE_DEPLOY_HEALTH_BACKOFF_MAX_SECONDS="${PRE_DEPLOY_HEALTH_BACKOFF_MAX_SECONDS:-30}"
SMOKE_CURL_IMAGE="${SMOKE_CURL_IMAGE:-curlimages/curl:8.11.1}"
CLUSTER_CURL_POD_PREFIX="deploy-smoke-pre"
IN_CLUSTER_API_URL="http://api-gateway.${MGMT_NS}.svc.cluster.local"
IN_CLUSTER_CONSOLE_URL="http://console-dev.${MGMT_NS}.svc.cluster.local"

require_env BASE_URL
require_env MGMT_CONTEXT
BASE_URL="${BASE_URL%/}"
[[ "${PRE_DEPLOY_HEALTH_MAX_ATTEMPTS}" =~ ^[1-9][0-9]*$ ]]
[[ "${PRE_DEPLOY_HEALTH_BACKOFF_MAX_SECONDS}" =~ ^[0-9]+$ ]]

for command in kubectl python3; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    echo "missing required command: ${command}" >&2
    exit 1
  fi
done

index_file="$(mktemp)"
health_file="$(mktemp)"
trap 'rm -f "${index_file}" "${health_file}"' EXIT

echo "==> pre-deploy gateway health" >&2
health_ready=0
for attempt in $(seq 1 "${PRE_DEPLOY_HEALTH_MAX_ATTEMPTS}"); do
  if health_response="$(cluster_curl "${IN_CLUSTER_API_URL}/api/healthz")"; then
    health_status="${health_response##*$'\n'}"
    printf '%s' "${health_response%$'\n'*}" >"${health_file}"
  else
    health_status="000"
  fi
  printf 'pre-deploy health attempt=%s/%s status=%s\n' \
    "${attempt}" "${PRE_DEPLOY_HEALTH_MAX_ATTEMPTS}" "${health_status}" >&2

  if [ "${health_status}" = "200" ] && python3 - "${health_file}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    document = json.load(handle)
if document.get("status") != "ok":
    raise SystemExit("pre-deploy gateway health is not ok")
PY
  then
    health_ready=1
    break
  fi

  if [ "${attempt}" -lt "${PRE_DEPLOY_HEALTH_MAX_ATTEMPTS}" ]; then
    backoff_seconds=$((1 << (attempt - 1)))
    if [ "${backoff_seconds}" -gt "${PRE_DEPLOY_HEALTH_BACKOFF_MAX_SECONDS}" ]; then
      backoff_seconds="${PRE_DEPLOY_HEALTH_BACKOFF_MAX_SECONDS}"
    fi
    printf 'pre-deploy health retry_in_seconds=%s\n' "${backoff_seconds}" >&2
    sleep "${backoff_seconds}"
  fi
done
test "${health_ready}" = "1"

echo "==> pre-deploy frontend" >&2
frontend_response="$(cluster_curl "${IN_CLUSTER_CONSOLE_URL}/")"
frontend_status="${frontend_response##*$'\n'}"
printf '%s' "${frontend_response%$'\n'*}" >"${index_file}"
test "${frontend_status}" = "200"
frontend_bundle="$(grep -Eom1 'index-[A-Za-z0-9_-]+\.js' "${index_file}")"
if [ -z "${frontend_bundle}" ]; then
  echo "pre-deploy frontend did not expose a versioned bundle" >&2
  exit 1
fi

echo "==> pre-deploy database connection" >&2
database_probe="$(
  kubectl --context "${MGMT_CONTEXT}" -n "${MGMT_NS}" exec statefulset/postgresql -- \
    sh -ec 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -Atc "SELECT 1"'
)"
test "${database_probe}" = "1"

printf 'frontend_bundle=%s\n' "${frontend_bundle}"
