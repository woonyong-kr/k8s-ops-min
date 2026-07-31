#!/usr/bin/env python3
"""Start a gated production release through the release-flow API."""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Any

from release_flow_smoke import (
    ApiClient,
    SmokeResult,
    append_github_output,
    append_github_step_summary,
    derive_api_base_url,
    emit_github_annotations,
    env_flag,
    redact_sensitive_text,
    validate_live_https_url,
    write_json_report,
    write_junit_report,
    write_markdown_report,
)

PRODUCTION_ENVIRONMENTS = {"prod", "production"}
PLACEHOLDER_CHANGE_TICKETS = {"CHG-PREFLIGHT"}
PLACEHOLDER_IMAGES = {"ghcr.io/example/release-flow-smoke:live-preflight"}
PLACEHOLDER_AUTH_EMAILS = {"release-oncall@example.com"}
PLACEHOLDER_AUTH_PASSWORDS = {"secret", "password", "changeme", "change-me", "replace-me"}
FAILED_RUN_STATES = {"cancelled", "canceled", "error", "failed", "failure", "rejected"}
RELEASE_PLAN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
REQUIRED_GATE_EVIDENCE_FIELDS = {
    "change_ticket": "change ticket",
    "runbook_url": "runbook URL",
    "image": "production image",
    "post_deploy_verification_url": "post-deploy verification URL",
    "safe_pr_workflow_run_id": "Safe PR workflow run id",
    "safe_pr_url": "Safe PR URL",
}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base-url", default=os.getenv("RELEASE_FLOW_API_BASE_URL", ""))
    parser.add_argument("--base-url", default=os.getenv("BASE_URL", ""))
    parser.add_argument(
        "--email", default=os.getenv("RELEASE_FLOW_AUTH_EMAIL") or os.getenv("AUTH_EMAIL", "")
    )
    parser.add_argument(
        "--password",
        default=os.getenv("RELEASE_FLOW_AUTH_PASSWORD") or os.getenv("AUTH_PASSWORD", ""),
    )
    parser.add_argument("--plan-id", default=os.getenv("RELEASE_FLOW_DEPLOY_PLAN_ID", ""))
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("RELEASE_FLOW_DEPLOY_TIMEOUT_SECONDS", "30")),
    )
    parser.add_argument(
        "--retry-attempts",
        type=int,
        default=int(os.getenv("RELEASE_FLOW_DEPLOY_RETRY_ATTEMPTS", "3")),
    )
    parser.add_argument(
        "--retry-delay-seconds",
        type=float,
        default=float(os.getenv("RELEASE_FLOW_DEPLOY_RETRY_DELAY_SECONDS", "1")),
    )
    parser.add_argument("--ci", action="store_true", default=env_flag("RELEASE_FLOW_DEPLOY_CI"))
    parser.add_argument(
        "--ci-artifacts-dir",
        default=os.getenv("RELEASE_FLOW_DEPLOY_CI_ARTIFACTS_DIR", "artifacts/release-flow-deploy"),
    )
    parser.add_argument("--report-path", default=os.getenv("RELEASE_FLOW_DEPLOY_REPORT_PATH", ""))
    parser.add_argument("--junit-path", default=os.getenv("RELEASE_FLOW_DEPLOY_JUNIT_PATH", ""))
    parser.add_argument(
        "--markdown-path", default=os.getenv("RELEASE_FLOW_DEPLOY_MARKDOWN_PATH", "")
    )
    parser.add_argument(
        "--github-step-summary",
        action="store_true",
        default=env_flag("RELEASE_FLOW_DEPLOY_GITHUB_STEP_SUMMARY"),
    )
    parser.add_argument(
        "--github-output",
        action="store_true",
        default=env_flag("RELEASE_FLOW_DEPLOY_GITHUB_OUTPUT"),
    )
    parser.add_argument(
        "--github-annotations",
        action="store_true",
        default=env_flag("RELEASE_FLOW_DEPLOY_GITHUB_ANNOTATIONS"),
    )
    parser.add_argument(
        "--change-ticket", default=os.getenv("RELEASE_FLOW_DEPLOY_CHANGE_TICKET", "")
    )
    parser.add_argument("--runbook-url", default=os.getenv("RELEASE_FLOW_DEPLOY_RUNBOOK_URL", ""))
    parser.add_argument("--image", default=os.getenv("RELEASE_FLOW_DEPLOY_IMAGE", ""))
    parser.add_argument(
        "--verification-url", default=os.getenv("RELEASE_FLOW_DEPLOY_VERIFICATION_URL", "")
    )
    parser.add_argument(
        "--safe-pr-workflow-run-id",
        default=os.getenv("RELEASE_FLOW_DEPLOY_SAFE_PR_WORKFLOW_RUN_ID", ""),
    )
    parser.add_argument("--safe-pr-url", default=os.getenv("RELEASE_FLOW_DEPLOY_SAFE_PR_URL", ""))
    return parser.parse_args(argv)


