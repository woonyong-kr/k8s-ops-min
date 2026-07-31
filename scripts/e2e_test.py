#!/usr/bin/env python3
"""KubeHeal E2E 파이프라인 테스트 — 실제 인프라 동작 검증.

API 응답만 보는 게 아니라 실제 K8s 리소스 변경, NATS 이벤트 발행/소비,
에이전트 커맨드 실행, 상태 전이를 추적합니다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass

import requests

BASE = os.environ.get("BASE_URL", "").rstrip("/")
EMAIL = os.environ.get("AUTH_EMAIL", "")
PASSWORD = os.environ.get("AUTH_PASSWORD", "")
CLUSTER_ID = os.environ.get("SMOKE_CLUSTER_ID", os.environ.get("TARGET_CLUSTER_ID", ""))
KUBECTL_CTX = os.environ.get("MGMT_CONTEXT", "")
TARGET_CTX = os.environ.get("TARGET_CONTEXT", os.environ.get("TARGET_CLUSTER", ""))
TEST_NS = "sandbox"
TEST_DEPLOY_NAME = "e2e-test-nginx"
TIMEOUT = 60


# ── result tracking ────────────────────────────────────────────
@dataclass
class Check:
    category: str
    name: str
    ok: bool
    detail: str = ""


checks: list[Check] = []


def check(category: str, name: str, ok: bool, detail: str = ""):
    c = Check(category, name, ok, detail)
    checks.append(c)
    sym = "✅" if ok else "❌"
    print(f"  {sym} [{category}] {name}  {detail}")
    return ok


def require_env(name: str, value: str) -> None:
    if value:
        return
    print(f"missing required environment variable: {name}", file=sys.stderr)
    raise SystemExit(2)


# ── helpers ────────────────────────────────────────────────────
def kubectl(ctx: str, *args: str, timeout: int = 30) -> tuple[int, str]:
    cmd = ["kubectl", "--context", ctx] + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return 1, "timeout"


def kubectl_json(ctx: str, *args: str) -> dict | list | None:
    rc, out = kubectl(ctx, *args, "-o", "json")
    if rc != 0:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def api_client() -> requests.Session:
    s = requests.Session()
    s.headers.update({"x-service-csrf": "same-origin"})
    resp = s.post(
        f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=TIMEOUT
    )
    resp.raise_for_status()
    return s


def wait_for(fn, desc: str, timeout: int = 60, interval: int = 3) -> bool:
    """Poll fn() until it returns True or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(interval)
    print(f"    ⏰ timeout waiting for: {desc}")
    return False


# ╔══════════════════════════════════════════════════════════════╗
# ║ 1. CLUSTER REGISTRATION E2E                                ║
# ╚══════════════════════════════════════════════════════════════╝
def test_cluster_registration(c: requests.Session):
    print("\n═══ 1. 클러스터 등록 E2E ═══")

    # 1-1. API에서 타겟 클러스터 등록 확인
    resp = c.get(f"{BASE}/clusters/{CLUSTER_ID}", timeout=TIMEOUT)
    data = resp.json() if resp.status_code == 200 else {}
    check("등록", "API에서 클러스터 조회", resp.status_code == 200, f"status={data.get('status')}")

    # 1-2. cluster-agent Deployment가 target 클러스터에 존재
    deploy = kubectl_json(TARGET_CTX, "-n", "target", "get", "deploy/cluster-agent")
    exists = deploy is not None
    ready = 0
    if exists:
        ready = deploy.get("status", {}).get("readyReplicas", 0) or 0
    check("등록", "cluster-agent Deployment 존재", exists)
    check("등록", "cluster-agent 파드 Ready", ready >= 1, f"ready={ready}")

    # 1-3. connection-status API
    resp = c.get(f"{BASE}/clusters/{CLUSTER_ID}/connection-status", timeout=TIMEOUT)
    status_body = resp.json() if resp.status_code == 200 else {}
    connected = status_body.get("connected", False)
    check("등록", "connection-status API 응답", resp.status_code == 200)
    check("등록", "에이전트 연결 상태 (connected)", connected, str(status_body))


