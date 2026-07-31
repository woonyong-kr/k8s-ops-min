#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT_DIR}/scripts/lib/env.sh"
source "${ROOT_DIR}/scripts/lib/auth.sh"

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

PROJECT_SLUG="${PROJECT_SLUG:-kubeheal}"
LOCAL_IMAGE_NAME="${LOCAL_IMAGE_NAME:-${PROJECT_SLUG}:local}"
IMAGE_NAME="${IMAGE_NAME:-${LOCAL_IMAGE_NAME}}"
MGMT_CLUSTER="${MGMT_CLUSTER:-management}"
TARGET_CLUSTER="${TARGET_CLUSTER:-target}"
POSTGRES_USER="${POSTGRES_USER:-service}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
POSTGRES_DB="${POSTGRES_DB:-service}"
DATABASE_URL="${DATABASE_URL:-}"
DATABASE_STARTUP_MODE="${DATABASE_STARTUP_MODE:-verify}"
NATS_URL="${NATS_URL:-nats://nats:4222}"
REDIS_URL="${REDIS_URL:-redis://redis:6379/0}"
GITHUB_WEBHOOK_SECRET="${GITHUB_WEBHOOK_SECRET:-}"
FILTER_CURSOR_SIGNING_KEY="${FILTER_CURSOR_SIGNING_KEY:-}"
RCA_TEST_RUNS_ENABLED="${RCA_TEST_RUNS_ENABLED:-1}"
RCA_TEST_RUNS_TOKEN="${RCA_TEST_RUNS_TOKEN:-}"
TEST_FIXTURE_PURGE_ENABLED="${TEST_FIXTURE_PURGE_ENABLED:-1}"
API_ROOT_PATH="${API_ROOT_PATH:-/api}"
GITHUB_REPO="${GITHUB_REPO:-$(default_github_repo)}"
GITHUB_BRANCH="${GITHUB_BRANCH:-dev}"
MANIFEST_PATH="${MANIFEST_PATH:-src/samples/smoke/deploy.yaml}"
GITHUB_TOKEN="${GITHUB_TOKEN:-}"
GITHUB_API_BASE="${GITHUB_API_BASE:-https://api.github.com}"
GIT_MANIFEST_SOURCE_MODE="${GIT_MANIFEST_SOURCE_MODE:-auto}"
GIT_LOCAL_MANIFEST_ENABLED="${GIT_LOCAL_MANIFEST_ENABLED:-1}"
GIT_CHECKOUT_CACHE_ENABLED="${GIT_CHECKOUT_CACHE_ENABLED:-0}"
GIT_CHECKOUT_CACHE_REQUIRED="${GIT_CHECKOUT_CACHE_REQUIRED:-0}"
GIT_CACHE_MAX_REPOS="${GIT_CACHE_MAX_REPOS:-8}"
GIT_CACHE_MAX_BYTES="${GIT_CACHE_MAX_BYTES:-1073741824}"
GIT_REMOTE_MANIFEST_ENABLED="${GIT_REMOTE_MANIFEST_ENABLED:-1}"
GIT_REMOTE_MANIFEST_REQUIRED="${GIT_REMOTE_MANIFEST_REQUIRED:-0}"
GITOPS_REQUIRE_APPROVED_SNAPSHOT="${GITOPS_REQUIRE_APPROVED_SNAPSHOT:-1}"
GITHUB_MANIFEST_TIMEOUT_SECONDS="${GITHUB_MANIFEST_TIMEOUT_SECONDS:-5}"
COMMAND_JANITOR_INTERVAL_SECONDS="${COMMAND_JANITOR_INTERVAL_SECONDS:-15}"
WORKER_IDLE_SLEEP_SECONDS="${WORKER_IDLE_SLEEP_SECONDS:-1.0}"
MAIL_DELIVERY_MODE="${MAIL_DELIVERY_MODE:-log}"
SMTP_HOST="${SMTP_HOST:-}"
SMTP_PORT="${SMTP_PORT:-587}"
SMTP_USERNAME="${SMTP_USERNAME:-}"
SMTP_PASSWORD="${SMTP_PASSWORD:-}"
SMTP_FROM="${SMTP_FROM:-}"
SMTP_STARTTLS="${SMTP_STARTTLS:-1}"
SMTP_TIMEOUT_SECONDS="${SMTP_TIMEOUT_SECONDS:-10}"
SCM_PROVIDER="${SCM_PROVIDER:-github}"
SCM_REPO="${SCM_REPO:-${GITHUB_REPO}}"
SCM_BASE_BRANCH="${SCM_BASE_BRANCH:-${GITHUB_BRANCH}}"
MINIO_ROOT_USER="${MINIO_ROOT_USER:-minioadmin}"
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-}"
TARGET_RUNTIME_CLUSTER_ID="${TARGET_RUNTIME_CLUSTER_ID:-${TARGET_CLUSTER}}"
EVIDENCE_INTERVAL_SECONDS="${EVIDENCE_INTERVAL_SECONDS:-30}"
SEED_DEMO_WORKSPACE="${SEED_DEMO_WORKSPACE:-1}"
AUTO_CONNECT_PROMETHEUS="${AUTO_CONNECT_PROMETHEUS:-1}"
LOCAL_PROMETHEUS_URL="${LOCAL_PROMETHEUS_URL:-http://prometheus.target.svc.cluster.local:9090}"
UP_WORKER_SET="${UP_WORKER_SET:-smoke}"
ENABLE_GITHUB_POLL_WORKER="${ENABLE_GITHUB_POLL_WORKER:-${ENABLE_GITHUB_POLL_CRON:-0}}"
SKIP_BUILD="${SKIP_BUILD:-0}"
LLM_PROVIDER="${LLM_PROVIDER:-}"
LLM_MODEL="${LLM_MODEL:-}"
LLM_BASE_URL="${LLM_BASE_URL:-}"
LLM_TIMEOUT_SECONDS="${LLM_TIMEOUT_SECONDS:-30}"
LLM_MAX_RETRIES="${LLM_MAX_RETRIES:-2}"
LLM_MAX_TOKENS="${LLM_MAX_TOKENS:-1024}"
LLM_API_KEY="${LLM_API_KEY:-}"
OPENAI_API_KEY="${OPENAI_API_KEY:-}"
OPENAI_BASE_URL="${OPENAI_BASE_URL:-}"
OPENAI_MODEL="${OPENAI_MODEL:-}"
OPENAI_COMPATIBLE_API_KEY="${OPENAI_COMPATIBLE_API_KEY:-}"
OPENAI_COMPATIBLE_BASE_URL="${OPENAI_COMPATIBLE_BASE_URL:-}"
OPENAI_COMPATIBLE_MODEL="${OPENAI_COMPATIBLE_MODEL:-}"
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"
ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-}"
ANTHROPIC_MODEL="${ANTHROPIC_MODEL:-}"
ANTHROPIC_VERSION="${ANTHROPIC_VERSION:-2023-06-01}"
GEMINI_API_KEY="${GEMINI_API_KEY:-}"
GOOGLE_API_KEY="${GOOGLE_API_KEY:-}"
GEMINI_BASE_URL="${GEMINI_BASE_URL:-}"
GEMINI_MODEL="${GEMINI_MODEL:-}"
AUTH_EMAIL="${AUTH_EMAIL:-admin}"
AUTH_PASSWORD="${AUTH_PASSWORD:-}"
PRINT_GENERATED_ADMIN_PASSWORD="${PRINT_GENERATED_ADMIN_PASSWORD:-0}"

