"""Deterministic Kubernetes manifest generation for release-plan steps."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any

import yaml

from domains.diagnostics.router import item, yaml_diagnostics
from packages.config.constants import Sandbox
from packages.contracts.gateway.responses import DiagnosticItem

DNS_LABEL_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
MANIFEST_EXTENSIONS = (".yaml", ".yml")
SECRET_KEY_HINTS = ("password", "passwd", "secret", "token", "apikey", "api_key")
MAX_DEPLOYMENT_REPLICAS = 100
DEFAULT_CONTAINER_PORT = 8080
DEFAULT_SERVICE_PORT = 80
DEFAULT_CPU_REQUEST = "100m"
DEFAULT_CPU_LIMIT = "500m"
DEFAULT_MEMORY_REQUEST = "128Mi"
DEFAULT_MEMORY_LIMIT = "512Mi"
REPLACE_ME_IMAGE = "ghcr.io/example/replace-me:required"


def render_release_step_manifest(
    plan: Mapping[str, Any],
    step_index: int,
    application: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Render production-shaped Kubernetes YAML from a release-plan step."""

    diagnostics: list[DiagnosticItem] = []
    warnings: list[str] = []
    steps = list_value(plan.get("steps"))
    if not steps:
        diagnostics.append(
            item(
                "error",
                "Release plan needs at least one step before a manifest can be generated.",
                "manifest.step_required",
                source="manifest",
                path="steps",
            )
        )
        return manifest_response("", [], [], diagnostics, warnings, "No release step selected.")
    if step_index < 0 or step_index >= len(steps):
        diagnostics.append(
            item(
                "error",
                f"Step index {step_index} is outside the release plan.",
                "manifest.step_index_invalid",
                source="manifest",
                path="step_index",
            )
        )
        return manifest_response("", [], [], diagnostics, warnings, "Invalid release step.")

    step = mapping_value(steps[step_index])
    application = mapping_value(application)
    settings = mapping_value(plan.get("settings"))
    config = mapping_value(step.get("config"))
    context = ManifestContext(plan, step, config, settings, application, diagnostics, warnings)
    resources = build_manifest_resources(context)
    manifest = dump_yaml_documents(resources)
    diagnostics.extend(yaml_diagnostics(manifest, {"namespace": context.namespace}))
    file_path = generated_manifest_path(context)
    if context.generated_path_warning:
        warnings.append(context.generated_path_warning)
    return manifest_response(
        manifest,
        [
            {
                "path": file_path,
                "content": manifest,
                "action": "upsert",
                "description": context.file_description,
            }
        ],
        resource_summaries(resources),
        diagnostics,
        warnings,
        f"Generated {len(resources)} Kubernetes resource(s) for {context.name}.",
    )


