#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KUBE_CONTEXT="${KUBE_CONTEXT:-mgmt}"
NAMESPACE="${NAMESPACE:-management}"
AGENT_API_SERVICE_NAME="${AGENT_API_SERVICE_NAME:-agent-api}"

: "${AGENT_API_DOMAIN:?AGENT_API_DOMAIN is required}"
: "${AGENT_API_ACM_CERT_ARN:?AGENT_API_ACM_CERT_ARN is required}"
: "${CLOUDFLARE_API_TOKEN:?CLOUDFLARE_API_TOKEN is required}"
: "${CLOUDFLARE_ZONE_ID:?CLOUDFLARE_ZONE_ID is required}"

if [[ ! "${AGENT_API_DOMAIN}" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]]; then
  echo "AGENT_API_DOMAIN 형식이 올바르지 않습니다" >&2
  exit 2
fi
if [[ ! "${AGENT_API_ACM_CERT_ARN}" =~ ^arn:aws:acm:[a-z0-9-]+:[0-9]{12}:certificate/[a-zA-Z0-9-]+$ ]]; then
  echo "AGENT_API_ACM_CERT_ARN 형식이 올바르지 않습니다" >&2
  exit 2
fi

kubectl --context "${KUBE_CONTEXT}" apply -f "${ROOT_DIR}/deploy/management/agent-api-proxy.yaml"
kubectl --context "${KUBE_CONTEXT}" -n "${NAMESPACE}" rollout status \
  deployment/agent-api-proxy --timeout=5m

# 인증서 ARN은 환경별 값이므로 소스에 기록하지 않고 배포 시점에만 주입한다.
cat <<YAML | kubectl --context "${KUBE_CONTEXT}" apply -f -
apiVersion: v1
kind: Service
metadata:
  name: ${AGENT_API_SERVICE_NAME}
  namespace: ${NAMESPACE}
  annotations:
    # TLS 종료 뒤에는 HTTP 재프록시가 아닌 TCP 전달로 WebSocket Upgrade를 보존한다.
    service.beta.kubernetes.io/aws-load-balancer-backend-protocol: tcp
    service.beta.kubernetes.io/aws-load-balancer-ssl-cert: ${AGENT_API_ACM_CERT_ARN}
    service.beta.kubernetes.io/aws-load-balancer-ssl-ports: https
    service.beta.kubernetes.io/aws-load-balancer-connection-idle-timeout: "60"
spec:
  type: LoadBalancer
  selector:
    app: agent-api-proxy
  ports:
    - name: https
      port: 443
      targetPort: http
YAML

load_balancer_hostname=""
for _ in $(seq 1 90); do
  load_balancer_hostname="$(
    kubectl --context "${KUBE_CONTEXT}" -n "${NAMESPACE}" get service \
      "${AGENT_API_SERVICE_NAME}" \
      -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true
  )"
  [[ -n "${load_balancer_hostname}" ]] && break
  sleep 5
done
if [[ -z "${load_balancer_hostname}" ]]; then
  echo "agent API LoadBalancer 주소를 확인하지 못했습니다" >&2
  exit 1
fi

cloudflare_api="https://api.cloudflare.com/client/v4/zones/${CLOUDFLARE_ZONE_ID}/dns_records"
existing_record="$(
  curl -fsS -G \
    -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
    --data-urlencode "type=CNAME" \
    --data-urlencode "name=${AGENT_API_DOMAIN}" \
    "${cloudflare_api}"
)"
record_id="$(printf '%s' "${existing_record}" | jq -r '.result[0].id // empty')"
record_body="$(
  jq -cn \
    --arg name "${AGENT_API_DOMAIN}" \
    --arg content "${load_balancer_hostname}" \
    '{type:"CNAME",name:$name,content:$content,ttl:300,proxied:false}'
)"

if [[ -n "${record_id}" ]]; then
  cloudflare_response="$(
    curl -fsS -X PUT \
      -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
      -H "Content-Type: application/json" \
      --data "${record_body}" \
      "${cloudflare_api}/${record_id}"
  )"
else
  cloudflare_response="$(
    curl -fsS -X POST \
      -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
      -H "Content-Type: application/json" \
      --data "${record_body}" \
      "${cloudflare_api}"
  )"
fi
if [[ "$(printf '%s' "${cloudflare_response}" | jq -r '.success')" != "true" ]]; then
  printf '%s\n' "${cloudflare_response}" | jq '{success,errors}' >&2
  exit 1
fi

agent_api_base_url="https://${AGENT_API_DOMAIN}/api"
runtime_patch="$(jq -cn --arg value "${agent_api_base_url}" '{data:{PUBLIC_MANAGEMENT_BASE_URL:$value}}')"
kubectl --context "${KUBE_CONTEXT}" -n "${NAMESPACE}" patch configmap \
  management-runtime-config --type merge -p "${runtime_patch}"
kubectl --context "${KUBE_CONTEXT}" -n "${NAMESPACE}" rollout restart deployment/api-gateway
kubectl --context "${KUBE_CONTEXT}" -n "${NAMESPACE}" rollout status \
  deployment/api-gateway --timeout=5m

agent_api_health() {
  local resolved_ip=""
  if curl -fsS "${agent_api_base_url}/healthz" | jq -e \
    '.status == "ok" and .service == "api-gateway"' >/dev/null; then
    return 0
  fi

  # 새 레코드의 로컬 NXDOMAIN 캐시가 남아도 공용 DoH 결과로 TLS·서비스를 검증한다.
  resolved_ip="$(
    curl -fsS \
      -H "accept: application/dns-json" \
      "https://cloudflare-dns.com/dns-query?name=${AGENT_API_DOMAIN}&type=A" \
      | jq -r '[.Answer[]? | select(.type == 1) | .data][0] // empty'
  )"
  [[ -n "${resolved_ip}" ]] || return 1
  curl -fsS \
    --resolve "${AGENT_API_DOMAIN}:443:${resolved_ip}" \
    "${agent_api_base_url}/healthz" \
    | jq -e '.status == "ok" and .service == "api-gateway"' >/dev/null
}

for _ in $(seq 1 60); do
  if agent_api_health; then
    printf 'agent API endpoint ready: %s\n' "${agent_api_base_url}"
    exit 0
  fi
  sleep 5
done

echo "agent API HTTPS 상태 확인에 실패했습니다" >&2
exit 1
