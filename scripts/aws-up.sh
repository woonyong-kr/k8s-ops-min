#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT_DIR}/scripts/lib/env.sh"

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

PROJECT_SLUG="${PROJECT_SLUG:-kubernetes-ops}"
AWS_REGION="${AWS_REGION:-us-east-1}"
MGMT_CLUSTER="${MGMT_CLUSTER:-${PROJECT_SLUG}-mgmt}"
TARGET_CLUSTER_1="${TARGET_CLUSTER_1:-${PROJECT_SLUG}-target-a}"
TARGET_CLUSTER_2="${TARGET_CLUSTER_2:-${PROJECT_SLUG}-target-b}"
TARGET_CLUSTER_ID_1="${TARGET_CLUSTER_ID_1:-${TARGET_CLUSTER_1}}"
TARGET_CLUSTER_ID_2="${TARGET_CLUSTER_ID_2:-${TARGET_CLUSTER_2}}"

if [[ "${MGMT_CLUSTER}" == "management" ]]; then
  MGMT_CLUSTER="${AWS_MGMT_CLUSTER:-${PROJECT_SLUG}-mgmt}"
fi

MGMT_DISPLAY_NAME="${MGMT_DISPLAY_NAME:-${MGMT_CLUSTER}}"
TARGET_1_DISPLAY_NAME="${TARGET_1_DISPLAY_NAME:-${TARGET_CLUSTER_1}}"
TARGET_2_DISPLAY_NAME="${TARGET_2_DISPLAY_NAME:-${TARGET_CLUSTER_2}}"

ECR_REPO="${ECR_REPO:-${PROJECT_SLUG}-service}"
CONSOLE_ECR_REPO="${CONSOLE_ECR_REPO:-${PROJECT_SLUG}-console}"
IMAGE_TAG="${IMAGE_TAG:-$(git -C "${ROOT_DIR}" rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}"
IMAGE_NAME="${IMAGE_NAME:-}"
CONSOLE_IMAGE_NAME="${CONSOLE_IMAGE_NAME:-}"
DOCKER_PLATFORM="${DOCKER_PLATFORM:-linux/amd64}"

MGMT_NODE_TYPE="${MGMT_NODE_TYPE:-t3.xlarge}"
MGMT_NODES="${MGMT_NODES:-2}"
TARGET_NODE_TYPE="${TARGET_NODE_TYPE:-t3.large}"
TARGET_NODES="${TARGET_NODES:-2}"
NODE_VOLUME_SIZE_GB="${NODE_VOLUME_SIZE_GB:-50}"

POSTGRES_USER="${POSTGRES_USER:-service}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
POSTGRES_DB="${POSTGRES_DB:-service}"
DATABASE_URL="${DATABASE_URL:-}"
DATABASE_STARTUP_MODE="${DATABASE_STARTUP_MODE:-verify}"
NATS_URL="${NATS_URL:-nats://nats:4222}"
REDIS_URL="${REDIS_URL:-redis://redis:6379/0}"
MINIO_ROOT_USER="${MINIO_ROOT_USER:-minioadmin}"
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-}"

GITHUB_WEBHOOK_SECRET="${GITHUB_WEBHOOK_SECRET:-}"
FILTER_CURSOR_SIGNING_KEY="${FILTER_CURSOR_SIGNING_KEY:-}"
RCA_TEST_RUNS_ENABLED="${RCA_TEST_RUNS_ENABLED:-1}"
RCA_TEST_RUNS_TOKEN="${RCA_TEST_RUNS_TOKEN:-}"
TEST_FIXTURE_PURGE_ENABLED="${TEST_FIXTURE_PURGE_ENABLED:-1}"
TRUSTED_PROXY_AUTH_SECRET="${TRUSTED_PROXY_AUTH_SECRET:-}"
TRUSTED_PROXY_AUTH_USER_ID="${TRUSTED_PROXY_AUTH_USER_ID:-}"
TRUSTED_PROXY_AUTH_WORKSPACE_ID="${TRUSTED_PROXY_AUTH_WORKSPACE_ID:-default}"
METRICS_TOKEN="${METRICS_TOKEN:-}"
API_ROOT_PATH="${API_ROOT_PATH:-/api}"
GITHUB_REPO="${GITHUB_REPO:-$(default_github_repo)}"
GITHUB_BRANCH="${GITHUB_BRANCH:-dev}"
SMOKE_MANIFEST_PATH="${SMOKE_MANIFEST_PATH:-src/samples/smoke/deploy.yaml}"
MANIFEST_PATH="${MANIFEST_PATH:-}"
GITHUB_TOKEN="${GITHUB_TOKEN:-}"
GITHUB_API_BASE="${GITHUB_API_BASE:-https://api.github.com}"
GIT_MANIFEST_SOURCE_MODE="${GIT_MANIFEST_SOURCE_MODE:-remote}"
GIT_LOCAL_MANIFEST_ENABLED="${GIT_LOCAL_MANIFEST_ENABLED:-0}"
GIT_CHECKOUT_CACHE_ENABLED="${GIT_CHECKOUT_CACHE_ENABLED:-1}"
GIT_CHECKOUT_CACHE_REQUIRED="${GIT_CHECKOUT_CACHE_REQUIRED:-0}"
GIT_CACHE_MAX_REPOS="${GIT_CACHE_MAX_REPOS:-8}"
GIT_CACHE_MAX_BYTES="${GIT_CACHE_MAX_BYTES:-1073741824}"
GIT_REMOTE_MANIFEST_ENABLED="${GIT_REMOTE_MANIFEST_ENABLED:-1}"
GIT_REMOTE_MANIFEST_REQUIRED="${GIT_REMOTE_MANIFEST_REQUIRED:-1}"
GITOPS_REQUIRE_APPROVED_SNAPSHOT="${GITOPS_REQUIRE_APPROVED_SNAPSHOT:-1}"
GITHUB_MANIFEST_TIMEOUT_SECONDS="${GITHUB_MANIFEST_TIMEOUT_SECONDS:-5}"
COMMAND_JANITOR_INTERVAL_SECONDS="${COMMAND_JANITOR_INTERVAL_SECONDS:-15}"
MAIL_DELIVERY_MODE="${MAIL_DELIVERY_MODE:-smtp}"
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
PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-}"
PUBLIC_API_BASE_URL="${PUBLIC_API_BASE_URL:-}"
PUBLIC_MANAGEMENT_BASE_URL="${PUBLIC_MANAGEMENT_BASE_URL:-${PUBLIC_API_BASE_URL}}"
PRINT_GENERATED_ADMIN_PASSWORD="${PRINT_GENERATED_ADMIN_PASSWORD:-0}"
RUN_SMOKE="${RUN_SMOKE:-1}"
SKIP_LB_HEALTH_WAIT="${SKIP_LB_HEALTH_WAIT:-0}"
CREATE_CLUSTERS="${CREATE_CLUSTERS:-1}"
ENSURE_EBS_CSI="${ENSURE_EBS_CSI:-1}"
BOOTSTRAP_ADMIN="${BOOTSTRAP_ADMIN:-1}"
REGISTER_TARGETS="${REGISTER_TARGETS:-1}"
CONFIGURE_ROUTE53="${CONFIGURE_ROUTE53:-0}"
CONFIGURE_CLOUDFLARE="${CONFIGURE_CLOUDFLARE:-0}"
CUSTOM_DOMAIN="${CUSTOM_DOMAIN:-}"
ROUTE53_ZONE_NAME="${ROUTE53_ZONE_NAME:-}"
CLOUDFLARE_ZONE_NAME="${CLOUDFLARE_ZONE_NAME:-}"
CLOUDFLARE_ZONE_ID="${CLOUDFLARE_ZONE_ID:-}"
CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN:-}"
CLOUDFLARE_PROXIED="${CLOUDFLARE_PROXIED:-1}"
INSTALL_NODE_COLLECTOR="${INSTALL_NODE_COLLECTOR:-true}"
INSTALL_TELEMETRY="${INSTALL_TELEMETRY:-true}"
EVIDENCE_INTERVAL_SECONDS="${EVIDENCE_INTERVAL_SECONDS:-15}"
LOKI_BASE_URL="${LOKI_BASE_URL:-http://loki-gateway.target.svc}"

