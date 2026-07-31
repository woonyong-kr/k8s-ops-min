#!/usr/bin/env bash
# 라이브 데모 시작 상태 복구 도우미.
#
# management 워크로드는 건드리지 않고 cluster-1 데모 시작 상태만 되돌린다.
# 기본값은 sandbox 데모 리소스 삭제와 public API 기반 cluster-1 등록 해제다.
# 실제 미연결 상태부터 리허설해야 할 때만 --uninstall-agent 를 붙인다.
# 붙이지 않으면 아직 떠 있는 agent가 다시 연결될 수 있다.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

source "${SCRIPT_DIR}/lib/env.sh"
source "${SCRIPT_DIR}/lib/auth.sh"

BASE_URL="${BASE_URL:-https://k8s.woonyong.org/api}"
WEB_BASE_URL="${WEB_BASE_URL:-https://k8s.woonyong.org}"
TARGET_CONTEXT="${TARGET_CONTEXT:-cluster-1}"
CLUSTER_ID="${CLUSTER_ID:-cluster-1}"
SANDBOX_NAMESPACE="${SANDBOX_NAMESPACE:-sandbox}"
AGENT_NAMESPACE="${AGENT_NAMESPACE:-target}"
COOKIE_JAR="${COOKIE_JAR:-$(mktemp)}"
UNINSTALL_AGENT="0"
CHECK_ONLY="0"

cleanup() {
  if [[ "${COOKIE_JAR}" == /tmp/* ]]; then
    rm -f "${COOKIE_JAR}"
  fi
}
trap cleanup EXIT

usage() {
  cat <<'EOF'
Usage:
  AUTH_EMAIL=... AUTH_PASSWORD=... bash scripts/demo-reset.sh [--uninstall-agent] [--check-only]

Environment:
  BASE_URL            API 기본 주소. 기본값: https://k8s.woonyong.org/api
  WEB_BASE_URL        same-origin write header 에 넣을 콘솔 origin.
  TARGET_CONTEXT      target cluster kubeconfig context. 기본값: cluster-1
  CLUSTER_ID          등록 해제할 platform cluster id. 기본값: cluster-1
  SANDBOX_NAMESPACE   데모 앱 namespace. 기본값: sandbox
  AGENT_NAMESPACE     설치된 cluster-agent namespace. 기본값: target

Flags:
  --uninstall-agent   target cluster 의 cluster-agent 런타임 오브젝트도 제거한다.
  --check-only        현재 시작 상태 점검 결과만 출력한다.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --uninstall-agent)
      UNINSTALL_AGENT="1"
      ;;
    --check-only)
      CHECK_ONLY="1"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
  shift
done

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

log() {
  printf '%s [demo-reset] %s\n' "$(date +%T)" "$*"
}

api_delete() {
  local path="$1"
  local status
  status="$(
    curl -sS -o /tmp/demo-reset-response.$$ -w '%{http_code}' \
      -X DELETE "${BASE_URL%/}${path}" \
      -H "origin: ${WEB_BASE_URL%/}" \
      -H "referer: ${WEB_BASE_URL%/}/clusters" \
      -H "x-service-csrf: same-origin" \
      -b "${COOKIE_JAR}"
  )"
  case "${status}" in
    204|404)
      rm -f /tmp/demo-reset-response.$$
      ;;
    *)
      cat /tmp/demo-reset-response.$$ >&2 || true
      rm -f /tmp/demo-reset-response.$$
      echo "DELETE ${path} failed with HTTP ${status}" >&2
      exit 1
      ;;
  esac
}

k() {
  kubectl --context "${TARGET_CONTEXT}" -n "${SANDBOX_NAMESPACE}" "$@"
}

check_state() {
  local clusters_json
  log "cluster registration status"
  clusters_json="$(curl -fsS "${BASE_URL%/}/clusters" -b "${COOKIE_JAR}")"
  CLUSTERS_JSON="${clusters_json}" python3 - "${CLUSTER_ID}" <<'PY'
import json
import os
import sys

target = sys.argv[1]
body = json.loads(os.environ["CLUSTERS_JSON"])
clusters = body.get("clusters", [])
match = next((cluster for cluster in clusters if cluster.get("cluster_id") == target), None)
if not match:
    print(f"{target}: not registered")
else:
    print(
        f"{target}: status={match.get('status')} "
        f"connection={match.get('connection_status')} "
        f"last_seen={match.get('last_seen_at') or '-'}"
    )
PY

  log "sandbox demo resources"
  kubectl --context "${TARGET_CONTEXT}" -n "${SANDBOX_NAMESPACE}" \
    get deploy,svc,cm -l app.kubernetes.io/part-of=k8s-incident-demo-target \
    --ignore-not-found
  kubectl --context "${TARGET_CONTEXT}" -n "${SANDBOX_NAMESPACE}" \
    get deploy,svc,cm,hpa -l scenario --ignore-not-found

  log "cluster-agent runtime"
  kubectl --context "${TARGET_CONTEXT}" -n "${AGENT_NAMESPACE}" \
    get deploy/cluster-agent cm/target-runtime-config secret/target-runtime-secret \
    --ignore-not-found
}

delete_sandbox_demo() {
  log "delete GreenCart demo resources from ${TARGET_CONTEXT}/${SANDBOX_NAMESPACE}"
  k delete deploy,svc,cm \
    -l app.kubernetes.io/part-of=k8s-incident-demo-target \
    --ignore-not-found
  k delete deploy,svc,cm,hpa -l scenario --ignore-not-found
  k delete deploy orders-api storefront-web --ignore-not-found
  k delete svc orders-api storefront-web --ignore-not-found
  k delete cm demo-target-config --ignore-not-found
}

delete_cluster_agent() {
  log "delete cluster-agent runtime from ${TARGET_CONTEXT}/${AGENT_NAMESPACE}"
  kubectl --context "${TARGET_CONTEXT}" -n "${AGENT_NAMESPACE}" delete \
    deploy/cluster-agent \
    cm/target-runtime-config cm/target-agent-policy \
    secret/target-runtime-secret \
    sa/cluster-agent \
    role/cluster-agent-self-manage role/cluster-agent-target-manage \
    rolebinding/cluster-agent-self-manage rolebinding/cluster-agent-target-manage \
    --ignore-not-found
  kubectl --context "${TARGET_CONTEXT}" -n "${SANDBOX_NAMESPACE}" delete \
    role/cluster-agent-sandbox-write rolebinding/cluster-agent-sandbox-write \
    --ignore-not-found
  kubectl --context "${TARGET_CONTEXT}" delete \
    clusterrole/cluster-agent-read clusterrolebinding/cluster-agent-read \
    --ignore-not-found
  kubectl --context "${TARGET_CONTEXT}" -n "${AGENT_NAMESPACE}" delete \
    daemonset/node-collector daemonset/target-node-collector \
    --ignore-not-found
}

need curl
need kubectl
need python3
require_env AUTH_EMAIL
require_env AUTH_PASSWORD

login_with_password "${BASE_URL%/}" "${COOKIE_JAR}"

if [[ "${CHECK_ONLY}" == "1" ]]; then
  check_state
  exit 0
fi

delete_sandbox_demo
if [[ "${UNINSTALL_AGENT}" == "1" ]]; then
  delete_cluster_agent
fi

log "unregister ${CLUSTER_ID} through API"
api_delete "/clusters/${CLUSTER_ID}"

check_state
log "reset complete"
