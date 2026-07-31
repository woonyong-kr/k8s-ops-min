#!/usr/bin/env bash
# DATABASE_URL 이 PostgreSQL 직결로 남아 있는 클러스터를 PgBouncer 경유로 복원한다.
#
# 배경: 서비스는 management-runtime-secret 의 DATABASE_URL 을 사용한다.
#   정상: postgresql://<user>:<pass>@pgbouncer:6432/<db>   (커넥션 풀 프록시 경유)
#   직결: postgresql://<user>:<pass>@postgresql:5432/<db>  (과거 secret 잔재)
#
# 사용:
#   MGMT_CONTEXT=kind-management bash scripts/restore-pgbouncer.sh
#   MGMT_CONTEXT=<eks-context> bash scripts/restore-pgbouncer.sh
set -euo pipefail

MGMT_CONTEXT="${MGMT_CONTEXT:-kind-management}"
NAMESPACE="${NAMESPACE:-management}"
RUNTIME_SECRET="${RUNTIME_SECRET:-management-runtime-secret}"
PGBOUNCER_HOST="${PGBOUNCER_HOST:-pgbouncer}"
PGBOUNCER_PORT="${PGBOUNCER_PORT:-6432}"

b64d() { base64 --decode 2>/dev/null || base64 -D; }

secret_value() {
  kubectl --context "${MGMT_CONTEXT}" -n "${NAMESPACE}" get secret "$1" \
    -o "jsonpath={.data.$2}" | b64d
}

current_url="$(secret_value "${RUNTIME_SECRET}" DATABASE_URL)"
if [ -z "${current_url}" ]; then
  echo "ERROR: ${RUNTIME_SECRET} 에 DATABASE_URL 이 없음" >&2
  exit 1
fi

echo "==> current DATABASE_URL host: $(echo "${current_url}" | sed -E 's#^[a-z]+://[^@]+@##')"
if echo "${current_url}" | grep -q "@${PGBOUNCER_HOST}:${PGBOUNCER_PORT}/"; then
  echo "이미 PgBouncer 경유 — 변경 없음"
  exit 0
fi

postgres_user="$(secret_value postgresql-secret POSTGRES_USER)"
postgres_password="$(secret_value postgresql-secret POSTGRES_PASSWORD)"
postgres_db="$(secret_value postgresql-secret POSTGRES_DB)"
new_url="postgresql://${postgres_user}:${postgres_password}@${PGBOUNCER_HOST}:${PGBOUNCER_PORT}/${postgres_db}"

echo "==> patching ${RUNTIME_SECRET}.DATABASE_URL -> ${PGBOUNCER_HOST}:${PGBOUNCER_PORT}"
encoded="$(printf '%s' "${new_url}" | base64 | tr -d '\n')"
kubectl --context "${MGMT_CONTEXT}" -n "${NAMESPACE}" patch secret "${RUNTIME_SECRET}" \
  --type merge -p "{\"data\":{\"DATABASE_URL\":\"${encoded}\"}}"

echo "==> ensuring pgbouncer deployment is ready"
kubectl --context "${MGMT_CONTEXT}" -n "${NAMESPACE}" rollout status deploy/pgbouncer --timeout=120s

echo "==> restarting deployments to pick up the new DATABASE_URL"
for deploy in $(kubectl --context "${MGMT_CONTEXT}" -n "${NAMESPACE}" get deploy -o name); do
  case "${deploy}" in
    */postgresql|*/redis|*/minio|*/nats|*/pgbouncer|*/console) continue ;;
  esac
  kubectl --context "${MGMT_CONTEXT}" -n "${NAMESPACE}" rollout restart "${deploy}"
done

echo
echo "done. 확인: kubectl --context ${MGMT_CONTEXT} -n ${NAMESPACE} get pods"
