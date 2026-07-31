#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLUSTER_NAME="${DEMO_CLUSTER_NAME:-opsia-demo}"
KIND_NODE_IMAGE="${DEMO_KIND_NODE_IMAGE:-kindest/node:v1.32.2}"
NAMESPACE="${DEMO_NAMESPACE:-opsia-demo}"
OPSIA_NAMESPACE="${DEMO_OPSIA_NAMESPACE:-opsia-system}"
OPSIA_RELEASE="${DEMO_OPSIA_RELEASE:-opsia}"
OPSIA_IMAGE="${DEMO_OPSIA_IMAGE:-service:local}"
OPSIA_CONSOLE_IMAGE="${DEMO_OPSIA_CONSOLE_IMAGE:-opsia-console:local}"
WORKLOAD="${DEMO_WORKLOAD:-checkout-api}"
DEFAULT_GOOD_IMAGE="opsia-demo-workload:local"
GOOD_IMAGE="${DEMO_GOOD_IMAGE:-${DEFAULT_GOOD_IMAGE}}"
BAD_IMAGE="${DEMO_BAD_IMAGE:-opsia-demo-workload:missing}"
ARTIFACT_DIR="${DEMO_ARTIFACT_DIR:-}"
DRY_RUN="${DEMO_DRY_RUN:-0}"
KEEP_CLUSTER="${DEMO_KEEP_CLUSTER:-0}"
API_PORT="${DEMO_API_PORT:-18080}"
SCM_PORT="${DEMO_SCM_PORT:-18081}"
SCM_WRITER_TOKEN="${DEMO_SCM_TOKEN:-}"
SCM_ADMIN_TOKEN="${DEMO_SCM_ADMIN_TOKEN:-}"
SCM_REVIEWER_TOKEN="${DEMO_SCM_REVIEWER_TOKEN:-}"
SCM_REPO="${DEMO_SCM_REPO:-opsia/demo}"
SCM_PUBLIC_URL="http://opsia-demo-scm.${OPSIA_NAMESPACE}.svc:8080"
MANIFEST_PATH="${DEMO_MANIFEST_PATH:-deploy/checkout.yaml}"
CLUSTER_ID="${DEMO_CLUSTER_ID:-opsia-self}"
SKIP_IMAGE_BUILD="${DEMO_SKIP_IMAGE_BUILD:-0}"

scene() {
  echo "[demo] $1"
}

if [[ "${DRY_RUN}" == "1" ]]; then
  scene "kind-cluster-ready"
  scene "opsia-installed"
  scene "bad-rollout-observed"
  scene "safe-pr-requested"
  scene "safe-pr-created"
  scene "review-merged"
  scene "gitops-sync-applied"
  scene "workload-normalized"
  exit 0
fi