# ╔══════════════════════════════════════════════════════════════╗
# ║ 2. AGENT LIVENESS & POLLING                                ║
# ╚══════════════════════════════════════════════════════════════╝
def test_agent_liveness():
    print("\n═══ 2. 에이전트 기동 & 폴링 ═══")

    # 2-1. cluster-agent 로그에서 연결/폴링 확인
    rc, logs = kubectl(TARGET_CTX, "-n", "target", "logs", "deploy/cluster-agent", "--tail=50")
    check("에이전트", "cluster-agent 로그 접근 가능", rc == 0)

    has_connect = (
        "agent/connect" in logs.lower() or "connected" in logs.lower() or "register" in logs.lower()
    )
    check(
        "에이전트",
        "로그에 연결/등록 흔적",
        has_connect,
        f"found in logs: {'yes' if has_connect else 'no'}",
    )

    has_poll = "poll" in logs.lower() or "commands" in logs.lower()
    check(
        "에이전트",
        "로그에 커맨드 폴링 흔적",
        has_poll,
        f"found in logs: {'yes' if has_poll else 'no'}",
    )


# ╔══════════════════════════════════════════════════════════════╗
# ║ 3. METRICS / INVENTORY COLLECTION                          ║
# ╚══════════════════════════════════════════════════════════════╝
def test_metrics_inventory(c: requests.Session):
    print("\n═══ 3. 메트릭/인벤토리 수집 ═══")

    # 3-1. node-collector DaemonSet 존재
    ds = kubectl_json(TARGET_CTX, "-n", "target", "get", "ds/optional-node-collector")
    exists = ds is not None
    desired = ds.get("status", {}).get("desiredNumberScheduled", 0) if ds else 0
    ready_nc = ds.get("status", {}).get("numberReady", 0) if ds else 0
    check("메트릭", "node-collector DaemonSet 존재", exists)
    check(
        "메트릭", "node-collector 파드 Ready", ready_nc >= 1, f"desired={desired} ready={ready_nc}"
    )

    # 3-2. node-collector /metrics 엔드포인트 (exec into pod)
    rc, metrics = kubectl(
        TARGET_CTX,
        "-n",
        "target",
        "exec",
        "ds/optional-node-collector",
        "--",
        "wget",
        "-qO-",
        "http://localhost:9100/metrics",
        timeout=15,
    )
    has_cpu = "node_collector_cpu" in metrics if rc == 0 else False
    check("메트릭", "node-collector /metrics 응답", rc == 0)
    check("메트릭", "CPU 메트릭 포함", has_cpu)

    # 3-3. inventory API에서 실제 데이터 조회
    for endpoint in ("workloads", "resources", "services", "events", "summary"):
        resp = c.get(f"{BASE}/clusters/{CLUSTER_ID}/inventory/{endpoint}", timeout=TIMEOUT)
        body = resp.json() if resp.status_code == 200 else {}
        # Check the response is not empty
        has_data = bool(body) and body != {} and body != []
        check(
            "인벤토리",
            f"/inventory/{endpoint} 실데이터",
            has_data,
            f"keys={list(body.keys()) if isinstance(body, dict) else len(body)}",
        )


