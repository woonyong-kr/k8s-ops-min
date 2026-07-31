#!/usr/bin/env bash

# Run one in-cluster curl probe without using `kubectl run --rm -i`.
# Short-lived curl pods can finish before kubectl attaches, producing either an
# empty response or replayed logs. Create the pod first, follow its logs through
# termination, and delete it explicitly so the response has one owner.
cluster_curl() {
  local url="$1"
  local pod_name="${CLUSTER_CURL_POD_PREFIX:-deploy-smoke}-${GITHUB_RUN_ID:-local}-${RANDOM}"
  local response

  if ! kubectl --context "${MGMT_CONTEXT}" -n "${MGMT_NS}" run "${pod_name}" \
      --restart=Never \
      --image="${SMOKE_CURL_IMAGE}" \
      --quiet -- \
      curl --silent --show-error \
        --connect-timeout 5 \
        --max-time 15 \
        --write-out $'\n__OPSIA_HTTP_STATUS__=%{http_code}' \
        "${url}" >/dev/null; then
    _delete_cluster_curl_pod "${pod_name}"
    return 1
  fi
  if ! _wait_for_cluster_curl_container "${pod_name}"; then
    kubectl --context "${MGMT_CONTEXT}" -n "${MGMT_NS}" get \
      "pod/${pod_name}" -o wide >&2 || true
    _delete_cluster_curl_pod "${pod_name}"
    return 1
  fi
  if ! response="$(
    kubectl --context "${MGMT_CONTEXT}" -n "${MGMT_NS}" logs \
      --follow \
      --pod-running-timeout=30s \
      "pod/${pod_name}"
  )"; then
    kubectl --context "${MGMT_CONTEXT}" -n "${MGMT_NS}" get \
      "pod/${pod_name}" -o wide >&2 || true
    _delete_cluster_curl_pod "${pod_name}"
    return 1
  fi
  _delete_cluster_curl_pod "${pod_name}"
  _normalize_cluster_curl_response <<<"${response}"
}

_wait_for_cluster_curl_container() {
  local pod_name="$1"
  local attempt
  local started_or_finished

  for attempt in $(seq 1 60); do
    started_or_finished="$(
      kubectl --context "${MGMT_CONTEXT}" -n "${MGMT_NS}" get \
        "pod/${pod_name}" \
        -o jsonpath='{.status.containerStatuses[0].state.running.startedAt}{.status.containerStatuses[0].state.terminated.finishedAt}' \
        2>/dev/null || true
    )"
    if [ -n "${started_or_finished}" ]; then
      return 0
    fi
    sleep 0.5
  done
  return 1
}

_delete_cluster_curl_pod() {
  kubectl --context "${MGMT_CONTEXT}" -n "${MGMT_NS}" delete \
    "pod/$1" \
    --ignore-not-found \
    --wait=false \
    >/dev/null 2>&1 || true
}

_normalize_cluster_curl_response() {
  local response
  local marker="__OPSIA_HTTP_STATUS__="
  local body
  local http_status

  response="$(cat)"
  if [[ "${response}" != *"${marker}"* ]]; then
    # Test doubles and older callers already return one canonical body/status
    # pair. Preserve that shape while all real probes use the marker above.
    printf '%s\n' "${response}"
    return 0
  fi

  http_status="${response##*${marker}}"
  http_status="${http_status%%$'\n'*}"
  body="${response%${marker}*}"
  if [[ "${body}" == *"${marker}"* ]]; then
    body="${body##*${marker}}"
    body="${body#*$'\n'}"
  fi
  body="${body%$'\n'}"
  printf '%s\n%s\n' "${body}" "${http_status}"
}