APP_WORKER_DEPLOYMENTS=(
  ai-chat-worker
  git-pull-worker
  manifest-render-worker
  diff-worker
  diff-analyze-worker
  safe-pr-worker
  scm-worker
  workflow-controller
  release-flow-worker
  alert-worker
  mail-worker
  command-worker
  command-janitor
  outbox-relay
  rca-timeline-janitor
  target-reconcile-worker
  rca-worker
  rca-feedback-worker
  evidence-worker
  incident-worker
  plan-worker
  analyze-worker
  recovery-worker
  select-worker
  dispatch-worker
  backlog-worker
  ai-diff-worker
  rollout-worker
  approval-worker
  audit-worker
  change-correlation-worker
  dashboard-worker
  realtime-gateway
  dead-letter-monitor
)

SMOKE_WORKER_DEPLOYMENTS=(
  git-pull-worker
  manifest-render-worker
  diff-worker
  diff-analyze-worker
  safe-pr-worker
  workflow-controller
  release-flow-worker
  outbox-relay
  audit-worker
  change-correlation-worker
  dashboard-worker
  dead-letter-monitor
)

RCA_WORKER_DEPLOYMENTS=(
  git-pull-worker
  manifest-render-worker
  diff-worker
  diff-analyze-worker
  safe-pr-worker
  scm-worker
  workflow-controller
  release-flow-worker
  alert-worker
  command-worker
  command-janitor
  outbox-relay
  rca-timeline-janitor
  evidence-worker
  incident-worker
  plan-worker
  analyze-worker
  rca-worker
  rca-feedback-worker
  recovery-worker
  select-worker
  dispatch-worker
  backlog-worker
  audit-worker
  change-correlation-worker
  dashboard-worker
  dead-letter-monitor
)

case "${UP_WORKER_SET}" in
  smoke)
    WORKER_DEPLOYMENTS_TO_START=("${SMOKE_WORKER_DEPLOYMENTS[@]}")
    ;;
  rca)
    WORKER_DEPLOYMENTS_TO_START=("${RCA_WORKER_DEPLOYMENTS[@]}")
    ;;
  full)
    WORKER_DEPLOYMENTS_TO_START=("${APP_WORKER_DEPLOYMENTS[@]}")
    ;;
  *)
    echo "UP_WORKER_SET must be one of: smoke, rca, full" >&2
    exit 1
    ;;
esac

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

need docker
need kind
need kubectl
need curl
need openssl
need python3
require_env AUTH_EMAIL
if [[ -z "${AUTH_PASSWORD}" ]]; then
  AUTH_PASSWORD="$(generate_password)"
  GENERATED_AUTH_PASSWORD="1"
else
  GENERATED_AUTH_PASSWORD="0"
fi

kubectl_retry() {
  local attempt
  for attempt in 1 2 3 4 5; do
    if kubectl "$@"; then
      return 0
    fi
    echo "kubectl retry ${attempt}/5: $*" >&2
    sleep "$((attempt * 3))"
  done
  kubectl "$@"
}