class ManifestContext:
    def __init__(
        self,
        plan: Mapping[str, Any],
        step: Mapping[str, Any],
        config: Mapping[str, Any],
        settings: Mapping[str, Any],
        application: Mapping[str, Any],
        diagnostics: list[DiagnosticItem],
        warnings: list[str],
    ) -> None:
        self.plan = plan
        self.step = step
        self.config = config
        self.settings = settings
        self.application = application
        self.diagnostics = diagnostics
        self.warnings = warnings
        self.application_id = str(
            step.get("application_id") or application.get("application_id") or ""
        )
        self.name = self.slug_field(
            "workload_name",
            config.get("workload_name")
            or config.get("name")
            or application.get("name")
            or step.get("name")
            or self.application_id
            or "application",
        )
        self.plan_slug = self.slug_value(str(plan.get("name") or "release"))
        self.namespace = self.slug_field(
            "namespace",
            config.get("namespace") or application.get("namespace") or Sandbox.NAMESPACE,
        )
        self.image = required_image(config.get("image") or settings.get("image"), diagnostics)
        self.replicas = self.int_field(
            "replicas", config.get("replicas"), 2, 0, MAX_DEPLOYMENT_REPLICAS
        )
        self.container_port = self.int_field(
            "container_port",
            config.get("container_port") or config.get("target_port"),
            DEFAULT_CONTAINER_PORT,
            1,
            65535,
        )
        self.service_port = self.int_field(
            "service_port",
            config.get("service_port"),
            DEFAULT_SERVICE_PORT,
            1,
            65535,
        )
        self.strategy = str(config.get("strategy") or settings.get("default_strategy") or "rolling")
        self.source_type = str(
            config.get("source_type")
            or mapping_value(application.get("metadata")).get("source_type")
            or ""
        )
        self.generated_path_warning = ""
        self.file_description = f"Generated manifest for {self.name}"

    @property
    def selector_labels(self) -> dict[str, str]:
        return {
            "app.kubernetes.io/name": self.name,
            "app.kubernetes.io/instance": self.name,
        }

    @property
    def labels(self) -> dict[str, str]:
        return {
            **self.selector_labels,
            "app.kubernetes.io/managed-by": "myjob",
            "app.kubernetes.io/part-of": self.plan_slug,
        }

    @property
    def annotations(self) -> dict[str, str]:
        values = {
            "myjob.io/application-id": self.application_id,
            "myjob.io/release-plan": str(self.plan.get("plan_id") or self.plan.get("name") or ""),
            "myjob.io/branch": str(
                self.config.get("branch") or self.application.get("branch") or ""
            ),
            "myjob.io/commit-sha": str(self.config.get("commit_sha") or ""),
            "myjob.io/rollout-strategy": self.strategy,
        }
        canary = self.config.get("canary_percent")
        if canary not in (None, "", 0):
            values["myjob.io/canary-percent"] = str(canary)
        return {key: value for key, value in values.items() if value}

    def slug_field(self, field: str, value: Any) -> str:
        raw = str(value or "").strip()
        slug = self.slug_value(raw or field)
        if raw and raw != slug:
            self.diagnostics.append(
                item(
                    "warning",
                    f"{field} was normalized to Kubernetes DNS label '{slug}'.",
                    f"manifest.{field}_normalized",
                    source="manifest",
                    path=f"config.{field}",
                    action="confirm",
                )
            )
        return slug

    def slug_value(self, value: str) -> str:
        slug = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
        slug = re.sub(r"-+", "-", slug)[:63].strip("-")
        if not slug:
            slug = "app"
        if not DNS_LABEL_RE.match(slug):
            slug = f"app-{hashlib.sha256(value.encode()).hexdigest()[:8]}"
        return slug

    def int_field(self, field: str, raw: Any, default: int, minimum: int, maximum: int) -> int:
        value = int_like(raw)
        if value is None:
            if raw not in (None, ""):
                self.diagnostics.append(
                    item(
                        "error",
                        f"{field} must be an integer.",
                        f"manifest.{field}_integer",
                        source="manifest",
                        path=f"config.{field}",
                    )
                )
            return default
        if value < minimum or value > maximum:
            self.diagnostics.append(
                item(
                    "error",
                    f"{field} must be between {minimum} and {maximum}.",
                    f"manifest.{field}_range",
                    source="manifest",
                    path=f"config.{field}",
                )
            )
            return max(minimum, min(maximum, value))
        return value


def build_manifest_resources(ctx: ManifestContext) -> list[dict[str, Any]]:
    resources: list[dict[str, Any]] = []
    config_map = config_map_data(ctx)
    if config_map:
        resources.append(config_map_manifest(ctx, config_map))
    resources.append(deployment_manifest(ctx, config_map))
    resources.append(service_manifest(ctx))
    if pdb_enabled(ctx):
        resources.append(pod_disruption_budget_manifest(ctx))
    if autoscaling_enabled(ctx):
        resources.append(horizontal_pod_autoscaler_manifest(ctx))
    ingress = ingress_manifest(ctx)
    if ingress is not None:
        resources.append(ingress)
    return resources


def deployment_manifest(ctx: ManifestContext, config_map: dict[str, str]) -> dict[str, Any]:
    container = {
        "name": ctx.name,
        "image": ctx.image,
        "imagePullPolicy": str(ctx.config.get("image_pull_policy") or "IfNotPresent"),
        "ports": [{"containerPort": ctx.container_port, "name": "http"}],
        "resources": resources_for(ctx),
        "securityContext": container_security_context(ctx),
    }
    env = env_vars(ctx)
    if env:
        container["env"] = env
    env_from = []
    if config_map and bool_field(ctx.config.get("env_from_config_map"), True):
        env_from.append({"configMapRef": {"name": config_map_name(ctx)}})
    if env_from:
        container["envFrom"] = env_from
    readiness_path = str(
        ctx.config.get("readiness_path") or ctx.config.get("health_check_path") or "/readyz"
    )
    liveness_path = str(ctx.config.get("liveness_path") or "/healthz")
    if readiness_path:
        container["readinessProbe"] = http_probe(readiness_path, ctx.container_port)
    if liveness_path:
        container["livenessProbe"] = http_probe(liveness_path, ctx.container_port, initial_delay=20)

    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": metadata(ctx, ctx.name),
        "spec": {
            "replicas": ctx.replicas,
            "revisionHistoryLimit": int_like(ctx.config.get("revision_history_limit")) or 5,
            "strategy": deployment_strategy(ctx),
            "selector": {"matchLabels": ctx.selector_labels},
            "template": {
                "metadata": {"labels": ctx.labels, "annotations": pod_annotations(ctx)},
                "spec": {
                    "securityContext": pod_security_context(ctx),
                    "terminationGracePeriodSeconds": int_like(
                        ctx.config.get("termination_grace_period_seconds")
                    )
                    or 30,
                    "containers": [container],
                },
            },
        },
    }


