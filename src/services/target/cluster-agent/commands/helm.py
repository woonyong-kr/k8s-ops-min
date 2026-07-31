from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from domains.catalog.install import (
    CatalogHelmInstallPayload,
    CatalogInstallValidationError,
    CatalogRecipeUnsupported,
    ServerHelmRecipe,
    server_helm_recipe,
    validate_catalog_values,
    validate_install_names,
)
from domains.helm.source_provider import compare_helm_chart_versions
from domains.release_flow.redaction import (
    REDACTED_VALUE,
    is_sensitive_key,
    redact_release_value,
)
from packages.config.constants import Sandbox
from packages.config.control import control_namespace_allowed
from packages.config.helm import HelmArtifactLimits, helm_artifact_limits
from packages.contracts.helm import (
    HelmArtifactCommandPayload,
    HelmArtifactResult,
    HelmHookDiffItem,
    HelmHooksDiff,
    HelmReleaseGuard,
    HelmReleaseOperationCommandPayload,
    HelmRenderedResourceChange,
    HelmRenderedResourceRef,
    HelmResourceFieldChange,
    HelmResourcesDiff,
    HelmValuesPreviewCommandPayload,
    HelmValuesPreviewResources,
    HelmValuesPreviewResult,
)
from packages.security.log_lines import redact_log_line

HELM_OPERATION_TIMEOUT_SECONDS = 300
HELM_SUBPROCESS_TIMEOUT_SECONDS = 330
HELM_STATUS_MAX_BYTES = 1024 * 1024
HELM_ENV_ALLOWLIST = (
    "PATH",
    "LANG",
    "LC_ALL",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "KUBERNETES_SERVICE_HOST",
    "KUBERNETES_SERVICE_PORT",
    "KUBERNETES_SERVICE_PORT_HTTPS",
)
RunCommand = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class HelmRunResult:
    succeeded: bool
    error_code: str = ""
    returncode: int | None = None


@dataclass(frozen=True)
class HelmArtifactRunResult:
    succeeded: bool
    artifact: HelmArtifactResult | None = None
    error_code: str = ""
    returncode: int | None = None


@dataclass(frozen=True)
class HelmValuesPreviewRunResult:
    succeeded: bool
    preview: HelmValuesPreviewResult | None = None
    error_code: str = ""
    returncode: int | None = None


@dataclass(frozen=True)
class _HookSnapshot:
    item: HelmHookDiffItem
    manifest_digest: str


@dataclass(frozen=True)
class _RenderedResource:
    ref: HelmRenderedResourceRef
    document: dict[str, Any]


_YAML_DOCUMENT_SEPARATOR = re.compile(r"(?m)^---[ \t]*(?:#.*)?$")
_MISSING = object()
_MAX_DIFF_VALUE_LENGTH = 1024


def nested_helm_values(values: dict[str, Any]) -> dict[str, Any]:
    nested: dict[str, Any] = {}
    for name, value in values.items():
        parts = name.split(".")
        if any(not part for part in parts):
            raise CatalogInstallValidationError("catalog value path is invalid")
        current = nested
        for part in parts[:-1]:
            existing = current.setdefault(part, {})
            if not isinstance(existing, dict):
                raise CatalogInstallValidationError("catalog value paths conflict")
            current = existing
        leaf = parts[-1]
        if leaf in current:
            raise CatalogInstallValidationError("catalog value paths conflict")
        current[leaf] = value
    return nested