quiesce_existing_management_apps() {
  echo "==> quiescing existing management app workloads"
  kubectl --context "kind-${MGMT_CLUSTER}" -n management scale deploy/github-poll-worker \
    --replicas=0 >/dev/null 2>&1 || true

  for job in $(
    kubectl --context "kind-${MGMT_CLUSTER}" -n management get job -o name 2>/dev/null \
      | grep '^job.batch/github-poll-worker-' || true
  ); do
    kubectl --context "kind-${MGMT_CLUSTER}" -n management delete "${job}" \
      --cascade=background --wait=false >/dev/null 2>&1 || true
  done

  for pod in $(
    kubectl --context "kind-${MGMT_CLUSTER}" -n management get pod -o name 2>/dev/null \
      | grep '^pod/github-poll-worker-' || true
  ); do
    kubectl --context "kind-${MGMT_CLUSTER}" -n management delete "${pod}" \
      --wait=false >/dev/null 2>&1 || true
  done

  for deploy in "${APP_WORKER_DEPLOYMENTS[@]}"; do
    kubectl --context "kind-${MGMT_CLUSTER}" -n management scale "deploy/${deploy}" \
      --replicas=0 >/dev/null 2>&1 || true
    kubectl --context "kind-${MGMT_CLUSTER}" -n management delete pod \
      -l "app=${deploy}" --wait=false >/dev/null 2>&1 || true
  done
}

wait_management_pod_ready() {
  local app_name="$1"
  local timeout="${2:-300s}"
  local pod_name
  for _ in $(seq 1 60); do
    pod_name="$(
      kubectl --context "kind-${MGMT_CLUSTER}" -n management get pod \
        -l "app=${app_name}" \
        -o name 2>/dev/null | head -1
    )"
    if [ -n "${pod_name}" ]; then
      break
    fi
    sleep 2
  done
  kubectl_retry --context "kind-${MGMT_CLUSTER}" -n management wait \
    --for=condition=ready pod \
    -l "app=${app_name}" \
    --timeout="${timeout}"
}

valid_github_token() {
  local token="$1"
  [[ -n "${token}" ]] || return 1
  [[ "${token}" != *"<"* && "${token}" != *">"* ]] || return 1
  [[ "${token}" != *PLACEHOLDER* && "${token}" != *TOKEN_HERE* ]] || return 1
  LC_ALL=C grep -q '^[[:print:]]\+$' <<<"${token}"
}

if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running." >&2
  exit 1
fi

RUNTIME_DIR="$(mktemp -d "${ROOT_DIR}/.up.XXXXXX")"
cleanup() {
  rm -rf "${RUNTIME_DIR}"
}
trap cleanup EXIT

pgbouncer_auth_hash() {
  python3 - "$POSTGRES_USER" "$POSTGRES_PASSWORD" <<'PY'
import hashlib
import sys

user, password = sys.argv[1], sys.argv[2]
print("md5" + hashlib.md5((password + user).encode()).hexdigest())
PY
}

existing_secret_value() {
  local secret_name="$1"
  local key="$2"
  { kubectl --context "kind-${MGMT_CLUSTER}" -n management get secret "${secret_name}" \
    -o "jsonpath={.data.${key}}" 2>/dev/null || true; } \
    | python3 -c 'import base64, sys; data=sys.stdin.read().strip(); print(base64.b64decode(data).decode() if data else "")'
}

image_repo_and_tag() {
  local image="$1"
  local last_segment="${image##*/}"
  if [[ "${last_segment}" == *:* ]]; then
    printf '%s\n%s\n' "${image%:*}" "${image##*:}"
  else
    printf '%s\n%s\n' "${image}" "latest"
  fi
}

local_admin_user_id() {
  python3 - "${PROJECT_SLUG}" "${AUTH_EMAIL}" <<'PY'
import sys
import uuid

project_slug, email = sys.argv[1], sys.argv[2].strip().lower()
print("user-" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"{project_slug}:{email}")))
PY
}

seed_local_demo_workspace() {
  if [ "${SEED_DEMO_WORKSPACE}" != "1" ]; then
    echo "==> skipping demo workspace seed (SEED_DEMO_WORKSPACE=${SEED_DEMO_WORKSPACE})"
    return
  fi

  local owner_user_id
  local image_manifest="${RUNTIME_DIR}/demo-workspace-seed-image.yaml"
  local runtime_manifest="${RUNTIME_DIR}/demo-workspace-seed-job.yaml"
  owner_user_id="$(local_admin_user_id)"

  echo "==> seeding the complete local UI demo workspace"
  kubectl_retry --context "kind-${MGMT_CLUSTER}" -n management delete \
    job/management-demo-workspace-seed --ignore-not-found --wait=true
  kubectl set image \
    --filename "${ROOT_DIR}/deploy/management/demo-workspace-seed-job.yaml" \
    seed="${IMAGE_NAME}" \
    --local \
    --output yaml >"${image_manifest}"
  kubectl set env \
    --filename "${image_manifest}" \
    DEMO_WORKSPACE_OWNER_USER_ID="${owner_user_id}" \
    --local \
    --output yaml >"${runtime_manifest}"
  kubectl --context "kind-${MGMT_CLUSTER}" apply --filename "${runtime_manifest}"
  if ! kubectl_retry --context "kind-${MGMT_CLUSTER}" -n management wait \
    --for=condition=complete job/management-demo-workspace-seed --timeout=330s; then
    kubectl --context "kind-${MGMT_CLUSTER}" -n management \
      describe job/management-demo-workspace-seed || true
    kubectl --context "kind-${MGMT_CLUSTER}" -n management \
      logs job/management-demo-workspace-seed --all-containers --tail=300 || true
    return 1
  fi
  kubectl --context "kind-${MGMT_CLUSTER}" -n management \
    logs job/management-demo-workspace-seed --all-containers --tail=20
}

