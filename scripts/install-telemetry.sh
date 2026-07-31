#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "missing required environment variable: ${name}" >&2
    exit 1
  fi
}

TARGET_CLUSTER="${TARGET_CLUSTER:-}"
TARGET_CONTEXT="${TARGET_CONTEXT:-${TARGET_CLUSTER}}"
TARGET_NAMESPACE="${TARGET_NAMESPACE:-target}"
TARGET_CLUSTER_ID="${TARGET_CLUSTER_ID:-}"
WORKSPACE_ID="${WORKSPACE_ID:-}"
MANAGEMENT_API_BASE_URL="${MANAGEMENT_API_BASE_URL:-}"
ALERTMANAGER_AGENT_TOKEN="${ALERTMANAGER_AGENT_TOKEN:-}"

PROMETHEUS_RELEASE="${PROMETHEUS_RELEASE:-prometheus}"
LOKI_RELEASE="${LOKI_RELEASE:-loki}"
TEMPO_RELEASE="${TEMPO_RELEASE:-tempo}"
OTEL_RELEASE="${OTEL_RELEASE:-opentelemetry-collector}"
PROMETHEUS_CHART_VERSION="${PROMETHEUS_CHART_VERSION:-29.19.0}"
LOKI_CHART_VERSION="${LOKI_CHART_VERSION:-7.1.0}"
TEMPO_CHART_VERSION="${TEMPO_CHART_VERSION:-1.24.4}"
OTEL_CHART_VERSION="${OTEL_CHART_VERSION:-0.165.0}"

PROMETHEUS_VALUES="${PROMETHEUS_VALUES:-${ROOT_DIR}/deploy/target/prometheus.yaml}"
LOKI_VALUES="${LOKI_VALUES:-${ROOT_DIR}/deploy/target/loki.yaml}"
TEMPO_VALUES="${TEMPO_VALUES:-${ROOT_DIR}/deploy/target/tempo.yaml}"
OTEL_VALUES="${OTEL_VALUES:-${ROOT_DIR}/deploy/target/opentelemetry.yaml}"
TARGET_MINIO_MANIFEST="${TARGET_MINIO_MANIFEST:-${ROOT_DIR}/deploy/target/minio.yaml}"
TELEMETRY_ASSET_BASE_URL="${TELEMETRY_ASSET_BASE_URL:-}"
MINIO_ROOT_USER="${MINIO_ROOT_USER:-minioadmin}"
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-}"
ALERTMANAGER_CONFIG_SECRET="${ALERTMANAGER_CONFIG_SECRET:-kyro-alertmanager-config}"
PROMETHEUS_SLI_ALERT_NAME="${PROMETHEUS_SLI_ALERT_NAME:-OpsiaSliFailureRatioHigh}"
TELEMETRY_TEMP_DIR=""
PROMETHEUS_DYNAMIC_VALUES=""
ALERTMANAGER_WEBHOOK_URL=""

cleanup() {
  if [[ -n "${TELEMETRY_TEMP_DIR}" && -d "${TELEMETRY_TEMP_DIR}" ]]; then
    rm -rf -- "${TELEMETRY_TEMP_DIR}"
  fi
}

trap cleanup EXIT

ensure_telemetry_temp_dir() {
  if [[ -z "${TELEMETRY_TEMP_DIR}" ]]; then
    TELEMETRY_TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/kyro-telemetry.XXXXXX")"
    chmod 700 "${TELEMETRY_TEMP_DIR}"
  fi
}

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

wait_rollouts() {
  local kind="$1"
  local selector="$2"
  local timeout="${3:-180s}"
  local resources

  resources="$(
    kubectl --context "${TARGET_CONTEXT}" -n "${TARGET_NAMESPACE}" \
      get "${kind}" -l "${selector}" -o name 2>/dev/null || true
  )"

  if [[ -z "${resources}" ]]; then
    return
  fi

  while IFS= read -r resource; do
    kubectl --context "${TARGET_CONTEXT}" -n "${TARGET_NAMESPACE}" \
      rollout status "${resource}" --timeout="${timeout}"
  done <<< "${resources}"
}

