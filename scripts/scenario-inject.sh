#!/usr/bin/env bash
# sandbox 실서비스 데이터 시나리오 주입기
#
# baseline(shop 마이크로서비스 + load generator)은 상시 유지하고,
# fault 시나리오(crashloop/oom/imagepull/probe-fail/sched-fail/svc-selector)를
# 한 개씩 주입/정리한다. 모든 리소스는 scenario=<name> label로 관리되어
# label selector 삭제만으로 정리가 끝난다.
#
# 안전 경계: 이 스크립트는 target cluster의 sandbox namespace만 만진다.
# (deploy/target/target.yaml의 cluster-agent-sandbox-write 정책과 동일한 경계)
#
# 사용:
#   TARGET_CONTEXT=target1 bash scripts/scenario-inject.sh baseline
#   TARGET_CONTEXT=target1 bash scripts/scenario-inject.sh inject crashloop
#   TARGET_CONTEXT=target1 bash scripts/scenario-inject.sh inject all
#   TARGET_CONTEXT=target1 bash scripts/scenario-inject.sh status [scenario]
#   TARGET_CONTEXT=target1 bash scripts/scenario-inject.sh cleanup crashloop
#   TARGET_CONTEXT=target1 bash scripts/scenario-inject.sh cleanup faults   # baseline은 남긴다
#   TARGET_CONTEXT=target1 bash scripts/scenario-inject.sh cleanup all     # baseline까지 제거
#   TARGET_CONTEXT=target1 bash scripts/scenario-inject.sh load start 8    # worker 8개로 데모 부하 켜기
#   TARGET_CONTEXT=target1 bash scripts/scenario-inject.sh load stop       # 데모 부하 끄기
#   TARGET_CONTEXT=target1 bash scripts/scenario-inject.sh load status
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT_DIR}/scripts/lib/env.sh"

SCENARIO_DIR="${ROOT_DIR}/src/samples/scenarios"
TARGET_CONTEXT="${TARGET_CONTEXT:-}"
NS="sandbox"
# scenario label이 붙는 리소스 종류 전체 — cleanup label selector와 정합 유지
RESOURCE_KINDS="deployments,services,configmaps,horizontalpodautoscalers"
FAULTS=(crashloop oom imagepull probe-fail sched-fail svc-selector)

require_env TARGET_CONTEXT

k() { kubectl --context "${TARGET_CONTEXT}" -n "${NS}" "$@"; }

log() { printf '%s [scenario] %s\n' "$(date +%T)" "$*"; }

ensure_namespace() {
  if ! kubectl --context "${TARGET_CONTEXT}" get namespace "${NS}" >/dev/null 2>&1; then
    log "namespace ${NS} 없음 — 생성"
    kubectl --context "${TARGET_CONTEXT}" create namespace "${NS}"
  fi
}

known_fault() {
  local name="$1"
  for fault in "${FAULTS[@]}"; do
    [ "${fault}" = "${name}" ] && return 0
  done
  return 1
}

apply_baseline() {
  ensure_namespace
  log "baseline 적용 (context=${TARGET_CONTEXT}, ns=${NS})"
  k apply -f "${SCENARIO_DIR}/base/"
  log "baseline rollout 대기"
  for deploy in shop-redis shop-api shop-frontend shop-worker shop-loadgen; do
    k rollout status "deploy/${deploy}" --timeout=180s
  done
  log "baseline 완료 — load generator가 상시 트래픽을 만든다"
}

inject_fault() {
  local name="$1"
  ensure_namespace
  log "fault 주입: ${name}"
  k apply -f "${SCENARIO_DIR}/faults/${name}.yaml"
  log "주입 완료 — 증상 확인: bash scripts/scenario-inject.sh status ${name}"
}

show_status() {
  local selector="scenario"
  if [ -n "${1:-}" ]; then
    selector="scenario=$1"
  fi
  log "pods (label ${selector})"
  k get pods -l "${selector}" -o wide || true
  log "최근 Warning 이벤트"
  k get events --field-selector type=Warning \
    --sort-by=.lastTimestamp 2>/dev/null | tail -15 || true
}