PORT_FORWARD_PIDS=()
RUNTIME_DIR=""
cleanup() {
  for pid in "${PORT_FORWARD_PIDS[@]:-}"; do
    kill "${pid}" >/dev/null 2>&1 || true
    wait "${pid}" >/dev/null 2>&1 || true
  done
  if [[ -n "${RUNTIME_DIR}" ]]; then
    rm -rf -- "${RUNTIME_DIR}"
  fi
  if [[ "${KEEP_CLUSTER}" != "1" ]]; then
    kind delete cluster --name "${CLUSTER_NAME}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

for command in kind kubectl docker helm curl python3; do
  command -v "${command}" >/dev/null || {
    echo "missing required command: ${command}" >&2
    exit 1
  }
done
if [[ -z "${SCM_WRITER_TOKEN}" ]]; then
  SCM_WRITER_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
fi
if [[ -z "${SCM_ADMIN_TOKEN}" ]]; then
  SCM_ADMIN_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
fi
if [[ -z "${SCM_REVIEWER_TOKEN}" ]]; then
  SCM_REVIEWER_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
fi
if [[ "${SCM_WRITER_TOKEN}" == "${SCM_ADMIN_TOKEN}" \
  || "${SCM_WRITER_TOKEN}" == "${SCM_REVIEWER_TOKEN}" \
  || "${SCM_ADMIN_TOKEN}" == "${SCM_REVIEWER_TOKEN}" ]]; then
  echo "demo SCM writer, admin, and reviewer tokens must differ" >&2
  exit 1
fi
RUNTIME_DIR="$(mktemp -d "${TMPDIR:-/tmp}/opsia-demo.XXXXXX")"
if [[ -z "${ARTIFACT_DIR}" ]]; then
  ARTIFACT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/opsia-demo-artifacts.XXXXXX")"
elif [[ -d "${ARTIFACT_DIR}" && -n "$(find "${ARTIFACT_DIR}" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  echo "artifact directory must be empty: ${ARTIFACT_DIR}" >&2
  exit 1
else
  mkdir -p "${ARTIFACT_DIR}"
fi
SCM_WRITER_HEADER="${RUNTIME_DIR}/scm-writer-header"
SCM_ADMIN_HEADER="${RUNTIME_DIR}/scm-admin-header"
SCM_REVIEWER_HEADER="${RUNTIME_DIR}/scm-reviewer-header"
SCM_WRITER_TOKEN_FILE="${RUNTIME_DIR}/writer-token"
SCM_ADMIN_TOKEN_FILE="${RUNTIME_DIR}/admin-token"
SCM_REVIEWER_TOKEN_FILE="${RUNTIME_DIR}/reviewer-token"
printf 'authorization: Bearer %s\n' "${SCM_WRITER_TOKEN}" >"${SCM_WRITER_HEADER}"
printf 'authorization: Bearer %s\n' "${SCM_ADMIN_TOKEN}" >"${SCM_ADMIN_HEADER}"
printf 'authorization: Bearer %s\n' "${SCM_REVIEWER_TOKEN}" >"${SCM_REVIEWER_HEADER}"
printf '%s' "${SCM_WRITER_TOKEN}" >"${SCM_WRITER_TOKEN_FILE}"
printf '%s' "${SCM_ADMIN_TOKEN}" >"${SCM_ADMIN_TOKEN_FILE}"
printf '%s' "${SCM_REVIEWER_TOKEN}" >"${SCM_REVIEWER_TOKEN_FILE}"
SCM_CREDENTIAL_VERSION="$(
  printf '%s\0%s\0%s' "${SCM_WRITER_TOKEN}" "${SCM_ADMIN_TOKEN}" "${SCM_REVIEWER_TOKEN}" \
    | python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
)"
docker info >/dev/null

wait_for_url() {
  local url="$1"
  local attempts="${2:-60}"
  for ((attempt = 1; attempt <= attempts; attempt += 1)); do
    if curl --connect-timeout 1 --max-time 2 -fsS "${url}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "timed out waiting for ${url}" >&2
  return 1
}

http() {
  curl --connect-timeout 5 --max-time 30 --retry 2 --retry-connrefused "$@"
}

secret_value() {
  local secret_name="$1"
  local key="$2"
  kubectl -n "${OPSIA_NAMESPACE}" get secret "${secret_name}" -o json | python3 -c '
import base64, json, sys
payload = json.load(sys.stdin)
print(base64.b64decode(payload["data"][sys.argv[1]]).decode())
' "${key}"
}

json_field() {
  local file="$1"
  local path="$2"
  python3 - "${file}" "${path}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    value = json.load(handle)
for part in sys.argv[2].split("."):
    value = value[part]
if isinstance(value, (dict, list)):
    print(json.dumps(value, separators=(",", ":")))
else:
    print(value)
PY
}

pull_request_number() {
  python3 - "$1" "${SCM_PUBLIC_URL}" <<'PY'
import re
import sys
from urllib.parse import urlparse

actual = urlparse(sys.argv[1])
expected = urlparse(sys.argv[2])
match = re.fullmatch(r"/demo/pulls/([1-9][0-9]*)", actual.path)
if (
    actual.scheme != expected.scheme
    or actual.netloc != expected.netloc
    or actual.params
    or actual.query
    or actual.fragment
    or match is None
):
    raise SystemExit("safe PR URL is outside the demo SCM authority")
print(match.group(1))
PY
}

write_application_request() {
  local output="$1"
  python3 - "${output}" "${WORKLOAD}" "${SCM_REPO}" "${MANIFEST_PATH}" \
    "${CLUSTER_ID}" "${NAMESPACE}" <<'PY'
import json
import sys

output, name, repo_ref, manifest_path, cluster_id, namespace = sys.argv[1:]
payload = {
    "name": name,
    "repo_ref": repo_ref,
    "default_branch": "main",
    "branch": "main",
    "manifest_path": manifest_path,
    "cluster_id": cluster_id,
    "namespace": namespace,
    "environment": "sandbox",
    "metadata": {"source_type": "raw-yaml"},
}
with open(output, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, separators=(",", ":"))
PY
}

write_release_request() {
  local output="$1"
  local application_id="$2"
  local image="$3"
  local commit_sha="$4"
  local safe_pr="$5"
  python3 - "${output}" "${application_id}" "${image}" "${commit_sha}" "${safe_pr}" \
    "${WORKLOAD}" "${SCM_REPO}" "${MANIFEST_PATH}" "${CLUSTER_ID}" "${NAMESPACE}" <<'PY'
import json
import sys

(
    output,
    application_id,
    image,
    commit_sha,
    safe_pr,
    workload,
    repo_ref,
    manifest_path,
    cluster_id,
    namespace,
) = sys.argv[1:]
payload = {
    "plan": {
        "plan_id": "opsia-demo-rollback",
        "name": "Opsia verified rollback demo",
        "settings": {"default_strategy": "rolling", "scm_provider": "github"},
        "steps": [
            {
                "application_id": application_id,
                "name": workload,
                "position": 0,
                "depends_on": [],
                "config": {
                    "image": image,
                    "repo_ref": repo_ref,
                    "branch": "main",
                    "commit_sha": commit_sha,
                    "manifest_path": manifest_path,
                    "cluster_id": cluster_id,
                    "namespace": namespace,
                    "environment": "sandbox",
                    "replicas": 1,
                    "container_port": 8080,
                    "service_port": 80,
                    "readiness_path": "/",
                    "liveness_path": "/",
                },
            }
        ],
    },
    "step_index": 0,
}
if safe_pr == "1":
    payload.update(
        {
            "title": "Restore the verified checkout image",
            "body": (
                "The demo reviewer must merge this generated manifest change before the "
                "external GitOps sync can normalize the workload."
            ),
        }
    )
with open(output, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, separators=(",", ":"))
PY
}

extract_manifest() {
  local response="$1"
  local output="$2"
  python3 - "${response}" "${output}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    manifest = json.load(handle)["manifest"]
with open(sys.argv[2], "w", encoding="utf-8") as handle:
    handle.write(manifest)
PY
}

write_files_payload() {
  local output="$1"
  local manifest="$2"
  local mode="$3"
  python3 - "${output}" "${manifest}" "${MANIFEST_PATH}" "${mode}" <<'PY'
import json
import sys

output, manifest_path, repo_path, mode = sys.argv[1:]
with open(manifest_path, encoding="utf-8") as handle:
    content = handle.read()
payload = {"files": {repo_path: content}}
if mode == "commit":
    payload.update({"branch": "main", "message": "Introduce the demo bad rollout"})
with open(output, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, separators=(",", ":"))
PY
}

external_gitops_sync() {
  local manifest="$1"
  kubectl apply --server-side --field-manager=opsia-demo-gitops -f "${manifest}" >/dev/null
}

if ! kind get clusters | grep -Fxq "${CLUSTER_NAME}"; then
  kind create cluster --name "${CLUSTER_NAME}" --image "${KIND_NODE_IMAGE}" --wait 120s
fi
kubectl config use-context "kind-${CLUSTER_NAME}" >/dev/null
scene "kind-cluster-ready"

if [[ "${SKIP_IMAGE_BUILD}" != "1" ]]; then
  docker build -f "${ROOT_DIR}/src/services/Dockerfile" -t "${OPSIA_IMAGE}" "${ROOT_DIR}"
  docker build -f "${ROOT_DIR}/references/ui-layer-lab/Dockerfile" \
    -t "${OPSIA_CONSOLE_IMAGE}" "${ROOT_DIR}/references/ui-layer-lab"
elif ! docker image inspect "${OPSIA_IMAGE}" >/dev/null 2>&1; then
  echo "DEMO_SKIP_IMAGE_BUILD=1 but ${OPSIA_IMAGE} is unavailable" >&2
  exit 1
fi
if ! docker image inspect "${OPSIA_CONSOLE_IMAGE}" >/dev/null 2>&1; then
  echo "console image is unavailable: ${OPSIA_CONSOLE_IMAGE}" >&2
  exit 1
fi
if [[ "${GOOD_IMAGE}" == "${DEFAULT_GOOD_IMAGE}" ]]; then
  docker build -f "${ROOT_DIR}/deploy/oss/demo-workload.Dockerfile" \
    -t "${GOOD_IMAGE}" "${ROOT_DIR}"
fi
kind load docker-image "${OPSIA_IMAGE}" --name "${CLUSTER_NAME}" >/dev/null
kind load docker-image "${OPSIA_CONSOLE_IMAGE}" --name "${CLUSTER_NAME}" >/dev/null
if docker image inspect "${GOOD_IMAGE}" >/dev/null 2>&1; then
  kind load docker-image "${GOOD_IMAGE}" --name "${CLUSTER_NAME}" >/dev/null
fi
IMAGE_REPOSITORY="${OPSIA_IMAGE%:*}"
IMAGE_TAG="${OPSIA_IMAGE##*:}"
CONSOLE_IMAGE_REPOSITORY="${OPSIA_CONSOLE_IMAGE%:*}"
CONSOLE_IMAGE_TAG="${OPSIA_CONSOLE_IMAGE##*:}"

kubectl create namespace "${OPSIA_NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n "${OPSIA_NAMESPACE}" create secret generic opsia-demo-scm \
  --from-file="writer-token=${SCM_WRITER_TOKEN_FILE}" \
  --from-file="admin-token=${SCM_ADMIN_TOKEN_FILE}" \
  --from-file="reviewer-token=${SCM_REVIEWER_TOKEN_FILE}" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl apply -f - >/dev/null <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: opsia-demo-scm
  namespace: ${OPSIA_NAMESPACE}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: opsia-demo-scm
  template:
    metadata:
      annotations:
        opsia.io/scm-credential-version: ${SCM_CREDENTIAL_VERSION}
      labels:
        app: opsia-demo-scm
    spec:
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        fsGroup: 10001
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: scm
          image: ${OPSIA_IMAGE}
          imagePullPolicy: IfNotPresent
          command: ["python", "src/entrypoints/demo_scm_fixture.py"]
          env:
            - name: DEMO_SCM_TOKEN
              valueFrom:
                secretKeyRef:
                  name: opsia-demo-scm
                  key: writer-token
            - name: DEMO_SCM_REVIEWER_TOKEN
              valueFrom:
                secretKeyRef:
                  name: opsia-demo-scm
                  key: reviewer-token
            - name: DEMO_SCM_ADMIN_TOKEN
              valueFrom:
                secretKeyRef:
                  name: opsia-demo-scm
                  key: admin-token
            - name: DEMO_SCM_REPO_REF
              value: ${SCM_REPO}
            - name: DEMO_SCM_PUBLIC_URL
              value: ${SCM_PUBLIC_URL}
            - name: HOME
              value: /tmp
            - name: PYTHONDONTWRITEBYTECODE
              value: "1"
          ports:
            - name: http
              containerPort: 8080
          readinessProbe:
            httpGet:
              path: /healthz
              port: http
            periodSeconds: 2
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - name: scratch
              mountPath: /tmp
      volumes:
        - name: scratch
          emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: opsia-demo-scm
  namespace: ${OPSIA_NAMESPACE}
spec:
  selector:
    app: opsia-demo-scm
  ports:
    - name: http
      port: 8080
      targetPort: http
EOF
kubectl -n "${OPSIA_NAMESPACE}" rollout status deployment/opsia-demo-scm --timeout=180s

helm upgrade --install "${OPSIA_RELEASE}" "${ROOT_DIR}/charts/opsia" \
  --namespace "${OPSIA_NAMESPACE}" \
  --create-namespace \
  --set "image.repository=${IMAGE_REPOSITORY}" \
  --set "image.tag=${IMAGE_TAG}" \
  --set image.pullPolicy=IfNotPresent \
  --set "console.image.repository=${CONSOLE_IMAGE_REPOSITORY}" \
  --set "console.image.tag=${CONSOLE_IMAGE_TAG}" \
  --set console.image.pullPolicy=IfNotPresent \
  --set access.mode=portforward \
  --set postgresql.persistence.enabled=false \
  --set-string "scm.repository=${SCM_REPO}" \
  --set-string "scm.github.apiBase=http://opsia-demo-scm:8080" \
  --set-string "scm.github.tokenSecretName=opsia-demo-scm" \
  --set-string "scm.github.tokenSecretKey=writer-token" \
  --set-string "scm.credentialVersion=${SCM_CREDENTIAL_VERSION}" \
  --wait \
  --timeout 5m
kubectl -n "${OPSIA_NAMESPACE}" rollout status deployment/opsia-controller --timeout=180s
kubectl -n "${OPSIA_NAMESPACE}" rollout status statefulset/opsia-postgresql --timeout=180s
kubectl -n "${OPSIA_NAMESPACE}" rollout status daemonset/opsia-agent --timeout=180s
scene "opsia-installed"

mkdir -p "${ARTIFACT_DIR}"
COOKIE_JAR="${RUNTIME_DIR}/cookies.txt"
API_BASE="http://127.0.0.1:${API_PORT}/api"
SCM_BASE="http://127.0.0.1:${SCM_PORT}"
kubectl -n "${OPSIA_NAMESPACE}" port-forward "service/${OPSIA_RELEASE}" \
  "${API_PORT}:80" >"${ARTIFACT_DIR}/api-port-forward.log" 2>&1 &
PORT_FORWARD_PIDS+=("$!")
kubectl -n "${OPSIA_NAMESPACE}" port-forward service/opsia-demo-scm \
  "${SCM_PORT}:8080" >"${ARTIFACT_DIR}/scm-port-forward.log" 2>&1 &
PORT_FORWARD_PIDS+=("$!")
wait_for_url "${API_BASE}/healthz"
wait_for_url "${SCM_BASE}/healthz"

ADMIN_EMAIL="$(secret_value "${OPSIA_RELEASE}-bootstrap" AUTH_EMAIL)"
ADMIN_PASSWORD="$(secret_value "${OPSIA_RELEASE}-bootstrap" AUTH_PASSWORD)"
printf '%s\0%s\0' "${ADMIN_EMAIL}" "${ADMIN_PASSWORD}" \
  | python3 -c '
import json
import sys

email, password, _ = sys.stdin.buffer.read().split(b"\0", maxsplit=2)
json.dump(
    {"email": email.decode(), "password": password.decode()},
    sys.stdout,
    separators=(",", ":"),
)
' \
  | http -fsS -c "${COOKIE_JAR}" -H 'content-type: application/json' \
    --data-binary @- "${API_BASE}/auth/login" >"${ARTIFACT_DIR}/login-response.json"

write_application_request "${ARTIFACT_DIR}/application-request.json"
http -fsS -b "${COOKIE_JAR}" -c "${COOKIE_JAR}" \
  -H 'content-type: application/json' -H 'x-service-csrf: same-origin' \
  --data-binary "@${ARTIFACT_DIR}/application-request.json" \
  "${API_BASE}/applications" >"${ARTIFACT_DIR}/application-response.json"
APPLICATION_ID="$(json_field "${ARTIFACT_DIR}/application-response.json" application.application_id)"

write_release_request "${ARTIFACT_DIR}/good-render-request.json" \
  "${APPLICATION_ID}" "${GOOD_IMAGE}" demo-seed 0
http -fsS -b "${COOKIE_JAR}" -H 'content-type: application/json' \
  -H 'x-service-csrf: same-origin' \
  --data-binary "@${ARTIFACT_DIR}/good-render-request.json" \
  "${API_BASE}/release-plans/render-manifest" >"${ARTIFACT_DIR}/good-render-response.json"
extract_manifest "${ARTIFACT_DIR}/good-render-response.json" "${ARTIFACT_DIR}/good.yaml"
write_files_payload "${ARTIFACT_DIR}/scm-reset.json" "${ARTIFACT_DIR}/good.yaml" reset
http -fsS -H "@${SCM_ADMIN_HEADER}" -H 'content-type: application/json' \
  --data-binary "@${ARTIFACT_DIR}/scm-reset.json" \
  "${SCM_BASE}/demo/reset" >"${ARTIFACT_DIR}/scm-reset-response.json"
GOOD_SHA="$(json_field "${ARTIFACT_DIR}/scm-reset-response.json" main_sha)"

kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
external_gitops_sync "${ARTIFACT_DIR}/good.yaml"
kubectl -n "${NAMESPACE}" rollout status "deployment/${WORKLOAD}" --timeout=180s >/dev/null

write_release_request "${ARTIFACT_DIR}/bad-render-request.json" \
  "${APPLICATION_ID}" "${BAD_IMAGE}" "${GOOD_SHA}" 0
http -fsS -b "${COOKIE_JAR}" -H 'content-type: application/json' \
  -H 'x-service-csrf: same-origin' \
  --data-binary "@${ARTIFACT_DIR}/bad-render-request.json" \
  "${API_BASE}/release-plans/render-manifest" >"${ARTIFACT_DIR}/bad-render-response.json"
extract_manifest "${ARTIFACT_DIR}/bad-render-response.json" "${ARTIFACT_DIR}/bad.yaml"
write_files_payload "${ARTIFACT_DIR}/bad-commit.json" "${ARTIFACT_DIR}/bad.yaml" commit
http -fsS -H "@${SCM_ADMIN_HEADER}" -H 'content-type: application/json' \
  --data-binary "@${ARTIFACT_DIR}/bad-commit.json" \
  "${SCM_BASE}/demo/commits" >"${ARTIFACT_DIR}/bad-commit-response.json"
BAD_SHA="$(json_field "${ARTIFACT_DIR}/bad-commit-response.json" commit_sha)"
external_gitops_sync "${ARTIFACT_DIR}/bad.yaml"
if kubectl -n "${NAMESPACE}" rollout status "deployment/${WORKLOAD}" --timeout=20s \
  >/dev/null 2>&1; then
  echo "bad rollout unexpectedly became ready" >&2
  exit 1
fi
scene "bad-rollout-observed"

write_release_request "${ARTIFACT_DIR}/safe-pr-request.json" \
  "${APPLICATION_ID}" "${GOOD_IMAGE}" "${BAD_SHA}" 1
http -fsS -b "${COOKIE_JAR}" -H 'content-type: application/json' \
  -H 'x-service-csrf: same-origin' \
  --data-binary "@${ARTIFACT_DIR}/safe-pr-request.json" \
  "${API_BASE}/release-plans/render-manifest/safe-pr" \
  >"${ARTIFACT_DIR}/safe-pr-response.json"
CORRELATION_ID="$(json_field "${ARTIFACT_DIR}/safe-pr-response.json" correlation_id)"
if [[ ! "${CORRELATION_ID}" =~ ^[A-Za-z0-9_-]+$ ]]; then
  echo "unsafe correlation id returned by API" >&2
  exit 1
fi
scene "safe-pr-requested"

PR_URL=""
for _attempt in {1..90}; do
  PR_URL="$(
    kubectl -n "${OPSIA_NAMESPACE}" exec "statefulset/${OPSIA_RELEASE}-postgresql" -- \
      sh -ec "PGPASSWORD=\"\${POSTGRES_PASSWORD}\" psql -U \"\${POSTGRES_USER}\" -d \"\${POSTGRES_DB}\" -Atc \"select payload->>'pr_url' from audit_log where correlation_id='${CORRELATION_ID}' and subject='safe_pr.created' order by id desc limit 1\"" \
      2>/dev/null || true
  )"
  [[ -n "${PR_URL}" ]] && break
  sleep 1
done
if [[ -z "${PR_URL}" ]]; then
  echo "safe_pr.created was not observed for ${CORRELATION_ID}" >&2
  kubectl -n "${OPSIA_NAMESPACE}" logs deployment/opsia-controller --tail=200 >&2 || true
  exit 1
fi
printf '%s\n' "${PR_URL}" >"${ARTIFACT_DIR}/safe-pr-url.txt"
PR_NUMBER="$(pull_request_number "${PR_URL}")"
http -fsS -H "@${SCM_WRITER_HEADER}" \
  "${SCM_BASE}/demo/pulls/${PR_NUMBER}" >"${ARTIFACT_DIR}/pull-request.json"
PR_BASE_SHA="$(json_field "${ARTIFACT_DIR}/pull-request.json" base.sha)"
PR_HEAD_SHA="$(json_field "${ARTIFACT_DIR}/pull-request.json" head.sha)"
if [[ "${PR_BASE_SHA}" != "${BAD_SHA}" ]]; then
  echo "safe PR base mismatch: expected ${BAD_SHA}, got ${PR_BASE_SHA}" >&2
  exit 1
fi
scene "safe-pr-created"

python3 - "${ARTIFACT_DIR}/merge-request.json" "${PR_BASE_SHA}" "${PR_HEAD_SHA}" <<'PY'
import json
import sys

with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(
        {"expected_base_sha": sys.argv[2], "expected_head_sha": sys.argv[3]},
        handle,
        separators=(",", ":"),
    )
PY
http -fsS -X POST -H "@${SCM_REVIEWER_HEADER}" -H 'content-type: application/json' \
  --data-binary "@${ARTIFACT_DIR}/merge-request.json" \
  "${SCM_BASE}/demo/pulls/${PR_NUMBER}/merge" >"${ARTIFACT_DIR}/merge-response.json"
MERGE_SHA="$(json_field "${ARTIFACT_DIR}/merge-response.json" merge_commit_sha)"
scene "review-merged"
http -fsS -G -H "@${SCM_WRITER_HEADER}" \
  --data-urlencode "ref=${MERGE_SHA}" \
  "${SCM_BASE}/demo/files/${MANIFEST_PATH}" >"${ARTIFACT_DIR}/merged-file.json"
python3 - "${ARTIFACT_DIR}/merged-file.json" "${ARTIFACT_DIR}/merged.yaml" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    content = json.load(handle)["content"]
with open(sys.argv[2], "w", encoding="utf-8") as handle:
    handle.write(content)
PY
if ! grep -Fq "image: ${GOOD_IMAGE}" "${ARTIFACT_DIR}/merged.yaml"; then
  echo "merged PR does not restore the verified image" >&2
  exit 1
fi
if grep -Fq "image: ${BAD_IMAGE}" "${ARTIFACT_DIR}/merged.yaml"; then
  echo "merged PR still contains the failed image" >&2
  exit 1
fi
external_gitops_sync "${ARTIFACT_DIR}/merged.yaml"
scene "gitops-sync-applied"
kubectl -n "${NAMESPACE}" rollout status "deployment/${WORKLOAD}" --timeout=180s >/dev/null
READY="$(kubectl -n "${NAMESPACE}" get deployment "${WORKLOAD}" -o jsonpath='{.status.readyReplicas}')"
FINAL_IMAGE="$(
  kubectl -n "${NAMESPACE}" get deployment "${WORKLOAD}" \
    -o jsonpath='{.spec.template.spec.containers[0].image}'
)"
if [[ "${READY}" != "1" || "${FINAL_IMAGE}" != "${GOOD_IMAGE}" ]]; then
  echo "workload did not normalize: ready=${READY:-0}, image=${FINAL_IMAGE}" >&2
  exit 1
fi
kubectl -n "${NAMESPACE}" get deployment "${WORKLOAD}" \
  --show-managed-fields -o json >"${ARTIFACT_DIR}/final-deployment.json"
python3 - "${ARTIFACT_DIR}/final-deployment.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    deployment = json.load(handle)
writers = sorted(
    {
        item.get("manager", "")
        for item in deployment["metadata"].get("managedFields", [])
        if item.get("subresource") in {None, ""}
        and item.get("operation") == "Apply"
        and "f:spec" in item.get("fieldsV1", {})
    }
)
if writers != ["opsia-demo-gitops"]:
    raise SystemExit(f"unexpected desired-state writers: {writers}")
PY
scene "workload-normalized"
echo "[demo] safe_pr_url=${PR_URL}"
echo "[demo] bad_revision=${BAD_SHA}"
echo "[demo] merged_revision=${MERGE_SHA}"
echo "[demo] artifacts=${ARTIFACT_DIR}"