require_service_endpoints() {
  local service_name="$1"
  local attempts="${2:-60}"
  local ready_endpoints

  kubectl --context "${TARGET_CONTEXT}" -n "${TARGET_NAMESPACE}" \
    get "service/${service_name}" >/dev/null
  for _ in $(seq 1 "${attempts}"); do
    ready_endpoints="$(
      kubectl --context "${TARGET_CONTEXT}" -n "${TARGET_NAMESPACE}" \
        get endpointslices \
        -l "kubernetes.io/service-name=${service_name}" \
        -o jsonpath='{range .items[*].endpoints[*]}{.conditions.ready}{"\n"}{end}' \
        2>/dev/null \
        | grep -vc '^false$' \
        || true
    )"
    if [[ "${ready_endpoints}" =~ ^[1-9][0-9]*$ ]]; then
      return 0
    fi
    sleep 2
  done
  echo "service/${service_name} has no ready endpoints" >&2
  return 1
}

require_release_workload() {
  local release="$1"
  local resources

  resources="$(
    {
      kubectl --context "${TARGET_CONTEXT}" -n "${TARGET_NAMESPACE}" \
        get deployment,statefulset,daemonset \
        -l "app.kubernetes.io/instance=${release}" \
        -o name 2>/dev/null \
        || true
    }
  )"
  if [[ -z "${resources}" ]]; then
    echo "telemetry release ${release} has no workload" >&2
    return 1
  fi
}

require_tempo_runtime_bounds() {
  local args
  local memory_limit
  local rendered_config
  local expected

  args="$(
    kubectl --context "${TARGET_CONTEXT}" -n "${TARGET_NAMESPACE}" \
      get statefulset/tempo \
      -o jsonpath='{.spec.template.spec.containers[0].args[*]}'
  )"
  memory_limit="$(
    kubectl --context "${TARGET_CONTEXT}" -n "${TARGET_NAMESPACE}" \
      get statefulset/tempo \
      -o jsonpath='{.spec.template.spec.containers[0].resources.limits.memory}'
  )"
  rendered_config="$(
    kubectl --context "${TARGET_CONTEXT}" -n "${TARGET_NAMESPACE}" \
      get configmap/tempo \
      -o jsonpath='{.data.tempo\.yaml}'
  )"

  if [[ " ${args} " != *" -mem-ballast-size-mbs=0 "* ]]; then
    echo "tempo runtime has an unsafe memory ballast: ${args}" >&2
    return 1
  fi
  if [[ "${memory_limit}" != "1Gi" ]]; then
    echo "tempo runtime memory limit is not the required 1Gi: ${memory_limit}" >&2
    return 1
  fi
  for expected in \
    "block_retention: 6h" \
    "trace_idle_period: 10s" \
    "max_block_duration: 5m" \
    "max_concurrent_queries: 4" \
    "concurrent_jobs: 32"; do
    if [[ "${rendered_config}" != *"${expected}"* ]]; then
      echo "tempo runtime config is missing required bound: ${expected}" >&2
      return 1
    fi
  done
}