RUNTIME_DIR="$(mktemp -d "${ROOT_DIR}/.aws-up.XXXXXX")"
PORT_FORWARD_PID=""

case "${RUN_SMOKE}" in
  1|true|TRUE|yes|YES|on|ON)
    RUN_SMOKE="1"
    if [ -z "${MANIFEST_PATH}" ]; then
      MANIFEST_PATH="${SMOKE_MANIFEST_PATH}"
    fi
    ;;
  0|false|FALSE|no|NO|off|OFF)
    RUN_SMOKE="0"
    ;;
  *)
    echo "RUN_SMOKE must be a boolean value" >&2
    exit 1
    ;;
esac
if [ -z "${MANIFEST_PATH}" ]; then
  echo "MANIFEST_PATH is required for AWS deployment; set SMOKE_MANIFEST_PATH only for smoke-only runs" >&2
  exit 1
fi
GENERATED_AUTH_PASSWORD="0"
CUSTOM_DOMAIN_CONFIGURED="0"

cleanup() {
  if [[ -n "${PORT_FORWARD_PID}" ]]; then
    kill "${PORT_FORWARD_PID}" >/dev/null 2>&1 || true
  fi
  rm -rf "${RUNTIME_DIR}"
}
trap cleanup EXIT

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

log() {
  printf '==> %s\n' "$1"
}

require_domain_config() {
  local provider="$1"
  local domain="$2"
  local zone="$3"
  if [[ -z "${domain}" || -z "${zone}" ]]; then
    echo "${provider} DNS requires CUSTOM_DOMAIN and zone name env values" >&2
    exit 1
  fi
}

need aws
if [[ "${CREATE_CLUSTERS}" == "1" || "${ENSURE_EBS_CSI}" == "1" ]]; then
  need eksctl
fi
need kubectl
need docker
need curl
need openssl
need python3
if [[ "${BOOTSTRAP_ADMIN}" == "1" ]]; then
  need uv
fi

if [[ "${BOOTSTRAP_ADMIN}" == "1" || "${REGISTER_TARGETS}" == "1" || "${RUN_SMOKE}" == "1" ]]; then
  require_env AUTH_EMAIL
fi

if [[ -z "${AUTH_PASSWORD}" && ( "${BOOTSTRAP_ADMIN}" == "1" || "${REGISTER_TARGETS}" == "1" || "${RUN_SMOKE}" == "1" ) ]]; then
  AUTH_PASSWORD="$(generate_password)"
  GENERATED_AUTH_PASSWORD="1"
fi

aws_account_id() {
  aws sts get-caller-identity --query Account --output text
}

cluster_exists() {
  aws eks describe-cluster \
    --region "${AWS_REGION}" \
    --name "$1" >/dev/null 2>&1
}

existing_secret_value() {
  local context="$1"
  local secret_name="$2"
  local key="$3"
  { kubectl --context "${context}" -n management get secret "${secret_name}" \
    -o "jsonpath={.data.${key}}" 2>/dev/null || true; } \
    | python3 -c 'import base64, sys; data=sys.stdin.read().strip(); print(base64.b64decode(data).decode() if data else "")'
}

existing_config_value() {
  local context="$1"
  local config_name="$2"
  local key="$3"
  kubectl --context "${context}" -n management get configmap "${config_name}" \
    -o "jsonpath={.data.${key}}" 2>/dev/null || true
}

valid_github_token() {
  local token="$1"
  [[ -n "${token}" ]] || return 1
  [[ "${token}" != *"<"* && "${token}" != *">"* ]] || return 1
  [[ "${token}" != *PLACEHOLDER* && "${token}" != *TOKEN_HERE* ]] || return 1
  LC_ALL=C grep -q '^[[:print:]]\+$' <<<"${token}"
}

pgbouncer_auth_hash() {
  python3 - "$POSTGRES_USER" "$POSTGRES_PASSWORD" <<'PY'
import hashlib
import sys

user, password = sys.argv[1], sys.argv[2]
print("md5" + hashlib.md5((password + user).encode()).hexdigest())
PY
}

render_eksctl_config() {
  local cluster_name="$1"
  local display_name="$2"
  local node_type="$3"
  local desired_nodes="$4"
  local role="$5"
  local output="$6"
  local min_nodes="1"
  local max_nodes="$((desired_nodes + 1))"

  cat >"${output}" <<YAML
apiVersion: eksctl.io/v1alpha5
kind: ClusterConfig
metadata:
  name: ${cluster_name}
  region: ${AWS_REGION}
  tags:
    DisplayName: "${display_name}"
    Project: "${PROJECT_SLUG}"
    Role: "${role}"
iam:
  withOIDC: true
managedNodeGroups:
  - name: ${cluster_name}-ng
    instanceType: ${node_type}
    desiredCapacity: ${desired_nodes}
    minSize: ${min_nodes}
    maxSize: ${max_nodes}
    volumeSize: ${NODE_VOLUME_SIZE_GB}
    labels:
      role: "${role}"
    tags:
      DisplayName: "${display_name}"
      Project: "${PROJECT_SLUG}"
      Role: "${role}"
YAML
}

tag_cluster() {
  local cluster_name="$1"
  local display_name="$2"
  local role="$3"
  local arn
  arn="$(aws eks describe-cluster \
    --region "${AWS_REGION}" \
    --name "${cluster_name}" \
    --query 'cluster.arn' \
    --output text)"
  aws eks tag-resource \
    --region "${AWS_REGION}" \
    --resource-arn "${arn}" \
    --tags "DisplayName=${display_name},Project=${PROJECT_SLUG},Role=${role}" >/dev/null
}

