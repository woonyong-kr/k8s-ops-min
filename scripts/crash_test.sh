#!/usr/bin/env bash
# 아웃박스 정확히 한 번 크래시 테스트
#
# N개 webhook 발사 후 gitops worker 강제 종료로 NATS redelivery 유도
# transactional outbox + ledger dedup 기준, crash 중에도 각 webhook
# (correlation_id)마다 pull request 정확히 1개
#
# 필요: AWS management cluster 접근 권한과 Gateway BASE_URL
# 사용: bash scripts/crash_test.sh   (N 기본값 6, env로 override)
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT_DIR}/scripts/lib/env.sh"

BASE_URL="${BASE_URL:-}"
CTX="${MGMT_CONTEXT:-}"
NS="${MGMT_NS:-management}"
N="${N:-6}"
POLL_TIMEOUT="${POLL_TIMEOUT:-300}"
GITHUB_WEBHOOK_SECRET="${GITHUB_WEBHOOK_SECRET:-}"
GITHUB_REPO="${GITHUB_REPO:-$(git -C "${ROOT_DIR}" config --get remote.origin.url 2>/dev/null | sed -E 's#^git@github.com:##; s#^https?://github.com/##; s#\\.git$##' || true)}"
GITHUB_BRANCH="${GITHUB_BRANCH:-$(git -C "${ROOT_DIR}" branch --show-current 2>/dev/null || true)}"
MANIFEST_PATH="${MANIFEST_PATH:-deploy/target/target.yaml}"
CRASH_TEST_IMAGE="${CRASH_TEST_IMAGE:-}"
POSTGRES_USER="${POSTGRES_USER:-service}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
POSTGRES_DB="${POSTGRES_DB:-service}"
require_env BASE_URL
require_env CTX
KILL_APPS=(manifest-render-worker scm-worker)

log() { printf '%s [crash-test] %s\n' "$(date +%T)" "$*"; }

psql_q() {
  kubectl --context "$CTX" -n "$NS" exec statefulset/postgresql -- \
    env PGPASSWORD="$POSTGRES_PASSWORD" \
      psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -c "$1" 2>/dev/null \
    | tr -d '[:space:]'
}

load_secret_key() {
  local secret_name="$1"
  local key="$2"
  kubectl --context "$CTX" -n "$NS" \
    get secret "$secret_name" -o "jsonpath={.data.${key}}" \
    | python3 -c 'import base64, sys; print(base64.b64decode(sys.stdin.read()).decode())'
}

load_webhook_secret() {
  load_secret_key management-runtime-secret GITHUB_WEBHOOK_SECRET
}

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

kill_workers() {
  for app in "${KILL_APPS[@]}"; do
    kubectl --context "$CTX" -n "$NS" delete pod -l "app=${app}" \
      --grace-period=0 --force >/dev/null 2>&1 || true
  done
}

ready_count() {
  kubectl --context "$CTX" -n "$NS" get pods -l "app=$1" \
    -o jsonpath='{range .items[*]}{.status.containerStatuses[0].ready}{"\n"}{end}' 2>/dev/null \
    | grep -c true || true
}

if [ -z "$POSTGRES_PASSWORD" ]; then
  POSTGRES_PASSWORD="$(load_secret_key postgresql-secret POSTGRES_PASSWORD)"
fi

if [ -z "$GITHUB_WEBHOOK_SECRET" ]; then
  GITHUB_WEBHOOK_SECRET="$(load_webhook_secret)"
fi
if [ -z "$GITHUB_REPO" ]; then
  echo "GITHUB_REPO is required for crash test webhooks" >&2
  exit 1
fi
if [ -z "$CRASH_TEST_IMAGE" ]; then
  echo "CRASH_TEST_IMAGE is required for crash test webhooks" >&2
  exit 1
fi

log "starting crash test: ${N} signed webhooks, kill targets ${KILL_APPS[*]}"
corr_ids=()
for i in $(seq 1 "$N"); do
  body="$(
    CRASH_COMMIT_SHA="crash${i}" \
    CRASH_TEST_IMAGE="${CRASH_TEST_IMAGE}" \
    GITHUB_REPO="${GITHUB_REPO}" \
    GITHUB_BRANCH="${GITHUB_BRANCH}" \
    MANIFEST_PATH="${MANIFEST_PATH}" \
    python3 - <<'PY'
import json
import os

print(json.dumps({
    "commit_sha": os.environ["CRASH_COMMIT_SHA"],
    "image": os.environ["CRASH_TEST_IMAGE"],
    "replicas": 2,
    "repo_ref": os.environ["GITHUB_REPO"],
    "branch": os.environ["GITHUB_BRANCH"],
    "manifest_path": os.environ["MANIFEST_PATH"],
}))
PY
  )"
  signature="$(sign_body "$body")"
  resp="$(curl -fsS -X POST "${BASE_URL}/github/webhook" \
    -H "content-type: application/json" \
    -H "x-hub-signature-256: ${signature}" \
    -d "$body")"
  cid="$(printf "%s" "$resp" | python3 -c 'import json,sys; print(json.load(sys.stdin)["correlation_id"])')"
  corr_ids+=("$cid")
  log "webhook ${i} accepted correlation_id=${cid}"
  if [ $((i % 3)) -eq 0 ]; then
    sleep 1
    kill_workers
    log "force-killed workers mid-flight: ${KILL_APPS[*]}"
  fi
done

log "polling: workers recover, NATS redelivers unacked messages, ledger dedups duplicates"
list="$(printf "'%s'," "${corr_ids[@]}")"
list="${list%,}"
count_sql="select count(*) from pull_requests where correlation_id in (${list})"
maxper_sql="select coalesce(max(c),0) from (select count(*) c from pull_requests where correlation_id in (${list}) group by correlation_id) t"

deadline=$(( $(date +%s) + POLL_TIMEOUT ))
total=0
maxper=0
while [ "$(date +%s)" -lt "$deadline" ]; do
  ready=""
  for app in "${KILL_APPS[@]}"; do
    ready+="${app}=$(ready_count "$app")/1 "
  done
  total="$(psql_q "$count_sql")"; total="${total:-0}"
  maxper="$(psql_q "$maxper_sql")"; maxper="${maxper:-0}"
  log "workers ${ready}| pull_requests=${total}/${N} max_per_correlation=${maxper}"
  [ "$maxper" -gt 1 ] && break
  [ "$total" -ge "$N" ] && break
  sleep 6
done

log "result total=${total} expected=${N} max_per_correlation=${maxper}"
if [ "$total" -eq "$N" ] && [ "$maxper" -eq 1 ]; then
  log "exactly-once verified: each webhook produced one pull request despite crashes"
  exit 0
fi
if [ "$maxper" -gt 1 ]; then
  log "failure: duplicate pull requests for a correlation_id, ledger dedup not effective"
else
  log "failure: observed ${total} pull requests, expected ${N} (possible loss or workers not recovering)"
fi
exit 1