configure_alertmanager_webhook() {
  local configured_count=0
  local config_file
  local value

  for value in \
    "${TARGET_CLUSTER_ID}" \
    "${WORKSPACE_ID}" \
    "${MANAGEMENT_API_BASE_URL}" \
    "${ALERTMANAGER_AGENT_TOKEN}"; do
    if [[ -n "${value}" ]]; then
      configured_count=$((configured_count + 1))
    fi
  done
  if [[ "${configured_count}" -eq 0 ]]; then
    return
  fi
  if [[ "${configured_count}" -ne 4 ]]; then
    echo "Alertmanager webhook requires TARGET_CLUSTER_ID, WORKSPACE_ID, MANAGEMENT_API_BASE_URL, and ALERTMANAGER_AGENT_TOKEN" >&2
    return 1
  fi

  MANAGEMENT_API_BASE_URL="${MANAGEMENT_API_BASE_URL%/}"
  ALERTMANAGER_WEBHOOK_URL="$(
    MANAGEMENT_API_BASE_URL="${MANAGEMENT_API_BASE_URL}" \
    TARGET_CLUSTER_ID="${TARGET_CLUSTER_ID}" \
    WORKSPACE_ID="${WORKSPACE_ID}" \
    python3 - <<'PY'
import os
from urllib.parse import urlencode, urlsplit

base = os.environ["MANAGEMENT_API_BASE_URL"]
parsed = urlsplit(base)
if (
    parsed.scheme not in {"http", "https"}
    or not parsed.hostname
    or parsed.username is not None
    or parsed.password is not None
    or parsed.query
    or parsed.fragment
    or parsed.path != "/api"
):
    raise SystemExit("MANAGEMENT_API_BASE_URL must be a normalized http(s) URL ending in /api")
query = urlencode(
    {
        "cluster_id": os.environ["TARGET_CLUSTER_ID"],
        "workspace_id": os.environ["WORKSPACE_ID"],
    }
)
print(f"{base}/webhooks/alertmanager?{query}")
PY
  )"

  ensure_telemetry_temp_dir
  config_file="${TELEMETRY_TEMP_DIR}/alertmanager.json"
  (
    umask 077
    ALERTMANAGER_WEBHOOK_URL="${ALERTMANAGER_WEBHOOK_URL}" \
    ALERTMANAGER_AGENT_TOKEN="${ALERTMANAGER_AGENT_TOKEN}" \
    python3 - <<'PY' > "${config_file}"
import json
import os

json.dump(
    {
        "global": {"resolve_timeout": "5m"},
        "route": {
            "receiver": "kyro-rca",
            "group_by": [
                "alertname",
                "opsia_namespace",
                "opsia_resource_kind",
                "opsia_resource_name",
                "opsia_service",
                "opsia_sli",
                "opsia_symptom",
            ],
            "group_wait": "5s",
            "group_interval": "15s",
            "repeat_interval": "5m",
        },
        "receivers": [
            {
                "name": "kyro-rca",
                "webhook_configs": [
                    {
                        "url": os.environ["ALERTMANAGER_WEBHOOK_URL"],
                        "send_resolved": True,
                        "http_config": {
                            "authorization": {
                                "type": "Bearer",
                                "credentials": os.environ["ALERTMANAGER_AGENT_TOKEN"],
                            }
                        },
                    }
                ],
            }
        ],
    },
    fp=__import__("sys").stdout,
    separators=(",", ":"),
)
PY
  )
  chmod 600 "${config_file}"

  echo "==> configuring authenticated Alertmanager delivery to the management API"
  kubectl --context "${TARGET_CONTEXT}" -n "${TARGET_NAMESPACE}" \
    create secret generic "${ALERTMANAGER_CONFIG_SECRET}" \
    --from-file="alertmanager.yml=${config_file}" \
    --dry-run=client -o yaml \
    | kubectl --context "${TARGET_CONTEXT}" -n "${TARGET_NAMESPACE}" apply -f -

  PROMETHEUS_DYNAMIC_VALUES="${TELEMETRY_TEMP_DIR}/prometheus-alertmanager-values.yaml"
  (
    umask 077
    printf '%s\n' \
      "alertmanager:" \
      "  config:" \
      "    enabled: false" \
      "  extraSecretMounts:" \
      "    - name: kyro-alertmanager-config" \
      "      mountPath: /etc/alertmanager/alertmanager.yml" \
      "      subPath: alertmanager.yml" \
      "      secretName: ${ALERTMANAGER_CONFIG_SECRET}" \
      "      readOnly: true" \
      > "${PROMETHEUS_DYNAMIC_VALUES}"
  )
  chmod 600 "${PROMETHEUS_DYNAMIC_VALUES}"
}

require_prometheus_sli_rule_loaded() {
  local rules_json

  for _ in $(seq 1 30); do
    rules_json="$(
      kubectl --context "${TARGET_CONTEXT}" \
        get --raw="/api/v1/namespaces/${TARGET_NAMESPACE}/services/http:prometheus:http/proxy/api/v1/rules" \
        2>/dev/null || true
    )"
    if PROMETHEUS_RULES_JSON="${rules_json}" \
      PROMETHEUS_SLI_ALERT_NAME="${PROMETHEUS_SLI_ALERT_NAME}" \
      python3 - <<'PY'