def apply_ci_defaults(args: argparse.Namespace) -> None:
    if not args.ci:
        return
    artifact_dir = str(args.ci_artifacts_dir or "artifacts/release-flow-deploy")
    if not str(args.report_path or "").strip():
        args.report_path = os.path.join(artifact_dir, "release-flow-deploy.json")
    if not str(args.junit_path or "").strip():
        args.junit_path = os.path.join(artifact_dir, "release-flow-deploy.junit.xml")
    if not str(args.markdown_path or "").strip():
        args.markdown_path = os.path.join(artifact_dir, "release-flow-deploy.md")
    if os.getenv("GITHUB_STEP_SUMMARY"):
        args.github_step_summary = True
    if os.getenv("GITHUB_OUTPUT"):
        args.github_output = True
    if env_flag("GITHUB_ACTIONS"):
        args.github_annotations = True


def release_plan_start_payload(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "plan_id": plan.get("plan_id"),
        "name": plan.get("name") or "Production release",
        "description": plan.get("description") or "",
        "status": plan.get("status") or "active",
        "settings": plan.get("settings") if isinstance(plan.get("settings"), dict) else {},
        "steps": plan.get("steps") if isinstance(plan.get("steps"), list) else [],
    }


def expected_plan_values(args: argparse.Namespace) -> dict[str, str]:
    return {
        "change_ticket": str(args.change_ticket or "").strip(),
        "runbook_url": str(args.runbook_url or "").strip(),
        "image": str(args.image or "").strip(),
        "post_deploy_verification_url": str(args.verification_url or "").strip(),
        "safe_pr_workflow_run_id": str(args.safe_pr_workflow_run_id or "").strip(),
        "safe_pr_url": str(args.safe_pr_url or "").strip(),
    }


def plan_value(settings: dict[str, Any], config: dict[str, Any], field: str) -> str:
    return str(config.get(field) or settings.get(field) or "").strip()


def validate_release_plan_id(plan_id: str) -> str | None:
    if not plan_id.strip():
        return "release_plan_id is required"
    if not RELEASE_PLAN_ID_PATTERN.fullmatch(plan_id):
        return "release_plan_id must be path-safe: letters, numbers, dot, underscore, colon, or hyphen only"
    return None


def validate_deploy_auth(args: argparse.Namespace) -> str | None:
    email = str(args.email or "").strip()
    password = str(args.password or "")
    lowered_email = email.lower()
    if (
        "@" not in email
        or lowered_email.endswith("@example.com")
        or lowered_email in PLACEHOLDER_AUTH_EMAILS
    ):
        return "release-flow deploy auth email must be a real operator account"
    if len(password) < 12 or password.lower() in PLACEHOLDER_AUTH_PASSWORDS:
        return "release-flow deploy auth password must be a non-placeholder secret of at least 12 characters"
    return None


def validate_deploy_gate_inputs(args: argparse.Namespace) -> str | None:
    values = expected_plan_values(args)
    missing = [
        label for field, label in REQUIRED_GATE_EVIDENCE_FIELDS.items() if not values.get(field)
    ]
    if missing:
        return "release-flow deploy requires gated evidence inputs: " + ", ".join(missing)
    change_ticket = values["change_ticket"]
    if change_ticket in PLACEHOLDER_CHANGE_TICKETS:
        return f"release-flow deploy change ticket must not use placeholder {change_ticket}"
    for field in ("runbook_url", "post_deploy_verification_url", "safe_pr_url"):
        try:
            validate_live_https_url(field, values[field], context="for production deploy evidence")
        except ValueError as exc:
            return str(exc)
    image = values["image"]
    if image in PLACEHOLDER_IMAGES:
        return f"release-flow deploy image must not use production placeholder value {image}"
    if image.endswith(":latest"):
        return "release-flow deploy image must not use mutable latest tag"
    if ":" not in image.rsplit("/", 1)[-1] and "@" not in image:
        return "release-flow deploy image must include an immutable tag or digest"
    return None