tag_node_instances() {
  local cluster_name="$1"
  local display_name="$2"
  local role="$3"
  local instance_ids

  instance_ids="$(
    aws ec2 describe-instances \
      --region "${AWS_REGION}" \
      --filters \
        "Name=tag:eks:cluster-name,Values=${cluster_name}" \
        "Name=instance-state-name,Values=pending,running" \
      --query 'Reservations[].Instances[].InstanceId' \
      --output text
  )"
  if [[ -z "${instance_ids}" ]]; then
    log "no EC2 node instances found yet for ${cluster_name}"
    return
  fi
  aws ec2 create-tags \
    --region "${AWS_REGION}" \
    --resources ${instance_ids} \
    --tags \
      "Key=Name,Value=${display_name}" \
      "Key=Project,Value=${PROJECT_SLUG}" \
      "Key=Role,Value=${role}" >/dev/null
}

ensure_cluster() {
  local cluster_name="$1"
  local display_name="$2"
  local node_type="$3"
  local desired_nodes="$4"
  local role="$5"
  local config_path="${RUNTIME_DIR}/${cluster_name}.eksctl.yaml"

  if cluster_exists "${cluster_name}"; then
    log "EKS cluster already exists: ${cluster_name}"
  else
    log "creating EKS cluster ${cluster_name} (${display_name})"
    render_eksctl_config "${cluster_name}" "${display_name}" "${node_type}" "${desired_nodes}" "${role}" "${config_path}"
    eksctl create cluster -f "${config_path}"
  fi

  aws eks wait cluster-active --region "${AWS_REGION}" --name "${cluster_name}"
  aws eks update-kubeconfig \
    --region "${AWS_REGION}" \
    --name "${cluster_name}" \
    --alias "${cluster_name}" >/dev/null
  tag_cluster "${cluster_name}" "${display_name}" "${role}"
  tag_node_instances "${cluster_name}" "${display_name}" "${role}"
}

configure_existing_cluster_context() {
  local cluster_name="$1"
  if ! cluster_exists "${cluster_name}"; then
    echo "EKS cluster does not exist: ${cluster_name}" >&2
    exit 1
  fi
  aws eks wait cluster-active --region "${AWS_REGION}" --name "${cluster_name}"
  aws eks update-kubeconfig \
    --region "${AWS_REGION}" \
    --name "${cluster_name}" \
    --alias "${cluster_name}" >/dev/null
}

ensure_ecr_repository() {
  local repo="$1"

  log "ensuring ECR repository: ${repo}"
  aws ecr describe-repositories \
    --region "${AWS_REGION}" \
    --repository-names "${repo}" >/dev/null 2>&1 \
    || aws ecr create-repository \
      --region "${AWS_REGION}" \
      --repository-name "${repo}" >/dev/null
}

build_and_push_image() {
  local account_id="$1"
  local repo="$2"
  local image_var_name="$3"
  local dockerfile="$4"
  local context_dir="$5"
  local label="$6"
  local registry="${account_id}.dkr.ecr.${AWS_REGION}.amazonaws.com"
  local repo_uri="${registry}/${repo}"
  local image_name="${!image_var_name}"

  ensure_ecr_repository "${repo}"

  log "logging in to ECR"
  aws ecr get-login-password --region "${AWS_REGION}" \
    | docker login --username AWS --password-stdin "${registry}" >/dev/null

  if [[ -z "${image_name}" ]]; then
    image_name="${repo_uri}:${IMAGE_TAG}"
    printf -v "${image_var_name}" "%s" "${image_name}"
  fi

  log "building Docker image (${label}): ${image_name}"
  docker build --platform "${DOCKER_PLATFORM}" -f "${dockerfile}" -t "${image_name}" "${context_dir}"

  log "pushing Docker image (${label}): ${image_name}"
  docker push "${image_name}"
}

ensure_ecr_images() {
  local account_id="$1"

  build_and_push_image \
    "${account_id}" \
    "${ECR_REPO}" \
    IMAGE_NAME \
    "${ROOT_DIR}/src/services/Dockerfile" \
    "${ROOT_DIR}" \
    "service"

  build_and_push_image \
    "${account_id}" \
    "${CONSOLE_ECR_REPO}" \
    CONSOLE_IMAGE_NAME \
    "${ROOT_DIR}/frontend/Dockerfile" \
    "${ROOT_DIR}/frontend" \
    "console"
}

ensure_ebs_csi() {
  local role_name="${MGMT_CLUSTER}-ebs-csi-driver"
  local policy_arn="arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicyV2"
  local role_arn

  if ! aws iam get-policy --policy-arn "${policy_arn}" >/dev/null 2>&1; then
    policy_arn="arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
  fi

  if ! aws iam get-role --role-name "${role_name}" >/dev/null 2>&1; then
    log "creating EBS CSI IAM role: ${role_name}"
    eksctl create iamserviceaccount \
      --name ebs-csi-controller-sa \
      --namespace kube-system \
      --cluster "${MGMT_CLUSTER}" \
      --region "${AWS_REGION}" \
      --role-name "${role_name}" \
      --role-only \
      --attach-policy-arn "${policy_arn}" \
      --approve
  fi

  role_arn="$(aws iam get-role --role-name "${role_name}" --query 'Role.Arn' --output text)"

  if aws eks describe-addon \
    --region "${AWS_REGION}" \
    --cluster-name "${MGMT_CLUSTER}" \
    --addon-name aws-ebs-csi-driver >/dev/null 2>&1; then
    log "EBS CSI addon already exists"
  else
    log "creating EBS CSI addon"
    aws eks create-addon \
      --region "${AWS_REGION}" \
      --cluster-name "${MGMT_CLUSTER}" \
      --addon-name aws-ebs-csi-driver \
      --service-account-role-arn "${role_arn}" >/dev/null
  fi

  aws eks wait addon-active \
    --region "${AWS_REGION}" \
    --cluster-name "${MGMT_CLUSTER}" \
    --addon-name aws-ebs-csi-driver
}

ensure_default_storage_class() {
  log "ensuring gp3 default StorageClass"
  while IFS= read -r storage_class; do
    [[ -n "${storage_class}" ]] || continue
    kubectl --context "${MGMT_CLUSTER}" annotate --overwrite \
      "${storage_class}" storageclass.kubernetes.io/is-default-class=false >/dev/null 2>&1 || true
  done < <(kubectl --context "${MGMT_CLUSTER}" get storageclass -o name 2>/dev/null || true)

  cat <<'YAML' | kubectl --context "${MGMT_CLUSTER}" apply -f -
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: ebs-gp3
  annotations:
    storageclass.kubernetes.io/is-default-class: "true"
provisioner: ebs.csi.aws.com
volumeBindingMode: WaitForFirstConsumer
allowVolumeExpansion: true
parameters:
  type: gp3
  encrypted: "true"
YAML
}