import json
import os
import re

try:
    body = json.loads(os.environ["PROMETHEUS_RULES_JSON"])
except (KeyError, json.JSONDecodeError):
    raise SystemExit(1)
if body.get("status") != "success":
    raise SystemExit(1)
expected = os.environ["PROMETHEUS_SLI_ALERT_NAME"]
rules = (
    rule
    for group in body.get("data", {}).get("groups", [])
    for rule in group.get("rules", [])
)
rules = list(rules)
record_query = next(
    (
        str(rule.get("query") or "")
        for rule in rules
        if rule.get("name") == "opsia_sli_failure_ratio"
    ),
    "",
)
normalized_record_query = re.sub(r"\s+", "", record_query)
six_label_sum = "sumby(namespace,resource_kind,resource_name,service,sli,symptom)"
recording_rule_valid = (
    "opsia_sli_requests_total" in normalized_record_query
    and 'outcome="failure"' in normalized_record_query
    and normalized_record_query.count(six_label_sum) == 2
    and "pod" not in normalized_record_query
    and "instance" not in normalized_record_query
)
required_query_parts = (
    'namespace!=""',
    'resource_kind!=""',
    'resource_name!=""',
    'service!=""',
    'sli!=""',
    'symptom!=""',
    "> 0.2",
)
required_labels = (
    "opsia_namespace",
    "opsia_resource_kind",
    "opsia_resource_name",
    "opsia_service",
    "opsia_sli",
    "opsia_symptom",
)
required_annotations = (
    "opsia_observed_value",
    "opsia_threshold",
)
alert_rule_valid = any(
    rule.get("name") == expected
    and all(part in str(rule.get("query") or "") for part in required_query_parts)
    and all(str((rule.get("labels") or {}).get(key) or "").strip() for key in required_labels)
    and all(
        str((rule.get("annotations") or {}).get(key) or "").strip()
        for key in required_annotations
    )
    for rule in rules
)
raise SystemExit(0 if recording_rule_valid and alert_rule_valid else 1)
PY
    then
      return
    fi
    sleep 2
  done
  echo "Prometheus did not load SLI recording rule opsia_sli_failure_ratio and alert rule ${PROMETHEUS_SLI_ALERT_NAME}" >&2
  return 1
}

restart_alertmanager_for_webhook_config() {
  local statefulsets

  if [[ -z "${ALERTMANAGER_WEBHOOK_URL}" ]]; then
    return
  fi
  statefulsets="$(
    kubectl --context "${TARGET_CONTEXT}" -n "${TARGET_NAMESPACE}" \
      get statefulset \
      -l "app.kubernetes.io/instance=${PROMETHEUS_RELEASE},app.kubernetes.io/name=alertmanager" \
      -o name
  )"
  if [[ -z "${statefulsets}" ]]; then
    echo "Alertmanager StatefulSet is missing" >&2
    return 1
  fi
  while IFS= read -r statefulset; do
    # Secret subPath mounts are refreshed only on Pod recreation. Always restart
    # after the idempotent Secret apply so token rotation cannot leave stale auth.
    kubectl --context "${TARGET_CONTEXT}" -n "${TARGET_NAMESPACE}" \
      rollout restart "${statefulset}"
    kubectl --context "${TARGET_CONTEXT}" -n "${TARGET_NAMESPACE}" \
      rollout status "${statefulset}" --timeout=180s
  done <<< "${statefulsets}"
}

