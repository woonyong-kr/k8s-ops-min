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
EXPECTED_CONSOLE_IMAGE="${EXPECTED_CONSOLE_IMAGE:-}"
CONSOLE_ROLLBACK_PLAN="${CONSOLE_ROLLBACK_PLAN:-}"
SOURCE_SHA="${SOURCE_SHA:-}"
SMOKE_CURL_IMAGE="${SMOKE_CURL_IMAGE:-curlimages/curl:8.11.1}"
CLUSTER_CURL_POD_PREFIX="deploy-smoke-console"
IN_CLUSTER_API_URL="http://api-gateway.${MGMT_NS}.svc.cluster.local"
IN_CLUSTER_CONSOLE_URL="http://console-dev.${MGMT_NS}.svc.cluster.local"

for variable in \
  BASE_URL \
  MGMT_CONTEXT \
  PRE_DEPLOY_FRONTEND_BUNDLE \
  EXPECTED_CONSOLE_IMAGE \
  CONSOLE_ROLLBACK_PLAN \
  SOURCE_SHA; do
  require_env "${variable}"
done
BASE_URL="${BASE_URL%/}"

for command in curl grep jq kubectl; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    echo "missing required command: ${command}" >&2
    exit 1
  fi
done

if ! [[ "${SOURCE_SHA}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "source SHA must be a full lowercase Git SHA" >&2
  exit 1
fi
if ! [[ "${EXPECTED_CONSOLE_IMAGE}" =~ @sha256:[0-9a-f]{64}$ ]]; then
  echo "expected console image must be digest-pinned" >&2
  exit 1
fi

index_file="$(mktemp)"
bundle_file="$(mktemp)"
cleanup() {
  rm -f "${index_file}" "${bundle_file}"
}
trap cleanup EXIT

echo "==> post-deploy gateway health"
health_response="$(cluster_curl "${IN_CLUSTER_API_URL}/api/healthz")"
test "${health_response##*$'\n'}" = "200"

echo "==> post-deploy frontend bundle"
frontend_response="$(cluster_curl "${IN_CLUSTER_CONSOLE_URL}/")"
frontend_status="${frontend_response##*$'\n'}"
printf '%s' "${frontend_response%$'\n'*}" >"${index_file}"
test "${frontend_status}" = "200"
post_bundle="$(grep -Eom1 'index-[A-Za-z0-9_-]+\.js' "${index_file}")"
test -n "${post_bundle}"
test "${post_bundle}" != "${PRE_DEPLOY_FRONTEND_BUNDLE}"

echo "==> post-deploy source provenance"
bundle_response="$(cluster_curl "${IN_CLUSTER_CONSOLE_URL}/assets/${post_bundle}")"
bundle_status="${bundle_response##*$'\n'}"
printf '%s' "${bundle_response%$'\n'*}" >"${bundle_file}"
test "${bundle_status}" = "200"
grep --fixed-strings --quiet "${SOURCE_SHA}" "${bundle_file}"

echo "==> post-deploy immutable console image"
while IFS=$'\t' read -r namespace resource container; do
  current_image="$(
    kubectl --context "${MGMT_CONTEXT}" -n "${namespace}" get "${resource}" -o json \
      | jq -r --arg container "${container}" \
        '.spec.template.spec.containers[] | select(.name == $container) | .image'
  )"
  test "${current_image}" = "${EXPECTED_CONSOLE_IMAGE}"
done < <(jq -r \
  '(.targets[]?, .bootstrap_targets[]?) | [.namespace, .resource, .container] | @tsv' \
  "${CONSOLE_ROLLBACK_PLAN}")

echo "==> post-deploy public edge convergence"
wait_for_public_edge_release "${BASE_URL}" "${post_bundle}" "${SOURCE_SHA}"

printf 'post-deploy console smoke passed: bundle=%s source_sha=%s\n' \
  "${post_bundle}" "${SOURCE_SHA}"