create_management_runtime() {
  local context="${MGMT_CLUSTER}"
  local pgbouncer_hash

  kubectl --context "${context}" apply -f "${ROOT_DIR}/deploy/management/namespace.yaml"

  if [[ -z "${POSTGRES_PASSWORD}" ]]; then
    POSTGRES_PASSWORD="$(existing_secret_value "${context}" postgresql-secret POSTGRES_PASSWORD)"
  fi
  if [[ -z "${POSTGRES_PASSWORD}" ]]; then
    POSTGRES_PASSWORD="$(openssl rand -hex 24)"
  fi
  if [[ -z "${DATABASE_URL}" ]]; then
    DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@pgbouncer:6432/${POSTGRES_DB}"
  fi

  if [[ -z "${MINIO_ROOT_PASSWORD}" ]]; then
    MINIO_ROOT_PASSWORD="$(existing_secret_value "${context}" minio-secret MINIO_ROOT_PASSWORD)"
  fi
  if [[ -z "${MINIO_ROOT_PASSWORD}" ]]; then
    MINIO_ROOT_PASSWORD="$(openssl rand -hex 32)"
  fi

  if [[ -z "${GITHUB_WEBHOOK_SECRET}" ]]; then
    GITHUB_WEBHOOK_SECRET="$(existing_secret_value "${context}" management-runtime-secret GITHUB_WEBHOOK_SECRET)"
  fi
  if [[ -z "${GITHUB_WEBHOOK_SECRET}" ]]; then
    GITHUB_WEBHOOK_SECRET="$(openssl rand -hex 32)"
  fi
  if [[ -z "${FILTER_CURSOR_SIGNING_KEY}" ]]; then
    FILTER_CURSOR_SIGNING_KEY="$(existing_secret_value "${context}" management-runtime-secret FILTER_CURSOR_SIGNING_KEY)"
  fi
  if [[ ${#FILTER_CURSOR_SIGNING_KEY} -lt 32 ]]; then
    FILTER_CURSOR_SIGNING_KEY="$(openssl rand -hex 32)"
  fi
  if [[ -z "${RCA_TEST_RUNS_TOKEN}" ]]; then
    RCA_TEST_RUNS_TOKEN="$(existing_secret_value "${context}" management-runtime-secret RCA_TEST_RUNS_TOKEN)"
  fi
  if [[ -z "${RCA_TEST_RUNS_TOKEN}" ]]; then
    RCA_TEST_RUNS_TOKEN="$(openssl rand -hex 32)"
  fi
  if [[ -z "${TRUSTED_PROXY_AUTH_SECRET}" ]]; then
    TRUSTED_PROXY_AUTH_SECRET="$(existing_secret_value "${context}" management-runtime-secret TRUSTED_PROXY_AUTH_SECRET)"
  fi
  if [[ ${#TRUSTED_PROXY_AUTH_SECRET} -lt 32 ]]; then
    TRUSTED_PROXY_AUTH_SECRET="$(openssl rand -hex 32)"
  fi
  if [[ -z "${TRUSTED_PROXY_AUTH_USER_ID}" ]]; then
    TRUSTED_PROXY_AUTH_USER_ID="$(existing_config_value "${context}" management-runtime-config TRUSTED_PROXY_AUTH_USER_ID)"
  fi
  if [[ -z "${TRUSTED_PROXY_AUTH_USER_ID}" ]]; then
    if [[ -z "${AUTH_EMAIL}" ]]; then
      echo "TRUSTED_PROXY_AUTH_USER_ID 또는 AUTH_EMAIL이 필요합니다" >&2
      return 1
    fi
    TRUSTED_PROXY_AUTH_USER_ID="$(python3 - "${PROJECT_SLUG}" "${AUTH_EMAIL}" <<'PY'
import sys
import uuid

project_slug, email = sys.argv[1:]
print("user-" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"{project_slug}:{email.strip().lower()}")))
PY
)"
  fi
  if [[ -z "${METRICS_TOKEN}" ]]; then
    METRICS_TOKEN="$(existing_secret_value "${context}" management-runtime-secret METRICS_TOKEN)"
  fi
  if [[ -z "${METRICS_TOKEN}" ]]; then
    METRICS_TOKEN="$(openssl rand -hex 32)"
  fi

  for key in \
    LLM_API_KEY \
    OPENAI_API_KEY \
    OPENAI_COMPATIBLE_API_KEY \
    ANTHROPIC_API_KEY \
    GEMINI_API_KEY \
    GOOGLE_API_KEY; do
    if [[ -z "${!key}" ]]; then
      printf -v "${key}" "%s" "$(existing_secret_value "${context}" management-runtime-secret "${key}")"
    fi
  done

  kubectl --context "${context}" -n management create secret generic postgresql-secret \
    --from-literal=POSTGRES_USER="${POSTGRES_USER}" \
    --from-literal=POSTGRES_PASSWORD="${POSTGRES_PASSWORD}" \
    --from-literal=POSTGRES_DB="${POSTGRES_DB}" \
    --dry-run=client -o yaml | kubectl --context "${context}" apply -f -

  kubectl --context "${context}" -n management create secret generic minio-secret \
    --from-literal=MINIO_ROOT_USER="${MINIO_ROOT_USER}" \
    --from-literal=MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD}" \
    --dry-run=client -o yaml | kubectl --context "${context}" apply -f -

  pgbouncer_hash="$(pgbouncer_auth_hash)"
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
  printf '"%s" "%s"\n' "${POSTGRES_USER}" "${pgbouncer_hash}" >"${RUNTIME_DIR}/userlist.txt"

  kubectl --context "${context}" -n management create secret generic pgbouncer-config \
    --from-file=pgbouncer.ini="${RUNTIME_DIR}/pgbouncer.ini" \
    --from-file=userlist.txt="${RUNTIME_DIR}/userlist.txt" \
    --dry-run=client -o yaml | kubectl --context "${context}" apply -f -

  local effective_public_base_url="${PUBLIC_BASE_URL}"
  if [[ -z "${effective_public_base_url}" && -n "${CUSTOM_DOMAIN}" ]]; then
    local public_scheme="http"
    case "${CLOUDFLARE_PROXIED}" in
      1|true|TRUE|yes|YES|on|ON) public_scheme="https" ;;
    esac
    effective_public_base_url="${public_scheme}://${CUSTOM_DOMAIN}"
  fi

  kubectl --context "${context}" -n management create configmap management-runtime-config \
    --from-literal=NATS_URL="${NATS_URL}" \
    --from-literal=REDIS_URL="${REDIS_URL}" \
    --from-literal=DATABASE_STARTUP_MODE="${DATABASE_STARTUP_MODE}" \
    --from-literal=RCA_TEST_RUNS_ENABLED="${RCA_TEST_RUNS_ENABLED}" \
    --from-literal=TEST_FIXTURE_PURGE_ENABLED="${TEST_FIXTURE_PURGE_ENABLED}" \
    --from-literal=TRUSTED_PROXY_AUTH_USER_ID="${TRUSTED_PROXY_AUTH_USER_ID}" \
    --from-literal=TRUSTED_PROXY_AUTH_WORKSPACE_ID="${TRUSTED_PROXY_AUTH_WORKSPACE_ID}" \
    --from-literal=API_ROOT_PATH="${API_ROOT_PATH}" \
    --from-literal=MANAGEMENT_CLUSTER_ID="${MGMT_CLUSTER}" \
    --from-literal=OUTBOX_RELAY_BATCH="${OUTBOX_RELAY_BATCH:-10}" \
    --from-literal=MANAGEMENT_BASE_URL="http://api-gateway:8000" \
    --from-literal=PUBLIC_BASE_URL="${effective_public_base_url}" \
    --from-literal=PUBLIC_API_BASE_URL="${PUBLIC_API_BASE_URL}" \
    --from-literal=PUBLIC_MANAGEMENT_BASE_URL="${PUBLIC_MANAGEMENT_BASE_URL}" \
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
    --dry-run=client -o yaml | kubectl --context "${context}" apply -f -

  local secret_args=(
    --from-literal=DATABASE_URL="${DATABASE_URL}"
    # 롱폴 웨이크업(LISTEN/NOTIFY) 전용 직결 URL — pgbouncer(transaction pooling)
    # 경유로는 LISTEN 이 불가해 postgres 에 직접 붙는다(게이트웨이당 커넥션 1개).
    --from-literal=COMMAND_NOTIFY_DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgresql:5432/${POSTGRES_DB}"
    --from-literal=GITHUB_WEBHOOK_SECRET="${GITHUB_WEBHOOK_SECRET}"
    --from-literal=FILTER_CURSOR_SIGNING_KEY="${FILTER_CURSOR_SIGNING_KEY}"
    --from-literal=RCA_TEST_RUNS_TOKEN="${RCA_TEST_RUNS_TOKEN}"
    --from-literal=TRUSTED_PROXY_AUTH_SECRET="${TRUSTED_PROXY_AUTH_SECRET}"
    --from-literal=METRICS_TOKEN="${METRICS_TOKEN}"
  )
  if valid_github_token "${GITHUB_TOKEN}"; then
    secret_args+=(--from-literal=GITHUB_TOKEN="${GITHUB_TOKEN}")
  fi
  for key in \
    LLM_API_KEY \
    OPENAI_API_KEY \
    OPENAI_COMPATIBLE_API_KEY \
    ANTHROPIC_API_KEY \
    GEMINI_API_KEY \
    GOOGLE_API_KEY \
    SMTP_USERNAME \
    SMTP_PASSWORD; do
    if [[ -n "${!key}" ]]; then
      secret_args+=(--from-literal="${key}=${!key}")
    fi
  done

  kubectl --context "${context}" -n management create secret generic management-runtime-secret \
    "${secret_args[@]}" \
    --dry-run=client -o yaml | kubectl --context "${context}" apply -f -
}

bootstrap_management_schema() {
  log "bootstrapping management database schema"
  kubectl --context "${MGMT_CLUSTER}" apply -f "${ROOT_DIR}/deploy/management/storage.yaml"
  kubectl --context "${MGMT_CLUSTER}" apply -f "${ROOT_DIR}/deploy/management/pgbouncer.yaml"
  management_rollout_status statefulset/postgresql
  management_rollout_status deployment/pgbouncer

  kubectl --context "${MGMT_CLUSTER}" -n management delete job/management-schema-bootstrap \
    --ignore-not-found --wait=true
  cat <<EOF | kubectl --context "${MGMT_CLUSTER}" apply -f -
apiVersion: batch/v1
kind: Job
metadata:
  name: management-schema-bootstrap
  namespace: management
spec:
  backoffLimit: 3
  activeDeadlineSeconds: 300
  ttlSecondsAfterFinished: 3600
  template:
    metadata:
      labels:
        app: management-schema-bootstrap
    spec:
      restartPolicy: Never
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: schema
          image: ${IMAGE_NAME}
          imagePullPolicy: IfNotPresent
          command:
            - python
            - -c
            - |
              from packages.storage.database import Database

              db = Database()
              db.init()
              db.verify_schema()
          envFrom:
            - configMapRef:
                name: management-runtime-config
            - secretRef:
                name: management-runtime-secret
          resources:
            requests:
              cpu: 25m
              memory: 64Mi
            limits:
              cpu: "1"
              memory: 512Mi
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: ["ALL"]
EOF
  if ! kubectl --context "${MGMT_CLUSTER}" -n management wait \
    --for=condition=complete job/management-schema-bootstrap --timeout=300s; then
    kubectl --context "${MGMT_CLUSTER}" -n management describe job/management-schema-bootstrap || true
    kubectl --context "${MGMT_CLUSTER}" -n management logs job/management-schema-bootstrap \
      --all-containers=true --tail=200 || true
    return 1
  fi
}

management_rollout_resources() {
  local attempt
  for attempt in 1 2 3; do
    if kubectl --context "${MGMT_CLUSTER}" -n management get statefulset,deploy -o name; then
      return 0
    fi
    if [[ "${attempt}" != "3" ]]; then
      echo "management rollout resource list failed (attempt ${attempt}/3); retrying" >&2
      sleep $((attempt * 10))
    fi
  done
  echo "management rollout resource list failed after 3 attempts" >&2
  kubectl --context "${MGMT_CLUSTER}" -n management get statefulset,deploy -o wide || true
  return 1
}

management_rollout_status() {
  local resource="$1"
  local attempt
  for attempt in 1 2 3; do
    if kubectl --context "${MGMT_CLUSTER}" -n management rollout status "${resource}" --timeout=300s; then
      return 0
    fi
    if [[ "${attempt}" != "3" ]]; then
      echo "management rollout status failed for ${resource} (attempt ${attempt}/3); retrying" >&2
      sleep $((attempt * 10))
    fi
  done
  echo "management rollout status failed after 3 attempts: ${resource}" >&2
  kubectl --context "${MGMT_CLUSTER}" -n management get "${resource}" -o wide || true
  kubectl --context "${MGMT_CLUSTER}" -n management describe "${resource}" || true
  return 1
}

apply_management_plane() {
  local overlay="${RUNTIME_DIR}/management-kustomization"
  local service_image_repo="${IMAGE_NAME%:*}"
  local service_image_tag="${IMAGE_NAME##*:}"
  local console_image_repo="${CONSOLE_IMAGE_NAME%:*}"
  local console_image_tag="${CONSOLE_IMAGE_NAME##*:}"

  mkdir -p "${overlay}"
  cat >"${overlay}/kustomization.yaml" <<EOF
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../deploy/management
images:
  - name: 000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/kubernetes-ops-service
    newName: ${service_image_repo}
    newTag: ${service_image_tag}
  - name: 000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/kubernetes-ops-console
    newName: ${console_image_repo}
    newTag: ${console_image_tag}
EOF

  log "applying management plane"
  kubectl --context "${MGMT_CLUSTER}" apply -k "${overlay}"

  # 레거시 워커 정리 — 옛 이름의 Deployment 가 남아 있으면 옛 코드가
  # 같은 subject 를 경합 소비해 이벤트 계약 위반(DLQ)과 파이프라인 오동작을 일으킨다.
  # (up.sh 의 로컬 정리 목록과 동일하게 유지할 것)
  log "removing legacy deployments"
  for old_deploy in \
    oauth-auth-service git-event-processor manifest-renderer desired-state-sync \
    command-orchestrator command-dispatcher agent-connection-gateway \
    evidence-builder ai-rca-service safe-pr-service rca-fallback-worker minio; do
    kubectl --context "${MGMT_CLUSTER}" -n management delete "deploy/${old_deploy}" --ignore-not-found
  done

  log "restarting management deployments"
  kubectl --context "${MGMT_CLUSTER}" -n management rollout restart deployment >/dev/null

  log "waiting for management rollouts"
  while IFS= read -r resource; do
    [[ -n "${resource}" ]] || continue
    management_rollout_status "${resource}"
  done < <(management_rollout_resources)

  log "exposing console with LoadBalancer"
  kubectl --context "${MGMT_CLUSTER}" -n management patch svc console --type merge \
    -p '{"spec":{"type":"LoadBalancer","ports":[{"name":"http","port":80,"targetPort":"http","protocol":"TCP"}]}}' >/dev/null
}

gateway_load_balancer_host() {
  local host=""
  for _ in $(seq 1 90); do
    host="$(
      kubectl --context "${MGMT_CLUSTER}" -n management get svc console \
        -o jsonpath='{.status.loadBalancer.ingress[0].hostname}{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true
    )"
    if [[ -n "${host}" ]] && {
      [[ "${SKIP_LB_HEALTH_WAIT}" == "1" ]] \
        || curl -fsS "http://${host}/api/healthz" | grep -q '"service":"api-gateway"'
    }; then
      printf '%s\n' "${host}"
      return
    fi
    sleep 10
  done
  echo "console LoadBalancer did not become reachable" >&2
  return 1
}