require_alertmanager_webhook_config() {
  local runtime_status
  local service_name
  local statefulsets

  if [[ -z "${ALERTMANAGER_WEBHOOK_URL}" ]]; then
    return
  fi

  kubectl --context "${TARGET_CONTEXT}" -n "${TARGET_NAMESPACE}" \
    get secret "${ALERTMANAGER_CONFIG_SECRET}" \
    -o jsonpath='{.data.alertmanager\.yml}' \
    | ALERTMANAGER_WEBHOOK_URL="${ALERTMANAGER_WEBHOOK_URL}" \
      ALERTMANAGER_AGENT_TOKEN="${ALERTMANAGER_AGENT_TOKEN}" \
      python3 -c '
import base64, hmac, json, os, sys
try:
    config = json.loads(base64.b64decode(sys.stdin.read().strip()).decode())
    hooks = next(
        receiver["webhook_configs"]
        for receiver in config["receivers"]
        if receiver["name"] == "kyro-rca"
    )
    hook = hooks[0]
    authorization = hook["http_config"]["authorization"]
    valid = (
        hook["url"] == os.environ["ALERTMANAGER_WEBHOOK_URL"]
        and hook["send_resolved"] is True
        and authorization["type"] == "Bearer"
        and hmac.compare_digest(
            authorization["credentials"],
            os.environ["ALERTMANAGER_AGENT_TOKEN"],
        )
    )
except (KeyError, ValueError, TypeError, StopIteration, json.JSONDecodeError):
    valid = False
raise SystemExit(0 if valid else 1)
'

  statefulsets="$(
    kubectl --context "${TARGET_CONTEXT}" -n "${TARGET_NAMESPACE}" \
      get statefulset \
      -l "app.kubernetes.io/instance=${PROMETHEUS_RELEASE},app.kubernetes.io/name=alertmanager" \
      -o json
  )"
ALERTMANAGER_STATEFULSETS="${statefulsets}" \
  ALERTMANAGER_CONFIG_SECRET="${ALERTMANAGER_CONFIG_SECRET}" \
  python3 - <<'PY'
import json
import os

body = json.loads(os.environ["ALERTMANAGER_STATEFULSETS"])
expected = os.environ["ALERTMANAGER_CONFIG_SECRET"]
for item in body.get("items", []):
    spec = item.get("spec", {}).get("template", {}).get("spec", {})
    secret_volumes = {
        volume.get("name")
        for volume in spec.get("volumes", [])
        if volume.get("secret", {}).get("secretName") == expected
    }
    config_mounts = [
        mount
        for container in spec.get("containers", [])
        for mount in container.get("volumeMounts", [])
        if mount.get("mountPath") == "/etc/alertmanager/alertmanager.yml"
    ]
    if (
        len(config_mounts) == 1
        and config_mounts[0].get("name") in secret_volumes
        and config_mounts[0].get("readOnly") is True
    ):
        raise SystemExit(0)
raise SystemExit(1)
PY

  service_name="$(
    kubectl --context "${TARGET_CONTEXT}" -n "${TARGET_NAMESPACE}" \
      get service \
      -l "app.kubernetes.io/instance=${PROMETHEUS_RELEASE},app.kubernetes.io/name=alertmanager" \
      -o jsonpath='{range .items[?(@.spec.clusterIP!="None")]}{.metadata.name}{"\n"}{end}'
  )"
  if [[ -z "${service_name}" ]]; then
    echo "Alertmanager Service is missing" >&2
    return 1
  fi
  for _ in $(seq 1 30); do
    runtime_status="$(
      kubectl --context "${TARGET_CONTEXT}" \
        get --raw="/api/v1/namespaces/${TARGET_NAMESPACE}/services/http:${service_name}:http/proxy/api/v2/status" \
        2>/dev/null || true
    )"
    if ALERTMANAGER_RUNTIME_STATUS="${runtime_status}" \
      python3 - <<'PY'
import json
import os
import re

try:
    status = json.loads(os.environ["ALERTMANAGER_RUNTIME_STATUS"])
    # Alertmanager returns its active config as redacted YAML even when the
    # mounted source is JSON. The Secret check above already verifies the exact
    # URL and bearer credential; this verifies that receiver is active without
    # adding a YAML package dependency to the operator terminal.
    config = status["config"]["original"]
    required = (
        r"(?m)^\s*receiver:\s+kyro-rca\s*$",
        r"(?m)^\s*-\s+name:\s+kyro-rca\s*$",
        r"(?m)^\s*(?:-\s+)?send_resolved:\s+true\s*$",
        r"(?m)^\s*authorization:\s*$",
        r"(?m)^\s*type:\s+Bearer\s*$",
        r"(?m)^\s*credentials:\s+<secret>\s*$",
        r"(?m)^\s*url:\s+<secret>\s*$",
    )
    valid = isinstance(config, str) and all(re.search(pattern, config) for pattern in required)