configure_local_prometheus() {
  if [ "${AUTO_CONNECT_PROMETHEUS}" != "1" ]; then
    echo "==> skipping Prometheus connection (AUTO_CONNECT_PROMETHEUS=${AUTO_CONNECT_PROMETHEUS})"
    return
  fi

  local base_url="${BASE_URL:-http://localhost:${GATEWAY_PORT}}"
  local api_base="${base_url%/}${API_ROOT_PATH}"
  local cookie_jar="${RUNTIME_DIR}/prometheus-auth-cookie.txt"
  local request_body="${RUNTIME_DIR}/prometheus-integration.json"
  local response=""
  local state=""
  local attempt

  echo "==> connecting Prometheus to the target cluster agent"
  login_with_password "${api_base}" "${cookie_jar}"
  LOCAL_PROMETHEUS_URL="${LOCAL_PROMETHEUS_URL}" \
  TARGET_RUNTIME_CLUSTER_ID="${TARGET_RUNTIME_CLUSTER_ID}" \
    python3 - <<'PY' >"${request_body}"
import json
import os

print(json.dumps({
    "cluster_id": os.environ["TARGET_RUNTIME_CLUSTER_ID"],
    "prometheus_url": os.environ["LOCAL_PROMETHEUS_URL"],
    "headers": {},
}))
PY
  curl -fsS -X PUT "${api_base}/integrations/prometheus" \
    -b "${cookie_jar}" \
    -H "content-type: application/json" \
    -H "x-service-csrf: same-origin" \
    --data-binary @"${request_body}" >/dev/null

  for attempt in $(seq 1 45); do
    response="$(curl -fsS \
      -b "${cookie_jar}" \
      "${api_base}/integrations/prometheus?cluster_id=${TARGET_RUNTIME_CLUSTER_ID}")"
    state="$(PROMETHEUS_STATUS="${response}" python3 - <<'PY'
import json
import os

print(json.loads(os.environ["PROMETHEUS_STATUS"]).get("state", ""))
PY
)"
    if [ "${state}" = "connected" ]; then
      echo "==> Prometheus connected: ${LOCAL_PROMETHEUS_URL}"
      return
    fi
    if [ "${state}" = "failed" ]; then
      echo "Prometheus connection failed: ${response}" >&2
      return 1
    fi
    sleep 2
  done

  echo "Prometheus connection did not reach connected state: ${response}" >&2
  return 1
}

if [ -z "${POSTGRES_PASSWORD}" ]; then
  POSTGRES_PASSWORD="$(existing_secret_value postgresql-secret POSTGRES_PASSWORD)"
fi
if [ -z "${POSTGRES_PASSWORD}" ]; then
  POSTGRES_PASSWORD="$(openssl rand -hex 24)"
fi

if [ -z "${DATABASE_URL}" ]; then
  DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@pgbouncer:6432/${POSTGRES_DB}"
fi

if [ -z "${GITHUB_WEBHOOK_SECRET}" ]; then
  GITHUB_WEBHOOK_SECRET="$(existing_secret_value management-runtime-secret GITHUB_WEBHOOK_SECRET)"
fi
if [ -z "${GITHUB_WEBHOOK_SECRET}" ]; then
  GITHUB_WEBHOOK_SECRET="$(openssl rand -hex 32)"
fi
if [ -z "${FILTER_CURSOR_SIGNING_KEY}" ]; then
  FILTER_CURSOR_SIGNING_KEY="$(existing_secret_value management-runtime-secret FILTER_CURSOR_SIGNING_KEY)"