configure_route53_record() {
  local lb_host="$1"
  require_domain_config "Route53" "${CUSTOM_DOMAIN}" "${ROUTE53_ZONE_NAME}"
  local record_name="${CUSTOM_DOMAIN%.}."
  local zone_name="${ROUTE53_ZONE_NAME%.}."
  local zone_id
  local change_id
  local change_batch="${RUNTIME_DIR}/route53-change.json"

  zone_id="$(
    aws route53 list-hosted-zones-by-name \
      --dns-name "${zone_name}" \
      --query "HostedZones[?Name=='${zone_name}'].Id | [0]" \
      --output text
  )"
  if [[ -z "${zone_id}" || "${zone_id}" == "None" ]]; then
    echo "Route53 hosted zone not found for ${zone_name}; skipping ${record_name}" >&2
    return 0
  fi
  zone_id="${zone_id##*/}"

  cat >"${change_batch}" <<JSON
{
  "Comment": "Point ${record_name} to ${MGMT_CLUSTER} console",
  "Changes": [
    {
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "${record_name}",
        "Type": "CNAME",
        "TTL": 60,
        "ResourceRecords": [
          {
            "Value": "${lb_host}"
          }
        ]
      }
    }
  ]
}
JSON

  log "upserting Route53 record ${record_name} -> ${lb_host}"
  change_id="$(
    aws route53 change-resource-record-sets \
      --hosted-zone-id "${zone_id}" \
      --change-batch "file://${change_batch}" \
      --query 'ChangeInfo.Id' \
      --output text
  )"
  aws route53 wait resource-record-sets-changed --id "${change_id}" || true
  CUSTOM_DOMAIN_CONFIGURED="1"
}