def service_manifest(ctx: ManifestContext) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": metadata(ctx, service_name(ctx)),
        "spec": {
            "type": str(ctx.config.get("service_type") or "ClusterIP"),
            "selector": ctx.selector_labels,
            "ports": [
                {
                    "name": "http",
                    "port": ctx.service_port,
                    "targetPort": ctx.container_port,
                    "protocol": "TCP",
                }
            ],
        },
    }


def config_map_manifest(ctx: ManifestContext, data: dict[str, str]) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": metadata(ctx, config_map_name(ctx)),
        "data": data,
    }


def pod_disruption_budget_manifest(ctx: ManifestContext) -> dict[str, Any]:
    return {
        "apiVersion": "policy/v1",
        "kind": "PodDisruptionBudget",
        "metadata": metadata(ctx, f"{ctx.name}-pdb"),
        "spec": {
            "maxUnavailable": int_like(ctx.config.get("pdb_max_unavailable")) or 1,
            "selector": {"matchLabels": ctx.selector_labels},
        },
    }


def horizontal_pod_autoscaler_manifest(ctx: ManifestContext) -> dict[str, Any]:
    min_replicas = int_like(ctx.config.get("min_replicas")) or max(1, ctx.replicas)
    max_replicas = int_like(ctx.config.get("max_replicas")) or max(min_replicas, 5)
    if max_replicas < min_replicas:
        ctx.diagnostics.append(
            item(
                "error",
                "max_replicas must be greater than or equal to min_replicas.",
                "manifest.hpa_replica_range",
                source="manifest",
                path="config.max_replicas",
            )
        )
        max_replicas = min_replicas
    return {
        "apiVersion": "autoscaling/v2",
        "kind": "HorizontalPodAutoscaler",
        "metadata": metadata(ctx, f"{ctx.name}-hpa"),
        "spec": {
            "scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": ctx.name},
            "minReplicas": min_replicas,
            "maxReplicas": max_replicas,
            "metrics": [
                {
                    "type": "Resource",
                    "resource": {
                        "name": "cpu",
                        "target": {
                            "type": "Utilization",
                            "averageUtilization": int_like(ctx.config.get("target_cpu_utilization"))
                            or 70,
                        },
                    },
                }
            ],
        },
    }


def ingress_manifest(ctx: ManifestContext) -> dict[str, Any] | None:
    if not bool_field(ctx.config.get("ingress_enabled"), False):
        return None
    host = str(ctx.config.get("ingress_host") or ctx.config.get("host") or "").strip()
    if not host:
        ctx.diagnostics.append(
            item(
                "error",
                "ingress_host is required when ingress is enabled.",
                "manifest.ingress_host_required",
                source="manifest",
                path="config.ingress_host",
            )
        )
        return None
    path = str(ctx.config.get("ingress_path") or "/")
    spec: dict[str, Any] = {
        "rules": [
            {
                "host": host,
                "http": {
                    "paths": [
                        {
                            "path": path,
                            "pathType": "Prefix",
                            "backend": {
                                "service": {
                                    "name": service_name(ctx),
                                    "port": {"number": ctx.service_port},
                                }
                            },
                        }
                    ]
                },
            }
        ]
    }
    tls_secret = str(ctx.config.get("tls_secret_name") or "").strip()
    if tls_secret:
        spec["tls"] = [{"hosts": [host], "secretName": tls_secret}]
    ingress_class = str(ctx.config.get("ingress_class_name") or "").strip()
    if ingress_class:
        spec["ingressClassName"] = ingress_class
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": metadata(ctx, f"{ctx.name}-ingress"),
        "spec": spec,
    }


def metadata(ctx: ManifestContext, name: str) -> dict[str, Any]:
    return {
        "name": name[:63].strip("-"),
        "namespace": ctx.namespace,
        "labels": ctx.labels,
        "annotations": ctx.annotations,
    }