# ╔══════════════════════════════════════════════════════════════╗
# ║ 4. NATS JETSTREAM & OUTBOX RELAY                           ║
# ╚══════════════════════════════════════════════════════════════╝
def test_nats_events():
    print("\n═══ 4. NATS JetStream & Outbox Relay ═══")

    # 4-1. NATS pod alive
    rc, _ = kubectl(
        KUBECTL_CTX, "-n", "management", "exec", "nats-0", "--", "sh", "-c", "echo healthy"
    )
    check("NATS", "NATS 파드 접근 가능", rc == 0)

    # 4-2. JetStream stream info via nats CLI (may not be available inside container)
    # Try using the nats tool that comes with nats image
    rc, stream_info = kubectl(
        KUBECTL_CTX,
        "-n",
        "management",
        "exec",
        "nats-0",
        "--",
        "sh",
        "-c",
        "wget -qO- 'http://localhost:8222/jsz?streams=true' 2>/dev/null || echo 'no_monitoring'",
    )
    if "no_monitoring" not in stream_info:
        try:
            jsz = json.loads(stream_info)
            streams = jsz.get("streams", []) if isinstance(jsz, dict) else []
            check(
                "NATS",
                "JetStream 스트림 존재",
                len(streams) > 0 if isinstance(streams, list) else bool(streams),
                f"streams={len(streams) if isinstance(streams, list) else 'N/A'}",
            )
        except json.JSONDecodeError:
            check("NATS", "JetStream 스트림 존재", False, f"parse error: {stream_info[:100]}")
    else:
        check("NATS", "JetStream 모니터링", False, "monitoring port not available")

    # 4-3. outbox relay: gateway 로그에서 relay 활동 확인
    rc, gw_logs = kubectl(
        KUBECTL_CTX, "-n", "management", "logs", "deploy/api-gateway", "--tail=100"
    )
    has_relay = (
        "relay" in gw_logs.lower() or "outbox" in gw_logs.lower() or "publish" in gw_logs.lower()
    )
    check("NATS", "gateway 로그에 outbox relay 활동", has_relay)

    # 4-4. audit-worker 로그에서 이벤트 소비 확인
    rc, audit_logs = kubectl(
        KUBECTL_CTX, "-n", "management", "logs", "deploy/audit-worker", "--tail=50"
    )
    has_events = len(audit_logs) > 50 if rc == 0 else False
    check("NATS", "audit-worker가 이벤트 소비 중", has_events, f"log_len={len(audit_logs)}")


