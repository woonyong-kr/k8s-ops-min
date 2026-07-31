#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-}"
API_BASE_URL="${API_BASE_URL:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${SCRIPT_DIR}/lib/env.sh"

default_github_repo() {
  local url
  url="$(git -C "${ROOT_DIR}" config --get remote.origin.url 2>/dev/null || true)"
  url="${url%.git}"
  case "${url}" in
    git@github.com:*) echo "${url#git@github.com:}" ;;
    https://github.com/*) echo "${url#https://github.com/}" ;;
    http://github.com/*) echo "${url#http://github.com/}" ;;
  esac
}

GITHUB_WEBHOOK_SECRET="${GITHUB_WEBHOOK_SECRET:-}"
MGMT_CONTEXT="${MGMT_CONTEXT:-}"
MGMT_NS="${MGMT_NS:-management}"
SMOKE_IMAGE="${SMOKE_IMAGE:-}"
SMOKE_COMMAND_RESOURCE="${SMOKE_COMMAND_RESOURCE:-}"
GITHUB_REPO="${GITHUB_REPO:-$(default_github_repo)}"
GITHUB_BRANCH="${GITHUB_BRANCH:-dev}"
MANIFEST_PATH="${MANIFEST_PATH:-}"
GITHUB_API_BASE="${GITHUB_API_BASE:-https://api.github.com}"
GITHUB_TOKEN="${GITHUB_TOKEN:-}"
SMOKE_COMMIT_SHA="${SMOKE_COMMIT_SHA:-}"
AUTH_EMAIL="${AUTH_EMAIL:-}"
AUTH_PASSWORD="${AUTH_PASSWORD:-}"
SMOKE_CLUSTER_ID="${SMOKE_CLUSTER_ID:-${TARGET_CLUSTER_ID:-}}"
SMOKE_RCA_CORRELATION_ID="${SMOKE_RCA_CORRELATION_ID:-}"
SMOKE_RCA_INCIDENT_ID="${SMOKE_RCA_INCIDENT_ID:-}"
SMOKE_GATEWAY_ATTEMPTS="${SMOKE_GATEWAY_ATTEMPTS:-60}"
SMOKE_GATEWAY_INTERVAL_SECONDS="${SMOKE_GATEWAY_INTERVAL_SECONDS:-5}"
COOKIE_JAR="$(mktemp)"
WEBHOOK_RESPONSE="$(mktemp)"
trap 'rm -f "${COOKIE_JAR}" "${WEBHOOK_RESPONSE}"' EXIT

source "${SCRIPT_DIR}/lib/auth.sh"

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

need curl
need python3
require_env BASE_URL
require_env AUTH_EMAIL
require_env AUTH_PASSWORD
require_env SMOKE_CLUSTER_ID
require_env SMOKE_RCA_CORRELATION_ID
require_env SMOKE_RCA_INCIDENT_ID

normalize_url() {
  local value="${1%/}"
  printf '%s\n' "${value}"
}

if [ -n "${API_BASE_URL}" ]; then
  API_BASE_URL="$(normalize_url "${API_BASE_URL}")"
fi

api_base_candidates() {
  local base
  base="$(normalize_url "${BASE_URL}")"
  if [ -n "${API_BASE_URL}" ]; then
    printf '%s\n' "${API_BASE_URL}"
    return
  fi
  printf '%s/api\n%s\n' "${base}" "${base}"
}

wait_for_gateway() {
  local attempt
  local candidate
  local candidates
  local output=""
  for attempt in $(seq 1 "${SMOKE_GATEWAY_ATTEMPTS}"); do
    while IFS= read -r candidate; do
      if output="$(curl -fsS "${candidate}/healthz" 2>&1)" \
        && printf '%s' "${output}" | grep -q '"service":"api-gateway"'; then
        API_BASE_URL="${candidate}"
        printf '%s\n' "${output}"
        return 0
      fi
    done < <(api_base_candidates)
    if [ "${attempt}" != "${SMOKE_GATEWAY_ATTEMPTS}" ]; then
      sleep "${SMOKE_GATEWAY_INTERVAL_SECONDS}"
    fi
  done
  candidates="$(api_base_candidates | tr '\n' ' ')"
  echo "gateway did not become reachable at candidates: ${candidates}" >&2
  printf '%s\n' "${output}" >&2
  return 1
}

load_management_config_value() {
  local key="$1"
  kubectl --context "${MGMT_CONTEXT}" -n "${MGMT_NS}" \
    get configmap management-runtime-config -o "jsonpath={.data.${key}}" 2>/dev/null || true
}