def pod_annotations(ctx: ManifestContext) -> dict[str, str]:
    values = {
        "myjob.io/commit-sha": str(ctx.config.get("commit_sha") or ""),
        "myjob.io/generated": "true",
    }
    return {key: value for key, value in values.items() if value}


def deployment_strategy(ctx: ManifestContext) -> dict[str, Any]:
    if ctx.strategy == "rolling":
        return {
            "type": "RollingUpdate",
            "rollingUpdate": {
                "maxUnavailable": str(ctx.config.get("max_unavailable") or "25%"),
                "maxSurge": str(ctx.config.get("max_surge") or "25%"),
            },
        }
    return {"type": "RollingUpdate", "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1}}


def resources_for(ctx: ManifestContext) -> dict[str, Any]:
    configured = mapping_value(ctx.config.get("resources"))
    requests = mapping_value(configured.get("requests"))
    limits = mapping_value(configured.get("limits"))
    return {
        "requests": {
            "cpu": str(requests.get("cpu") or ctx.config.get("cpu_request") or DEFAULT_CPU_REQUEST),
            "memory": str(
                requests.get("memory") or ctx.config.get("memory_request") or DEFAULT_MEMORY_REQUEST
            ),
        },
        "limits": {
            "cpu": str(limits.get("cpu") or ctx.config.get("cpu_limit") or DEFAULT_CPU_LIMIT),
            "memory": str(
                limits.get("memory") or ctx.config.get("memory_limit") or DEFAULT_MEMORY_LIMIT
            ),
        },
    }


def pod_security_context(ctx: ManifestContext) -> dict[str, Any]:
    configured = mapping_value(ctx.config.get("pod_security_context"))
    return {
        "runAsNonRoot": configured.get("runAsNonRoot", True),
        "seccompProfile": configured.get("seccompProfile", {"type": "RuntimeDefault"}),
    }


def container_security_context(ctx: ManifestContext) -> dict[str, Any]:
    configured = mapping_value(ctx.config.get("security_context"))
    base = {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "runAsNonRoot": True,
        "capabilities": {"drop": ["ALL"]},
    }
    return {**base, **configured}


def http_probe(path: str, port: int, initial_delay: int = 5) -> dict[str, Any]:
    return {
        "httpGet": {"path": path, "port": port},
        "initialDelaySeconds": initial_delay,
        "periodSeconds": 10,
        "timeoutSeconds": 2,
        "failureThreshold": 3,
    }


def env_vars(ctx: ManifestContext) -> list[dict[str, Any]]:
    env: list[dict[str, Any]] = []
    for entry in list_value(ctx.config.get("env")):
        if not isinstance(entry, Mapping):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        if "value" in entry:
            if likely_secret_key(name):
                ctx.diagnostics.append(
                    item(
                        "warning",
                        "Plain env values with secret-like names should use secret_refs instead.",
                        "manifest.plain_secret_env",
                        source="manifest",
                        path="config.env",
                        action="approval_required",
                    )
                )
            env.append({"name": name, "value": str(entry.get("value") or "")})
    for ref in secret_refs(ctx):
        env.append(ref)
    return env


def secret_refs(ctx: ManifestContext) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for entry in list_value(ctx.config.get("secret_refs")):
        if not isinstance(entry, Mapping):
            continue
        env_name = str(entry.get("env") or entry.get("name") or "").strip()
        secret_name = str(entry.get("secret_name") or entry.get("secret") or "").strip()
        secret_key = str(entry.get("secret_key") or entry.get("key") or "").strip()
        if not env_name or not secret_name or not secret_key:
            ctx.diagnostics.append(
                item(
                    "error",
                    "secret_refs entries require env, secret_name, and secret_key.",
                    "manifest.secret_ref_incomplete",
                    source="manifest",
                    path="config.secret_refs",
                )
            )
            continue
        refs.append(
            {
                "name": env_name,
                "valueFrom": {"secretKeyRef": {"name": secret_name, "key": secret_key}},
            }
        )
    return refs


def config_map_data(ctx: ManifestContext) -> dict[str, str]:
    raw = ctx.config.get("config_map") or ctx.config.get("config")
    if not isinstance(raw, Mapping):
        return {}
    data: dict[str, str] = {}
    for key, value in raw.items():
        text_key = str(key)
        if likely_secret_key(text_key):
            ctx.diagnostics.append(
                item(
                    "error",
                    "Secret-like config keys must use secret_refs and are not emitted into ConfigMap data.",
                    "manifest.secret_like_config_key",
                    source="manifest",
                    path="config.config_map",
                    action="approval_required",
                )
            )
            continue
        if isinstance(value, str | int | float | bool):
            data[text_key] = str(value)
        else:
            data[text_key] = yaml.safe_dump(value, sort_keys=True).strip()
    return data


def generated_manifest_path(ctx: ManifestContext) -> str:
    explicit = str(ctx.config.get("generated_manifest_path") or "").strip()
    raw = explicit or str(
        ctx.config.get("manifest_path") or ctx.application.get("manifest_path") or ""
    )
    if unsafe_manifest_path(raw):
        ctx.diagnostics.append(
            item(
                "error",
                "manifest_path must be a repository-relative YAML path without absolute, dot, or parent segments.",
                "manifest.path_unsafe",
                source="manifest",
                path="config.generated_manifest_path" if explicit else "config.manifest_path",
            )
        )
    path = normalize_manifest_path(raw, ctx.name)
    if ctx.source_type in {"helm", "kustomize"} and not explicit:
        base = path.rsplit("/", 1)[0] if "/" in path else "deploy"
        path = normalize_manifest_path(f"{base}/generated/{ctx.name}.generated.yaml", ctx.name)
        ctx.generated_path_warning = (
            "Helm/Kustomize sources should use generated_manifest_path or include this "
            "standalone generated file from the chart/overlay intentionally."
        )
    return path


def unsafe_manifest_path(raw: str) -> bool:
    value = str(raw or "").strip()
    if not value:
        return False
    if value.startswith("/") or "\\" in value:
        return True
    return any(part in {".", ".."} for part in value.split("/"))


def normalize_manifest_path(raw: str, name: str) -> str:
    value = (raw or f"deploy/{name}.yaml").replace("\\", "/").strip()
    value = value.lstrip("/")
    parts = [part for part in value.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        return f"deploy/{name}.yaml"
    normalized = "/".join(parts)
    if normalized.endswith("/"):
        return f"{normalized}{name}.generated.yaml"
    if not normalized.endswith(MANIFEST_EXTENSIONS):
        return f"{normalized.rstrip('/')}/{name}.generated.yaml"
    return normalized


def service_name(ctx: ManifestContext) -> str:
    return ctx.slug_value(str(ctx.config.get("service_name") or f"{ctx.name}-svc"))


def config_map_name(ctx: ManifestContext) -> str:
    return ctx.slug_value(str(ctx.config.get("config_map_name") or f"{ctx.name}-config"))


def autoscaling_enabled(ctx: ManifestContext) -> bool:
    return bool_field(ctx.config.get("autoscaling_enabled"), False) or any(
        key in ctx.config for key in ("min_replicas", "max_replicas", "target_cpu_utilization")
    )


def pdb_enabled(ctx: ManifestContext) -> bool:
    return bool_field(ctx.config.get("pdb_enabled"), ctx.replicas > 1)


def required_image(raw: Any, diagnostics: list[DiagnosticItem]) -> str:
    image = str(raw or "").strip()
    if image:
        return image
    diagnostics.append(
        item(
            "error",
            "image is required before this generated manifest can be used.",
            "manifest.image_required",
            source="manifest",
            path="config.image",
        )
    )
    return REPLACE_ME_IMAGE


def likely_secret_key(value: str) -> bool:
    lowered = value.lower()
    return any(hint in lowered for hint in SECRET_KEY_HINTS)


def resource_summaries(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for resource in resources:
        metadata_value = mapping_value(resource.get("metadata"))
        summaries.append(
            {
                "api_version": str(resource.get("apiVersion") or ""),
                "kind": str(resource.get("kind") or ""),
                "namespace": str(metadata_value.get("namespace") or ""),
                "name": str(metadata_value.get("name") or ""),
            }
        )
    return summaries


def dump_yaml_documents(resources: list[dict[str, Any]]) -> str:
    return "---\n".join(
        yaml.safe_dump(resource, sort_keys=False, default_flow_style=False)
        for resource in resources
    )


def manifest_response(
    manifest: str,
    files: list[dict[str, Any]],
    resources: list[dict[str, Any]],
    diagnostics: list[DiagnosticItem],
    warnings: list[str],
    summary: str,
) -> dict[str, Any]:
    return {
        "manifest": manifest,
        "files": files,
        "resources": resources,
        "resource_count": len(resources),
        "diagnostics": diagnostics,
        "warnings": warnings,
        "summary": summary,
    }


def mapping_value(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def int_like(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value)
    return None


def bool_field(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return default