# ╔══════════════════════════════════════════════════════════════╗
# ║ 5. K8S COMMAND EXECUTION — FULL E2E                        ║
# ╚══════════════════════════════════════════════════════════════╝
def test_k8s_commands(c: requests.Session):
    print("\n═══ 5. K8s 커맨드 실행 E2E ═══")

    # 5-0. Create a test deployment in target cluster
    print("  → 테스트 디플로이먼트 생성...")
    manifest = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": TEST_DEPLOY_NAME, "namespace": TEST_NS},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": TEST_DEPLOY_NAME}},
            "template": {
                "metadata": {"labels": {"app": TEST_DEPLOY_NAME}},
                "spec": {
                    "containers": [
                        {
                            "name": "nginx",
                            "image": "nginx:alpine",
                            "ports": [{"containerPort": 80}],
                        }
                    ],
                },
            },
        },
    }
    manifest_json = json.dumps(manifest)
    proc = subprocess.run(
        ["kubectl", "--context", TARGET_CTX, "-n", TEST_NS, "apply", "-f", "-"],
        input=manifest_json,
        capture_output=True,
        text=True,
        timeout=15,
    )
    check("커맨드", "테스트 디플로이먼트 생성", proc.returncode == 0, proc.stdout.strip())

    # Wait for deployment to be ready
    wait_for(
        lambda: (
            (kubectl_json(TARGET_CTX, "-n", TEST_NS, "get", f"deploy/{TEST_DEPLOY_NAME}") or {})
            .get("status", {})
            .get("readyReplicas", 0)
            >= 1
        ),
        "test deployment ready",
        timeout=90,
    )

    # 5-1. ROLLOUT RESTART via API
    print("\n  ── 5-1. Rollout Restart ──")
    resp = c.post(
        f"{BASE}/clusters/{CLUSTER_ID}/namespaces/{TEST_NS}/deployments/{TEST_DEPLOY_NAME}/restart",
        timeout=TIMEOUT,
    )
    restart_ok = resp.status_code in (200, 201, 202)
    restart_body = resp.json() if resp.status_code in (200, 201, 202) else resp.text[:200]
    check("커맨드", "rollout_restart API 호출", restart_ok, str(restart_body)[:150])

    if restart_ok and isinstance(restart_body, dict):
        cmd_id = restart_body.get("command_id") or restart_body.get("id")
        if cmd_id:
            # 5-1a. Track command state transitions
            print(f"    command_id={cmd_id}")

            # Wait for command to complete — check agent logs
            def check_restart_completed():
                rc2, agent_logs = kubectl(
                    TARGET_CTX,
                    "-n",
                    "target",
                    "logs",
                    "deploy/cluster-agent",
                    "--tail=30",
                    "--since=60s",
                )
                return (
                    "restart" in agent_logs.lower()
                    or "completed" in agent_logs.lower()
                    or "result" in agent_logs.lower()
                )

            completed = wait_for(check_restart_completed, "restart command completion", timeout=90)
            check("커맨드", "에이전트가 restart 커맨드 수신/실행", completed)

            # 5-1b. Verify actual pod restart happened
            # After rollout restart, the pod generation should change
            deploy_data = kubectl_json(
                TARGET_CTX, "-n", TEST_NS, "get", f"deploy/{TEST_DEPLOY_NAME}"
            )
            annotations = (
                (deploy_data or {})
                .get("spec", {})
                .get("template", {})
                .get("metadata", {})
                .get("annotations", {})
            )
            has_restart_annotation = "kubectl.kubernetes.io/restartedAt" in str(
                annotations
            ) or "restartedAt" in str(annotations)
            check(
                "커맨드",
                "실제 파드 restart 발생 (annotation)",
                has_restart_annotation,
                str(annotations)[:100],
            )
    else:
        check("커맨드", "rollout_restart 커맨드 ID 확인", False, str(restart_body)[:100])

    # 5-2. SCALE via API
    print("\n  ── 5-2. Scale ──")
    resp = c.post(
        f"{BASE}/clusters/{CLUSTER_ID}/namespaces/{TEST_NS}/deployments/{TEST_DEPLOY_NAME}/scale",
        json={"replicas": 2},
        timeout=TIMEOUT,
    )
    scale_ok = resp.status_code in (200, 201, 202)
    scale_body = resp.json() if scale_ok else resp.text[:200]
    check("커맨드", "scale API 호출", scale_ok, str(scale_body)[:150])

    if scale_ok:
        # Wait for scale to take effect
        def check_scaled():
            d = kubectl_json(TARGET_CTX, "-n", TEST_NS, "get", f"deploy/{TEST_DEPLOY_NAME}")
            return (d or {}).get("status", {}).get("readyReplicas", 0) >= 2

        scaled = wait_for(check_scaled, "scale to 2 replicas", timeout=90)
        check("커맨드", "실제 레플리카 2개로 스케일", scaled)

    # 5-3. Command state tracking via command-worker logs
    print("\n  ── 5-3. 커맨드 상태 전이 확인 ──")
    rc, cw_logs = kubectl(
        KUBECTL_CTX,
        "-n",
        "management",
        "logs",
        "deploy/command-worker",
        "--tail=100",
        "--since=120s",
    )
    has_requested = (
        "command.requested" in cw_logs.lower()
        or "commandrequested" in cw_logs.lower()
        or "handle_command" in cw_logs.lower()
    )
    has_dispatched = "dispatch" in cw_logs.lower()
    has_queued = "queued" in cw_logs.lower()
    check("상태전이", "command-worker: command.requested 처리", has_requested)
    check("상태전이", "command-worker: dispatch 처리", has_dispatched)
    check("상태전이", "command-worker: queued_for_agent 발행", has_queued)

    # Agent-side state transitions
    rc, agent_logs = kubectl(
        TARGET_CTX, "-n", "target", "logs", "deploy/cluster-agent", "--tail=100", "--since=120s"
    )
    has_leased = "lease" in agent_logs.lower() or "start" in agent_logs.lower()
    has_running = (
        "running" in agent_logs.lower()
        or "executing" in agent_logs.lower()
        or "execute" in agent_logs.lower()
    )
    has_completed = (
        "completed" in agent_logs.lower()
        or "result" in agent_logs.lower()
        or "success" in agent_logs.lower()
    )
    check("상태전이", "에이전트: 커맨드 lease/start", has_leased)
    check("상태전이", "에이전트: 커맨드 실행", has_running)
    check("상태전이", "에이전트: 결과 보고 (completed)", has_completed)

    # 5-4. NATS event chain verification
    print("\n  ── 5-4. 이벤트 체인 (command → completion) ──")
    # workflow-controller should have processed events
    rc, wf_logs = kubectl(
        KUBECTL_CTX,
        "-n",
        "management",
        "logs",
        "deploy/workflow-controller",
        "--tail=50",
        "--since=120s",
    )
    wf_has_queued = "queued" in wf_logs.lower() or "command" in wf_logs.lower()
    check("이벤트체인", "workflow-controller: command 이벤트 소비", wf_has_queued)

    # 5-5. CLEANUP: delete test deployment
    print("\n  ── 5-5. 테스트 디플로이먼트 정리 ──")
    rc, out = kubectl(
        TARGET_CTX, "-n", TEST_NS, "delete", f"deploy/{TEST_DEPLOY_NAME}", "--ignore-not-found"
    )
    check("정리", "테스트 디플로이먼트 삭제", rc == 0)


