"""Diagnostics API shared by YAML editing and configuration forms."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import yaml
from fastapi import APIRouter, Depends, Request, Response

from domains.diagnostics.runtime import collect_runtime_diagnostics
from domains.diagnostics.version_check import VersionCheckService
from domains.identity.dependencies import require_session
from packages.config.environments import is_production_environment
from packages.contracts.bootstrap import RuntimeDiagnosticsResponse, VersionCheckResponse
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.gateway.requests import DiagnosticsRequest
from packages.contracts.gateway.responses import DiagnosticItem, DiagnosticsResponse
from packages.contracts.gitops import supported_kubernetes_resource
from packages.runtime.dependencies import get_db

router = APIRouter()
VERSION_CHECK_SERVICE_STATE_KEY = "version_check_service"

DNS_LABEL_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
REPO_REF_RE = re.compile(r"^[\w.-]+/[\w.-]+$")
MANIFEST_EXTENSIONS = (".yaml", ".yml", ".json")
SECRET_KEY_HINTS = ("password", "passwd", "secret", "token", "apikey", "api_key")
EXECUTION_MODES = {"preview_only", "manual_dispatch", "sequential_apply", "promotion"}
RUNTIME_MODES = {"demo", "live"}
PROVIDER_MODES = {"dry_run", "live"}
APPROVAL_POLICIES = {
    "auto_safe",
    "manual_each_step",
    "production_only",
    "external_change_ticket",
}
FAILURE_POLICIES = {"stop_on_failure", "pause_for_operator", "continue_independent"}
ROLLBACK_POLICIES = {"manual", "safe_pr", "restart_last_successful", "disabled"}
DEPLOY_STRATEGIES = {"rolling", "canary", "blue_green"}
APPROVAL_GATES = {"inherit", "auto", "manual", "safe_pr"}


def get_version_check_service(request: Request) -> VersionCheckService:
    service = getattr(request.app.state, VERSION_CHECK_SERVICE_STATE_KEY, None)
    if service is None:
        service = VersionCheckService()
        setattr(request.app.state, VERSION_CHECK_SERVICE_STATE_KEY, service)
    return service


@router.get(
    gateway_routes.DIAGNOSTICS_PATH,
    response_model=RuntimeDiagnosticsResponse,
)
async def runtime_diagnostics(
    response: Response,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> RuntimeDiagnosticsResponse:
    response.headers["Cache-Control"] = "no-store"
    return await collect_runtime_diagnostics(db, current)


@router.get(
    gateway_routes.VERSION_CHECK_PATH,
    response_model=VersionCheckResponse,
)
async def version_check(
    response: Response,
    _current: Any = Depends(require_session),
    service: VersionCheckService = Depends(get_version_check_service),
) -> VersionCheckResponse:
    response.headers["Cache-Control"] = "no-store"
    return await service.check()


@router.post(gateway_routes.DIAGNOSTICS_PATH, response_model=DiagnosticsResponse)
async def diagnose(
    payload: DiagnosticsRequest,
    _current: Any = Depends(require_session),
) -> DiagnosticsResponse:
    if payload.mode == "yaml":
        items = yaml_diagnostics(payload.content, payload.context)
    elif payload.mode == "release_plan":
        items = release_plan_diagnostics(payload.settings, payload.context)
    else:
        items = settings_diagnostics(payload.settings, payload.context)
    return DiagnosticsResponse(diagnostics=items)


def item(
    severity: str,
    message: str,
    code: str,
    *,
    source: str = "myjob",
    line: int = 1,
    column: int = 1,
    end_line: int | None = None,
    end_column: int | None = None,
    path: str | None = None,
    action: str | None = None,
) -> DiagnosticItem:
    return DiagnosticItem(
        source=source,
        severity=severity,
        message=message,
        code=code,
        line=max(1, line),
        column=max(1, column),
        end_line=max(1, end_line or line),
        end_column=max(2, end_column or column + 1),
        path=path,
        action=action,
    )


def yaml_diagnostics(
    content: str, context: Mapping[str, Any] | None = None
) -> list[DiagnosticItem]:
    context = context or {}
    if not content.strip():
        return [
            item(
                "warning",
                "YAML content is empty.",
                "yaml.empty",
                source="yaml",
            )
        ]

    try:
        documents = list(yaml.safe_load_all(content))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
        return [
            item(
                "error",
                compact_yaml_error(exc),
                "yaml.syntax",
                source="yaml",
                line=(getattr(mark, "line", 0) + 1) if mark else 1,
                column=(getattr(mark, "column", 0) + 1) if mark else 1,
            )
        ]

    diagnostics: list[DiagnosticItem] = []
    non_empty = [doc for doc in documents if doc is not None]
    if not non_empty:
        diagnostics.append(item("warning", "YAML has no Kubernetes objects.", "yaml.no_objects"))
        return diagnostics

    expected_namespace = str(context.get("namespace") or "")
    typed_docs: list[Mapping[str, Any]] = []
    for index, doc in enumerate(non_empty, start=1):
        if not isinstance(doc, Mapping):
            diagnostics.append(
                item(
                    "error",
                    f"Document {index} must be a YAML object.",
                    "k8s.document_object",
                    source="kubernetes",
                    path=f"documents[{index - 1}]",
                )
            )
            continue
        typed_docs.append(doc)
        diagnostics.extend(kubernetes_object_diagnostics(content, doc, index, expected_namespace))
    previous_content = str(context.get("previous_content") or context.get("baseline_content") or "")
    if previous_content.strip():
        previous_docs = parse_yaml_objects(previous_content)
        diagnostics.extend(risk_diff_diagnostics(content, previous_docs, typed_docs))
    return diagnostics


def kubernetes_object_diagnostics(
    content: str,
    doc: Mapping[str, Any],
    index: int,
    expected_namespace: str,
) -> list[DiagnosticItem]:
    diagnostics: list[DiagnosticItem] = []
    api_version = str(doc.get("apiVersion") or "")
    kind = str(doc.get("kind") or "")
    metadata = doc.get("metadata")
    metadata_name = metadata.get("name") if isinstance(metadata, Mapping) else None

    if not api_version:
        diagnostics.append(missing_key(content, "apiVersion", "k8s.api_version", index))
    if not kind:
        diagnostics.append(missing_key(content, "kind", "k8s.kind", index))
    if not isinstance(metadata, Mapping):
        diagnostics.append(missing_key(content, "metadata", "k8s.metadata", index))
    elif not metadata_name:
        diagnostics.append(
            missing_key(content, "name", "k8s.metadata.name", index, "metadata.name")
        )

    if api_version and kind:
        try:
            supported_kubernetes_resource(api_version, kind)
        except ValueError:
            diagnostics.append(
                item(
                    "warning",
                    f"{api_version}/{kind} can render, but field-level diff support is limited.",
                    "k8s.kind_limited_support",
                    source="kubernetes",
                    line=find_key_line(content, "kind"),
                    path="kind",
                )
            )

    namespace = ""
    if isinstance(metadata, Mapping):
        namespace = str(metadata.get("namespace") or "")
    if expected_namespace and namespace and namespace != expected_namespace:
        diagnostics.append(
            item(
                "error",
                f"metadata.namespace must match the selected target namespace ({expected_namespace}).",
                "k8s.namespace_mismatch",
                source="kubernetes",
                line=find_key_line(content, "namespace"),
                path="metadata.namespace",
            )
        )
    if is_production_environment(namespace):
        diagnostics.append(
            item(
                "warning",
                "metadata.namespace points at a production namespace; require Safe PR or explicit approval before applying.",
                "risk.production_namespace",
                source="policy",
                line=find_key_line(content, "namespace"),
                path="metadata.namespace",
                action="safe_pr",
            )
        )

    if kind == "Deployment":
        diagnostics.extend(deployment_diagnostics(content, doc))
    if kind == "Service":
        diagnostics.extend(service_diagnostics(content, doc))
    return diagnostics


def deployment_diagnostics(content: str, doc: Mapping[str, Any]) -> list[DiagnosticItem]:
    diagnostics: list[DiagnosticItem] = []
    spec = doc.get("spec")
    if not isinstance(spec, Mapping):
        return [missing_key(content, "spec", "k8s.deployment.spec", 1)]

    replicas = spec.get("replicas", 1)
    if isinstance(replicas, bool) or not isinstance(replicas, int):
        diagnostics.append(
            item(
                "error",
                "Deployment spec.replicas must be an integer.",
                "k8s.replicas_type",
                source="kubernetes",
                line=find_key_line(content, "replicas"),
                path="spec.replicas",
            )
        )
    elif replicas < 0:
        diagnostics.append(
            item(
                "error",
                "Deployment spec.replicas cannot be negative.",
                "k8s.replicas_negative",
                source="kubernetes",
                line=find_key_line(content, "replicas"),
                path="spec.replicas",
            )
        )
    elif replicas == 0:
        diagnostics.append(
            item(
                "warning",
                "Deployment replicas is 0. This can be an intentional maintenance stop, but the service will not serve traffic.",
                "risk.replicas_zero",
                source="policy",
                line=find_key_line(content, "replicas"),
                path="spec.replicas",
                action="confirm",
            )
        )

    template = spec.get("template")
    pod_spec = template.get("spec", {}) if isinstance(template, Mapping) else {}
    if isinstance(pod_spec, Mapping) and pod_spec.get("hostNetwork") is True:
        diagnostics.append(
            item(
                "warning",
                "hostNetwork weakens pod network isolation and needs explicit approval.",
                "risk.host_network_enabled",
                source="policy",
                line=find_key_line(content, "hostNetwork"),
                path="spec.template.spec.hostNetwork",
                action="approval_required",
            )
        )
    for idx, volume in enumerate(
        list_value(pod_spec.get("volumes")) if isinstance(pod_spec, Mapping) else []
    ):
        if isinstance(volume, Mapping) and isinstance(volume.get("hostPath"), Mapping):
            diagnostics.append(
                item(
                    "warning",
                    "hostPath mounts can expose node filesystem access and need explicit approval.",
                    "risk.host_path_added",
                    source="policy",
                    line=find_key_line(content, "hostPath"),
                    path=f"spec.template.spec.volumes[{idx}].hostPath",
                    action="approval_required",
                )
            )
    containers = pod_spec.get("containers", []) if isinstance(pod_spec, Mapping) else []
    if not isinstance(containers, list) or not containers:
        diagnostics.append(
            item(
                "error",
                "Deployment must define at least one container.",
                "k8s.containers_missing",
                source="kubernetes",
                line=find_key_line(content, "containers"),
                path="spec.template.spec.containers",
            )
        )
        return diagnostics

    for idx, container in enumerate(containers):
        if not isinstance(container, Mapping):
            continue
        image = str(container.get("image") or "")
        if not image:
            diagnostics.append(
                item(
                    "error",
                    "Container image is required.",
                    "k8s.container_image_missing",
                    source="kubernetes",
                    line=find_key_line(content, "image"),
                    path=f"spec.template.spec.containers[{idx}].image",
                )
            )
        elif image.endswith(":latest") or ":" not in image.rsplit("/", 1)[-1]:
            diagnostics.append(
                item(
                    "warning",
                    "Use an immutable image tag instead of latest or an implicit tag.",
                    "k8s.image_tag_mutable",
                    source="kubernetes",
                    line=find_key_line(content, "image"),
                    path=f"spec.template.spec.containers[{idx}].image",
                )
            )
        security_context = mapping_value(container.get("securityContext"))
        if security_context.get("privileged") is True:
            diagnostics.append(
                item(
                    "error",
                    "Container privileged mode is enabled. This is a privilege escalation risk.",
                    "risk.privileged_enabled",
                    source="policy",
                    line=find_key_line(content, "privileged"),
                    path=f"spec.template.spec.containers[{idx}].securityContext.privileged",
                    action="approval_required",
                )
            )
        run_as_user = security_context.get("runAsUser")
        if run_as_user == 0:
            diagnostics.append(
                item(
                    "warning",
                    "Container is configured to run as root. Confirm this is intentional.",
                    "risk.run_as_root",
                    source="policy",
                    line=find_key_line(content, "runAsUser"),
                    path=f"spec.template.spec.containers[{idx}].securityContext.runAsUser",
                    action="confirm",
                )
            )
        for env_index, env in enumerate(list_value(container.get("env"))):
            if not isinstance(env, Mapping):
                continue
            key = str(env.get("name") or "").lower()
            if "value" in env and any(hint in key for hint in SECRET_KEY_HINTS):
                diagnostics.append(
                    item(
                        "warning",
                        "A likely secret is set as a plain env value; use Secret refs instead.",
                        "risk.plain_secret_env",
                        source="policy",
                        line=find_key_line(content, "value"),
                        path=f"spec.template.spec.containers[{idx}].env[{env_index}].value",
                        action="approval_required",
                    )
                )
    return diagnostics


def service_diagnostics(content: str, doc: Mapping[str, Any]) -> list[DiagnosticItem]:
    spec = doc.get("spec")
    if not isinstance(spec, Mapping):
        return [missing_key(content, "spec", "k8s.service.spec", 1)]
    ports = spec.get("ports")
    if not isinstance(ports, list) or not ports:
        return [
            item(
                "error",
                "Service spec.ports must include at least one port.",
                "k8s.service_ports_missing",
                source="kubernetes",
                line=find_key_line(content, "ports"),
                path="spec.ports",
            )
        ]
    return []


def settings_diagnostics(
    settings: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
    path_prefix: str = "",
) -> list[DiagnosticItem]:
    context = context or {}
    diagnostics: list[DiagnosticItem] = []
    repo_ref = str(settings.get("repo_ref") or "")
    if repo_ref and not REPO_REF_RE.match(repo_ref):
        diagnostics.append(
            item(
                "error",
                "repo_ref must use owner/name format.",
                "settings.repo_ref_format",
                source="settings",
                path=scoped_path(path_prefix, "repo_ref"),
            )
        )

    branch = str(settings.get("branch") or settings.get("default_branch") or "")
    if "branch" in settings and not branch:
        diagnostics.append(
            item(
                "error",
                "branch is required.",
                "settings.branch_required",
                source="settings",
                path=scoped_path(path_prefix, "branch"),
            )
        )

    manifest_path = str(settings.get("manifest_path") or "")
    if (
        manifest_path
        and not manifest_path.endswith(MANIFEST_EXTENSIONS)
        and not manifest_path.endswith("/")
    ):
        diagnostics.append(
            item(
                "warning",
                "manifest_path should point to YAML/JSON or a renderer directory.",
                "settings.manifest_path_shape",
                source="settings",
                path=scoped_path(path_prefix, "manifest_path"),
            )
        )

    namespace = str(settings.get("namespace") or "")
    if namespace and not valid_dns_label(namespace):
        diagnostics.append(
            item(
                "error",
                "namespace must be a Kubernetes DNS label.",
                "settings.namespace_format",
                source="settings",
                path=scoped_path(path_prefix, "namespace"),
            )
        )
    if is_production_environment(namespace):
        diagnostics.append(
            item(
                "warning",
                "namespace points at production. Use Safe PR or an explicit approval before applying.",
                "risk.settings_production_namespace",
                source="settings",
                path=scoped_path(path_prefix, "namespace"),
                action="safe_pr",
            )
        )

    replicas = settings.get("replicas")
    if replicas is not None and (
        isinstance(replicas, bool) or not isinstance(replicas, int) or replicas < 0
    ):
        diagnostics.append(
            item(
                "error",
                "replicas must be a non-negative integer.",
                "settings.replicas_range",
                source="settings",
                path=scoped_path(path_prefix, "replicas"),
            )
        )
    elif replicas == 0:
        diagnostics.append(
            item(
                "warning",
                "replicas is 0. This may be intentional, but the service will stop serving traffic.",
                "risk.settings_replicas_zero",
                source="settings",
                path=scoped_path(path_prefix, "replicas"),
                action="confirm",
            )
        )

    diagnostics.extend(release_policy_diagnostics(settings, path_prefix))
    previous = mapping_value(context.get("previous_settings"))
    diagnostics.extend(settings_risk_diff(settings, previous, path_prefix))
    return diagnostics


def release_policy_diagnostics(
    settings: Mapping[str, Any],
    path_prefix: str = "",
) -> list[DiagnosticItem]:
    diagnostics: list[DiagnosticItem] = []
    diagnostics.extend(choice_diagnostic(settings, "execution_mode", EXECUTION_MODES, path_prefix))
    diagnostics.extend(choice_diagnostic(settings, "runtime_mode", RUNTIME_MODES, path_prefix))
    diagnostics.extend(choice_diagnostic(settings, "provider_mode", PROVIDER_MODES, path_prefix))
    diagnostics.extend(
        choice_diagnostic(settings, "approval_policy", APPROVAL_POLICIES, path_prefix)
    )
    diagnostics.extend(choice_diagnostic(settings, "failure_policy", FAILURE_POLICIES, path_prefix))
    diagnostics.extend(
        choice_diagnostic(settings, "rollback_policy", ROLLBACK_POLICIES, path_prefix)
    )
    diagnostics.extend(
        choice_diagnostic(settings, "default_strategy", DEPLOY_STRATEGIES, path_prefix)
    )
    diagnostics.extend(choice_diagnostic(settings, "strategy", DEPLOY_STRATEGIES, path_prefix))
    diagnostics.extend(choice_diagnostic(settings, "approval_gate", APPROVAL_GATES, path_prefix))
    diagnostics.extend(int_range_diagnostic(settings, "concurrency", 1, 20, path_prefix))
    diagnostics.extend(
        int_range_diagnostic(settings, "health_timeout_seconds", 30, 3600, path_prefix)
    )
    diagnostics.extend(int_range_diagnostic(settings, "timeout_seconds", 30, 3600, path_prefix))
    diagnostics.extend(int_range_diagnostic(settings, "retry_attempts", 0, 10, path_prefix))

    environment = str(settings.get("environment") or "")
    if environment and not valid_dns_label(environment):
        diagnostics.append(
            item(
                "error",
                "environment must be a Kubernetes-style DNS label.",
                "release.environment_format",
                source="release_flow",
                path=scoped_path(path_prefix, "environment"),
            )
        )

    env_order = settings.get("environment_order")
    if env_order is not None:
        if not isinstance(env_order, list):
            diagnostics.append(
                item(
                    "error",
                    "environment_order must be a list.",
                    "release.environment_order_type",
                    source="release_flow",
                    path=scoped_path(path_prefix, "environment_order"),
                )
            )
        else:
            environments = [str(env).strip() for env in env_order if str(env).strip()]
            duplicates = sorted({env for env in environments if environments.count(env) > 1})
            for env in environments:
                if not valid_dns_label(env):
                    diagnostics.append(
                        item(
                            "error",
                            f"environment {env} must be a DNS label.",
                            "release.environment_order_format",
                            source="release_flow",
                            path=scoped_path(path_prefix, "environment_order"),
                        )
                    )
            if duplicates:
                diagnostics.append(
                    item(
                        "error",
                        f"environment_order contains duplicates: {', '.join(duplicates)}.",
                        "release.environment_order_duplicate",
                        source="release_flow",
                        path=scoped_path(path_prefix, "environment_order"),
                    )
                )

    strategy = str(settings.get("strategy") or settings.get("default_strategy") or "")
    canary_percent = int_like(settings.get("canary_percent"))
    if strategy == "canary" and (canary_percent is None or not 1 <= canary_percent <= 99):
        diagnostics.append(
            item(
                "error",
                "canary_percent must be between 1 and 99 for canary strategy.",
                "release.canary_percent_range",
                source="release_flow",
                path=scoped_path(path_prefix, "canary_percent"),
            )
        )
    if strategy == "blue_green" and not str(settings.get("service_name") or "").strip():
        diagnostics.append(
            item(
                "warning",
                "blue_green strategy should name the Service that switches traffic.",
                "release.blue_green_service_missing",
                source="release_flow",
                path=scoped_path(path_prefix, "service_name"),
                action="confirm",
            )
        )

    health_check_path = str(settings.get("health_check_path") or "")
    if health_check_path and not health_check_path.startswith("/"):
        diagnostics.append(
            item(
                "error",
                "health_check_path must start with /.",
                "release.health_check_path_format",
                source="release_flow",
                path=scoped_path(path_prefix, "health_check_path"),
            )
        )

    approval_policy = str(settings.get("approval_policy") or "")
    rollback_policy = str(settings.get("rollback_policy") or "")
    failure_policy = str(settings.get("failure_policy") or "")
    environments = [str(env).strip() for env in list_value(settings.get("environment_order"))]
    if approval_policy == "auto_safe" and any(
        is_production_environment(environment) for environment in environments
    ):
        diagnostics.append(
            item(
                "warning",
                "auto_safe approval with production in the promotion path should be upgraded to manual or external approval.",
                "risk.release_auto_approval_production",
                source="release_flow",
                path=scoped_path(path_prefix, "approval_policy"),
                action="approval_required",
            )
        )
    if rollback_policy == "disabled" and any(
        is_production_environment(environment) for environment in environments
    ):
        diagnostics.append(
            item(
                "warning",
                "production releases should keep a rollback policy enabled.",
                "risk.release_rollback_disabled_production",
                source="release_flow",
                path=scoped_path(path_prefix, "rollback_policy"),
                action="confirm",
            )
        )
    if failure_policy == "continue_independent" and rollback_policy in {"", "manual", "disabled"}:
        diagnostics.append(
            item(
                "warning",
                "continue_independent should be paired with an automated rollback or Safe PR path.",
                "risk.release_continue_without_rollback",
                source="release_flow",
                path=scoped_path(path_prefix, "failure_policy"),
                action="confirm",
            )
        )
    return diagnostics


def choice_diagnostic(
    settings: Mapping[str, Any],
    field: str,
    allowed: set[str],
    path_prefix: str,
) -> list[DiagnosticItem]:
    if field not in settings:
        return []
    value = str(settings.get(field) or "")
    if value in allowed:
        return []
    return [
        item(
            "error",
            f"{field} must be one of: {', '.join(sorted(allowed))}.",
            f"release.{field}_choice",
            source="release_flow",
            path=scoped_path(path_prefix, field),
        )
    ]


def int_range_diagnostic(
    settings: Mapping[str, Any],
    field: str,
    minimum: int,
    maximum: int,
    path_prefix: str,
) -> list[DiagnosticItem]:
    if field not in settings:
        return []
    value = int_like(settings.get(field))
    if value is not None and minimum <= value <= maximum:
        return []
    return [
        item(
            "error",
            f"{field} must be an integer between {minimum} and {maximum}.",
            f"release.{field}_range",
            source="release_flow",
            path=scoped_path(path_prefix, field),
        )
    ]


def release_plan_diagnostics(
    settings: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
) -> list[DiagnosticItem]:
    context = context or {}
    plan_settings = mapping_value(settings.get("settings"))
    diagnostics = settings_diagnostics(plan_settings, context, "settings") if plan_settings else []
    steps = settings.get("steps", [])
    if not isinstance(steps, list) or not steps:
        diagnostics.append(
            item(
                "warning",
                "Add at least one application step to make this release flow useful.",
                "release.steps_empty",
                source="release_flow",
                path="steps",
            )
        )
        return diagnostics

    ids: list[str] = []
    for index, step in enumerate(steps):
        if not isinstance(step, Mapping):
            diagnostics.append(
                item(
                    "error",
                    f"Step {index + 1} must be an object.",
                    "release.step_object",
                    source="release_flow",
                    path=f"steps[{index}]",
                )
            )
            continue
        application_id = str(step.get("application_id") or "")
        if not application_id:
            diagnostics.append(
                item(
                    "error",
                    f"Step {index + 1} needs an application.",
                    "release.step_application_required",
                    source="release_flow",
                    path=f"steps[{index}].application_id",
                )
            )
        ids.append(application_id)
        config = step.get("config", {})
        if isinstance(config, Mapping):
            baselines = mapping_value(context.get("previous_settings_by_application"))
            diagnostics.extend(
                settings_diagnostics(
                    config,
                    {"previous_settings": mapping_value(baselines.get(application_id))},
                    path_prefix=f"steps[{index}].config",
                )
            )
            diagnostics.extend(step_policy_diagnostics(config, plan_settings, index))
            diagnostics.extend(step_dispatch_diagnostics(config, plan_settings, index))

    duplicates = sorted({app_id for app_id in ids if app_id and ids.count(app_id) > 1})
    for app_id in duplicates:
        diagnostics.append(
            item(
                "warning",
                f"{app_id} appears more than once in the release flow.",
                "release.duplicate_application",
                source="release_flow",
                path="steps",
            )
        )

    known = set(ids)
    graph: dict[str, list[str]] = {}
    for step in [s for s in steps if isinstance(s, Mapping)]:
        app_id = str(step.get("application_id") or "")
        deps = [str(dep) for dep in step.get("depends_on", []) if dep]
        graph.setdefault(app_id, []).extend(deps)
        for dep in deps:
            if dep not in known:
                diagnostics.append(
                    item(
                        "error",
                        f"{app_id} depends on unknown step {dep}.",
                        "release.unknown_dependency",
                        source="release_flow",
                        path="depends_on",
                    )
                )
    if has_cycle(graph):
        diagnostics.append(
            item(
                "error",
                "Release flow dependencies contain a cycle.",
                "release.dependency_cycle",
                source="release_flow",
                path="steps",
            )
        )
    return diagnostics


def step_policy_diagnostics(
    config: Mapping[str, Any],
    plan_settings: Mapping[str, Any],
    index: int,
) -> list[DiagnosticItem]:
    diagnostics: list[DiagnosticItem] = []
    path_prefix = f"steps[{index}].config"
    environment = str(config.get("environment") or "")
    env_order = [str(env) for env in list_value(plan_settings.get("environment_order"))]
    if environment and env_order and environment not in env_order:
        diagnostics.append(
            item(
                "warning",
                f"{environment} is not in the plan promotion path.",
                "release.step_environment_outside_order",
                source="release_flow",
                path=scoped_path(path_prefix, "environment"),
                action="confirm",
            )
        )
    gate = str(config.get("approval_gate") or "inherit")
    effective_gate = gate
    if gate == "inherit":
        effective_gate = str(plan_settings.get("approval_policy") or "auto_safe")
    if effective_gate == "auto" and is_production_environment(environment):
        diagnostics.append(
            item(
                "warning",
                "production step should not use an automatic approval gate.",
                "risk.release_step_auto_gate_production",
                source="release_flow",
                path=scoped_path(path_prefix, "approval_gate"),
                action="approval_required",
            )
        )
    if gate in {"manual", "safe_pr"}:
        diagnostics.append(
            item(
                "info",
                f"Step waits for {gate} before apply.",
                "release.step_gate_enabled",
                source="release_flow",
                path=scoped_path(path_prefix, "approval_gate"),
            )
        )
    return diagnostics


def step_dispatch_diagnostics(
    config: Mapping[str, Any],
    plan_settings: Mapping[str, Any],
    index: int,
) -> list[DiagnosticItem]:
    execution_mode = str(plan_settings.get("execution_mode") or "")
    if execution_mode in {"", "preview_only"}:
        return []
    diagnostics: list[DiagnosticItem] = []
    path_prefix = f"steps[{index}].config"
    if not str(config.get("commit_sha") or "").strip():
        diagnostics.append(
            item(
                "error",
                "commit_sha is required before dispatching this release step.",
                "release.step_commit_sha_required",
                source="release_flow",
                path=scoped_path(path_prefix, "commit_sha"),
            )
        )
    if not str(config.get("image") or "").strip():
        diagnostics.append(
            item(
                "error",
                "image is required before dispatching this release step.",
                "release.step_image_required",
                source="release_flow",
                path=scoped_path(path_prefix, "image"),
            )
        )
    return diagnostics


def parse_yaml_objects(content: str) -> list[Mapping[str, Any]]:
    try:
        docs = yaml.safe_load_all(content)
    except yaml.YAMLError:
        return []
    return [doc for doc in docs if isinstance(doc, Mapping)]


def risk_diff_diagnostics(
    content: str,
    previous_docs: list[Mapping[str, Any]],
    current_docs: list[Mapping[str, Any]],
) -> list[DiagnosticItem]:
    diagnostics: list[DiagnosticItem] = []
    previous_exact = {resource_key(doc, include_namespace=True): doc for doc in previous_docs}
    previous_by_name = {resource_key(doc, include_namespace=False): doc for doc in previous_docs}

    for current in current_docs:
        previous = previous_exact.get(resource_key(current, include_namespace=True))
        previous = previous or previous_by_name.get(resource_key(current, include_namespace=False))
        if previous is None:
            continue
        diagnostics.extend(resource_risk_diff(content, previous, current))
    return diagnostics


def resource_risk_diff(
    content: str,
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> list[DiagnosticItem]:
    diagnostics: list[DiagnosticItem] = []
    old_namespace = resource_namespace(previous)
    new_namespace = resource_namespace(current)
    if old_namespace and new_namespace and old_namespace != new_namespace:
        diagnostics.append(
            item(
                "warning",
                f"Namespace changes from {old_namespace} to {new_namespace}; confirm the target environment.",
                "risk.namespace_changed",
                source="policy",
                line=find_key_line(content, "namespace"),
                path="metadata.namespace",
                action="approval_required",
            )
        )

    kind = str(current.get("kind") or "")
    if kind == "Deployment":
        diagnostics.extend(deployment_risk_diff(content, previous, current))
    elif kind == "Service":
        diagnostics.extend(service_risk_diff(content, previous, current))
    elif kind == "ConfigMap":
        diagnostics.extend(configmap_risk_diff(previous, current))
    return diagnostics


def deployment_risk_diff(
    content: str,
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> list[DiagnosticItem]:
    diagnostics: list[DiagnosticItem] = []
    old_spec = mapping_value(previous.get("spec"))
    new_spec = mapping_value(current.get("spec"))
    old_replicas = int_like(old_spec.get("replicas", 1))
    new_replicas = int_like(new_spec.get("replicas", 1))
    if old_replicas is not None and old_replicas > 0 and new_replicas == 0:
        diagnostics.append(
            item(
                "warning",
                f"replicas changes from {old_replicas} to 0. The service can stop serving traffic.",
                "risk.replicas_scaled_to_zero",
                source="policy",
                line=find_key_line(content, "replicas"),
                path="spec.replicas",
                action="confirm",
            )
        )

    for index, old_container, new_container in matched_containers(previous, current):
        old_image = str(old_container.get("image") or "")
        new_image = str(new_container.get("image") or "")
        if old_image and new_image and image_repo(old_image) != image_repo(new_image):
            diagnostics.append(
                item(
                    "warning",
                    "Container image repository changes; confirm the new image source is trusted.",
                    "risk.image_repository_changed",
                    source="policy",
                    line=find_key_line(content, "image"),
                    path=f"spec.template.spec.containers[{index}].image",
                    action="approval_required",
                )
            )

        old_limits = mapping_value(mapping_value(old_container.get("resources")).get("limits"))
        new_limits = mapping_value(mapping_value(new_container.get("resources")).get("limits"))
        if old_limits and not new_limits:
            diagnostics.append(
                item(
                    "warning",
                    "Container resource limits were removed; CPU or memory usage can become unbounded.",
                    "risk.resource_limits_removed",
                    source="policy",
                    line=find_key_line(content, "resources"),
                    path=f"spec.template.spec.containers[{index}].resources.limits",
                    action="confirm",
                )
            )

        for probe in ("readinessProbe", "livenessProbe"):
            if isinstance(old_container.get(probe), Mapping) and not isinstance(
                new_container.get(probe), Mapping
            ):
                diagnostics.append(
                    item(
                        "warning",
                        f"{probe} was removed; rollout health behavior can change.",
                        f"risk.{to_code(probe)}_removed",
                        source="policy",
                        line=find_key_line(content, probe),
                        path=f"spec.template.spec.containers[{index}].{probe}",
                        action="confirm",
                    )
                )
    return diagnostics


def service_risk_diff(
    content: str,
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> list[DiagnosticItem]:
    diagnostics: list[DiagnosticItem] = []
    old_spec = mapping_value(previous.get("spec"))
    new_spec = mapping_value(current.get("spec"))
    if mapping_value(old_spec.get("selector")) != mapping_value(new_spec.get("selector")):
        diagnostics.append(
            item(
                "warning",
                "Service selector changed; the Service may stop routing to the existing Pods.",
                "risk.service_selector_changed",
                source="policy",
                line=find_key_line(content, "selector"),
                path="spec.selector",
                action="approval_required",
            )
        )

    old_ports = list_value(old_spec.get("ports"))
    new_ports = list_value(new_spec.get("ports"))
    for index, (old_port, new_port) in enumerate(zip(old_ports, new_ports, strict=False)):
        if not isinstance(old_port, Mapping) or not isinstance(new_port, Mapping):
            continue
        if old_port.get("targetPort") != new_port.get("targetPort"):
            diagnostics.append(
                item(
                    "warning",
                    "Service targetPort changed; the Service can stay up while app traffic fails.",
                    "risk.service_target_port_changed",
                    source="policy",
                    line=find_key_line(content, "targetPort"),
                    path=f"spec.ports[{index}].targetPort",
                    action="confirm",
                )
            )
    return diagnostics


def configmap_risk_diff(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> list[DiagnosticItem]:
    old_keys = set(mapping_value(previous.get("data")).keys())
    new_keys = set(mapping_value(current.get("data")).keys())
    removed = sorted(old_keys - new_keys)
    if not removed:
        return []
    return [
        item(
            "warning",
            f"ConfigMap keys removed: {', '.join(removed)}. Apps depending on them may fail at runtime.",
            "risk.configmap_keys_removed",
            source="policy",
            path="data",
            action="confirm",
        )
    ]


def settings_risk_diff(
    current: Mapping[str, Any],
    previous: Mapping[str, Any],
    path_prefix: str,
) -> list[DiagnosticItem]:
    if not previous:
        return []
    diagnostics: list[DiagnosticItem] = []

    old_replicas = int_like(previous.get("replicas"))
    new_replicas = int_like(current.get("replicas"))
    if old_replicas is not None and old_replicas > 0 and new_replicas == 0:
        diagnostics.append(
            item(
                "warning",
                f"replicas changes from {old_replicas} to 0. Confirm this planned stop before continuing.",
                "risk.settings_replicas_scaled_to_zero",
                source="settings",
                path=scoped_path(path_prefix, "replicas"),
                action="confirm",
            )
        )

    comparisons = [
        (
            "repo_ref",
            "risk.settings_repo_changed",
            "Repository target changed; confirm this release still points to the intended source.",
            "approval_required",
        ),
        (
            "branch",
            "risk.settings_branch_changed",
            "Branch changed; confirm the release source.",
            "confirm",
        ),
        (
            "manifest_path",
            "risk.settings_manifest_path_changed",
            "Manifest path changed; this can deploy a different Kubernetes object set.",
            "confirm",
        ),
        (
            "namespace",
            "risk.settings_namespace_changed",
            "Namespace changed; confirm the target environment before applying.",
            "approval_required",
        ),
    ]
    for field, code, message, action in comparisons:
        old_value = str(previous.get(field) or "")
        new_value = str(current.get(field) or "")
        if old_value and new_value and old_value != new_value:
            diagnostics.append(
                item(
                    "warning",
                    message,
                    code,
                    source="settings",
                    path=scoped_path(path_prefix, field),
                    action=action,
                )
            )
    return diagnostics


def resource_key(doc: Mapping[str, Any], *, include_namespace: bool) -> tuple[str, str, str, str]:
    metadata = mapping_value(doc.get("metadata"))
    namespace = resource_namespace(doc) if include_namespace else ""
    return (
        str(doc.get("apiVersion") or ""),
        str(doc.get("kind") or ""),
        namespace,
        str(metadata.get("name") or ""),
    )


def resource_namespace(doc: Mapping[str, Any]) -> str:
    return str(mapping_value(doc.get("metadata")).get("namespace") or "")


def matched_containers(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> list[tuple[int, Mapping[str, Any], Mapping[str, Any]]]:
    old = [
        container for container in deployment_containers(previous) if isinstance(container, Mapping)
    ]
    new = [
        container for container in deployment_containers(current) if isinstance(container, Mapping)
    ]
    old_by_name = {
        str(container.get("name") or index): container for index, container in enumerate(old)
    }
    matched: list[tuple[int, Mapping[str, Any], Mapping[str, Any]]] = []
    for index, container in enumerate(new):
        old_container = old_by_name.get(str(container.get("name") or index))
        if old_container is not None:
            matched.append((index, old_container, container))
    return matched


def deployment_containers(doc: Mapping[str, Any]) -> list[Any]:
    spec = mapping_value(doc.get("spec"))
    template = mapping_value(spec.get("template"))
    pod_spec = mapping_value(template.get("spec"))
    return list_value(pod_spec.get("containers"))


def mapping_value(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def int_like(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value):
        return int(value)
    return None


def image_repo(image: str) -> str:
    image_without_digest = image.split("@", 1)[0]
    last_segment = image_without_digest.rsplit("/", 1)[-1]
    if ":" not in last_segment:
        return image_without_digest
    repo_prefix = image_without_digest.rsplit(":", 1)[0]
    return repo_prefix


def scoped_path(prefix: str, path: str) -> str:
    return f"{prefix}.{path}" if prefix else path


def to_code(value: str) -> str:
    return re.sub(r"(?<!^)([A-Z])", r"_\1", value).lower()


def has_cycle(graph: Mapping[str, list[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for dep in graph.get(node, []):
            if visit(dep):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in graph)


def missing_key(
    content: str,
    key: str,
    code: str,
    document_index: int,
    path: str | None = None,
) -> DiagnosticItem:
    return item(
        "error",
        f"Document {document_index} is missing {path or key}.",
        code,
        source="kubernetes",
        line=find_key_line(content, key),
        path=path or key,
    )


def find_key_line(content: str, key: str) -> int:
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*:", re.MULTILINE)
    match = pattern.search(content)
    if not match:
        return 1
    return content[: match.start()].count("\n") + 1


def compact_yaml_error(exc: yaml.YAMLError) -> str:
    message = str(exc).strip().splitlines()
    return " ".join(part.strip() for part in message if part.strip())[:500]


def valid_dns_label(value: str) -> bool:
    return len(value) <= 63 and bool(DNS_LABEL_RE.match(value))