fi
if [ ${#FILTER_CURSOR_SIGNING_KEY} -lt 32 ]; then
  FILTER_CURSOR_SIGNING_KEY="$(openssl rand -hex 32)"
fi
if [ -z "${RCA_TEST_RUNS_TOKEN}" ]; then
  RCA_TEST_RUNS_TOKEN="$(existing_secret_value management-runtime-secret RCA_TEST_RUNS_TOKEN)"
fi
if [ -z "${RCA_TEST_RUNS_TOKEN}" ]; then
  RCA_TEST_RUNS_TOKEN="$(openssl rand -hex 32)"
fi
if [ -z "${GITHUB_TOKEN}" ]; then
  GITHUB_TOKEN="$(existing_secret_value management-runtime-secret GITHUB_TOKEN)"
fi
for key in \
  LLM_API_KEY \
  OPENAI_API_KEY \
  OPENAI_COMPATIBLE_API_KEY \
  ANTHROPIC_API_KEY \
  GEMINI_API_KEY \
  GOOGLE_API_KEY; do
  if [ -z "${!key}" ]; then
    printf -v "${key}" "%s" "$(existing_secret_value management-runtime-secret "${key}")"
  fi
done

if [ -z "${MINIO_ROOT_PASSWORD}" ]; then
  MINIO_ROOT_PASSWORD="$(existing_secret_value minio-secret MINIO_ROOT_PASSWORD)"
fi
if [ -z "${MINIO_ROOT_PASSWORD}" ]; then
  MINIO_ROOT_PASSWORD="$(openssl rand -hex 32)"
fi

if [ "${SKIP_BUILD}" = "1" ]; then
  echo "==> skipping image build for ${IMAGE_NAME}"
else
  echo "==> building ${IMAGE_NAME}"
  docker build -f "${ROOT_DIR}/src/services/Dockerfile" -t "${IMAGE_NAME}" "${ROOT_DIR}"
fi

if ! kind get clusters | grep -qx "${MGMT_CLUSTER}"; then
  echo "==> creating management cluster: ${MGMT_CLUSTER}"
  kind create cluster --name "${MGMT_CLUSTER}" --config "${ROOT_DIR}/deploy/kind/management.yaml"
fi

if ! kind get clusters | grep -qx "${TARGET_CLUSTER}"; then
  echo "==> creating target cluster: ${TARGET_CLUSTER}"
  kind create cluster --name "${TARGET_CLUSTER}" --config "${ROOT_DIR}/deploy/kind/target.yaml"
fi

echo "==> loading image into both clusters"
kind load docker-image "${IMAGE_NAME}" --name "${MGMT_CLUSTER}"
kind load docker-image "${IMAGE_NAME}" --name "${TARGET_CLUSTER}"

echo "==> deploying management plane"
kubectl --context "kind-${MGMT_CLUSTER}" apply -f "${ROOT_DIR}/deploy/management/namespace.yaml"
quiesce_existing_management_apps
kubectl --context "kind-${MGMT_CLUSTER}" -n management create secret generic postgresql-secret \
  --from-literal=POSTGRES_USER="${POSTGRES_USER}" \
  --from-literal=POSTGRES_PASSWORD="${POSTGRES_PASSWORD}" \
  --from-literal=POSTGRES_DB="${POSTGRES_DB}" \
  --dry-run=client -o yaml | kubectl --context "kind-${MGMT_CLUSTER}" apply -f -
kubectl --context "kind-${MGMT_CLUSTER}" -n management create secret generic minio-secret \
  --from-literal=MINIO_ROOT_USER="${MINIO_ROOT_USER}" \
  --from-literal=MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD}" \
  --dry-run=client -o yaml | kubectl --context "kind-${MGMT_CLUSTER}" apply -f -
PGBOUNCER_AUTH_HASH="$(pgbouncer_auth_hash)"
cat >"${RUNTIME_DIR}/pgbouncer.ini" <<EOF
[databases]
${POSTGRES_DB} = host=postgresql port=5432 dbname=${POSTGRES_DB} user=${POSTGRES_USER} password=${POSTGRES_PASSWORD}

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 6432
auth_type = md5
auth_file = /etc/pgbouncer/userlist.txt
pool_mode = transaction
max_client_conn = 1000
default_pool_size = 50
reserve_pool_size = 15
reserve_pool_timeout = 3
server_idle_timeout = 60
pidfile = /tmp/pgbouncer.pid
logfile =
unix_socket_dir = /tmp
ignore_startup_parameters = extra_float_digits,options
EOF
printf '"%s" "%s"\n' "${POSTGRES_USER}" "${PGBOUNCER_AUTH_HASH}" >"${RUNTIME_DIR}/userlist.txt"
kubectl --context "kind-${MGMT_CLUSTER}" -n management create secret generic pgbouncer-config \
  --from-file=pgbouncer.ini="${RUNTIME_DIR}/pgbouncer.ini" \
  --from-file=userlist.txt="${RUNTIME_DIR}/userlist.txt" \
  --dry-run=client -o yaml | kubectl --context "kind-${MGMT_CLUSTER}" apply -f -
kubectl --context "kind-${MGMT_CLUSTER}" -n management create configmap management-runtime-config \
  --from-literal=NATS_URL="${NATS_URL}" \
  --from-literal=REDIS_URL="${REDIS_URL}" \
  --from-literal=DATABASE_STARTUP_MODE="${DATABASE_STARTUP_MODE}" \
  --from-literal=RCA_TEST_RUNS_ENABLED="${RCA_TEST_RUNS_ENABLED}" \
  --from-literal=TEST_FIXTURE_PURGE_ENABLED="${TEST_FIXTURE_PURGE_ENABLED}" \
  --from-literal=API_ROOT_PATH="${API_ROOT_PATH}" \
  --from-literal=MANAGEMENT_CLUSTER_ID="${MGMT_CLUSTER}" \
  --from-literal=MANAGEMENT_BASE_URL="http://api-gateway:8000" \
  --from-literal=GITHUB_REPO="${GITHUB_REPO}" \
  --from-literal=GITHUB_BRANCH="${GITHUB_BRANCH}" \
  --from-literal=MANIFEST_PATH="${MANIFEST_PATH}" \
  --from-literal=GITOPS_WEBHOOK_IMAGE="${IMAGE_NAME}" \
  --from-literal=GITHUB_API_BASE="${GITHUB_API_BASE}" \
  --from-literal=GIT_MANIFEST_SOURCE_MODE="${GIT_MANIFEST_SOURCE_MODE}" \
  --from-literal=GIT_LOCAL_MANIFEST_ENABLED="${GIT_LOCAL_MANIFEST_ENABLED}" \
  --from-literal=GIT_CHECKOUT_CACHE_ENABLED="${GIT_CHECKOUT_CACHE_ENABLED}" \
  --from-literal=GIT_CHECKOUT_CACHE_REQUIRED="${GIT_CHECKOUT_CACHE_REQUIRED}" \
  --from-literal=GIT_CACHE_MAX_REPOS="${GIT_CACHE_MAX_REPOS}" \
  --from-literal=GIT_CACHE_MAX_BYTES="${GIT_CACHE_MAX_BYTES}" \
  --from-literal=GIT_REMOTE_MANIFEST_ENABLED="${GIT_REMOTE_MANIFEST_ENABLED}" \
  --from-literal=GIT_REMOTE_MANIFEST_REQUIRED="${GIT_REMOTE_MANIFEST_REQUIRED}" \
  --from-literal=GITOPS_REQUIRE_APPROVED_SNAPSHOT="${GITOPS_REQUIRE_APPROVED_SNAPSHOT}" \
  --from-literal=GITHUB_MANIFEST_TIMEOUT_SECONDS="${GITHUB_MANIFEST_TIMEOUT_SECONDS}" \
  --from-literal=COMMAND_JANITOR_INTERVAL_SECONDS="${COMMAND_JANITOR_INTERVAL_SECONDS}" \
  --from-literal=WORKER_IDLE_SLEEP_SECONDS="${WORKER_IDLE_SLEEP_SECONDS}" \
  --from-literal=MAIL_DELIVERY_MODE="${MAIL_DELIVERY_MODE}" \
  --from-literal=SMTP_HOST="${SMTP_HOST}" \
  --from-literal=SMTP_PORT="${SMTP_PORT}" \
  --from-literal=SMTP_FROM="${SMTP_FROM}" \
  --from-literal=SMTP_STARTTLS="${SMTP_STARTTLS}" \
  --from-literal=SMTP_TIMEOUT_SECONDS="${SMTP_TIMEOUT_SECONDS}" \
  --from-literal=SCM_PROVIDER="${SCM_PROVIDER}" \
  --from-literal=SCM_REPO="${SCM_REPO}" \
  --from-literal=SCM_BASE_BRANCH="${SCM_BASE_BRANCH}" \
  --from-literal=LLM_PROVIDER="${LLM_PROVIDER}" \
  --from-literal=LLM_MODEL="${LLM_MODEL}" \
  --from-literal=LLM_BASE_URL="${LLM_BASE_URL}" \
  --from-literal=LLM_TIMEOUT_SECONDS="${LLM_TIMEOUT_SECONDS}" \
  --from-literal=LLM_MAX_RETRIES="${LLM_MAX_RETRIES}" \
  --from-literal=LLM_MAX_TOKENS="${LLM_MAX_TOKENS}" \
  --from-literal=OPENAI_BASE_URL="${OPENAI_BASE_URL}" \
  --from-literal=OPENAI_MODEL="${OPENAI_MODEL}" \
  --from-literal=OPENAI_COMPATIBLE_BASE_URL="${OPENAI_COMPATIBLE_BASE_URL}" \
  --from-literal=OPENAI_COMPATIBLE_MODEL="${OPENAI_COMPATIBLE_MODEL}" \
  --from-literal=ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL}" \
  --from-literal=ANTHROPIC_MODEL="${ANTHROPIC_MODEL}" \
  --from-literal=ANTHROPIC_VERSION="${ANTHROPIC_VERSION}" \
  --from-literal=GEMINI_BASE_URL="${GEMINI_BASE_URL}" \
  --from-literal=GEMINI_MODEL="${GEMINI_MODEL}" \
  --dry-run=client -o yaml | kubectl --context "kind-${MGMT_CLUSTER}" apply -f -