cloudflare_api_token_value() {
  CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN}" python3 - <<'PY'
from __future__ import annotations

import os
import re

raw_value = os.environ.get("CLOUDFLARE_API_TOKEN", "")
value = raw_value.strip().strip("\"'")
assignment = re.search(
    r"(?:^|\s)(?:export\s+)?cloudflare_api_token\s*[:=]\s*(.+)$",
    value,
    flags=re.IGNORECASE | re.DOTALL,
)
if assignment:
    value = assignment.group(1).strip().strip("\"'")

authorization = re.search(
    r"authorization\s*:\s*(.+)$",
    value,
    flags=re.IGNORECASE | re.DOTALL,
)
if authorization:
    value = authorization.group(1).strip().strip("\"'")

bearer = re.search(r"bearer\s+([^\s\"']+)", value, flags=re.IGNORECASE)
if bearer:
    value = bearer.group(1)
elif value.lower().startswith("bearer"):
    value = value[6:].strip().strip("\"'")

value = "".join(ch for ch in value.strip().strip("\"'") if not ch.isspace())
if not re.fullmatch(r"[A-Za-z0-9._~+/=-]{20,}", value):
    candidate = re.search(r"[A-Za-z0-9._~+/=-]{20,}", raw_value)
    if candidate:
        value = candidate.group(0)
print(value, end="")
PY
}

cloudflare_validate_api_token() {
  CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN}" \
  CLOUDFLARE_NORMALIZED_API_TOKEN="$(cloudflare_api_token_value)" \
  python3 - <<'PY'
from __future__ import annotations

import os
import re
import sys

raw = os.environ.get("CLOUDFLARE_API_TOKEN", "")
token = os.environ.get("CLOUDFLARE_NORMALIZED_API_TOKEN", "")
allowed = bool(re.fullmatch(r"[A-Za-z0-9._~+/=-]+", token))
if token and allowed and len(token) >= 20:
    raise SystemExit(0)

print(
    "Cloudflare API token is not a valid Bearer token after normalization "
    f"(raw_length={len(raw)}, normalized_length={len(token)}, "
    f"allowed_bearer_charset={str(allowed).lower()}). "
    "Set the GitHub environment secret CLOUDFLARE_API_TOKEN to the raw Cloudflare API token only.",
    file=sys.stderr,
)
raise SystemExit(1)
PY
}

cloudflare_authorization_value() {
  local token
  token="$(cloudflare_api_token_value)"
  printf 'Bearer %s\n' "${token}"
}

cloudflare_zone_id() {
  if [[ -n "${CLOUDFLARE_ZONE_ID}" ]]; then
    printf '%s\n' "${CLOUDFLARE_ZONE_ID}"
    return
  fi
  CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN}" \
  CLOUDFLARE_AUTHORIZATION="$(cloudflare_authorization_value)" \
  CLOUDFLARE_ZONE_NAME="${CLOUDFLARE_ZONE_NAME}" \
  python3 - <<'PY'
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

authorization = os.environ["CLOUDFLARE_AUTHORIZATION"]
zone_name = os.environ["CLOUDFLARE_ZONE_NAME"]
query = urllib.parse.urlencode({"name": zone_name})
request = urllib.request.Request(
    f"https://api.cloudflare.com/client/v4/zones?{query}",
    headers={"Authorization": authorization},
)
try:
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.load(response)
except urllib.error.HTTPError as exc:
    print(f"Cloudflare zone lookup failed with HTTP {exc.code}", file=sys.stderr)
    print(exc.read().decode("utf-8", errors="replace"), file=sys.stderr)
    raise SystemExit(1) from None
for zone in payload.get("result", []):
    if zone.get("name") == zone_name:
        print(zone["id"])
        break
PY
}