def validate_production_plan(
    plan: dict[str, Any],
    *,
    expected_values: dict[str, str] | None = None,
) -> list[str]:
    blockers: list[str] = []
    settings = plan.get("settings") if isinstance(plan.get("settings"), dict) else {}
    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    expected_values = expected_values or {}
    if settings.get("runtime_mode") != "live":
        blockers.append("release plan settings.runtime_mode must be live")
    if settings.get("rollback_policy") != "safe_pr":
        blockers.append(
            "release plan settings.rollback_policy must be safe_pr for gated production deploy"
        )
    if not steps:
        blockers.append("release plan must contain at least one step")
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            blockers.append(f"release plan step {index} is not an object")
            continue
        config = step.get("config") if isinstance(step.get("config"), dict) else {}
        application_id = str(step.get("application_id") or f"step-{index}")
        environment = str(config.get("environment") or settings.get("environment") or "").lower()
        if environment not in PRODUCTION_ENVIRONMENTS:
            blockers.append(f"{application_id} environment must be production")
        for field in (
            "change_ticket",
            "runbook_url",
            "post_deploy_verification_url",
            "abort_criteria",
            "image",
            "safe_pr_workflow_run_id",
            "safe_pr_url",
        ):
            if not plan_value(settings, config, field):
                blockers.append(f"{application_id} requires {field}")
        for field, expected in expected_values.items():
            if expected and plan_value(settings, config, field) != expected:
                blockers.append(f"{application_id} {field} must match gated deploy input")
        change_ticket = plan_value(settings, config, "change_ticket")
        if change_ticket in PLACEHOLDER_CHANGE_TICKETS:
            blockers.append(
                f"{application_id} change_ticket must not use placeholder {change_ticket}"
            )
        for field in ("runbook_url", "post_deploy_verification_url"):
            value = plan_value(settings, config, field)
            if value:
                try:
                    validate_live_https_url(field, value, context="for production release plan")
                except ValueError as exc:
                    blockers.append(f"{application_id} {exc}")
        safe_pr_url = plan_value(settings, config, "safe_pr_url")
        if safe_pr_url:
            try:
                validate_live_https_url(
                    "safe_pr_url", safe_pr_url, context="for production release plan"
                )
            except ValueError as exc:
                blockers.append(f"{application_id} {exc}")
        image = plan_value(settings, config, "image")
        if image:
            if image in PLACEHOLDER_IMAGES:
                blockers.append(
                    f"{application_id} image must not use production placeholder value {image}"
                )
            if image.endswith(":latest"):
                blockers.append(f"{application_id} image must not use mutable latest tag")
            if ":" not in image.rsplit("/", 1)[-1] and "@" not in image:
                blockers.append(f"{application_id} image must include an immutable tag or digest")
    return blockers


def validate_start_response(run: dict[str, Any], *, expected_step_count: int) -> list[str]:
    blockers: list[str] = []
    run_id = str(run.get("run_id") or "").strip()
    if not run_id:
        blockers.append("release start response must include run_id")
    status = str(run.get("status") or "").strip().lower()
    if status in FAILED_RUN_STATES:
        blockers.append(f"release start response status must not be {status}")
    steps = run.get("steps")
    if not isinstance(steps, list) or not steps:
        blockers.append("release start response must include step evidence")
        return blockers
    if len(steps) < expected_step_count:
        blockers.append("release start response must include every planned step")
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            blockers.append(f"release start response step {index} is not an object")
            continue
        step_status = str(step.get("status") or "").strip().lower()
        if step_status in FAILED_RUN_STATES:
            blockers.append(f"release start response step {index} status must not be {step_status}")
        details = step.get("details")
        if not isinstance(details, dict):
            blockers.append(f"release start response step {index} must include details")
            continue
        if details.get("side_effects") is not True:
            blockers.append(f"release start response step {index} must confirm side_effects")
    return blockers