SECRET_ARGS=(
  --from-literal=DATABASE_URL="${DATABASE_URL}"
  --from-literal=GITHUB_WEBHOOK_SECRET="${GITHUB_WEBHOOK_SECRET}"
  --from-literal=FILTER_CURSOR_SIGNING_KEY="${FILTER_CURSOR_SIGNING_KEY}"
  --from-literal=RCA_TEST_RUNS_TOKEN="${RCA_TEST_RUNS_TOKEN}"
)
if valid_github_token "${GITHUB_TOKEN}"; then
  SECRET_ARGS+=(--from-literal=GITHUB_TOKEN="${GITHUB_TOKEN}")
fi
for key in \
  LLM_API_KEY \
  OPENAI_API_KEY \
  OPENAI_COMPATIBLE_API_KEY \
  ANTHROPIC_API_KEY \
  GEMINI_API_KEY \
  GOOGLE_API_KEY; do
  if [ -n "${!key}" ]; then
    SECRET_ARGS+=(--from-literal="${key}=${!key}")
  fi
done
for key in SMTP_USERNAME SMTP_PASSWORD; do
  if [ -n "${!key}" ]; then
    SECRET_ARGS+=(--from-literal="${key}=${!key}")
  fi
done
kubectl --context "kind-${MGMT_CLUSTER}" -n management create secret generic management-runtime-secret \
  "${SECRET_ARGS[@]}" \
  --dry-run=client -o yaml | kubectl --context "kind-${MGMT_CLUSTER}" apply -f -
kubectl --context "kind-${MGMT_CLUSTER}" -n management create secret generic management-admin-bootstrap \
  --from-literal=AUTH_EMAIL="${AUTH_EMAIL}" \
  --from-literal=AUTH_PASSWORD="${AUTH_PASSWORD}" \
  --dry-run=client -o yaml | kubectl --context "kind-${MGMT_CLUSTER}" apply -f -
kubectl --context "kind-${MGMT_CLUSTER}" -n management delete \
  deploy/management-api-gateway \
  svc/management-api-gateway \
  deploy/api-gateway \
  svc/api-gateway \
  --ignore-not-found