except (KeyError, ValueError, TypeError, json.JSONDecodeError):
    valid = False
raise SystemExit(0 if valid else 1)
PY
    then
      return
    fi
    sleep 2
  done
  echo "Alertmanager runtime did not load the authenticated webhook config" >&2
  return 1
}

existing_secret_value() {
  local secret_name="$1"
  local key="$2"

  {
    kubectl --context "${TARGET_CONTEXT}" -n "${TARGET_NAMESPACE}" \
      get secret "${secret_name}" -o "jsonpath={.data.${key}}" 2>/dev/null \
      || true
  } | python3 -c 'import base64, sys; data=sys.stdin.read().strip(); print(base64.b64decode(data).decode() if data else "")'
}

random_hex() {
  python3 -c 'import secrets; print(secrets.token_hex(32))'
}

ensure_target_minio() {
  if [[ -z "${MINIO_ROOT_PASSWORD}" ]]; then
    MINIO_ROOT_PASSWORD="$(existing_secret_value minio-secret MINIO_ROOT_PASSWORD)"
  fi
  if [[ -z "${MINIO_ROOT_PASSWORD}" ]]; then
    MINIO_ROOT_PASSWORD="$(random_hex)"
  fi

  echo "==> ensuring target MinIO credentials secret exists"
  kubectl --context "${TARGET_CONTEXT}" -n "${TARGET_NAMESPACE}" create secret generic minio-secret \
    --from-literal=MINIO_ROOT_USER="${MINIO_ROOT_USER}" \
    --from-literal=MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD}" \
    --dry-run=client -o yaml \
    | kubectl --context "${TARGET_CONTEXT}" -n "${TARGET_NAMESPACE}" apply -f -

  echo "==> installing target MinIO object store for Loki"
  kubectl --context "${TARGET_CONTEXT}" -n "${TARGET_NAMESPACE}" \
    delete job/minio-create-buckets --ignore-not-found --wait=true
  kubectl --context "${TARGET_CONTEXT}" -n "${TARGET_NAMESPACE}" apply -f "${TARGET_MINIO_MANIFEST}"
  kubectl --context "${TARGET_CONTEXT}" -n "${TARGET_NAMESPACE}" \
    rollout status statefulset/minio --timeout=180s
  kubectl --context "${TARGET_CONTEXT}" -n "${TARGET_NAMESPACE}" \
    wait --for=condition=complete job/minio-create-buckets --timeout=180s
}

need helm
need kubectl
need python3

if [[ -z "${TARGET_CONTEXT}" ]]; then
  TARGET_CONTEXT="$(kubectl config current-context)"
fi
require_env TARGET_CONTEXT

if [[ -n "${TELEMETRY_ASSET_BASE_URL}" ]]; then
  need curl
  ensure_telemetry_temp_dir
  for asset in prometheus.yaml loki.yaml tempo.yaml opentelemetry.yaml minio.yaml; do
    curl -fsSL "${TELEMETRY_ASSET_BASE_URL%/}/${asset}" \
      -o "${TELEMETRY_TEMP_DIR}/${asset}"
  done
  PROMETHEUS_VALUES="${TELEMETRY_TEMP_DIR}/prometheus.yaml"
  LOKI_VALUES="${TELEMETRY_TEMP_DIR}/loki.yaml"
  TEMPO_VALUES="${TELEMETRY_TEMP_DIR}/tempo.yaml"
  OTEL_VALUES="${TELEMETRY_TEMP_DIR}/opentelemetry.yaml"
  TARGET_MINIO_MANIFEST="${TELEMETRY_TEMP_DIR}/minio.yaml"
fi

echo "==> ensuring target namespace exists: ${TARGET_NAMESPACE}"
kubectl --context "${TARGET_CONTEXT}" create namespace "${TARGET_NAMESPACE}" \
  --dry-run=client -o yaml \
  | kubectl --context "${TARGET_CONTEXT}" apply -f -

