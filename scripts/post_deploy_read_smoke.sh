#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/env.sh"
source "${SCRIPT_DIR}/lib/auth.sh"

API_BASE_URL="${API_BASE_URL:-}"
AUTH_EMAIL="${AUTH_EMAIL:-}"
AUTH_PASSWORD="${AUTH_PASSWORD:-}"
AUTH_COOKIE_JAR_OUT="${AUTH_COOKIE_JAR_OUT:-}"
READ_CONNECT_TIMEOUT_SECONDS="${READ_CONNECT_TIMEOUT_SECONDS:-5}"
READ_TIMEOUT_SECONDS="${READ_TIMEOUT_SECONDS:-20}"
COOKIE_JAR="$(mktemp)"
RESPONSE_DIR="$(mktemp -d)"
trap 'rm -f "${COOKIE_JAR}"; rm -rf "${RESPONSE_DIR}"' EXIT

for variable in \
  API_BASE_URL \
  AUTH_EMAIL \
  AUTH_PASSWORD; do
  require_env "${variable}"
done

for timeout_value in "${READ_CONNECT_TIMEOUT_SECONDS}" "${READ_TIMEOUT_SECONDS}"; do
  if ! [[ "${timeout_value}" =~ ^[1-9][0-9]*$ ]]; then
    echo "read smoke timeout must be a positive integer" >&2
    exit 1
  fi
done

if [ -n "${AUTH_COOKIE_JAR_OUT}" ] && [[ "${AUTH_COOKIE_JAR_OUT}" != /* ]]; then
  echo "AUTH_COOKIE_JAR_OUT must be an absolute path" >&2
  exit 1
fi

echo "==> post-deploy operator login"
login_with_password "${API_BASE_URL}" "${COOKIE_JAR}"

probe_index=0

probe_read() {
  local label="$1"
  local path="$2"
  local expected_type="$3"
  local expected_key="$4"
  local output_file
  local elapsed

  probe_index=$((probe_index + 1))
  output_file="${RESPONSE_DIR}/$(printf '%02d' "${probe_index}").json"
  elapsed="$(curl --fail --silent --show-error \
    --connect-timeout "${READ_CONNECT_TIMEOUT_SECONDS}" \
    --max-time "${READ_TIMEOUT_SECONDS}" \
    --cookie "${COOKIE_JAR}" \
    --output "${output_file}" \
    --write-out '%{time_total}' \
    "${API_BASE_URL}${path}")"

  python3 - "${output_file}" "${label}" "${expected_type}" "${expected_key}" "${elapsed}" <<'PY'
import json
import sys

path, label, expected_type, expected_key, elapsed = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    document = json.load(handle)
if expected_type == "object":
    if not isinstance(document, dict) or expected_key not in document:
        raise SystemExit(f"{label} response contract is invalid")
elif expected_type == "array":
    if not isinstance(document, list):
        raise SystemExit(f"{label} response contract is invalid")
else:
    raise SystemExit(f"{label} smoke contract type is invalid")
try:
    measured = float(elapsed)
except ValueError as exc:
    raise SystemExit(f"{label} latency measurement is invalid") from exc
if measured < 0:
    raise SystemExit(f"{label} latency measurement is invalid")
PY
  printf 'read smoke passed: %s latency=%ss\n' "${label}" "${elapsed}"
}

echo "==> post-deploy operational surface reads"
probe_read "session" "/auth/session" object user_id
probe_read "bootstrap diagnostics" "/diagnostics" object observed_at
probe_read "version check" "/version-check" object current_version
probe_read "clusters" "/clusters?limit=100" object clusters
probe_read "resources" "/resources?limit=1" object items
probe_read "issues" "/dashboard/rca/issues?contract_version=2&limit=1" object items
probe_read "applications" "/applications?limit=1" object applications
probe_read "timeline" "/timeline/capabilities" object selected_source_mode
probe_read "traffic" "/traffic/flows?limit=1" object scope_coverage
probe_read "traffic sources" "/traffic/sources" object clusters
probe_read "helm" "/helm/releases" object releases
probe_read "gitops" "/gitops/overview?limit=1" object items
probe_read \
  "activity overview" \
  "/activity/overview?from=1784419200000&to=1784422800000&bucket=300000" \
  object \
  buckets
probe_read "checks" "/checks/overview" object scope_coverage
probe_read "cost overview" "/cost/overview?range=6h" object scope_coverage
probe_read "cost nodes" "/cost/nodes?limit=1" object items
probe_read "alerts" "/alert-events?limit=1" array ignored

if [ -n "${AUTH_COOKIE_JAR_OUT}" ]; then
  rm -f -- "${AUTH_COOKIE_JAR_OUT}"
  install -m 600 -- "${COOKIE_JAR}" "${AUTH_COOKIE_JAR_OUT}"
fi

echo "post-deploy read smoke passed"