def run_deploy(client: ApiClient, args: argparse.Namespace) -> list[SmokeResult]:
    results: list[SmokeResult] = []
    plan_id_error = validate_release_plan_id(str(args.plan_id or ""))
    if plan_id_error:
        return [SmokeResult("release-plan.id", False, plan_id_error)]
    client.request("POST", "/auth/login", {"email": args.email, "password": args.password})
    session = client.request("GET", "/auth/session")
    results.append(SmokeResult("auth.session", bool(session.get("authenticated")), str(session)))
    plan_response = client.request("GET", f"/release-plans/{args.plan_id}")
    plan = plan_response.get("plan", {})
    if not isinstance(plan, dict):
        raise ValueError("release plan response did not contain a plan object")
    blockers = validate_production_plan(plan, expected_values=expected_plan_values(args))
    if str(plan.get("plan_id") or "") != str(args.plan_id):
        blockers.append("release plan response plan_id must match requested plan_id")
    results.append(
        SmokeResult(
            "release-plan.production-contract",
            not blockers,
            "ok" if not blockers else "; ".join(blockers),
        )
    )
    if blockers:
        return results
    run = client.request("POST", "/release-plans/start", release_plan_start_payload(plan)).get(
        "run", {}
    )
    if not isinstance(run, dict):
        run = {}
    expected_step_count = len(plan.get("steps") if isinstance(plan.get("steps"), list) else [])
    start_blockers = validate_start_response(run, expected_step_count=expected_step_count)
    results.append(
        SmokeResult(
            "release-plans.start.production",
            not start_blockers,
            str(run.get("run_id") or "") if not start_blockers else "; ".join(start_blockers),
        )
    )
    return results


def write_reports(
    args: argparse.Namespace,
    *,
    ok: bool,
    api_base_url: str,
    results: list[SmokeResult],
    error: str | None = None,
) -> None:
    payload = {
        "ok": ok,
        "api_base_url": api_base_url,
        "plan_id": args.plan_id,
        "checks": [item.__dict__ for item in results],
    }
    if error:
        payload["error"] = redact_sensitive_text(error)
    write_json_report(args.report_path, payload)
    write_junit_report(args.junit_path, results, error=error)
    write_markdown_report(
        args.markdown_path, ok=ok, api_base_url=api_base_url, results=results, error=error
    )
    append_github_step_summary(
        args.github_step_summary, ok=ok, api_base_url=api_base_url, results=results, error=error
    )
    append_github_output(
        args.github_output, ok=ok, api_base_url=api_base_url, results=results, error=error
    )
    emit_github_annotations(args.github_annotations, results=results, error=error)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    apply_ci_defaults(args)
    api_base_url = derive_api_base_url(args)
    results: list[SmokeResult] = []
    if not api_base_url or not args.email or not args.password or not args.plan_id:
        error = "RELEASE_FLOW_API_BASE_URL, RELEASE_FLOW_AUTH_EMAIL, RELEASE_FLOW_AUTH_PASSWORD, and release_plan_id are required"
        write_reports(args, ok=False, api_base_url=api_base_url, results=results, error=error)
        print(error, file=sys.stderr)
        return 2
    try:
        validate_live_https_url("api_base_url", api_base_url, context="for production deploy")
    except ValueError as exc:
        error = str(exc)
        write_reports(args, ok=False, api_base_url=api_base_url, results=results, error=error)
        print(error, file=sys.stderr)
        return 2
    auth_error = validate_deploy_auth(args)
    if auth_error:
        write_reports(args, ok=False, api_base_url=api_base_url, results=results, error=auth_error)
        print(auth_error, file=sys.stderr)
        return 2
    gate_input_error = validate_deploy_gate_inputs(args)
    if gate_input_error:
        write_reports(
            args, ok=False, api_base_url=api_base_url, results=results, error=gate_input_error
        )
        print(gate_input_error, file=sys.stderr)
        return 2
    client = ApiClient(
        api_base_url,
        timeout=args.timeout,
        retry_attempts=args.retry_attempts,
        retry_delay_seconds=args.retry_delay_seconds,
    )
    try:
        results = run_deploy(client, args)
        ok = all(item.ok for item in results)
        write_reports(args, ok=ok, api_base_url=client.api_base_url, results=results)
        return 0 if ok else 1
    except Exception as exc:
        error = redact_sensitive_text(str(exc))
        write_reports(
            args, ok=False, api_base_url=client.api_base_url, results=results, error=error
        )
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