def merge_helm_values(base: dict[str, Any], enforced: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for name, value in enforced.items():
        existing = merged.get(name)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[name] = merge_helm_values(existing, value)
        else:
            merged[name] = value
    return merged


def helm_subprocess_env(runtime_dir: Path) -> dict[str, str]:
    child_env = {name: os.environ[name] for name in HELM_ENV_ALLOWLIST if name in os.environ}
    child_env.update(
        {
            "HELM_CACHE_HOME": str(runtime_dir / "cache"),
            "HELM_CONFIG_HOME": str(runtime_dir / "config"),
            "HELM_DATA_HOME": str(runtime_dir / "data"),
        }
    )
    return child_env


def run_catalog_helm_install(
    payload: CatalogHelmInstallPayload,
    *,
    helm_binary: str | None = None,
    run: RunCommand = subprocess.run,
) -> HelmRunResult:
    executable = helm_binary or shutil.which("helm")
    if not executable:
        return HelmRunResult(False, "helm_not_available")

    try:
        guard = (
            HelmReleaseGuard.model_validate(payload.upgrade_guard.model_dump(mode="json"))
            if payload.upgrade_guard is not None
            else None
        )
        recipe, values = _catalog_helm_candidate(
            catalog_item_id=payload.catalog_item_id,
            catalog_version=payload.catalog_version,
            namespace=payload.namespace,
            application_name=payload.application_name,
            release_name=payload.release_name,
            submitted_values=payload.values,
            guard=guard,
        )
    except CatalogRecipeUnsupported:
        return HelmRunResult(False, "catalog_recipe_unsupported")
    except CatalogInstallValidationError:
        return HelmRunResult(False, "catalog_install_validation_error")

    with tempfile.TemporaryDirectory(prefix="catalog-helm-") as tmp:
        runtime_dir = Path(tmp)
        runtime_dir.chmod(0o700)
        values_path = runtime_dir / "values.yaml"
        values_path.write_text(
            yaml.safe_dump(values, sort_keys=True, allow_unicode=False),
            encoding="utf-8",
        )
        values_path.chmod(0o600)
        if payload.upgrade_guard is not None:
            guard_result = _validate_live_helm_status(
                executable,
                payload,
                run=run,
                env=helm_subprocess_env(runtime_dir),
            )
            if guard_result is not None:
                return guard_result
        args = [executable, "upgrade"]
        if payload.upgrade_guard is None:
            args.append("--install")
        args.extend(
            [
                payload.release_name,
                recipe.digest_reference,
                "--version",
                recipe.chart_version,
                "--namespace",
                payload.namespace,
                "--values",
                str(values_path),
                "--wait",
                "--atomic",
                "--timeout",
                f"{HELM_OPERATION_TIMEOUT_SECONDS}s",
            ]
        )
        try:
            completed = run(
                args,
                check=False,
                capture_output=True,
                text=True,
                timeout=HELM_SUBPROCESS_TIMEOUT_SECONDS,
                shell=False,
                env=helm_subprocess_env(runtime_dir),
            )
        except subprocess.TimeoutExpired:
            return HelmRunResult(False, "helm_timeout")
        except (FileNotFoundError, PermissionError):
            return HelmRunResult(False, "helm_not_available")
        except OSError:
            return HelmRunResult(False, "helm_execution_error")

    if completed.returncode != 0:
        return HelmRunResult(False, "helm_exit_nonzero", completed.returncode)
    return HelmRunResult(True, returncode=completed.returncode)


def _catalog_helm_candidate(
    *,
    catalog_item_id: str,
    catalog_version: str,
    namespace: str,
    application_name: str,
    release_name: str,
    submitted_values: Mapping[str, Any],
    guard: HelmReleaseGuard | None,
) -> tuple[ServerHelmRecipe, dict[str, Any]]:
    """Resolve and validate the one server-owned chart candidate used by preview and apply."""

    recipe = server_helm_recipe(catalog_item_id, catalog_version)
    validate_install_names(
        application_name=application_name,
        namespace=namespace,
        release_name=release_name,
    )
    if namespace != Sandbox.NAMESPACE or not control_namespace_allowed(namespace):
        raise CatalogInstallValidationError(
            "catalog Helm execution is limited to the sandbox control namespace"
        )
    if guard is not None:
        guard.validate_target(namespace=namespace, release_name=release_name)
        if (
            recipe.chart_name != guard.chart_name
            or compare_helm_chart_versions(recipe.chart_version, guard.chart_version) <= 0
        ):
            raise CatalogInstallValidationError(
                "catalog recipe does not upgrade the guarded Helm chart"
            )
    user_values = nested_helm_values(
        validate_catalog_values(recipe.values_schema, dict(submitted_values))
    )
    fixed_values = nested_helm_values(dict(recipe.fixed_values))
    return recipe, merge_helm_values(user_values, fixed_values)


def run_helm_values_preview(
    payload: HelmValuesPreviewCommandPayload,
    *,
    helm_binary: str | None = None,
    run: RunCommand = subprocess.run,
    limits: HelmArtifactLimits | None = None,
) -> HelmValuesPreviewRunResult:
    """Render one digest-pinned candidate and compare it with the guarded live revision."""

    executable = helm_binary or shutil.which("helm")
    if not executable:
        return HelmValuesPreviewRunResult(False, error_code="helm_not_available")
    effective_limits = limits or helm_artifact_limits()
    try:
        recipe, values = _catalog_helm_candidate(
            catalog_item_id=payload.catalog_item_id,
            catalog_version=payload.catalog_version,
            namespace=payload.namespace,
            application_name=payload.release_name,
            release_name=payload.release_name,
            submitted_values=payload.values,
            guard=payload.guard,
        )
    except CatalogRecipeUnsupported:
        return HelmValuesPreviewRunResult(
            False,
            error_code="catalog_recipe_unsupported",
        )
    except (CatalogInstallValidationError, ValueError):
        return HelmValuesPreviewRunResult(
            False,
            error_code="helm_values_preview_validation_error",
        )

    with tempfile.TemporaryDirectory(prefix="helm-values-preview-") as tmp:
        runtime_dir = Path(tmp)
        runtime_dir.chmod(0o700)
        values_path = runtime_dir / "values.yaml"
        values_path.write_text(
            yaml.safe_dump(values, sort_keys=True, allow_unicode=False),
            encoding="utf-8",
        )
        values_path.chmod(0o600)
        env = helm_subprocess_env(runtime_dir)
        guard_result = _validate_release_status(
            executable,
            release_name=payload.release_name,
            namespace=payload.namespace,
            guard=payload.guard,
            run=run,
            env=env,
        )
        if guard_result is not None:
            return HelmValuesPreviewRunResult(
                False,
                error_code=guard_result.error_code,
                returncode=guard_result.returncode,
            )
        current = _run_helm_preview_source(
            [
                executable,
                "get",
                "manifest",
                payload.release_name,
                "--namespace",
                payload.namespace,
                "--revision",
                str(payload.guard.expected_revision),
            ],
            run=run,
            env=env,
            limits=effective_limits,
        )
        if isinstance(current, HelmValuesPreviewRunResult):
            return current
        candidate = _run_helm_preview_source(
            [
                executable,
                "template",
                payload.release_name,
                recipe.digest_reference,
                "--version",
                recipe.chart_version,
                "--namespace",
                payload.namespace,
                "--values",
                str(values_path),
                "--is-upgrade",
                "--include-crds",
            ],
            run=run,
            env=env,
            limits=effective_limits,
        )
        if isinstance(candidate, HelmValuesPreviewRunResult):
            return candidate

    current_documents, current_errors = _sanitized_yaml_documents(current)
    candidate_documents, candidate_errors = _sanitized_yaml_documents(candidate)
    resources = _values_preview_resources(
        _rendered_resources(current_documents),
        _rendered_resources(candidate_documents),
        parse_error_count=current_errors + candidate_errors,
    )
    bounded = _bounded_structured_projection(
        "resources_diff",
        resources.model_dump(mode="json"),
        effective_limits.output_max_bytes,
    )
    if bounded is None:
        return HelmValuesPreviewRunResult(
            False,
            error_code="helm_values_preview_projection_too_large",
        )
    projection, encoded, truncated = bounded
    preview = HelmValuesPreviewResult(
        namespace=payload.namespace,
        release_name=payload.release_name,
        expected_revision=payload.guard.expected_revision,
        catalog_item_id=recipe.item_id,
        catalog_version=recipe.version,
        chart_name=recipe.chart_name,
        chart_version=recipe.chart_version,
        resources=HelmValuesPreviewResources.model_validate(projection),
        projection_sha256=hashlib.sha256(encoded).hexdigest(),
        projection_bytes=len(encoded),
        source_bytes=len(current.encode("utf-8")) + len(candidate.encode("utf-8")),
        redaction_applied=True,
        truncated=truncated,
    )
    return HelmValuesPreviewRunResult(True, preview=preview)


def _run_helm_preview_source(
    args: list[str],
    *,
    run: RunCommand,
    env: dict[str, str],
    limits: HelmArtifactLimits,
) -> str | HelmValuesPreviewRunResult:
    try:
        completed = run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=limits.timeout_seconds,
            shell=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return HelmValuesPreviewRunResult(False, error_code="helm_values_preview_timeout")
    except (FileNotFoundError, PermissionError):
        return HelmValuesPreviewRunResult(False, error_code="helm_not_available")
    except OSError:
        return HelmValuesPreviewRunResult(
            False,
            error_code="helm_values_preview_execution_error",
        )
    if completed.returncode != 0:
        return HelmValuesPreviewRunResult(
            False,
            error_code="helm_values_preview_exit_nonzero",
            returncode=completed.returncode,
        )
    output = completed.stdout or ""
    if len(output.encode("utf-8")) > limits.source_max_bytes:
        return HelmValuesPreviewRunResult(
            False,
            error_code="helm_values_preview_source_too_large",
        )
    return output


def validate_catalog_helm_upgrade_secret(
    observed: Mapping[str, Any],
    payload: CatalogHelmInstallPayload,
) -> None:
    guard = payload.upgrade_guard
    if guard is None:
        return
    validate_helm_release_secret(
        observed,
        namespace=payload.namespace,
        release_name=payload.release_name,
        guard=HelmReleaseGuard.model_validate(guard.model_dump(mode="json")),
    )


def validate_helm_release_operation_secret(
    observed: Mapping[str, Any],
    payload: HelmReleaseOperationCommandPayload,
) -> None:
    validate_helm_release_secret(
        observed,
        namespace=payload.namespace,
        release_name=payload.release_name,
        guard=payload.guard,
    )


def validate_helm_release_secret(
    observed: Mapping[str, Any],
    *,
    namespace: str,
    release_name: str,
    guard: HelmReleaseGuard,
) -> None:
    metadata_value = observed.get("metadata")
    metadata = metadata_value if isinstance(metadata_value, Mapping) else {}
    labels_value = metadata.get("labels")
    labels = labels_value if isinstance(labels_value, Mapping) else {}
    if (
        str(observed.get("apiVersion") or "") != "v1"
        or str(observed.get("kind") or "").casefold() != "secret"
        or str(metadata.get("namespace") or "") != namespace
        or str(metadata.get("name") or "") != guard.storage.name
        or str(metadata.get("uid") or "") != guard.storage.uid
        or str(metadata.get("resourceVersion") or "") != guard.storage_resource_version
        or str(labels.get("owner") or "").casefold() != "helm"
        or str(labels.get("name") or "") != release_name
        or str(labels.get("version") or "") != str(guard.expected_revision)
    ):
        raise ValueError("Helm release storage identity is stale")


def run_helm_release_operation(
    payload: HelmReleaseOperationCommandPayload,
    *,
    helm_binary: str | None = None,
    run: RunCommand = subprocess.run,
) -> HelmRunResult:
    """Run one guarded Helm mutation with an absolute subprocess deadline."""

    executable = helm_binary or shutil.which("helm")
    if not executable:
        return HelmRunResult(False, "helm_not_available")
    try:
        payload.guard.validate_target(
            namespace=payload.namespace,
            release_name=payload.release_name,
        )
        if payload.namespace != Sandbox.NAMESPACE or not control_namespace_allowed(
            payload.namespace
        ):
            raise ValueError("Helm release operation namespace is not allowed")
    except ValueError:
        return HelmRunResult(False, "helm_release_operation_validation_error")

    with tempfile.TemporaryDirectory(prefix="helm-release-operation-") as tmp:
        runtime_dir = Path(tmp)
        runtime_dir.chmod(0o700)
        env = helm_subprocess_env(runtime_dir)
        guard_result = _validate_release_status(
            executable,
            release_name=payload.release_name,
            namespace=payload.namespace,
            guard=payload.guard,
            run=run,
            env=env,
        )
        if guard_result is not None:
            return guard_result
        if payload.operation == "rollback":
            args = [
                executable,
                "rollback",
                payload.release_name,
                str(payload.rollback_revision),
                "--namespace",
                payload.namespace,
                "--wait",
                "--cleanup-on-fail",
                "--timeout",
                f"{HELM_OPERATION_TIMEOUT_SECONDS}s",
            ]
        else:
            args = [
                executable,
                "uninstall",
                payload.release_name,
                "--namespace",
                payload.namespace,
                "--wait",
                "--timeout",
                f"{HELM_OPERATION_TIMEOUT_SECONDS}s",
            ]
        try:
            completed = run(
                args,
                check=False,
                capture_output=True,
                text=True,
                timeout=HELM_SUBPROCESS_TIMEOUT_SECONDS,
                shell=False,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return HelmRunResult(False, "helm_timeout")
        except (FileNotFoundError, PermissionError):
            return HelmRunResult(False, "helm_not_available")
        except OSError:
            return HelmRunResult(False, "helm_execution_error")
    if completed.returncode != 0:
        return HelmRunResult(False, "helm_exit_nonzero", completed.returncode)
    return HelmRunResult(True, returncode=completed.returncode)


def _validate_live_helm_status(
    executable: str,
    payload: CatalogHelmInstallPayload,
    *,
    run: RunCommand,
    env: dict[str, str],
) -> HelmRunResult | None:
    guard = payload.upgrade_guard
    if guard is None:
        return None
    return _validate_release_status(
        executable,
        release_name=payload.release_name,
        namespace=payload.namespace,
        guard=HelmReleaseGuard.model_validate(guard.model_dump(mode="json")),
        run=run,
        env=env,
    )


def _validate_release_status(
    executable: str,
    *,
    release_name: str,
    namespace: str,
    guard: HelmReleaseGuard,
    run: RunCommand,
    env: dict[str, str],
) -> HelmRunResult | None:
    args = [
        executable,
        "status",
        release_name,
        "--namespace",
        namespace,
        "--output",
        "json",
    ]
    try:
        completed = run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=HELM_SUBPROCESS_TIMEOUT_SECONDS,
            shell=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return HelmRunResult(False, "helm_release_guard_timeout")
    except (FileNotFoundError, PermissionError):
        return HelmRunResult(False, "helm_not_available")
    except OSError:
        return HelmRunResult(False, "helm_release_guard_error")
    if completed.returncode != 0:
        return HelmRunResult(False, "helm_release_guard_unavailable", completed.returncode)
    encoded = completed.stdout.encode("utf-8", errors="replace")
    if len(encoded) > HELM_STATUS_MAX_BYTES:
        return HelmRunResult(False, "helm_release_guard_invalid")
    try:
        status = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        return HelmRunResult(False, "helm_release_guard_invalid")
    chart_value = status.get("chart") if isinstance(status, Mapping) else None
    chart = chart_value if isinstance(chart_value, Mapping) else {}
    metadata_value = chart.get("metadata")
    chart_metadata = metadata_value if isinstance(metadata_value, Mapping) else {}
    if (
        str(status.get("name") or "") != release_name
        or str(status.get("namespace") or "") != namespace
        or str(status.get("version") or "") != str(guard.expected_revision)
        or str(chart_metadata.get("name") or "") != guard.chart_name
        or not str(chart_metadata.get("version") or "")
        or compare_helm_chart_versions(
            str(chart_metadata.get("version") or ""),
            guard.chart_version,
        )
        != 0
    ):
        return HelmRunResult(False, "helm_release_guard_stale")
    return None


def run_helm_artifact_query(
    payload: HelmArtifactCommandPayload,
    *,
    helm_binary: str | None = None,
    run: RunCommand = subprocess.run,
    limits: HelmArtifactLimits | None = None,
) -> HelmArtifactRunResult:
    """Read revision artifacts locally and return only a bounded redacted projection."""

    executable = helm_binary or shutil.which("helm")
    if not executable:
        return HelmArtifactRunResult(False, error_code="helm_not_available")
    effective_limits = limits or helm_artifact_limits()
    revisions = [payload.revision]
    if payload.comparison_revision is not None:
        revisions.append(payload.comparison_revision)
    raw_artifacts: list[str] = []
    source_bytes = 0
    with tempfile.TemporaryDirectory(prefix="helm-artifact-") as tmp:
        runtime_dir = Path(tmp)
        runtime_dir.chmod(0o700)
        for revision in revisions:
            args = _helm_artifact_args(executable, payload, revision)
            try:
                completed = run(
                    args,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=effective_limits.timeout_seconds,
                    shell=False,
                    env=helm_subprocess_env(runtime_dir),
                )
            except subprocess.TimeoutExpired:
                return HelmArtifactRunResult(False, error_code="helm_artifact_timeout")
            except (FileNotFoundError, PermissionError):
                return HelmArtifactRunResult(False, error_code="helm_not_available")
            except OSError:
                return HelmArtifactRunResult(False, error_code="helm_artifact_execution_error")
            if completed.returncode != 0:
                return HelmArtifactRunResult(
                    False,
                    error_code="helm_artifact_exit_nonzero",
                    returncode=completed.returncode,
                )
            raw = completed.stdout or ""
            raw_bytes = len(raw.encode("utf-8"))
            source_bytes += raw_bytes
            if raw_bytes > effective_limits.source_max_bytes:
                return HelmArtifactRunResult(False, error_code="helm_artifact_source_too_large")
            raw_artifacts.append(raw)

    if payload.artifact == "hooks_diff":
        return _helm_hooks_diff_result(
            payload,
            raw_artifacts,
            source_bytes=source_bytes,
            output_max_bytes=effective_limits.output_max_bytes,
        )
    if payload.artifact == "resources_diff":
        return _helm_resources_diff_result(
            payload,
            raw_artifacts,
            source_bytes=source_bytes,
            output_max_bytes=effective_limits.output_max_bytes,
        )

    try:
        sanitized = [_sanitize_helm_artifact(payload.artifact, raw) for raw in raw_artifacts]
    except (TypeError, ValueError, yaml.YAMLError):
        return HelmArtifactRunResult(False, error_code="helm_artifact_invalid_yaml")
    content = (
        _unified_artifact_diff(
            sanitized[0],
            sanitized[1],
            payload.revision,
            payload.comparison_revision or payload.revision,
        )
        if payload.artifact.endswith("_diff")
        else sanitized[0]
    )
    bounded, truncated = _bounded_utf8(content, effective_limits.output_max_bytes)
    encoded = bounded.encode("utf-8")
    artifact = HelmArtifactResult(
        artifact=payload.artifact,
        format="unified_diff" if payload.artifact.endswith("_diff") else "yaml",
        namespace=payload.namespace,
        release_name=payload.release_name,
        revision=payload.revision,
        comparison_revision=payload.comparison_revision,
        all_values=payload.all_values,
        content=bounded,
        content_sha256=hashlib.sha256(encoded).hexdigest(),
        content_bytes=len(encoded),
        source_bytes=source_bytes,
        redaction_applied=True,
        truncated=truncated,
    )
    return HelmArtifactRunResult(True, artifact=artifact)


def _helm_artifact_args(
    executable: str,
    payload: HelmArtifactCommandPayload,
    revision: int,
) -> list[str]:
    if payload.artifact in {"manifest", "manifest_diff", "resources_diff"}:
        target = "manifest"
    elif payload.artifact == "notes_diff":
        target = "notes"
    elif payload.artifact == "hooks_diff":
        target = "hooks"
    else:
        target = "values"
    args = [
        executable,
        "get",
        target,
        payload.release_name,
        "--namespace",
        payload.namespace,
        "--revision",
        str(revision),
    ]
    if target == "values":
        args.extend(("--output", "yaml"))
        if payload.all_values:
            args.append("--all")
    return args


def _sanitize_helm_artifact(artifact: str, raw: str) -> str:
    if artifact == "notes_diff":
        return "".join(f"{redact_log_line(line)}\n" for line in raw.splitlines()).rstrip("\n")
    documents = list(yaml.safe_load_all(raw))
    sanitized = [
        _redact_manifest_document(document)
        if artifact in {"manifest", "manifest_diff"}
        else redact_release_value(document)
        for document in documents
        if document is not None
    ]
    if artifact in {"manifest", "manifest_diff"}:
        return yaml.safe_dump_all(
            sanitized,
            allow_unicode=True,
            explicit_start=True,
            sort_keys=True,
        )
    value = sanitized[0] if len(sanitized) == 1 else sanitized
    return yaml.safe_dump(value, allow_unicode=True, sort_keys=True)


def _redact_manifest_document(document: object) -> object:
    if not isinstance(document, dict):
        return _redact_manifest_value(document)
    copied = dict(document)
    if str(copied.get("kind") or "").casefold() == "secret":
        for field in ("data", "stringData"):
            values = copied.get(field)
            if isinstance(values, dict):
                copied[field] = {str(key): REDACTED_VALUE for key in values}
    return _redact_manifest_value(copied)


def _redact_manifest_value(value: object) -> object:
    if isinstance(value, dict):
        redacted: dict[str, object] = {}
        named_secret = is_sensitive_key(str(value.get("name") or ""))
        for key, item in value.items():
            key_text = str(key)
            if is_sensitive_key(key_text) or (named_secret and key_text == "value"):
                redacted[key_text] = REDACTED_VALUE
            else:
                redacted[key_text] = _redact_manifest_value(item)
        return redacted
    if isinstance(value, list):
        return [_redact_manifest_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_manifest_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _unified_artifact_diff(left: str, right: str, left_revision: int, right_revision: int) -> str:
    return "".join(
        difflib.unified_diff(
            left.splitlines(keepends=True),
            right.splitlines(keepends=True),
            fromfile=f"revision-{left_revision}.yaml",
            tofile=f"revision-{right_revision}.yaml",
        )
    )


def _helm_hooks_diff_result(
    payload: HelmArtifactCommandPayload,
    raw_artifacts: Sequence[str],
    *,
    source_bytes: int,
    output_max_bytes: int,
) -> HelmArtifactRunResult:
    if len(raw_artifacts) != 2 or payload.comparison_revision is None:
        return HelmArtifactRunResult(False, error_code="helm_artifact_invalid_revisions")
    left_documents, left_errors = _sanitized_yaml_documents(raw_artifacts[0])
    right_documents, right_errors = _sanitized_yaml_documents(raw_artifacts[1])
    left = _hook_snapshots(left_documents, payload.namespace)
    right = _hook_snapshots(right_documents, payload.namespace)
    hook_diff = _diff_hooks(
        left,
        right,
        revision1=payload.revision,
        revision2=payload.comparison_revision,
        parse_error_count=left_errors + right_errors,
    )
    bounded = _bounded_structured_projection(
        "hooks_diff",
        hook_diff.model_dump(mode="json"),
        output_max_bytes,
    )
    if bounded is None:
        return HelmArtifactRunResult(False, error_code="helm_artifact_projection_too_large")
    projection, encoded, truncated = bounded
    artifact = HelmArtifactResult(
        artifact="hooks_diff",
        format="structured",
        namespace=payload.namespace,
        release_name=payload.release_name,
        revision=payload.revision,
        comparison_revision=payload.comparison_revision,
        hooks_diff=HelmHooksDiff.model_validate(projection),
        projection_sha256=hashlib.sha256(encoded).hexdigest(),
        projection_bytes=len(encoded),
        source_bytes=source_bytes,
        redaction_applied=True,
        truncated=truncated,
    )
    return HelmArtifactRunResult(True, artifact=artifact)


def _helm_resources_diff_result(
    payload: HelmArtifactCommandPayload,
    raw_artifacts: Sequence[str],
    *,
    source_bytes: int,
    output_max_bytes: int,
) -> HelmArtifactRunResult:
    if len(raw_artifacts) != 2 or payload.comparison_revision is None:
        return HelmArtifactRunResult(False, error_code="helm_artifact_invalid_revisions")
    left_documents, left_errors = _sanitized_yaml_documents(raw_artifacts[0])
    right_documents, right_errors = _sanitized_yaml_documents(raw_artifacts[1])
    resource_diff = _diff_rendered_resources(
        _rendered_resources(left_documents),
        _rendered_resources(right_documents),
        revision1=payload.revision,
        revision2=payload.comparison_revision,
        parse_error_count=left_errors + right_errors,
    )
    bounded = _bounded_structured_projection(
        "resources_diff",
        resource_diff.model_dump(mode="json"),
        output_max_bytes,
    )
    if bounded is None:
        return HelmArtifactRunResult(False, error_code="helm_artifact_projection_too_large")
    projection, encoded, truncated = bounded
    artifact = HelmArtifactResult(
        artifact="resources_diff",
        format="structured",
        namespace=payload.namespace,
        release_name=payload.release_name,
        revision=payload.revision,
        comparison_revision=payload.comparison_revision,
        resources_diff=HelmResourcesDiff.model_validate(projection),
        projection_sha256=hashlib.sha256(encoded).hexdigest(),
        projection_bytes=len(encoded),
        source_bytes=source_bytes,
        redaction_applied=True,
        truncated=truncated,
    )
    return HelmArtifactRunResult(True, artifact=artifact)


def _sanitized_yaml_documents(raw: str) -> tuple[list[dict[str, Any]], int]:
    documents: list[dict[str, Any]] = []
    parse_error_count = 0
    for source in _YAML_DOCUMENT_SEPARATOR.split(raw):
        if not source.strip():
            continue
        try:
            loaded = yaml.safe_load(source)
        except yaml.YAMLError:
            parse_error_count += 1
            continue
        if loaded is None:
            continue
        if not isinstance(loaded, dict):
            parse_error_count += 1
            continue
        redacted = _redact_manifest_document(loaded)
        if not isinstance(redacted, dict):
            parse_error_count += 1
            continue
        documents.append(redacted)
    return documents, parse_error_count


def _hook_snapshots(
    documents: Sequence[Mapping[str, Any]],
    default_namespace: str,
) -> tuple[_HookSnapshot, ...]:
    snapshots: list[_HookSnapshot] = []
    for document in documents:
        metadata = _mapping(document.get("metadata"))
        annotations = _mapping(metadata.get("annotations"))
        events = _csv_values(annotations.get("helm.sh/hook"))
        name = _clean_text(metadata.get("name"))
        kind = _clean_text(document.get("kind"))
        if not events or not name or not kind:
            continue
        item = HelmHookDiffItem(
            api_version=_clean_text(document.get("apiVersion")),
            kind=kind,
            name=name,
            namespace=_clean_text(metadata.get("namespace")) or default_namespace,
            events=events,
            weight=_safe_int(annotations.get("helm.sh/hook-weight")),
            delete_policies=_csv_values(annotations.get("helm.sh/hook-delete-policy")),
            output_log_policies=_csv_values(annotations.get("helm.sh/hook-output-log-policy")),
        )
        snapshots.append(
            _HookSnapshot(
                item=item,
                manifest_digest=hashlib.sha256(_canonical_json(document)).hexdigest(),
            )
        )
    return tuple(sorted(snapshots, key=lambda item: _hook_key(item.item)))


def _diff_hooks(
    left: Sequence[_HookSnapshot],
    right: Sequence[_HookSnapshot],
    *,
    revision1: int,
    revision2: int,
    parse_error_count: int,
) -> HelmHooksDiff:
    left_by_key = {_hook_key(snapshot.item): snapshot for snapshot in left}
    right_by_key = {_hook_key(snapshot.item): snapshot for snapshot in right}
    added: list[HelmHookDiffItem] = []
    removed: list[HelmHookDiffItem] = []
    modified: list[HelmHookDiffItem] = []
    unchanged: list[HelmHookDiffItem] = []
    for key in sorted(set(left_by_key) | set(right_by_key)):
        previous = left_by_key.get(key)
        current = right_by_key.get(key)
        if previous is None and current is not None:
            added.append(current.item)
        elif current is None and previous is not None:
            removed.append(previous.item)
        elif previous is not None and current is not None:
            if previous == current:
                unchanged.append(current.item)
            else:
                modified.append(
                    current.item.model_copy(
                        update={
                            "manifest_changed": (
                                previous.manifest_digest != current.manifest_digest
                            )
                        }
                    )
                )
    return HelmHooksDiff(
        revision1=revision1,
        revision2=revision2,
        added=tuple(added),
        removed=tuple(removed),
        modified=tuple(modified),
        unchanged=tuple(unchanged),
        parse_error_count=parse_error_count,
    )


def _hook_key(item: HelmHookDiffItem) -> tuple[str, str, str, str]:
    return (item.api_version, item.kind, item.namespace, item.name)


def _rendered_resources(
    documents: Sequence[Mapping[str, Any]],
) -> tuple[_RenderedResource, ...]:
    resources: list[_RenderedResource] = []
    for source in documents:
        document = _normalized_rendered_resource(source)
        metadata = _mapping(document.get("metadata"))
        name = _clean_text(metadata.get("name"))
        kind = _clean_text(document.get("kind"))
        if not name or not kind:
            continue
        ref = HelmRenderedResourceRef(
            api_version=_clean_text(document.get("apiVersion")),
            kind=kind,
            name=name,
            namespace=_clean_text(metadata.get("namespace")),
        )
        resources.append(_RenderedResource(ref=ref, document=document))
    return tuple(sorted(resources, key=lambda item: _resource_key(item.ref)))


def _normalized_rendered_resource(source: Mapping[str, Any]) -> dict[str, Any]:
    document = json.loads(_canonical_json(source))
    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        return document
    labels = metadata.get("labels")
    if not isinstance(labels, dict):
        return document
    labels.pop("helm.sh/chart", None)
    if not labels:
        metadata.pop("labels", None)
    return document


def _diff_rendered_resources(
    left: Sequence[_RenderedResource],
    right: Sequence[_RenderedResource],
    *,
    revision1: int,
    revision2: int,
    parse_error_count: int,
) -> HelmResourcesDiff:
    added, removed, modified, unchanged = _rendered_resource_changes(left, right)
    return HelmResourcesDiff(
        revision1=revision1,
        revision2=revision2,
        added=added,
        removed=removed,
        modified=modified,
        unchanged=unchanged,
        parse_error_count=parse_error_count,
    )


def _values_preview_resources(
    current: Sequence[_RenderedResource],
    candidate: Sequence[_RenderedResource],
    *,
    parse_error_count: int,
) -> HelmValuesPreviewResources:
    added, removed, modified, unchanged = _rendered_resource_changes(current, candidate)
    return HelmValuesPreviewResources(
        added=added,
        removed=removed,
        modified=modified,
        unchanged=unchanged,
        parse_error_count=parse_error_count,
    )


def _rendered_resource_changes(
    left: Sequence[_RenderedResource],
    right: Sequence[_RenderedResource],
) -> tuple[
    tuple[HelmRenderedResourceRef, ...],
    tuple[HelmRenderedResourceRef, ...],
    tuple[HelmRenderedResourceChange, ...],
    tuple[HelmRenderedResourceRef, ...],
]:
    left_by_key = {_resource_key(resource.ref): resource for resource in left}
    right_by_key = {_resource_key(resource.ref): resource for resource in right}
    added: list[HelmRenderedResourceRef] = []
    removed: list[HelmRenderedResourceRef] = []
    modified: list[HelmRenderedResourceChange] = []
    unchanged: list[HelmRenderedResourceRef] = []
    for key in sorted(set(left_by_key) | set(right_by_key)):
        previous = left_by_key.get(key)
        current = right_by_key.get(key)
        if previous is None and current is not None:
            added.append(current.ref)
            continue
        if current is None and previous is not None:
            removed.append(previous.ref)
            continue
        if previous is None or current is None:
            continue
        fields = _resource_field_changes(previous.document, current.document)
        if not fields:
            unchanged.append(current.ref)
            continue
        modified.append(
            HelmRenderedResourceChange(
                **current.ref.model_dump(),
                summary=f"{len(fields)} fields changed",
                field_count=len(fields),
                fields=tuple(fields),
            )
        )
    return tuple(added), tuple(removed), tuple(modified), tuple(unchanged)


def _resource_key(item: HelmRenderedResourceRef) -> tuple[str, str, str, str]:
    return (item.api_version, item.kind, item.namespace, item.name)


def _resource_field_changes(
    previous: object,
    current: object,
    path: str = "",
) -> list[HelmResourceFieldChange]:
    if isinstance(previous, Mapping) and isinstance(current, Mapping):
        changes: list[HelmResourceFieldChange] = []
        for key in sorted(set(previous) | set(current), key=str):
            child_path = f"{path}.{key}" if path else str(key)
            changes.extend(
                _resource_field_changes(
                    previous.get(key, _MISSING),
                    current.get(key, _MISSING),
                    child_path,
                )
            )
        return changes
    if (
        isinstance(previous, Sequence)
        and not isinstance(previous, (str, bytes, bytearray))
        and isinstance(current, Sequence)
        and not isinstance(current, (str, bytes, bytearray))
    ):
        changes = []
        for index in range(max(len(previous), len(current))):
            changes.extend(
                _resource_field_changes(
                    previous[index] if index < len(previous) else _MISSING,
                    current[index] if index < len(current) else _MISSING,
                    f"{path}[{index}]",
                )
            )
        return changes
    if previous == current:
        return []
    return [
        HelmResourceFieldChange(
            path=path or "$",
            old_value=_resource_field_value(previous),
            new_value=_resource_field_value(current),
        )
    ]


def _resource_field_value(value: object) -> str | int | bool | None:
    if value is _MISSING or value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return _bounded_text(value, _MAX_DIFF_VALUE_LENGTH)
    if isinstance(value, float):
        return _bounded_text(repr(value), _MAX_DIFF_VALUE_LENGTH)
    return _bounded_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        _MAX_DIFF_VALUE_LENGTH,
    )


def _bounded_structured_projection(
    kind: str,
    projection: dict[str, Any],
    limit: int,
) -> tuple[dict[str, Any], bytes, bool] | None:
    bounded = json.loads(_canonical_json(projection))
    encoded = _canonical_json(bounded)
    if len(encoded) <= limit:
        return bounded, encoded, False
    while len(encoded) > limit:
        changed = (
            _trim_hook_projection(bounded)
            if kind == "hooks_diff"
            else _trim_resource_projection(bounded)
        )
        if not changed:
            return None
        encoded = _canonical_json(bounded)
    return bounded, encoded, True


def _trim_hook_projection(projection: dict[str, Any]) -> bool:
    for key in ("unchanged", "modified", "added", "removed"):
        values = projection.get(key)
        if isinstance(values, list) and values:
            values.pop()
            return True
    return False


def _trim_resource_projection(projection: dict[str, Any]) -> bool:
    unchanged = projection.get("unchanged")
    if isinstance(unchanged, list) and unchanged:
        unchanged.pop()
        return True
    modified = projection.get("modified")
    if isinstance(modified, list):
        for item in reversed(modified):
            if isinstance(item, dict):
                fields = item.get("fields")
                if isinstance(fields, list) and fields:
                    fields.pop()
                    return True
    for key in ("added", "removed", "modified"):
        values = projection.get(key)
        if isinstance(values, list) and values:
            values.pop()
            return True
    return False


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _csv_values(value: object) -> tuple[str, ...]:
    return tuple(sorted({item.strip() for item in str(value or "").split(",") if item.strip()}))


def _safe_int(value: object) -> int:
    try:
        return int(str(value or "0").strip())
    except ValueError:
        return 0


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _bounded_text(value: str, limit: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    return encoded[:limit].decode("utf-8", errors="ignore")


def _bounded_utf8(value: str, limit: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value, False
    suffix = "\n# artifact truncated\n"
    budget = max(0, limit - len(suffix.encode("utf-8")))
    prefix = encoded[:budget].decode("utf-8", errors="ignore")
    bounded = f"{prefix}{suffix}"
    while len(bounded.encode("utf-8")) > limit and prefix:
        prefix = prefix[:-1]
        bounded = f"{prefix}{suffix}"
    return bounded, True