# ╔══════════════════════════════════════════════════════════════╗
# ║ 6. GITOPS FLOW (limited — no real git repo)                ║
# ╚══════════════════════════════════════════════════════════════╝
def test_gitops_flow():
    print("\n═══ 6. GitOps 파이프라인 (워커 상태 확인) ═══")

    # GitOps requires a real GitHub repo. In local-test mode we verify:
    # - git-pull-worker is running and subscribed
    # - manifest-render-worker is running
    # - diff-worker, diff-analyze-worker, safe-pr-worker are running
    # - workflow-controller is orchestrating

    workers = [
        "git-pull-worker",
        "manifest-render-worker",
        "diff-worker",
        "diff-analyze-worker",
        "safe-pr-worker",
        "workflow-controller",
    ]
    for w in workers:
        deploy = kubectl_json(KUBECTL_CTX, "-n", "management", "get", f"deploy/{w}")
        if deploy is None:
            check("GitOps", f"{w} Deployment 존재", False, "not found")
            continue
        ready = deploy.get("status", {}).get("readyReplicas", 0) or 0
        replicas = deploy.get("spec", {}).get("replicas", 0) or 0
        # Workers with 0 replicas are fine if they're intentionally not needed for smoke
        if replicas == 0:
            check("GitOps", f"{w} 배포됨 (replicas=0, 스케일다운)", True, "의도적 0 replicas")
        else:
            check("GitOps", f"{w} Ready", ready >= 1, f"ready={ready}/{replicas}")

    # Check github-poll-worker Deployment exists (scaled down by default for local)
    poller = kubectl_json(KUBECTL_CTX, "-n", "management", "get", "deploy/github-poll-worker")
    exists = poller is not None
    replicas = (poller or {}).get("spec", {}).get("replicas", 0) or 0
    ready = (poller or {}).get("status", {}).get("readyReplicas", 0) or 0
    check("GitOps", "github-poll-worker Deployment 존재", exists, f"ready={ready}/{replicas}")


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════
def main():
    require_env("BASE_URL", BASE)
    require_env("AUTH_EMAIL", EMAIL)
    require_env("AUTH_PASSWORD", PASSWORD)
    require_env("SMOKE_CLUSTER_ID or TARGET_CLUSTER_ID", CLUSTER_ID)
    require_env("MGMT_CONTEXT", KUBECTL_CTX)
    require_env("TARGET_CONTEXT or TARGET_CLUSTER", TARGET_CTX)

    print("=" * 72)
    print("  KubeHeal E2E Pipeline Test")
    print(f"  Management: {KUBECTL_CTX}  |  Target: {TARGET_CTX}")
    print(f"  Gateway: {BASE}")
    print("=" * 72)

    c = api_client()
    try:
        test_cluster_registration(c)
        test_agent_liveness()
        test_metrics_inventory(c)
        test_nats_events()
        test_k8s_commands(c)
        test_gitops_flow()
    finally:
        c.close()

    # ── summary ────────────────────────────────────────────
    print("\n" + "=" * 72)
    total = len(checks)
    passed = sum(1 for c in checks if c.ok)
    failed = sum(1 for c in checks if not c.ok)
    print(f"  TOTAL: {total}  |  PASSED: {passed}  |  FAILED: {failed}")
    print("=" * 72)

    if failed:
        print("\n❌ FAILED CHECKS:")
        for c in checks:
            if not c.ok:
                print(f"  [{c.category}] {c.name}  {c.detail}")
        print()

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