load_management_secret_value() {
  local key="$1"
  kubectl --context "${MGMT_CONTEXT}" -n "${MGMT_NS}" \
    get secret management-runtime-secret -o "jsonpath={.data.${key}}" 2>/dev/null \
    | python3 -c 'import base64, sys; data=sys.stdin.read().strip(); print(base64.b64decode(data).decode() if data else "")'
}

latest_commit_sha() {
  if [ -z "${GITHUB_REPO}" ]; then
    echo "GITHUB_REPO is required when the current git remote is not a GitHub repository." >&2
    exit 1
  fi
  local header_args=()
  if [ -n "${GITHUB_TOKEN}" ]; then
    header_args=(-H "authorization: Bearer ${GITHUB_TOKEN}")
  fi
  local response
  if [ "${#header_args[@]}" -gt 0 ]; then
    response="$(curl -fsS "${header_args[@]}" \
      "${GITHUB_API_BASE%/}/repos/${GITHUB_REPO}/commits?per_page=1&sha=${GITHUB_BRANCH}")" \
      || return 1
  else
    response="$(curl -fsS \
      "${GITHUB_API_BASE%/}/repos/${GITHUB_REPO}/commits?per_page=1&sha=${GITHUB_BRANCH}")" \
      || return 1
  fi
  GITHUB_COMMITS_JSON="${response}" python3 - <<'PY'
import json
import os

commits = json.loads(os.environ["GITHUB_COMMITS_JSON"])
if not commits:
    raise SystemExit("GitHub returned no commits for the configured smoke repo/branch")
print(commits[0]["sha"])
PY
}

local_commit_sha() {
  git -C "${ROOT_DIR}" rev-parse HEAD 2>/dev/null || true
}

load_webhook_secret() {
  kubectl --context "${MGMT_CONTEXT}" -n "${MGMT_NS}" \
    get secret management-runtime-secret -o jsonpath='{.data.GITHUB_WEBHOOK_SECRET}' \
    | python3 -c 'import base64, sys; print(base64.b64decode(sys.stdin.read()).decode())'
}

if [ -z "${GITHUB_WEBHOOK_SECRET}" ] || [ -z "${GITHUB_TOKEN}" ] \
  || [ -z "${SMOKE_IMAGE}" ] || [ -z "${MANIFEST_PATH}" ]; then
  need kubectl
  require_env MGMT_CONTEXT
fi

if [ -z "${GITHUB_WEBHOOK_SECRET}" ]; then
  GITHUB_WEBHOOK_SECRET="$(load_webhook_secret)"
fi
if [ -z "${GITHUB_TOKEN}" ]; then
  GITHUB_TOKEN="$(load_management_secret_value GITHUB_TOKEN)"
fi
if [ -z "${SMOKE_IMAGE}" ]; then
  SMOKE_IMAGE="$(load_management_config_value GITOPS_WEBHOOK_IMAGE)"
fi
if [ -z "${SMOKE_IMAGE}" ]; then
  SMOKE_IMAGE="${IMAGE_NAME:-kubeheal:local}"
fi
if [ -z "${MANIFEST_PATH}" ]; then
  MANIFEST_PATH="$(load_management_config_value MANIFEST_PATH)"
fi
if [ -z "${MANIFEST_PATH}" ]; then
  MANIFEST_PATH="src/samples/smoke/deploy.yaml"
fi

sign_body() {
  BODY="$1" WEBHOOK_SECRET="${GITHUB_WEBHOOK_SECRET}" python3 - <<'PY'
import hashlib
import hmac
import os

print(
    "sha256="
    + hmac.new(
        os.environ["WEBHOOK_SECRET"].encode(),
        os.environ["BODY"].encode(),
        hashlib.sha256,
    ).hexdigest()
)
PY
}

echo "==> checking gateway"
wait_for_gateway

echo "==> logging in operator"
login_with_password "${API_BASE_URL}" "${COOKIE_JAR}"

if [ -z "${SMOKE_COMMIT_SHA}" ]; then
  echo "==> resolving latest Git commit for ${GITHUB_REPO}@${GITHUB_BRANCH}"
  if ! SMOKE_COMMIT_SHA="$(latest_commit_sha)"; then
    SMOKE_COMMIT_SHA="$(local_commit_sha)"
    if [ -z "${SMOKE_COMMIT_SHA}" ]; then
      echo "failed to resolve SMOKE_COMMIT_SHA from GitHub or local git" >&2
      exit 1
    fi
    echo "    GitHub lookup unavailable; using local HEAD ${SMOKE_COMMIT_SHA}"
  fi
