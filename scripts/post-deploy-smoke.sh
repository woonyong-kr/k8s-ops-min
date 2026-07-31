#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/env.sh"
source "${SCRIPT_DIR}/lib/cluster-curl.sh"
source "${SCRIPT_DIR}/lib/public-edge.sh"

BASE_URL="${BASE_URL:-}"
MGMT_CONTEXT="${MGMT_CONTEXT:-}"
MGMT_NS="${MGMT_NS:-management}"
PRE_DEPLOY_FRONTEND_BUNDLE="${PRE_DEPLOY_FRONTEND_BUNDLE:-}"
REQUIRE_FRONTEND_BUNDLE_CHANGE="${REQUIRE_FRONTEND_BUNDLE_CHANGE:-}"
EXPECTED_ALEMBIC_HEAD="${EXPECTED_ALEMBIC_HEAD:-}"
EXPECTED_SERVICE_IMAGE="${EXPECTED_SERVICE_IMAGE:-}"
EXPECTED_CONSOLE_IMAGE="${EXPECTED_CONSOLE_IMAGE:-}"
SERVICE_ROLLBACK_PLAN="${SERVICE_ROLLBACK_PLAN:-}"
CONSOLE_ROLLBACK_PLAN="${CONSOLE_ROLLBACK_PLAN:-}"
SOURCE_SHA="${SOURCE_SHA:-}"
SMOKE_CURL_IMAGE="${SMOKE_CURL_IMAGE:-curlimages/curl:8.11.1}"
CLUSTER_CURL_POD_PREFIX="deploy-smoke-post"
IN_CLUSTER_API_URL="http://api-gateway.${MGMT_NS}.svc.cluster.local"
IN_CLUSTER_CONSOLE_URL="http://console-dev.${MGMT_NS}.svc.cluster.local"

for variable in \
  BASE_URL \
  MGMT_CONTEXT \
  PRE_DEPLOY_FRONTEND_BUNDLE \
  REQUIRE_FRONTEND_BUNDLE_CHANGE \
  EXPECTED_ALEMBIC_HEAD \
  EXPECTED_SERVICE_IMAGE \
  EXPECTED_CONSOLE_IMAGE \
  SERVICE_ROLLBACK_PLAN \
  CONSOLE_ROLLBACK_PLAN \
  SOURCE_SHA; do
  require_env "${variable}"
done
BASE_URL="${BASE_URL%/}"

for command in curl jq kubectl python3 uv; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    echo "missing required command: ${command}" >&2
    exit 1
  fi
done

if ! [[ "${EXPECTED_SERVICE_IMAGE}" =~ @sha256:[0-9a-f]{64}$ ]]; then
  echo "expected service image must be digest-pinned" >&2
  exit 1
fi
if ! [[ "${EXPECTED_CONSOLE_IMAGE}" =~ @sha256:[0-9a-f]{64}$ ]]; then
  echo "expected console image must be digest-pinned" >&2
  exit 1
fi

index_file="$(mktemp)"
health_file="$(mktemp)"
port_forward_log="$(mktemp)"
PORT_FORWARD_PID=""

cleanup() {
  if [ -n "${PORT_FORWARD_PID}" ] && kill -0 "${PORT_FORWARD_PID}" 2>/dev/null; then
    kill "${PORT_FORWARD_PID}" 2>/dev/null || true
    wait "${PORT_FORWARD_PID}" 2>/dev/null || true
  fi
  rm -f "${index_file}" "${health_file}" "${port_forward_log}"
}
trap cleanup EXIT

start_api_port_forward() {
  local attempt

  kubectl --context "${MGMT_CONTEXT}" -n "${MGMT_NS}" port-forward \
    --address 127.0.0.1 service/api-gateway :80 \
    >"${port_forward_log}" 2>&1 &
  PORT_FORWARD_PID="$!"

  for attempt in $(seq 1 20); do
    API_FORWARD_PORT="$(sed -nE \
      's/^Forwarding from 127\.0\.0\.1:([0-9]+) -> 8000$/\1/p' \
      "${port_forward_log}" | head -n 1)"
    if [ -n "${API_FORWARD_PORT}" ]; then
      return 0
    fi
    if ! kill -0 "${PORT_FORWARD_PID}" 2>/dev/null; then
      cat "${port_forward_log}" >&2
      return 1
    fi
    sleep 0.5
  done

  cat "${port_forward_log}" >&2
  echo "api-gateway port-forward did not become ready" >&2
  return 1
}