configure_cloudflare_record() {
  local lb_host="$1"
  local zone_id
  local record_id
  local body_file="${RUNTIME_DIR}/cloudflare-record.json"
  local proxied
  local ttl

  require_domain_config "Cloudflare" "${CUSTOM_DOMAIN}" "${CLOUDFLARE_ZONE_NAME}"

  if [[ -z "${CLOUDFLARE_API_TOKEN}" ]]; then
    log "Cloudflare API token is not set; skipping ${CUSTOM_DOMAIN}"
    return 0
  fi
  if [[ -z "$(cloudflare_api_token_value)" ]]; then
    echo "Cloudflare API token is blank after normalization; skipping ${CUSTOM_DOMAIN}" >&2
    return 0
  fi
  if ! cloudflare_validate_api_token; then
    log "skipping Cloudflare DNS for ${CUSTOM_DOMAIN}; AWS LoadBalancer remains available"
    return 0
  fi

  if ! zone_id="$(cloudflare_zone_id)"; then
    log "skipping Cloudflare DNS for ${CUSTOM_DOMAIN}; zone lookup failed"
    return 0
  fi
  if [[ -z "${zone_id}" ]]; then
    echo "Cloudflare zone not found for ${CLOUDFLARE_ZONE_NAME}; skipping ${CUSTOM_DOMAIN}" >&2
    return 0
  fi

  if ! record_id="$(
    CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN}" \
    CLOUDFLARE_AUTHORIZATION="$(cloudflare_authorization_value)" \
    CUSTOM_DOMAIN="${CUSTOM_DOMAIN}" \
    CF_ZONE_ID="${zone_id}" \
    python3 - <<'PY'
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

authorization = os.environ["CLOUDFLARE_AUTHORIZATION"]
zone_id = os.environ["CF_ZONE_ID"]
query = urllib.parse.urlencode({"type": "CNAME", "name": os.environ["CUSTOM_DOMAIN"]})
request = urllib.request.Request(
    f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?{query}",
    headers={"Authorization": authorization},
)
try:
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.load(response)
except urllib.error.HTTPError as exc:
    print(f"Cloudflare DNS record lookup failed with HTTP {exc.code}", file=sys.stderr)
    print(exc.read().decode("utf-8", errors="replace"), file=sys.stderr)
    raise SystemExit(1) from None
records = payload.get("result", [])
print(records[0]["id"] if records else "")
PY
  )"; then
    log "skipping Cloudflare DNS for ${CUSTOM_DOMAIN}; DNS record lookup failed"
    return 0
  fi
  proxied="$(cloudflare_proxied_json)"
  ttl="$(cloudflare_ttl_json)"

  cat >"${body_file}" <<JSON
{
  "type": "CNAME",
  "name": "${CUSTOM_DOMAIN}",
  "content": "${lb_host}",
  "ttl": ${ttl},
  "proxied": ${proxied},
  "comment": "${PROJECT_SLUG} console"
}
JSON

  if [[ -n "${record_id}" ]]; then
    log "updating Cloudflare record ${CUSTOM_DOMAIN} -> ${lb_host}"
    cloudflare_dns_record_request \
      "PUT" \
      "https://api.cloudflare.com/client/v4/zones/${zone_id}/dns_records/${record_id}" \
      "${body_file}"
  else
    log "creating Cloudflare record ${CUSTOM_DOMAIN} -> ${lb_host}"
    cloudflare_dns_record_request \
      "POST" \
      "https://api.cloudflare.com/client/v4/zones/${zone_id}/dns_records" \
      "${body_file}"
  fi
  CUSTOM_DOMAIN_CONFIGURED="1"
}

cloudflare_dns_record_request() {
  local method="$1"
  local url="$2"
  local body_file="$3"
  local response_file="${RUNTIME_DIR}/cloudflare-response.json"
  local http_code

  http_code="$(
    curl -sS \
      -o "${response_file}" \
      -w "%{http_code}" \
      -X "${method}" \
      -H "Authorization: $(cloudflare_authorization_value)" \
      -H "Content-Type: application/json" \
      --data @"${body_file}" \
      "${url}"
  )"

  if [[ "${http_code}" -lt 200 || "${http_code}" -ge 300 ]]; then
    echo "Cloudflare API ${method} failed with HTTP ${http_code}" >&2
    python3 - "${response_file}" <<'PY' >&2
from __future__ import annotations

import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    print(path.read_text(encoding="utf-8", errors="replace"))
    raise SystemExit(0)

errors = payload.get("errors") or []
messages = payload.get("messages") or []
if errors:
    for error in errors:
        code = error.get("code", "unknown")
        message = error.get("message", "")
        print(f"- Cloudflare error {code}: {message}")
if messages:
    for message in messages:
        print(f"- Cloudflare message: {message}")
if not errors and not messages:
    print(json.dumps(payload, ensure_ascii=False))
PY
    return 1
  fi
}

cloudflare_is_proxied() {
  case "${CLOUDFLARE_PROXIED}" in
    1|true|TRUE|yes|YES|on|ON)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

cloudflare_proxied_json() {
  if cloudflare_is_proxied; then
    printf 'true'
  else
    printf 'false'
  fi
}

cloudflare_ttl_json() {
  if cloudflare_is_proxied; then
    # Cloudflare proxied records use "Auto" TTL. API value 1 means automatic.
    printf '1'
  else
    printf '60'
  fi
}

custom_domain_base_url() {
  local scheme="http"
  if [[ "${CONFIGURE_ROUTE53}" != "1" && "${CONFIGURE_CLOUDFLARE}" != "1" ]]; then
    return 1
  fi
  if [[ "${CONFIGURE_CLOUDFLARE}" == "1" ]] && cloudflare_is_proxied; then
    scheme="https"
  fi
  for _ in $(seq 1 30); do
    if curl -fsS "${scheme}://${CUSTOM_DOMAIN}/api/healthz" | grep -q '"service":"api-gateway"'; then
      printf '%s://%s\n' "${scheme}" "${CUSTOM_DOMAIN}"
      return
    fi
    sleep 10
  done
  return 1
}

bootstrap_admin() {
  log "bootstrapping admin account: ${AUTH_EMAIL}"
  kubectl --context "${MGMT_CLUSTER}" -n management port-forward svc/postgresql 15432:5432 >/dev/null 2>&1 &
  PORT_FORWARD_PID="$!"
  sleep 5

  DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:15432/${POSTGRES_DB}" \
  MIGRATION_EXPECTED_HEAD="$(PYTHONPATH="${ROOT_DIR}/src" uv run alembic heads | awk '{print $1}')" \
  PYTHONPATH="${ROOT_DIR}/src:${ROOT_DIR}/src/services/gateway/api-gateway" \
  AUTH_PASSWORD="${AUTH_PASSWORD}" \
  PROJECT_SLUG="${PROJECT_SLUG}" \
  uv run python -m entrypoints.bootstrap_admin

  kill "${PORT_FORWARD_PID}" >/dev/null 2>&1 || true
  PORT_FORWARD_PID=""
}