fi

echo "==> sending signed GitHub webhook"
webhook_body="$(
  SMOKE_IMAGE="${SMOKE_IMAGE}" \
  SMOKE_COMMIT_SHA="${SMOKE_COMMIT_SHA}" \
  GITHUB_REPO="${GITHUB_REPO}" \
  GITHUB_BRANCH="${GITHUB_BRANCH}" \
  MANIFEST_PATH="${MANIFEST_PATH}" \
  SMOKE_CLUSTER_ID="${SMOKE_CLUSTER_ID}" \
  python3 - <<'PY'
import json
import os

print(
    json.dumps(
        {
            "commit_sha": os.environ["SMOKE_COMMIT_SHA"],
            "image": os.environ["SMOKE_IMAGE"],
            "replicas": 2,
            "repo_ref": os.environ["GITHUB_REPO"],
            "branch": os.environ["GITHUB_BRANCH"],
            "manifest_path": os.environ["MANIFEST_PATH"],
            "cluster_id": os.environ["SMOKE_CLUSTER_ID"],
            "force": True,
        }
    )
)
PY
)"
signature="$(sign_body "${webhook_body}")"
curl -fsS -X POST "${API_BASE_URL}/github/webhook" \
  -H "content-type: application/json" \
  -H "x-hub-signature-256: ${signature}" \
  -d "${webhook_body}" | tee "${WEBHOOK_RESPONSE}"
echo
webhook_correlation_id="$(WEBHOOK_RESPONSE="${WEBHOOK_RESPONSE}" python3 - <<'PY'
import json
import os

with open(os.environ["WEBHOOK_RESPONSE"], encoding="utf-8") as handle:
    print(json.load(handle)["correlation_id"])
PY
)"

if [ -n "${SMOKE_COMMAND_RESOURCE}" ]; then
  echo "==> sending manual UI command"
  SMOKE_COMMAND_RESOURCE="${SMOKE_COMMAND_RESOURCE}" SMOKE_CLUSTER_ID="${SMOKE_CLUSTER_ID}" python3 - <<'PY' > "${WEBHOOK_RESPONSE}.command.json"
import json
import os

print(json.dumps({
    "cluster_id": os.environ["SMOKE_CLUSTER_ID"],
    "action": "rollout_restart",
    "namespace": "sandbox",
    "reason": "manual smoke command",
    "diff": {
        "resource": os.environ["SMOKE_COMMAND_RESOURCE"],
        "namespace": "sandbox",
        "desired_image": "",
        "actual_image": "",
        "risk": "sandbox-only",
    },
}))
PY
  curl -fsS -X POST "${API_BASE_URL}/commands" \
    -b "${COOKIE_JAR}" \
    -H "content-type: application/json" \
    -H "x-service-csrf: same-origin" \
    -d @"${WEBHOOK_RESPONSE}.command.json"
  echo
fi

postgres_query() {
  local sql="$1"
  kubectl --context "${MGMT_CONTEXT}" -n "${MGMT_NS}" exec statefulset/postgresql -- \
    sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -tAc \"$sql\""
}

echo "==> waiting for async workers"
required_subjects_csv="git.webhook.received,git.changed,manifest.rendered,desired.diff.detected,diff.analyzed"
subject_count=0
for _ in $(seq 1 36); do
  subject_count="$(
    postgres_query \
      "select count(distinct subject) from events where correlation_id='${webhook_correlation_id}' and subject = any(string_to_array('${required_subjects_csv}', ','));" \
      2>/dev/null \
      | tr -d '[:space:]' || true
  )"
  if [ "${subject_count}" = "5" ]; then
    break
  fi
  sleep 2
done

if [ "${subject_count}" != "5" ]; then
  echo "smoke correlation ${webhook_correlation_id} did not reach all required subjects" >&2
  postgres_query \
    "select subject from events where correlation_id='${webhook_correlation_id}' order by created_at;" >&2 || true
  exit 1
fi

echo "==> checking strict read APIs"
python3 "${SCRIPT_DIR}/strict_api_smoke.py" \
  --base-url "${API_BASE_URL}" \
  --cookie-jar "${COOKIE_JAR}" \
  --correlation-id "${SMOKE_RCA_CORRELATION_ID}" \
  --incident-id "${SMOKE_RCA_INCIDENT_ID}"

echo "Smoke test passed."