echo "==> post-deploy gateway health"
health_response="$(cluster_curl "${IN_CLUSTER_API_URL}/api/healthz")"
health_status="${health_response##*$'\n'}"
printf '%s' "${health_response%$'\n'*}" >"${health_file}"
test "${health_status}" = "200"
python3 - "${health_file}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    document = json.load(handle)
if document.get("status") != "ok":
    raise SystemExit("post-deploy gateway health is not ok")
PY

echo "==> post-deploy frontend bundle"
frontend_response="$(cluster_curl "${IN_CLUSTER_CONSOLE_URL}/")"
frontend_status="${frontend_response##*$'\n'}"
printf '%s' "${frontend_response%$'\n'*}" >"${index_file}"
test "${frontend_status}" = "200"
post_bundle="$(grep -Eom1 'index-[A-Za-z0-9_-]+\.js' "${index_file}")"
test -n "${post_bundle}"
case "${REQUIRE_FRONTEND_BUNDLE_CHANGE}" in
  1) test "${post_bundle}" != "${PRE_DEPLOY_FRONTEND_BUNDLE}" ;;
  0) ;;
  *) echo "REQUIRE_FRONTEND_BUNDLE_CHANGE must be 0 or 1" >&2; exit 1 ;;
esac

echo "==> post-deploy login, cluster list, and resource list"
start_api_port_forward
IN_CLUSTER_FORWARD_URL="http://127.0.0.1:${API_FORWARD_PORT}"
API_BASE_URL="${IN_CLUSTER_FORWARD_URL}" \
  bash "${SCRIPT_DIR}/post_deploy_read_smoke.sh"

echo "==> post-deploy Alembic head"
runtime_database="$({
  kubectl --context "${MGMT_CONTEXT}" -n "${MGMT_NS}" \
    get secret management-runtime-secret \
    -o jsonpath='{.data.COMMAND_NOTIFY_DATABASE_URL}'
} | python3 -c '
import base64
import sys
from urllib.parse import urlsplit

value = base64.b64decode(sys.stdin.buffer.read(), validate=True).decode()
parsed = urlsplit(value)
if parsed.scheme not in {"postgres", "postgresql"} or parsed.path.count("/") != 1:
    raise SystemExit("runtime database URL is invalid")
print(parsed.path[1:])
')"
test -n "${runtime_database}"
database_head="$(
  kubectl --context "${MGMT_CONTEXT}" -n "${MGMT_NS}" exec statefulset/postgresql -- \
    env OPSIA_RUNTIME_DATABASE="${runtime_database}" \
    sh -ec 'psql -U "$POSTGRES_USER" -d "$OPSIA_RUNTIME_DATABASE" -v ON_ERROR_STOP=1 -Atc "SELECT version_num FROM alembic_version"'
)"
test "${database_head}" = "${EXPECTED_ALEMBIC_HEAD}"

echo "==> post-deploy auth bypass policy"
uv run python "${SCRIPT_DIR}/verify_dev_auth_bypass.py" live \
  --context "${MGMT_CONTEXT}" \
  --namespace "${MGMT_NS}"

verify_plan_images() {
  local plan="$1"
  local expected_image="$2"
  local live_deployments

  live_deployments="$(
    kubectl --context "${MGMT_CONTEXT}" -n "${MGMT_NS}" get deployments -o json
  )"
  jq -e \
    --arg expected_image "${expected_image}" \
    --arg management_namespace "${MGMT_NS}" \
    --slurpfile plan "${plan}" \
    '
      . as $live
      | ($plan[0] | [(.targets[]?, .bootstrap_targets[]?)]) as $targets
      | all(
          $targets[];
          . as $target
          | any(
              $live.items[];
              $target.namespace == $management_namespace
              and .metadata.namespace == $target.namespace
              and ("deployment/" + .metadata.name) == $target.resource
              and any(
                .spec.template.spec.containers[];
                .name == $target.container and .image == $expected_image
              )
            )
        )
    ' <<<"${live_deployments}" >/dev/null
}

echo "==> post-deploy immutable images"
verify_plan_images "${SERVICE_ROLLBACK_PLAN}" "${EXPECTED_SERVICE_IMAGE}"
verify_plan_images "${CONSOLE_ROLLBACK_PLAN}" "${EXPECTED_CONSOLE_IMAGE}"

echo "==> post-deploy public edge convergence"
wait_for_public_edge_release "${BASE_URL}" "${post_bundle}" "${SOURCE_SHA}"

printf 'post-deploy smoke passed: bundle=%s head=%s\n' \
  "${post_bundle}" "${database_head}"
