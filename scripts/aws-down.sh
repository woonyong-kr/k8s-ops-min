#!/usr/bin/env bash
set -euo pipefail

PROJECT_SLUG="${PROJECT_SLUG:-kubernetes-ops}"
AWS_REGION="${AWS_REGION:-us-east-1}"
MGMT_CLUSTER="${MGMT_CLUSTER:-${PROJECT_SLUG}-mgmt}"
TARGET_CLUSTER_1="${TARGET_CLUSTER_1:-${PROJECT_SLUG}-target-a}"
TARGET_CLUSTER_2="${TARGET_CLUSTER_2:-${PROJECT_SLUG}-target-b}"
ECR_REPO="${ECR_REPO:-${PROJECT_SLUG}-service}"
CONSOLE_ECR_REPO="${CONSOLE_ECR_REPO:-${PROJECT_SLUG}-console}"
DELETE_ECR="${DELETE_ECR:-0}"

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

cluster_exists() {
  aws eks describe-cluster \
    --region "${AWS_REGION}" \
    --name "$1" >/dev/null 2>&1
}

delete_cluster_if_exists() {
  local cluster_name="$1"
  if cluster_exists "${cluster_name}"; then
    echo "==> deleting EKS cluster: ${cluster_name}"
    eksctl delete cluster --region "${AWS_REGION}" --name "${cluster_name}" --wait
  else
    echo "==> EKS cluster already absent: ${cluster_name}"
  fi
}

need aws
need eksctl

delete_cluster_if_exists "${TARGET_CLUSTER_2}"
delete_cluster_if_exists "${TARGET_CLUSTER_1}"
delete_cluster_if_exists "${MGMT_CLUSTER}"

if [[ "${DELETE_ECR}" == "1" ]]; then
  for repo in "${ECR_REPO}" "${CONSOLE_ECR_REPO}"; do
    echo "==> deleting ECR repository: ${repo}"
    aws ecr delete-repository \
      --region "${AWS_REGION}" \
      --repository-name "${repo}" \
      --force >/dev/null 2>&1 || true
  done
else
  echo "==> keeping ECR repositories ${ECR_REPO}, ${CONSOLE_ECR_REPO} (set DELETE_ECR=1 to remove them)"
fi