control_load() {
  local action="$1"
  local factor="${2:-8}"
  case "${action}" in
    start)
      [[ "${factor}" =~ ^[1-9][0-9]*$ ]] || {
        echo "부하 worker 수는 1 이상의 정수여야 합니다: ${factor}" >&2
        exit 1
      }
      log "데모 부하 시작: shop-loadgen ${factor} workers"
      # EKS 단일 노드의 Pod 상한을 넘기지 않도록 Pod는 하나만 두고 내부 worker를 조절한다.
      k scale deployment/shop-loadgen --replicas=0
      k wait --for=delete pod -l app=shop-loadgen --timeout=120s 2>/dev/null || true
      k set env deployment/shop-loadgen LOAD_FACTOR="${factor}"
      k scale deployment/shop-loadgen --replicas=1
      k rollout status deployment/shop-loadgen --timeout=120s
      ;;
    stop)
      log "데모 부하 중지: shop-loadgen 0 replicas"
      k scale deployment/shop-loadgen --replicas=0
      k wait --for=delete pod -l app=shop-loadgen --timeout=120s 2>/dev/null || true
      ;;
    status)
      k get deployment/shop-loadgen -o custom-columns='NAME:.metadata.name,DESIRED:.spec.replicas,READY:.status.readyReplicas,WORKERS:.spec.template.spec.containers[0].env[?(@.name=="LOAD_FACTOR")].value'
      ;;
    *)
      echo "알 수 없는 load 명령: ${action} (가능: start [workers], stop, status)" >&2
      exit 1
      ;;
  esac
}

cleanup_scenario() {
  local name="$1"
  log "cleanup: scenario=${name}"
  k delete "${RESOURCE_KINDS}" -l "scenario=${name}" --ignore-not-found
  # 삭제 완료 확인 — 남은 pod가 있으면 종료를 기다린다
  k wait --for=delete pods -l "scenario=${name}" --timeout=120s 2>/dev/null || true
  local remain
  remain="$(k get "${RESOURCE_KINDS}" -l "scenario=${name}" --no-headers 2>/dev/null | wc -l | tr -d ' ')"
  log "cleanup 결과: scenario=${name} 남은 리소스 ${remain}개"
}

usage() {
  sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 1
}

COMMAND="${1:-}"
ARG="${2:-}"
VALUE="${3:-}"

case "${COMMAND}" in
  baseline)
    apply_baseline
    ;;
  inject)
    [ -n "${ARG}" ] || usage
    if [ "${ARG}" = "all" ]; then
      for fault in "${FAULTS[@]}"; do inject_fault "${fault}"; done
    else
      known_fault "${ARG}" || { echo "알 수 없는 시나리오: ${ARG} (가능: ${FAULTS[*]})" >&2; exit 1; }
      inject_fault "${ARG}"
    fi
    ;;
  status)
    show_status "${ARG}"
    ;;
  load)
    [ -n "${ARG}" ] || usage
    control_load "${ARG}" "${VALUE:-8}"
    ;;
  cleanup)
    [ -n "${ARG}" ] || usage
    case "${ARG}" in
      all)
        for fault in "${FAULTS[@]}"; do cleanup_scenario "${fault}"; done
        cleanup_scenario baseline
        ;;
      faults)
        for fault in "${FAULTS[@]}"; do cleanup_scenario "${fault}"; done
        ;;
      *)
        if [ "${ARG}" != "baseline" ]; then
          known_fault "${ARG}" || { echo "알 수 없는 시나리오: ${ARG}" >&2; exit 1; }
        fi
        cleanup_scenario "${ARG}"
        ;;
    esac
    ;;
  list)
    echo "baseline"
    printf '%s\n' "${FAULTS[@]}"
    ;;
  *)
    usage
    ;;
esac