configure_alertmanager_webhook
ensure_target_minio

echo "==> adding Helm repositories"
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update
helm repo add grafana https://grafana.github.io/helm-charts --force-update
helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts --force-update
helm repo update

echo "==> installing Prometheus"
prometheus_values_args=(--values "${PROMETHEUS_VALUES}")
if [[ -n "${PROMETHEUS_DYNAMIC_VALUES}" ]]; then
  prometheus_values_args+=(--values "${PROMETHEUS_DYNAMIC_VALUES}")
fi
helm upgrade --install "${PROMETHEUS_RELEASE}" prometheus-community/prometheus \
  --version "${PROMETHEUS_CHART_VERSION}" \
  --kube-context "${TARGET_CONTEXT}" \
  --namespace "${TARGET_NAMESPACE}" \
  "${prometheus_values_args[@]}" \
  --wait \
  --timeout 5m
restart_alertmanager_for_webhook_config

echo "==> installing Loki"
helm upgrade --install "${LOKI_RELEASE}" grafana/loki \
  --version "${LOKI_CHART_VERSION}" \
  --kube-context "${TARGET_CONTEXT}" \
  --namespace "${TARGET_NAMESPACE}" \
  --values "${LOKI_VALUES}" \
  --wait \
  --timeout 5m

echo "==> installing Tempo"
helm upgrade --install "${TEMPO_RELEASE}" grafana/tempo \
  --version "${TEMPO_CHART_VERSION}" \
  --kube-context "${TARGET_CONTEXT}" \
  --namespace "${TARGET_NAMESPACE}" \
  --values "${TEMPO_VALUES}" \
  --wait \
  --timeout 5m

echo "==> installing OpenTelemetry Collector"
helm upgrade --install "${OTEL_RELEASE}" open-telemetry/opentelemetry-collector \
  --version "${OTEL_CHART_VERSION}" \
  --kube-context "${TARGET_CONTEXT}" \
  --namespace "${TARGET_NAMESPACE}" \
  --values "${OTEL_VALUES}" \
  --wait \
  --timeout 5m

echo "==> waiting for telemetry rollouts"
wait_rollouts deployment "app.kubernetes.io/instance=${PROMETHEUS_RELEASE}" 180s
wait_rollouts statefulset "app.kubernetes.io/instance=${PROMETHEUS_RELEASE}" 180s
wait_rollouts daemonset "app.kubernetes.io/instance=${PROMETHEUS_RELEASE}" 180s
wait_rollouts deployment "app.kubernetes.io/instance=${LOKI_RELEASE}" 180s
wait_rollouts statefulset "app.kubernetes.io/instance=${LOKI_RELEASE}" 180s
wait_rollouts daemonset "app.kubernetes.io/instance=${LOKI_RELEASE}" 180s
wait_rollouts deployment "app.kubernetes.io/instance=${TEMPO_RELEASE}" 180s
wait_rollouts statefulset "app.kubernetes.io/instance=${TEMPO_RELEASE}" 180s
wait_rollouts daemonset "app.kubernetes.io/instance=${TEMPO_RELEASE}" 180s
wait_rollouts deployment "app.kubernetes.io/instance=${OTEL_RELEASE}" 180s
wait_rollouts statefulset "app.kubernetes.io/instance=${OTEL_RELEASE}" 180s
wait_rollouts daemonset "app.kubernetes.io/instance=${OTEL_RELEASE}" 180s

echo "==> verifying required telemetry services and workloads"
require_release_workload "${PROMETHEUS_RELEASE}"
require_release_workload "${LOKI_RELEASE}"
require_release_workload "${TEMPO_RELEASE}"
require_release_workload "${OTEL_RELEASE}"
require_tempo_runtime_bounds
require_service_endpoints prometheus
require_prometheus_sli_rule_loaded
require_alertmanager_webhook_config
require_service_endpoints loki-gateway
require_service_endpoints tempo
require_service_endpoints opentelemetry-collector

echo
echo "telemetry is installed in context ${TARGET_CONTEXT}, namespace ${TARGET_NAMESPACE}."