register_target() {
  local context="$1"
  local cluster_id="$2"
  local display_name="$3"
  local base_url="$4"

  log "registering target ${display_name} (${context})"
  BASE_URL="${base_url}" \
  MANAGEMENT_BASE_URL="${base_url%/}/api" \
  TARGET_CONTEXT="${context}" \
  TARGET_CLUSTER_ID="${cluster_id}" \
  TARGET_NAME="${display_name}" \
  TARGET_ENVIRONMENT="aws-test" \
  LOKI_BASE_URL="${LOKI_BASE_URL}" \
  EVIDENCE_INTERVAL_SECONDS="${EVIDENCE_INTERVAL_SECONDS}" \
  IMAGE_NAME="${IMAGE_NAME}" \
  INSTALL_TELEMETRY="${INSTALL_TELEMETRY}" \
  MINIO_ROOT_USER="${MINIO_ROOT_USER}" \
  MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD}" \
  INSTALL_NODE_COLLECTOR="${INSTALL_NODE_COLLECTOR}" \
  AUTH_EMAIL="${AUTH_EMAIL}" \
  AUTH_PASSWORD="${AUTH_PASSWORD}" \
  bash "${ROOT_DIR}/scripts/register-target.sh"
}

basic_status() {
  local base_url="$1"
  log "management pods"
  kubectl --context "${MGMT_CLUSTER}" -n management get pods -o wide
  log "${TARGET_1_DISPLAY_NAME} target pods"
  kubectl --context "${TARGET_CLUSTER_1}" -n target get pods -o wide
  log "${TARGET_2_DISPLAY_NAME} target pods"
  kubectl --context "${TARGET_CLUSTER_2}" -n target get pods -o wide
  log "console/api health"
  if ! curl -fsS "${base_url}/api/healthz" | grep -q '"service":"api-gateway"'; then
    echo "console/api health check failed from this machine; verify DNS propagation for ${base_url}" >&2
  fi
  echo
}

run_smoke_if_requested() {
  local base_url="$1"
  if [[ "${RUN_SMOKE}" != "1" ]]; then
    log "skipping smoke test (set RUN_SMOKE=1 to run scripts/smoke.sh)"
    return
  fi
  BASE_URL="${base_url}" \
  MGMT_CONTEXT="${MGMT_CLUSTER}" \
  SMOKE_IMAGE="${IMAGE_NAME}" \
  SMOKE_CLUSTER_ID="${SMOKE_CLUSTER_ID:-${TARGET_CLUSTER_ID_1}}" \
  GITHUB_REPO="${GITHUB_REPO}" \
  GITHUB_BRANCH="${GITHUB_BRANCH}" \
  MANIFEST_PATH="${MANIFEST_PATH}" \
  GITHUB_API_BASE="${GITHUB_API_BASE}" \
  GITHUB_TOKEN="${GITHUB_TOKEN}" \
  AUTH_EMAIL="${AUTH_EMAIL}" \
  AUTH_PASSWORD="${AUTH_PASSWORD}" \
  bash "${ROOT_DIR}/scripts/smoke.sh"
}

main() {
  local account_id
  local base_url
  local lb_host

  account_id="$(aws_account_id)"
  log "using AWS account ${account_id}, region ${AWS_REGION}"

  ensure_ecr_images "${account_id}"

  if [[ "${CREATE_CLUSTERS}" == "1" ]]; then
    ensure_cluster "${MGMT_CLUSTER}" "${MGMT_DISPLAY_NAME}" "${MGMT_NODE_TYPE}" "${MGMT_NODES}" "management"
    ensure_cluster "${TARGET_CLUSTER_1}" "${TARGET_1_DISPLAY_NAME}" "${TARGET_NODE_TYPE}" "${TARGET_NODES}" "target"
    ensure_cluster "${TARGET_CLUSTER_2}" "${TARGET_2_DISPLAY_NAME}" "${TARGET_NODE_TYPE}" "${TARGET_NODES}" "target"
  else
    log "using existing EKS clusters"
    configure_existing_cluster_context "${MGMT_CLUSTER}"
    configure_existing_cluster_context "${TARGET_CLUSTER_1}"
    configure_existing_cluster_context "${TARGET_CLUSTER_2}"
  fi

  if [[ "${ENSURE_EBS_CSI}" == "1" ]]; then
    ensure_ebs_csi
    ensure_default_storage_class
  else
    log "skipping EBS CSI setup"
  fi
  create_management_runtime
  bootstrap_management_schema
  apply_management_plane
  lb_host="$(gateway_load_balancer_host)"
  base_url="http://${lb_host}"
  if [[ "${CONFIGURE_ROUTE53}" == "1" ]]; then
    configure_route53_record "${lb_host}"
  fi
  if [[ "${CONFIGURE_CLOUDFLARE}" == "1" ]]; then
    configure_cloudflare_record "${lb_host}"
  fi
  if [[ "${CUSTOM_DOMAIN_CONFIGURED}" == "1" ]]; then
    if domain_url="$(custom_domain_base_url)"; then
      base_url="${domain_url}"
    else
      log "custom domain not reachable yet; continuing with ${base_url}"
    fi
  else
    log "skipping Route53 custom domain setup"
  fi
  if [[ "${BOOTSTRAP_ADMIN}" == "1" ]]; then
    bootstrap_admin
  else
    log "skipping admin bootstrap"
  fi

  if [[ "${REGISTER_TARGETS}" == "1" ]]; then
    register_target "${TARGET_CLUSTER_1}" "${TARGET_CLUSTER_ID_1}" "${TARGET_1_DISPLAY_NAME}" "${base_url}"
    register_target "${TARGET_CLUSTER_2}" "${TARGET_CLUSTER_ID_2}" "${TARGET_2_DISPLAY_NAME}" "${base_url}"
  else
    log "skipping target registration"
  fi

  basic_status "${base_url}"
  run_smoke_if_requested "${base_url}"

  echo
  echo "AWS setup is ready."
  echo "Public origin: ${base_url}"
  if [[ "${CUSTOM_DOMAIN_CONFIGURED}" == "1" ]]; then
    echo "Custom domain: ${CUSTOM_DOMAIN}"
  fi
  echo "Management cluster: ${MGMT_CLUSTER} (${MGMT_DISPLAY_NAME})"
  echo "Target 1: ${TARGET_CLUSTER_1} (${TARGET_1_DISPLAY_NAME})"
  echo "Target 2: ${TARGET_CLUSTER_2} (${TARGET_2_DISPLAY_NAME})"
  echo "Admin email: ${AUTH_EMAIL}"
  if [[ "${GENERATED_AUTH_PASSWORD}" == "1" ]]; then
    if [[ "${PRINT_GENERATED_ADMIN_PASSWORD}" == "1" ]]; then
      echo "Generated admin password: ${AUTH_PASSWORD}"
    else
      echo "Generated admin password: hidden; set PRINT_GENERATED_ADMIN_PASSWORD=1 for local debug output"
    fi
  else
    echo "Admin password: provided through AUTH_PASSWORD"
  fi
}

main "$@"
