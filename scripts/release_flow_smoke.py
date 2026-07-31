#!/usr/bin/env python3
"""Release-flow operational smoke check.

The default mode is read-only except for authenticated session creation. Pass
--demo-run only when you want to create a tracked demo release run. Pass
--ops-rehearsal to also exercise safe operator actions against that demo run.
The script never attempts live release dispatch. Pass --live-preflight only to
check live readiness gates without starting or dispatching a release run.
Pass --production-preflight to run the common production-readiness checks before
starting another release.
Pass --verification-preflight to fail fast when existing release runs already
have failed or timed-out post-deploy verification jobs.
Pass --run-health-preflight to fail fast when existing release runs still need
operator attention before a new release starts.
Pass --policy-override-preflight to fail fast when existing release runs already
used operator policy overrides that should be reviewed before another release.
Pass --change-freeze-preflight to fail fast when existing release runs were
evaluated during an active change freeze or used a freeze override.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

JsonMap = dict[str, Any]
RETRYABLE_HTTP_STATUSES = {429, 502, 503, 504}
RETRYABLE_METHODS = {"GET", "HEAD", "OPTIONS"}
TRUTHY_VALUES = {"1", "true", "yes"}
REDACTED_VALUE = "<redacted>"
LIVE_PREFLIGHT_PLACEHOLDERS = {
    "live_change_ticket": {"CHG-PREFLIGHT"},
    "live_runbook_url": {"https://example.com/runbooks/release-flow"},
    "live_release_owner": {"release-operator"},
    "live_oncall_contact": {"release-oncall@example.com"},
    "live_image": {"ghcr.io/example/release-flow-smoke:live-preflight"},
    "live_verification_url": {"https://example.com/verify/release-flow"},
}
LIVE_PREFLIGHT_PLACEHOLDER_HOSTS = {"example.com", "example.test", "localhost", "127.0.0.1", "::1"}
SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"(?P<prefix>(?:\"|')?(?:authorization|bearer|credential|password|passwd|private[_ -]?key|secret|token|api[_ -]?key|apikey|cookie|set[_ -]?cookie)(?:\"|')?\s*[:=]\s*)(?P<quote>\"|')?(?P<value>[^,}\]\s\"']+)(?P=quote)?",
    re.IGNORECASE,
)
BEARER_PATTERN = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)


@dataclass
class SmokeResult:
    name: str
    ok: bool
    detail: str


class ApiError(RuntimeError):
    def __init__(self, method: str, path: str, status: int, body: str) -> None:
        super().__init__(f"{method} {path} failed with HTTP {status}: {body[:500]}")
        self.method = method
        self.path = path
        self.status = status
        self.body = body


class ApiClient:
    def __init__(
        self,
        api_base_url: str,
        timeout: float = 15.0,
        *,
        retry_attempts: int = 3,
        retry_delay_seconds: float = 1.0,
    ) -> None:
        self.api_base_url = normalize_base_url(api_base_url)
        self.timeout = timeout
        self.retry_attempts = max(1, retry_attempts)
        self.retry_delay_seconds = max(0.0, retry_delay_seconds)
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def request(
        self,
        method: str,
        path: str,
        payload: JsonMap | None = None,
        *,
        expected: tuple[int, ...] = (200,),
    ) -> JsonMap:
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            api_url(self.api_base_url, path),
            data=body,
            method=method,
            headers={
                "accept": "application/json",
                "content-type": "application/json",
                "x-service-csrf": "same-origin",
            },
        )
        method_upper = method.upper()
        for attempt in range(1, self.retry_attempts + 1):
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    raw = response.read().decode()
                    status = int(response.status)
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode(errors="replace")
                status = int(exc.code)
            except (TimeoutError, urllib.error.URLError, OSError):
                if self.should_retry(method_upper, attempt):
                    self.sleep_before_retry()
                    continue
                raise
            if status in expected:
                return json.loads(raw) if raw else {}
            if self.should_retry(method_upper, attempt, status):
                self.sleep_before_retry()
                continue
            raise ApiError(method, path, status, raw)
        raise ApiError(method, path, 0, "request retry attempts exhausted")

    def should_retry(self, method: str, attempt: int, status: int | None = None) -> bool:
        if method not in RETRYABLE_METHODS:
            return False
        if attempt >= self.retry_attempts:
            return False
        return status is None or status in RETRYABLE_HTTP_STATUSES

    def sleep_before_retry(self) -> None:
        if self.retry_delay_seconds > 0:
            time.sleep(self.retry_delay_seconds)


def normalize_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value:
        raise ValueError("api base URL is required")
    return value


def api_url(base_url: str, path: str) -> str:
    return f"{normalize_base_url(base_url)}/{path.lstrip('/')}"


def query_path(path: str, params: dict[str, Any]) -> str:
    query = urllib.parse.urlencode(
        {key: value for key, value in params.items() if value not in (None, "")}
    )
    return f"{path}?{query}" if query else path


def derive_api_base_url(args: argparse.Namespace) -> str:
    if args.api_base_url:
        return args.api_base_url
    base_url = args.base_url or os.getenv("BASE_URL", "")
    if not base_url:
        return os.getenv("API_BASE_URL", "")
    return f"{base_url.rstrip('/')}/api"


def build_demo_plan(applications: list[JsonMap]) -> JsonMap:
    if not applications:
        raise ValueError("at least one application is required for release-flow smoke")
    selected = applications[: min(3, len(applications))]
    steps = [demo_step(app, index, selected) for index, app in enumerate(selected)]
    return {
        "name": "Release flow smoke check",
        "description": "Generated by scripts/release_flow_smoke.py",
        "status": "draft",
        "settings": {
            "runtime_mode": "demo",
            "provider_mode": "dry_run",
            "execution_mode": "sequential_apply",
            "approval_policy": "manual_each_step",
            "failure_policy": "pause_for_operator",
            "rollback_policy": "safe_pr",
            "default_strategy": "rolling",
            "environment_order": ["sandbox", "staging", "production"],
            "require_diagnostics_pass": True,
        },
        "steps": steps,
    }


def build_live_preflight_plan(applications: list[JsonMap], args: argparse.Namespace) -> JsonMap:
    validate_live_preflight_inputs(args)
    plan = build_demo_plan(applications[:1])
    now_label = "release-flow-live-preflight"
    window_start, window_end = live_preflight_window(args)
    settings = dict(plan["settings"])
    settings.update(
        {
            "runtime_mode": "live",
            "provider_mode": "live",
            "approval_policy": "auto_safe",
            "approval_granted": True,
            "approval_granted_by": args.live_approval_by,
            "approval_reason": args.live_approval_reason,
            "approval_granted_at": args.live_approval_at or live_preflight_timestamp(),
            "change_ticket": args.live_change_ticket,
            "release_window_start": window_start,
            "release_window_end": window_end,
            "runbook_url": args.live_runbook_url,
            "release_owner": args.live_release_owner,
            "oncall_contact": args.live_oncall_contact,
            "rollback_policy": args.live_rollback_policy,
            "require_diagnostics_pass": True,
        }
    )
    step = dict(plan["steps"][0])
    config = dict(step["config"])
    config.update(
        {
            "environment": args.live_environment,
            "namespace": args.live_namespace,
            "commit_sha": args.live_commit_sha or now_label,
            "image": args.live_image,
            "approval_gate": args.live_approval_gate,
        }
    )
    if args.live_verification_url:
        config["post_deploy_verification_url"] = args.live_verification_url
    if args.live_safe_pr_workflow_run_id:
        config["safe_pr_workflow_run_id"] = args.live_safe_pr_workflow_run_id
    if args.live_safe_pr_url:
        config["safe_pr_url"] = args.live_safe_pr_url
    step["config"] = config
    plan.update(
        {
            "name": "Release flow live preflight",
            "description": "Generated by scripts/release_flow_smoke.py --live-preflight",
            "settings": settings,
            "steps": [step],
        }
    )
    return plan


def live_preflight_window(args: argparse.Namespace) -> tuple[str, str]:
    if args.live_window_start and args.live_window_end:
        return args.live_window_start, args.live_window_end
    now = datetime.now(UTC)
    start = (now - timedelta(minutes=15)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    end = (now + timedelta(hours=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return start, end


def live_preflight_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def validate_live_https_url(field_name: str, value: str, *, context: str) -> None:
    parsed = urllib.parse.urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme.lower() != "https":
        raise ValueError(f"{field_name} must use https {context}")
    if (
        host in LIVE_PREFLIGHT_PLACEHOLDER_HOSTS
        or host.endswith(".localhost")
        or host.endswith(".example.com")
        or host.endswith(".example.test")
    ):
        raise ValueError(f"{field_name} must not use localhost or example hosts placeholder value")


def validate_live_preflight_inputs(args: argparse.Namespace) -> None:
    if not getattr(args, "live_preflight", False):
        return
    approval_gate = str(getattr(args, "live_approval_gate", "") or "").strip()
    if approval_gate not in {"manual", "safe_pr"}:
        raise ValueError("live_approval_gate must be manual or safe_pr")
    safe_pr_workflow_run_id = str(getattr(args, "live_safe_pr_workflow_run_id", "") or "").strip()
    safe_pr_url = str(getattr(args, "live_safe_pr_url", "") or "").strip()
    if safe_pr_url:
        validate_live_https_url(
            "live_safe_pr_url",
            safe_pr_url,
            context="when live_approval_gate is safe_pr",
        )
    if approval_gate == "safe_pr":
        if not safe_pr_workflow_run_id:
            raise ValueError(
                "live_safe_pr_workflow_run_id is required when live_approval_gate is safe_pr"
            )
        if not safe_pr_url:
            raise ValueError("live_safe_pr_url is required when live_approval_gate is safe_pr")
    environment = str(getattr(args, "live_environment", "") or "").strip().lower()
    if environment != "production":
        return
    required_values = {
        "live_change_ticket": getattr(args, "live_change_ticket", ""),
        "live_runbook_url": getattr(args, "live_runbook_url", ""),
        "live_verification_url": getattr(args, "live_verification_url", ""),
        "live_image": getattr(args, "live_image", ""),
    }
    for name, value in required_values.items():
        if not str(value or "").strip():
            raise ValueError(f"{name} is required for production live preflight")
    runbook_url = str(getattr(args, "live_runbook_url", "") or "").strip()
    validate_live_https_url(
        "live_runbook_url", runbook_url, context="for production live preflight"
    )
    verification_url = str(getattr(args, "live_verification_url", "") or "").strip()
    validate_live_https_url(
        "live_verification_url", verification_url, context="for production live preflight"
    )
    if (
        not str(getattr(args, "live_release_owner", "") or "").strip()
        and not str(getattr(args, "live_oncall_contact", "") or "").strip()
    ):
        raise ValueError(
            "live_release_owner or live_oncall_contact is required for production live preflight"
        )
    for name, placeholders in LIVE_PREFLIGHT_PLACEHOLDERS.items():
        value = str(getattr(args, name, "") or "").strip()
        if value in placeholders:
            raise ValueError(f"{name} must not use production placeholder value {value}")


def demo_step(app: JsonMap, index: int, selected: list[JsonMap]) -> JsonMap:
    app_id = str(app.get("application_id") or app.get("id") or "")
    if not app_id:
        raise ValueError("application is missing application_id")
    return {
        "application_id": app_id,
        "name": str(app.get("name") or app_id),
        "position": index,
        "depends_on": [] if index == 0 else [str(selected[index - 1].get("application_id"))],
        "config": {
            "repo_ref": str(app.get("repo_ref") or ""),
            "branch": str(app.get("branch") or app.get("default_branch") or "main"),
            "manifest_path": str(app.get("manifest_path") or "deploy.yaml"),
            "cluster_id": str(app.get("cluster_id") or "target"),
            "environment": "sandbox",
            "namespace": "sandbox",
            "commit_sha": "release-flow-smoke",
            "image": "ghcr.io/example/release-flow-smoke:demo",
            "replicas": 1,
            "strategy": "rolling",
            "approval_gate": "inherit",
            "health_check_path": "/readyz",
            "timeout_seconds": 300,
        },
    }


def run_smoke(
    client: ApiClient,
    email: str,
    password: str,
    *,
    demo_run: bool,
    ops_rehearsal: bool = False,
    live_preflight: bool = False,
    alert_preflight: bool = False,
    verification_preflight: bool = False,
    run_health_preflight: bool = False,
    policy_override_preflight: bool = False,
    change_freeze_preflight: bool = False,
    args: argparse.Namespace | None = None,
) -> list[SmokeResult]:
    results: list[SmokeResult] = []
    health = client.request("GET", "/healthz")
    results.append(SmokeResult("healthz", health.get("status") == "ok", str(health)))
    ready = client.request("GET", "/readyz")
    results.append(SmokeResult("readyz", bool(ready), str(ready)))
    client.request("POST", "/auth/login", {"email": email, "password": password})
    session = client.request("GET", "/auth/session")
    results.append(SmokeResult("auth.session", bool(session.get("authenticated")), str(session)))
    applications = client.request("GET", "/applications").get("applications", [])
    if not isinstance(applications, list):
        raise ValueError("/applications response did not contain an applications list")
    results.append(
        SmokeResult("applications", bool(applications), f"{len(applications)} application(s)")
    )
    plans = client.request("GET", "/release-plans").get("plans", [])
    results.append(SmokeResult("release-plans", isinstance(plans, list), f"{len(plans)} plan(s)"))
    summary = client.request("GET", "/release-runs/summary")
    results.append(SmokeResult("release-runs.summary", "total_runs" in summary, str(summary)))
    if run_health_preflight:
        if args is None:
            raise ValueError("run health preflight arguments are required")
        results.extend(run_release_health_preflight(client, summary, args))
    if verification_preflight:
        if args is None:
            raise ValueError("verification preflight arguments are required")
        results.extend(run_verification_preflight(client, summary, args))
    if policy_override_preflight:
        if args is None:
            raise ValueError("policy override preflight arguments are required")
        results.extend(run_policy_override_preflight(client, summary, args))
    if change_freeze_preflight:
        if args is None:
            raise ValueError("change freeze preflight arguments are required")
        results.extend(run_change_freeze_preflight(client, summary, args))
    plan = build_demo_plan(applications)
    preview = client.request("POST", "/release-plans/preview", plan).get("preview", {})
    results.append(
        SmokeResult(
            "release-plans.preview",
            bool(preview.get("executable")),
            str(preview.get("summary") or preview),
        )
    )
    results.append(run_generated_manifest_check(client, plan, "release-plans.generated-manifest"))
    if demo_run or ops_rehearsal:
        run = client.request("POST", "/release-plans/start", plan).get("run", {})
        run_id = str(run.get("run_id") or "")
        side_effects = [
            step.get("details", {}).get("side_effects")
            for step in run.get("steps", [])
            if isinstance(step, dict)
        ]
        results.append(
            SmokeResult(
                "release-plans.start.demo",
                bool(run_id) and all(value is False for value in side_effects),
                run_id or str(run),
            )
        )
        if run_id:
            fetched = client.request("GET", f"/release-runs/{run_id}").get("run", {})
            results.append(
                SmokeResult(
                    "release-runs.get",
                    fetched.get("run_id") == run_id,
                    str(fetched.get("status") or fetched),
                )
            )
            if ops_rehearsal:
                results.extend(run_operator_rehearsal(client, run_id))
    if live_preflight:
        if args is None:
            raise ValueError("live preflight arguments are required")
        live_plan = build_live_preflight_plan(applications, args)
        results.append(
            run_generated_manifest_check(
                client,
                live_plan,
                "release-plans.generated-manifest.live-preflight",
            )
        )
        readiness = client.request("POST", "/release-readiness", live_plan)
        blockers = readiness.get("blockers", [])
        checks = readiness.get("checks", [])
        ready = bool(readiness.get("ready"))
        results.append(
            SmokeResult(
                "release-readiness.live-preflight",
                ready,
                (
                    f"ready={ready}; "
                    f"blockers={len(blockers) if isinstance(blockers, list) else 'unknown'}; "
                    f"checks={len(checks) if isinstance(checks, list) else 'unknown'}"
                ),
            )
        )
    if alert_preflight:
        if args is None:
            raise ValueError("alert preflight arguments are required")
        results.extend(run_alert_preflight(client, args))
    return results


def run_generated_manifest_check(client: ApiClient, plan: JsonMap, name: str) -> SmokeResult:
    generated = client.request(
        "POST",
        "/release-plans/render-manifest",
        {"plan": plan, "step_index": 0},
    )
    files = generated.get("files", [])
    diagnostics = generated.get("diagnostics", [])
    error_diagnostics = [
        item
        for item in diagnostics
        if isinstance(item, dict) and str(item.get("severity") or "").lower() == "error"
    ]
    resource_count = int_count(generated.get("resource_count"))
    manifest = str(generated.get("manifest") or "")
    ok = (
        bool(manifest.strip())
        and isinstance(files, list)
        and bool(files)
        and resource_count > 0
        and not error_diagnostics
    )
    first_file = (
        files[0] if isinstance(files, list) and files and isinstance(files[0], dict) else {}
    )
    path = str(first_file.get("path") or "")
    detail = (
        f"path={path or 'unknown'}; resources={resource_count}; "
        f"errors={len(error_diagnostics)}; warnings={len(generated.get('warnings', []) or [])}"
    )
    return SmokeResult(name, ok, detail)


def run_release_health_preflight(
    client: ApiClient,
    summary: JsonMap,
    args: argparse.Namespace,
) -> list[SmokeResult]:
    counts = {
        "attention_required_runs": int_count(summary.get("attention_required_runs")),
        "stale_runs": int_count(summary.get("stale_runs")),
        "failed_runs": int_count(summary.get("failed_runs")),
        "rollback_requested_runs": int_count(summary.get("rollback_requested_runs")),
        "waiting_for_approval_runs": int_count(summary.get("waiting_for_approval_runs")),
        "unhealthy_runs": int_count(summary.get("unhealthy_runs")),
    }
    blocking = {name: count for name, count in counts.items() if count > 0}
    if not blocking:
        return [
            SmokeResult(
                "release-runs.run-health-preflight",
                True,
                "no existing release runs require operator attention",
            )
        ]

    params = {"plan_id": args.run_health_plan_id, "limit": args.run_health_run_limit}
    attention_runs = release_runs_for_filter(client, params, "attention_only")
    details = [format_counts(blocking)]
    if attention_runs:
        details.append(
            run_list_detail(sum(blocking.values()), attention_runs, "operator attention")
        )
    if counts["stale_runs"] > 0:
        stale_runs = release_runs_for_filter(client, params, "stale_only")
        if stale_runs:
            details.append(run_list_detail(counts["stale_runs"], stale_runs, "stale-run follow-up"))
    return [
        SmokeResult(
            "release-runs.run-health-preflight",
            False,
            "; ".join(details),
        )
    ]


def run_verification_preflight(
    client: ApiClient,
    summary: JsonMap,
    args: argparse.Namespace,
) -> list[SmokeResult]:
    failed_count = int_count(summary.get("verification_failed_runs"))
    timeout_count = int_count(summary.get("verification_pending_timeout_runs"))
    if failed_count <= 0 and timeout_count <= 0:
        return [
            SmokeResult(
                "release-runs.verification-preflight",
                True,
                "no failed or timed-out post-deploy verification jobs",
            )
        ]

    params = {"plan_id": args.verification_plan_id, "limit": args.verification_run_limit}
    results: list[SmokeResult] = []
    if failed_count > 0:
        failed_runs = release_runs_for_filter(client, params, "verification_failed_only")
        results.append(
            SmokeResult(
                "release-runs.verification-failed",
                False,
                run_list_detail(failed_count, failed_runs, "verification follow-up"),
            )
        )
    if timeout_count > 0:
        timed_out_runs = release_runs_for_filter(
            client, params, "verification_pending_timeout_only"
        )
        results.append(
            SmokeResult(
                "release-runs.verification-timeout",
                False,
                run_list_detail(timeout_count, timed_out_runs, "verification follow-up"),
            )
        )
    return results


def run_policy_override_preflight(
    client: ApiClient,
    summary: JsonMap,
    args: argparse.Namespace,
) -> list[SmokeResult]:
    override_count = int_count(summary.get("policy_override_runs"))
    if override_count <= 0:
        return [
            SmokeResult(
                "release-runs.policy-override-preflight",
                True,
                "no existing release runs used policy overrides",
            )
        ]

    params = {
        "plan_id": args.policy_override_plan_id,
        "limit": args.policy_override_run_limit,
        "policy_override_source": args.policy_override_source,
    }
    override_runs = release_runs_for_filter(client, params, "policy_override_only")
    breakdown = summary.get("policy_override_breakdown")
    detail_parts = [run_list_detail(override_count, override_runs, "policy override review")]
    if isinstance(breakdown, dict) and breakdown:
        detail_parts.append(
            "sources: "
            + format_counts({str(source): int_count(count) for source, count in breakdown.items()})
        )
    return [
        SmokeResult(
            "release-runs.policy-override-preflight",
            False,
            "; ".join(detail_parts),
        )
    ]


def run_change_freeze_preflight(
    client: ApiClient,
    summary: JsonMap,
    args: argparse.Namespace,
) -> list[SmokeResult]:
    active_count = int_count(summary.get("active_change_freeze_runs"))
    override_count = int_count(summary.get("change_freeze_override_runs"))
    if active_count <= 0 and override_count <= 0:
        return [
            SmokeResult(
                "release-runs.change-freeze-preflight",
                True,
                "no active change freeze or freeze override runs",
            )
        ]

    params = {"plan_id": args.change_freeze_plan_id, "limit": args.change_freeze_run_limit}
    detail_parts = [
        format_counts(
            {
                "active_change_freeze_runs": active_count,
                "change_freeze_override_runs": override_count,
            }
        )
    ]
    if active_count > 0:
        active_runs = release_runs_for_filter(client, params, "active_change_freeze_only")
        detail_parts.append(
            run_list_detail(active_count, active_runs, "active change freeze review")
        )
    if override_count > 0:
        override_runs = release_runs_for_filter(client, params, "change_freeze_override_only")
        detail_parts.append(
            run_list_detail(override_count, override_runs, "change freeze override review")
        )
    return [
        SmokeResult(
            "release-runs.change-freeze-preflight",
            False,
            "; ".join(detail_parts),
        )
    ]


def release_runs_for_filter(
    client: ApiClient, params: dict[str, Any], filter_name: str
) -> list[JsonMap]:
    payload = dict(params)
    payload[filter_name] = "true"
    response = client.request("GET", query_path("/release-runs", payload))
    runs = response.get("runs", [])
    return [run for run in runs if isinstance(run, dict)] if isinstance(runs, list) else []


def int_count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def format_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{name}={count}" for name, count in counts.items())


def run_list_detail(expected_count: int, runs: list[JsonMap], action: str) -> str:
    labels = []
    for run in runs[:5]:
        labels.append(
            str(
                run.get("run_id")
                or run.get("id")
                or run.get("name")
                or run.get("plan_id")
                or "unknown-run"
            )
        )
    suffix = f": {', '.join(labels)}" if labels else ""
    return f"{expected_count} run(s) require {action}{suffix}"


def run_alert_preflight(client: ApiClient, args: argparse.Namespace) -> list[SmokeResult]:
    channels = client.request("GET", "/alert-channels").get("channels", [])
    if not isinstance(channels, list):
        raise ValueError("/alert-channels response did not contain a channels list")
    selected = alert_preflight_channels(channels, args)
    if not selected:
        return [
            SmokeResult(
                "alert-channels.preflight",
                False,
                f"no enabled alert channel can receive {args.alert_severity} release events",
            )
        ]
    results: list[SmokeResult] = []
    for channel in selected:
        payload = {
            "channel_id": str(channel.get("channel_id") or ""),
            "name": str(channel.get("name") or "release-flow alert preflight"),
            "kind": str(channel.get("kind") or "webhook"),
            "url": str(channel.get("url") or ""),
            "min_severity": str(channel.get("min_severity") or "warning"),
            "severity": args.alert_severity,
            "message": args.alert_message,
        }
        response = client.request("POST", "/alert-channels/test", payload)
        delivered = bool(response.get("delivered") or response.get("valid"))
        detail = str(response.get("detail") or response)
        results.append(
            SmokeResult(
                f"alert-channels.preflight.{payload['channel_id'] or payload['name']}",
                delivered,
                detail,
            )
        )
    return results


def alert_preflight_channels(channels: list[Any], args: argparse.Namespace) -> list[JsonMap]:
    selected: list[JsonMap] = []
    limit = max(1, int(args.alert_channel_limit))
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        channel_id = str(channel.get("channel_id") or "")
        if args.alert_channel_id and channel_id != args.alert_channel_id:
            continue
        if channel.get("enabled") is False:
            continue
        if not str(channel.get("url") or "").strip():
            continue
        min_severity = str(channel.get("min_severity") or "warning")
        if severity_rank(args.alert_severity) < severity_rank(min_severity):
            continue
        selected.append(channel)
        if len(selected) >= limit:
            break
    return selected


def severity_rank(value: str) -> int:
    return {"info": 0, "warning": 1, "critical": 2}.get(value.strip().lower(), 1)


def run_operator_rehearsal(client: ApiClient, run_id: str) -> list[SmokeResult]:
    actions = [
        ("pause", "release-flow smoke rehearsal pause", {"paused"}),
        ("notify", "release-flow smoke rehearsal notify", set()),
        ("resume", "release-flow smoke rehearsal resume", {"running", "waiting_for_approval"}),
        ("cancel", "release-flow smoke rehearsal cleanup", {"cancelled"}),
    ]
    results: list[SmokeResult] = []
    for action, reason, expected_statuses in actions:
        response = client.request("POST", f"/release-runs/{run_id}/{action}", {"reason": reason})
        run = response.get("run", {}) if isinstance(response, dict) else {}
        status = str(run.get("derived_status") or run.get("status") or "")
        accepted = response.get("accepted")
        ok = bool(accepted) if action == "notify" else status in expected_statuses
        results.append(
            SmokeResult(
                f"release-runs.{action}",
                ok,
                f"accepted={accepted}" if action == "notify" else status or str(response),
            )
        )
    fetched = client.request("GET", f"/release-runs/{run_id}").get("run", {})
    results.append(
        SmokeResult(
            "release-runs.cleanup",
            fetched.get("run_id") == run_id and str(fetched.get("status") or "") == "cancelled",
            str(fetched.get("status") or fetched),
        )
    )
    return results


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base-url", default=os.getenv("API_BASE_URL", ""))
    parser.add_argument("--base-url", default=os.getenv("BASE_URL", ""))
    parser.add_argument("--email", default=os.getenv("AUTH_EMAIL", ""))
    parser.add_argument("--password", default=os.getenv("AUTH_PASSWORD", ""))
    parser.add_argument(
        "--timeout", type=float, default=float(os.getenv("SMOKE_TIMEOUT_SECONDS", "15"))
    )
    parser.add_argument(
        "--retry-attempts",
        type=int,
        default=int(os.getenv("RELEASE_FLOW_SMOKE_RETRY_ATTEMPTS", "3")),
        help="retry safe read-only HTTP requests this many times for transient CI/network failures",
    )
    parser.add_argument(
        "--retry-delay-seconds",
        type=float,
        default=float(os.getenv("RELEASE_FLOW_SMOKE_RETRY_DELAY_SECONDS", "1")),
        help="wait this many seconds between safe smoke request retries",
    )
    parser.add_argument(
        "--ci",
        action="store_true",
        default=env_flag("RELEASE_FLOW_SMOKE_CI"),
        help="enable the standard release-flow CI artifact and GitHub Actions integration bundle",
    )
    parser.add_argument(
        "--ci-artifacts-dir",
        default=os.getenv("RELEASE_FLOW_SMOKE_CI_ARTIFACTS_DIR", "artifacts"),
        help="write default --ci artifacts under this directory when explicit paths are not supplied",
    )
    parser.add_argument("--demo-run", action="store_true", help="create a tracked demo release run")
    parser.add_argument(
        "--report-path",
        default=os.getenv("RELEASE_FLOW_SMOKE_REPORT_PATH", ""),
        help="write the smoke JSON result to this path for CI artifacts",
    )
    parser.add_argument(
        "--junit-path",
        default=os.getenv("RELEASE_FLOW_SMOKE_JUNIT_PATH", ""),
        help="write the smoke result as JUnit XML for CI test reports",
    )
    parser.add_argument(
        "--markdown-path",
        default=os.getenv("RELEASE_FLOW_SMOKE_MARKDOWN_PATH", ""),
        help="write the smoke result as Markdown for PR comments or handoff notes",
    )
    parser.add_argument(
        "--github-step-summary",
        action="store_true",
        default=env_flag("RELEASE_FLOW_SMOKE_GITHUB_STEP_SUMMARY"),
        help="append the smoke result Markdown to GITHUB_STEP_SUMMARY when running in GitHub Actions",
    )
    parser.add_argument(
        "--github-output",
        action="store_true",
        default=env_flag("RELEASE_FLOW_SMOKE_GITHUB_OUTPUT"),
        help="append machine-readable smoke outputs to GITHUB_OUTPUT for downstream GitHub Actions steps",
    )
    parser.add_argument(
        "--github-annotations",
        action="store_true",
        default=env_flag("RELEASE_FLOW_SMOKE_GITHUB_ANNOTATIONS"),
        help="emit GitHub Actions error annotations for failed smoke checks",
    )
    parser.add_argument(
        "--production-preflight",
        action="store_true",
        help=(
            "run run-health, verification, policy override, and change-freeze "
            "preflight checks before release smoke"
        ),
    )
    parser.add_argument(
        "--production-preflight-plan-id",
        default=os.getenv("PRODUCTION_PREFLIGHT_PLAN_ID", ""),
        help="apply one release plan scope to every production preflight check",
    )
    parser.add_argument(
        "--production-preflight-run-limit",
        type=int,
        default=int(os.getenv("PRODUCTION_PREFLIGHT_RUN_LIMIT", "20")),
        help="apply one release run list limit to every production preflight check",
    )
    parser.add_argument(
        "--alert-preflight",
        action="store_true",
        help="send a validation alert through enabled alert channels",
    )
    parser.add_argument("--alert-channel-id", default=os.getenv("ALERT_PREFLIGHT_CHANNEL_ID", ""))
    parser.add_argument(
        "--alert-channel-limit",
        type=int,
        default=int(os.getenv("ALERT_PREFLIGHT_CHANNEL_LIMIT", "1")),
    )
    parser.add_argument(
        "--alert-severity",
        choices=("info", "warning", "critical"),
        default=os.getenv("ALERT_PREFLIGHT_SEVERITY", "warning"),
    )
    parser.add_argument(
        "--alert-message",
        default=os.getenv("ALERT_PREFLIGHT_MESSAGE", "release-flow alert channel preflight"),
    )
    parser.add_argument(
        "--verification-preflight",
        action="store_true",
        help="fail when existing release runs have failed or timed-out post-deploy verification jobs",
    )
    parser.add_argument(
        "--verification-plan-id", default=os.getenv("VERIFICATION_PREFLIGHT_PLAN_ID", "")
    )
    parser.add_argument(
        "--verification-run-limit",
        type=int,
        default=int(os.getenv("VERIFICATION_PREFLIGHT_RUN_LIMIT", "20")),
    )
    parser.add_argument(
        "--run-health-preflight",
        action="store_true",
        help="fail when existing release runs still require operator attention",
    )
    parser.add_argument(
        "--run-health-plan-id", default=os.getenv("RUN_HEALTH_PREFLIGHT_PLAN_ID", "")
    )
    parser.add_argument(
        "--run-health-run-limit",
        type=int,
        default=int(os.getenv("RUN_HEALTH_PREFLIGHT_RUN_LIMIT", "20")),
    )
    parser.add_argument(
        "--policy-override-preflight",
        action="store_true",
        help="fail when existing release runs used operator policy overrides",
    )
    parser.add_argument(
        "--policy-override-plan-id", default=os.getenv("POLICY_OVERRIDE_PREFLIGHT_PLAN_ID", "")
    )
    parser.add_argument(
        "--policy-override-source", default=os.getenv("POLICY_OVERRIDE_PREFLIGHT_SOURCE", "")
    )
    parser.add_argument(
        "--policy-override-run-limit",
        type=int,
        default=int(os.getenv("POLICY_OVERRIDE_PREFLIGHT_RUN_LIMIT", "20")),
    )
    parser.add_argument(
        "--change-freeze-preflight",
        action="store_true",
        help="fail when existing release runs were evaluated during a change freeze",
    )
    parser.add_argument(
        "--change-freeze-plan-id", default=os.getenv("CHANGE_FREEZE_PREFLIGHT_PLAN_ID", "")
    )
    parser.add_argument(
        "--change-freeze-run-limit",
        type=int,
        default=int(os.getenv("CHANGE_FREEZE_PREFLIGHT_RUN_LIMIT", "20")),
    )
    parser.add_argument(
        "--live-preflight",
        action="store_true",
        help="check live release readiness gates without starting or dispatching a run",
    )
    parser.add_argument(
        "--live-environment", default=os.getenv("LIVE_PREFLIGHT_ENVIRONMENT", "production")
    )
    parser.add_argument(
        "--live-namespace", default=os.getenv("LIVE_PREFLIGHT_NAMESPACE", "production")
    )
    parser.add_argument(
        "--live-change-ticket", default=os.getenv("LIVE_PREFLIGHT_CHANGE_TICKET", "")
    )
    parser.add_argument(
        "--live-approval-by", default=os.getenv("LIVE_PREFLIGHT_APPROVAL_BY", "release-operator")
    )
    parser.add_argument(
        "--live-approval-reason",
        default=os.getenv("LIVE_PREFLIGHT_APPROVAL_REASON", "live preflight approval evidence"),
    )
    parser.add_argument("--live-window-start", default=os.getenv("LIVE_PREFLIGHT_WINDOW_START", ""))
    parser.add_argument("--live-window-end", default=os.getenv("LIVE_PREFLIGHT_WINDOW_END", ""))
    parser.add_argument("--live-approval-at", default=os.getenv("LIVE_PREFLIGHT_APPROVAL_AT", ""))
    parser.add_argument(
        "--live-runbook-url",
        default=os.getenv("LIVE_PREFLIGHT_RUNBOOK_URL", ""),
    )
    parser.add_argument(
        "--live-release-owner",
        default=os.getenv("LIVE_PREFLIGHT_RELEASE_OWNER", ""),
    )
    parser.add_argument(
        "--live-oncall-contact",
        default=os.getenv("LIVE_PREFLIGHT_ONCALL_CONTACT", ""),
    )
    parser.add_argument(
        "--live-verification-url", default=os.getenv("LIVE_PREFLIGHT_VERIFICATION_URL", "")
    )
    parser.add_argument("--live-commit-sha", default=os.getenv("LIVE_PREFLIGHT_COMMIT_SHA", ""))
    parser.add_argument("--live-image", default=os.getenv("LIVE_PREFLIGHT_IMAGE", ""))
    parser.add_argument(
        "--live-approval-gate", default=os.getenv("LIVE_PREFLIGHT_APPROVAL_GATE", "manual")
    )
    parser.add_argument(
        "--live-safe-pr-workflow-run-id",
        default=os.getenv("LIVE_PREFLIGHT_SAFE_PR_WORKFLOW_RUN_ID", ""),
    )
    parser.add_argument("--live-safe-pr-url", default=os.getenv("LIVE_PREFLIGHT_SAFE_PR_URL", ""))
    parser.add_argument(
        "--live-rollback-policy", default=os.getenv("LIVE_PREFLIGHT_ROLLBACK_POLICY", "safe_pr")
    )
    parser.add_argument(
        "--ops-rehearsal",
        action="store_true",
        help="create a tracked demo release run and exercise pause/notify/resume/cancel",
    )
    return parser.parse_args(argv)


def env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in TRUTHY_VALUES


def apply_production_preflight_flags(args: argparse.Namespace) -> None:
    if not args.production_preflight:
        return
    args.run_health_preflight = True
    args.verification_preflight = True
    args.policy_override_preflight = True
    args.change_freeze_preflight = True
    if args.production_preflight_plan_id:
        args.run_health_plan_id = args.production_preflight_plan_id
        args.verification_plan_id = args.production_preflight_plan_id
        args.policy_override_plan_id = args.production_preflight_plan_id
        args.change_freeze_plan_id = args.production_preflight_plan_id
    if args.production_preflight_run_limit:
        args.run_health_run_limit = args.production_preflight_run_limit
        args.verification_run_limit = args.production_preflight_run_limit
        args.policy_override_run_limit = args.production_preflight_run_limit
        args.change_freeze_run_limit = args.production_preflight_run_limit


def apply_ci_defaults(args: argparse.Namespace) -> None:
    if not args.ci:
        return
    artifact_dir = str(args.ci_artifacts_dir or "artifacts")
    if not str(args.report_path or "").strip():
        args.report_path = os.path.join(artifact_dir, "release-flow-smoke.json")
    if not str(args.junit_path or "").strip():
        args.junit_path = os.path.join(artifact_dir, "release-flow-smoke.junit.xml")
    if not str(args.markdown_path or "").strip():
        args.markdown_path = os.path.join(artifact_dir, "release-flow-smoke.md")
    if os.getenv("GITHUB_STEP_SUMMARY"):
        args.github_step_summary = True
    if os.getenv("GITHUB_OUTPUT"):
        args.github_output = True
    if env_flag("GITHUB_ACTIONS"):
        args.github_annotations = True


def smoke_report_payload(ok: bool, api_base_url: str, results: list[SmokeResult]) -> JsonMap:
    return {
        "ok": ok,
        "api_base_url": api_base_url,
        "checks": [redacted_smoke_result(item).__dict__ for item in results],
    }


def redacted_smoke_result(result: SmokeResult) -> SmokeResult:
    return SmokeResult(result.name, result.ok, redact_sensitive_text(result.detail))


def redact_sensitive_text(value: Any) -> str:
    text = str(value)
    text = BEARER_PATTERN.sub("Bearer <redacted>", text)
    return SENSITIVE_ASSIGNMENT_PATTERN.sub(_redact_assignment, text)


def _redact_assignment(match: re.Match[str]) -> str:
    quote = match.group("quote") or ""
    if (
        "authorization" in match.group("prefix").lower()
        and match.group("value").lower() == "bearer"
    ):
        return match.group(0)
    return f"{match.group('prefix')}{quote}{REDACTED_VALUE}{quote}"


def write_json_report(path: str, payload: JsonMap) -> None:
    if not str(path or "").strip():
        return
    target = os.path.abspath(path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as report:
        json.dump(payload, report, ensure_ascii=False, indent=2)
        report.write("\n")


def write_junit_report(path: str, results: list[SmokeResult], *, error: str | None = None) -> None:
    if not str(path or "").strip():
        return
    target = os.path.abspath(path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    failures = sum(1 for item in results if not item.ok) + (1 if error else 0)
    tests = len(results) + (1 if error else 0)
    suite = ET.Element(
        "testsuite",
        {
            "name": "release-flow-smoke",
            "tests": str(tests),
            "failures": str(failures),
        },
    )
    for item in results:
        item = redacted_smoke_result(item)
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": "release_flow_smoke",
                "name": item.name,
            },
        )
        output = ET.SubElement(case, "system-out")
        output.text = item.detail
        if not item.ok:
            failure = ET.SubElement(case, "failure", {"message": item.detail})
            failure.text = item.detail
    if error:
        error = redact_sensitive_text(error)
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": "release_flow_smoke",
                "name": "release-flow-smoke.error",
            },
        )
        failure = ET.SubElement(case, "failure", {"message": error})
        failure.text = error
    tree = ET.ElementTree(suite)
    tree.write(target, encoding="utf-8", xml_declaration=True)


def write_markdown_report(
    path: str,
    *,
    ok: bool,
    api_base_url: str,
    results: list[SmokeResult],
    error: str | None = None,
) -> None:
    if not str(path or "").strip():
        return
    target = os.path.abspath(path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as report:
        report.write(
            build_markdown_report(ok=ok, api_base_url=api_base_url, results=results, error=error)
        )
        report.write("\n")


def append_github_step_summary(
    enabled: bool,
    *,
    ok: bool,
    api_base_url: str,
    results: list[SmokeResult],
    error: str | None = None,
) -> None:
    if not enabled:
        return
    path = os.getenv("GITHUB_STEP_SUMMARY", "")
    if not str(path or "").strip():
        return
    target = os.path.abspath(path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "a", encoding="utf-8") as summary:
        summary.write(
            build_markdown_report(ok=ok, api_base_url=api_base_url, results=results, error=error)
        )
        summary.write("\n\n")


def append_github_output(
    enabled: bool,
    *,
    ok: bool,
    api_base_url: str,
    results: list[SmokeResult],
    error: str | None = None,
) -> None:
    if not enabled:
        return
    path = os.getenv("GITHUB_OUTPUT", "")
    if not str(path or "").strip():
        return
    failed_checks = [item.name for item in results if not item.ok]
    outputs = {
        "release_smoke_ok": "true" if ok else "false",
        "release_smoke_api_base_url": api_base_url or "",
        "release_smoke_failed_count": str(len(failed_checks) + (1 if error else 0)),
        "release_smoke_failed_checks": ",".join(failed_checks),
        "release_smoke_error": redact_sensitive_text(error or ""),
    }
    target = os.path.abspath(path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "a", encoding="utf-8") as github_output:
        for key, value in outputs.items():
            github_output.write(f"{key}={github_output_value(value)}\n")


def github_output_value(value: Any) -> str:
    return str(value).replace("\r", " ").replace("\n", " ")


def emit_github_annotations(
    enabled: bool,
    *,
    results: list[SmokeResult],
    error: str | None = None,
    stream: Any | None = None,
) -> None:
    if not enabled:
        return
    output = stream or sys.stderr
    for item in results:
        if item.ok:
            continue
        item = redacted_smoke_result(item)
        print(
            "::error "
            f"title={github_command_property_escape(f'Release smoke failed: {item.name}')}::"
            f"{github_command_escape(item.detail)}",
            file=output,
        )
    if error:
        print(
            "::error "
            f"title={github_command_property_escape('Release smoke error')}::"
            f"{github_command_escape(redact_sensitive_text(error))}",
            file=output,
        )


def github_command_escape(value: Any) -> str:
    return str(value).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def github_command_property_escape(value: Any) -> str:
    return github_command_escape(value).replace(":", "%3A").replace(",", "%2C")


def build_markdown_report(
    *,
    ok: bool,
    api_base_url: str,
    results: list[SmokeResult],
    error: str | None = None,
) -> str:
    lines = [
        "# Release Flow Smoke Report",
        "",
        f"- Result: {'passed' if ok else 'failed'}",
        f"- API base URL: `{api_base_url or 'not configured'}`",
    ]
    if error:
        lines.extend(["", "## Error", "", redact_sensitive_text(error)])
    if results:
        lines.extend(["", "## Checks", "", "| Check | Result | Detail |", "| --- | --- | --- |"])
        for item in results:
            item = redacted_smoke_result(item)
            lines.append(
                f"| `{markdown_escape(item.name)}` | {'pass' if item.ok else 'fail'} | {markdown_escape(item.detail)} |"
            )
    return "\n".join(lines)


def markdown_escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    apply_production_preflight_flags(args)
    apply_ci_defaults(args)
    api_base_url = derive_api_base_url(args)
    if not api_base_url or not args.email or not args.password:
        payload = {
            "ok": False,
            "api_base_url": api_base_url,
            "error": "API_BASE_URL or BASE_URL, AUTH_EMAIL, and AUTH_PASSWORD are required",
        }
        write_json_report(args.report_path, payload)
        write_junit_report(args.junit_path, [], error=str(payload["error"]))
        write_markdown_report(
            args.markdown_path,
            ok=False,
            api_base_url=api_base_url,
            results=[],
            error=str(payload["error"]),
        )
        append_github_step_summary(
            args.github_step_summary,
            ok=False,
            api_base_url=api_base_url,
            results=[],
            error=str(payload["error"]),
        )
        append_github_output(
            args.github_output,
            ok=False,
            api_base_url=api_base_url,
            results=[],
            error=str(payload["error"]),
        )
        emit_github_annotations(args.github_annotations, results=[], error=str(payload["error"]))
        print(payload["error"], file=sys.stderr)
        return 2
    try:
        validate_live_preflight_inputs(args)
    except ValueError as exc:
        payload = {
            "ok": False,
            "api_base_url": api_base_url,
            "error": redact_sensitive_text(str(exc)),
        }
        write_json_report(args.report_path, payload)
        write_junit_report(args.junit_path, [], error=str(payload["error"]))
        write_markdown_report(
            args.markdown_path,
            ok=False,
            api_base_url=api_base_url,
            results=[],
            error=str(payload["error"]),
        )
        append_github_step_summary(
            args.github_step_summary,
            ok=False,
            api_base_url=api_base_url,
            results=[],
            error=str(payload["error"]),
        )
        append_github_output(
            args.github_output,
            ok=False,
            api_base_url=api_base_url,
            results=[],
            error=str(payload["error"]),
        )
        emit_github_annotations(args.github_annotations, results=[], error=str(payload["error"]))
        print(payload["error"], file=sys.stderr)
        return 1
    client = ApiClient(
        api_base_url,
        timeout=args.timeout,
        retry_attempts=args.retry_attempts,
        retry_delay_seconds=args.retry_delay_seconds,
    )
    try:
        results = run_smoke(
            client,
            args.email,
            args.password,
            demo_run=args.demo_run,
            ops_rehearsal=args.ops_rehearsal,
            live_preflight=args.live_preflight,
            alert_preflight=args.alert_preflight,
            verification_preflight=args.verification_preflight,
            run_health_preflight=args.run_health_preflight,
            policy_override_preflight=args.policy_override_preflight,
            change_freeze_preflight=args.change_freeze_preflight,
            args=args,
        )
    except Exception as exc:
        payload = {
            "ok": False,
            "api_base_url": client.api_base_url,
            "error": redact_sensitive_text(str(exc)),
        }
        write_json_report(args.report_path, payload)
        write_junit_report(args.junit_path, [], error=str(payload["error"]))
        write_markdown_report(
            args.markdown_path,
            ok=False,
            api_base_url=client.api_base_url,
            results=[],
            error=str(payload["error"]),
        )
        append_github_step_summary(
            args.github_step_summary,
            ok=False,
            api_base_url=client.api_base_url,
            results=[],
            error=str(payload["error"]),
        )
        append_github_output(
            args.github_output,
            ok=False,
            api_base_url=client.api_base_url,
            results=[],
            error=str(payload["error"]),
        )
        emit_github_annotations(args.github_annotations, results=[], error=str(payload["error"]))
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    ok = all(item.ok for item in results)
    payload = smoke_report_payload(ok, client.api_base_url, results)
    write_json_report(args.report_path, payload)
    write_junit_report(args.junit_path, results)
    write_markdown_report(
        args.markdown_path, ok=ok, api_base_url=client.api_base_url, results=results
    )
    append_github_step_summary(
        args.github_step_summary, ok=ok, api_base_url=client.api_base_url, results=results
    )
    append_github_output(
        args.github_output, ok=ok, api_base_url=client.api_base_url, results=results
    )
    emit_github_annotations(args.github_annotations, results=results)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