MANAGEMENT_INFRA_OVERLAY="${RUNTIME_DIR}/management-infra-kustomization"
MANAGEMENT_APP_OVERLAY="${RUNTIME_DIR}/management-app-kustomization"
mkdir -p "${MANAGEMENT_INFRA_OVERLAY}" "${MANAGEMENT_APP_OVERLAY}"
cp \
  "${ROOT_DIR}/deploy/management/namespace.yaml" \
  "${ROOT_DIR}/deploy/management/scheduling.yaml" \
  "${ROOT_DIR}/deploy/management/storage.yaml" \
  "${ROOT_DIR}/deploy/management/pgbouncer.yaml" \
  "${ROOT_DIR}/deploy/management/nats.yaml" \
  "${ROOT_DIR}/deploy/management/rbac.yaml" \
  "${MANAGEMENT_INFRA_OVERLAY}/"
cp \
  "${ROOT_DIR}/deploy/management/services.yaml" \
  "${ROOT_DIR}/deploy/management/ai-workers.yaml" \
  "${ROOT_DIR}/deploy/management/github-poll-worker.yaml" \
  "${MANAGEMENT_APP_OVERLAY}/"
IMAGE_REPO_TAG="$(image_repo_and_tag "${IMAGE_NAME}")"
IMAGE_REPO="$(printf '%s\n' "${IMAGE_REPO_TAG}" | sed -n '1p')"
IMAGE_TAG="$(printf '%s\n' "${IMAGE_REPO_TAG}" | sed -n '2p')"
cat >"${MANAGEMENT_INFRA_OVERLAY}/kustomization.yaml" <<EOF
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - namespace.yaml
  - scheduling.yaml
  - storage.yaml
  - pgbouncer.yaml
  - nats.yaml
  - rbac.yaml
images:
  - name: kubeheal-service
    newName: ${IMAGE_REPO}
    newTag: ${IMAGE_TAG}
EOF
cat >"${MANAGEMENT_APP_OVERLAY}/kustomization.yaml" <<EOF
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - services.yaml
  - ai-workers.yaml
  - github-poll-worker.yaml
images:
  - name: kubeheal-service
    newName: ${IMAGE_REPO}
    newTag: ${IMAGE_TAG}
EOF
kubectl --context "kind-${MGMT_CLUSTER}" apply -k "${MANAGEMENT_INFRA_OVERLAY}"
for old_deploy in \
  oauth-auth-service git-event-processor manifest-renderer desired-state-sync \
  command-orchestrator command-dispatcher agent-connection-gateway \
  evidence-builder ai-rca-service safe-pr-service rca-fallback-worker minio; do
  kubectl --context "kind-${MGMT_CLUSTER}" -n management delete "deploy/${old_deploy}" --ignore-not-found
done
kubectl_retry --context "kind-${MGMT_CLUSTER}" -n management rollout status statefulset/postgresql --timeout=600s
kubectl_retry --context "kind-${MGMT_CLUSTER}" -n management rollout status deploy/pgbouncer --timeout=120s
kubectl_retry --context "kind-${MGMT_CLUSTER}" -n management rollout status statefulset/nats --timeout=300s

echo "==> bootstrapping management database schema and local admin"
kubectl_retry --context "kind-${MGMT_CLUSTER}" -n management delete job/management-schema-bootstrap --ignore-not-found --wait=true
cat <<EOF | kubectl --context "kind-${MGMT_CLUSTER}" apply -f -
apiVersion: batch/v1
kind: Job
metadata:
  name: management-schema-bootstrap
  namespace: management
spec:
  backoffLimit: 3
  ttlSecondsAfterFinished: 300
  template:
    spec:
      restartPolicy: OnFailure
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        fsGroup: 10001
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: schema
          image: ${IMAGE_NAME}
          imagePullPolicy: IfNotPresent
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: ["ALL"]
          resources:
            requests:
              cpu: 25m
              memory: 64Mi
            limits:
              cpu: "1"
              memory: 512Mi
          command:
            - python
            - -c
            - |
              import os
              import sys
              import uuid

              sys.path.insert(0, "/app/src/services/gateway/api-gateway")

              from packages.storage.database import Database
              from passwords import default_display_name, hash_password, normalize_email

              db = Database()
              db.init()
              email = normalize_email(os.environ["AUTH_EMAIL"])
              user_id = "user-" + str(
                  uuid.uuid5(uuid.NAMESPACE_URL, f"{os.environ['PROJECT_SLUG']}:{email}")
              )
              db.upsert_admin_account(
                  user_id=user_id,
                  email=email,
                  password_hash=hash_password(os.environ["AUTH_PASSWORD"]),
                  display_name=default_display_name(email),
              )
          env:
            - name: PROJECT_SLUG
              value: "${PROJECT_SLUG}"
          envFrom:
            - secretRef:
                name: management-admin-bootstrap
            - configMapRef:
                name: management-runtime-config
            - secretRef:
                name: management-runtime-secret
EOF
if ! kubectl_retry --context "kind-${MGMT_CLUSTER}" -n management wait --for=condition=complete job/management-schema-bootstrap --timeout=240s; then
  kubectl --context "kind-${MGMT_CLUSTER}" -n management describe job/management-schema-bootstrap || true
  kubectl --context "kind-${MGMT_CLUSTER}" -n management logs job/management-schema-bootstrap --tail=120 || true
  exit 1
fi

python3 - "${MANAGEMENT_APP_OVERLAY}" "${APP_WORKER_DEPLOYMENTS[@]}" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
deployments = sys.argv[2:]
for file_name in ("services.yaml", "ai-workers.yaml", "github-poll-worker.yaml"):
    path = root / file_name
    text = path.read_text()
    targets = ("github-poll-worker",) if file_name == "github-poll-worker.yaml" else deployments
    for deployment in targets:
        pattern = (
            r"(kind: Deployment\nmetadata:\n  name: "
            + re.escape(deployment)
            + r"\n  namespace: management\nspec:\n  )replicas: \d+"
        )
        text = re.sub(pattern, r"\g<1>replicas: 0", text)
    path.write_text(text)
PY
# ── local overrides: HTTP-only cookie, log-based mail ──
kubectl --context "kind-${MGMT_CLUSTER}" -n management patch configmap management-runtime-config \
  --type merge -p '{"data":{"COOKIE_SECURE":"0","MAIL_DELIVERY_MODE":"log"}}'

kubectl --context "kind-${MGMT_CLUSTER}" apply -k "${MANAGEMENT_APP_OVERLAY}"
# 기본 매니페스트는 외부 비노출 ClusterIP다. kind에서만 target agent 실습용
# NodePort를 명시적으로 열어 운영 배포와 개발 노출 경계를 분리한다.
kubectl --context "kind-${MGMT_CLUSTER}" -n management patch svc api-gateway --type merge \
  -p '{"spec":{"type":"NodePort","ports":[{"name":"http","port":8000,"targetPort":"http","nodePort":30080}]}}'
kubectl --context "kind-${MGMT_CLUSTER}" -n management patch svc realtime-gateway --type merge \
  -p '{"spec":{"type":"NodePort","ports":[{"name":"http","port":8000,"targetPort":"http","nodePort":30090}]}}'
kubectl_retry --context "kind-${MGMT_CLUSTER}" -n management rollout status deploy/redis --timeout=120s
kubectl_retry --context "kind-${MGMT_CLUSTER}" -n management rollout status statefulset/minio --timeout=120s
kubectl_retry --context "kind-${MGMT_CLUSTER}" -n management get deploy/github-poll-worker >/dev/null
wait_management_pod_ready api-gateway 300s

for deploy in "${WORKER_DEPLOYMENTS_TO_START[@]}"; do
  kubectl_retry --context "kind-${MGMT_CLUSTER}" -n management scale "deploy/${deploy}" --replicas=1
  wait_management_pod_ready "${deploy}" 300s
done
if [ "${ENABLE_GITHUB_POLL_WORKER}" = "1" ]; then
  kubectl_retry --context "kind-${MGMT_CLUSTER}" -n management scale deploy/github-poll-worker --replicas=1
  wait_management_pod_ready github-poll-worker 300s
else
  echo "==> leaving github-poll-worker Deployment scaled to 0 (set ENABLE_GITHUB_POLL_WORKER=1 to enable)"
fi

seed_local_demo_workspace

MGMT_NODE="${MGMT_CLUSTER}-control-plane"
MGMT_NODE_IP="$(docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "${MGMT_NODE}")"
MANAGEMENT_BASE_URL="http://${MGMT_NODE_IP}:30080"

echo "==> deploying target cluster with management URL: ${MANAGEMENT_BASE_URL}"
GATEWAY_PORT="${GATEWAY_PORT:-18080}"
INSTALL_TELEMETRY="${INSTALL_TELEMETRY:-1}"
BASE_URL="${BASE_URL:-http://localhost:${GATEWAY_PORT}}" \
MANAGEMENT_BASE_URL="${MANAGEMENT_BASE_URL}" \
TARGET_CONTEXT="kind-${TARGET_CLUSTER}" \
TARGET_CLUSTER_ID="${TARGET_RUNTIME_CLUSTER_ID}" \
TARGET_ENVIRONMENT="test" \
EVIDENCE_INTERVAL_SECONDS="${EVIDENCE_INTERVAL_SECONDS}" \
IMAGE_NAME="${IMAGE_NAME}" \
INSTALL_TELEMETRY="${INSTALL_TELEMETRY}" \
MINIO_ROOT_USER="${MINIO_ROOT_USER}" \
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD}" \
INSTALL_SAMPLE_WORKLOAD="${INSTALL_SAMPLE_WORKLOAD:-false}" \
SAMPLE_WORKLOAD_NAME="${SAMPLE_WORKLOAD_NAME:-}" \
SAMPLE_WORKLOAD_IMAGE="${SAMPLE_WORKLOAD_IMAGE:-}" \
bash "${ROOT_DIR}/scripts/register-target.sh"

if [ "${INSTALL_TELEMETRY}" = "1" ]; then
  configure_local_prometheus
fi

echo
echo "service is ready."
echo "Gateway:      ${BASE_URL:-http://localhost:${GATEWAY_PORT}}"
echo "Health:       ${BASE_URL:-http://localhost:${GATEWAY_PORT}}/healthz"
echo "Admin email:  ${AUTH_EMAIL}"
if [ "${GENERATED_AUTH_PASSWORD}" = "1" ]; then
  if [ "${PRINT_GENERATED_ADMIN_PASSWORD}" = "1" ]; then
    echo "Generated admin pass: ${AUTH_PASSWORD}"
  else
    echo "Admin pass:   generated; set PRINT_GENERATED_ADMIN_PASSWORD=1 to print it"
  fi
else
  echo "Admin pass:   provided through AUTH_PASSWORD"
fi
echo
echo "Worker set:   ${UP_WORKER_SET}"
echo "Poll worker:  ${ENABLE_GITHUB_POLL_WORKER}"
echo
echo "Run smoke test:"
echo "  bash ${ROOT_DIR}/scripts/smoke.sh"
