"""manifest-render-worker — git.changed → manifest 렌더 → manifest.rendered.

우현 원본 GitOpsSyncWorkflow.handle()의 manifest dict 생성, repo_change 저장,
rendered Kubernetes object 생성, MANIFEST_RENDERED 발행 블록에 대응. Git 변경을
Kubernetes manifest로 바꾸는 책임만 분리.
"""

from __future__ import annotations

import base64
import binascii
import json
import subprocess
from collections.abc import AsyncIterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib import error, parse, request

import yaml
from repo_cache import GitRepoCache, GitRepoCacheError, extract_git_archive

from domains.gitops.diffing import extract_declared_field_paths, resource_ref
from domains.gitops.events import (
    GitChangedBody,
    ManifestInvalidBody,
    ManifestRenderedBody,
    RenderedManifest,
    RenderedMetadata,
    RenderedSpec,
)
from domains.gitops.repository_discovery import (
    ManifestRenderValidationError,
    find_kustomization_path,
    kustomize_local_references,
    normalize_kustomize_local_reference,
    parse_kustomization_document,
    repository_directories,
)
from domains.gitops.source_patch import canonical_manifest_digest
from packages.config.constants import Sandbox
from packages.config.settings import env
from packages.contracts.event_bus.bodies import EventBody
from packages.contracts.gitops import (
    DEFAULT_GITHUB_API_BASE,
    DEFAULT_GITHUB_WEB_BASE,
    GITHUB_API_BASE_ENV,
    GITHUB_TOKEN_ENV,
    GITHUB_TOKEN_REF_ENV,
    GITHUB_WEB_BASE_ENV,
    ManifestArtifactStatus,
)
from packages.contracts.security import SecretRef
from packages.contracts.stores import RepoChangeStore
from packages.runtime.app import App, EventContext
from packages.security import SecretNotFound, build_token_vault

app = App("manifest-render-worker")

MANIFEST_KIND = "Deployment"
METADATA_FIELD = "metadata"
SPEC_FIELD = "spec"
TEMPLATE_FIELD = "template"
CONTAINERS_FIELD = "containers"
DEFAULT_NAMESPACED_KINDS = {
    "ConfigMap",
    "CronJob",
    "DaemonSet",
    "Deployment",
    "HorizontalPodAutoscaler",
    "Ingress",
    "Job",
    "Pod",
    "PersistentVolumeClaim",
    "ReplicaSet",
    "Role",
    "RoleBinding",
    "Secret",
    "Service",
    "ServiceAccount",
    "StatefulSet",
}
RENDERER_VERSION = "manifest-render-v2"
SOURCE_TYPE_RAW_YAML = "raw-yaml"
SOURCE_TYPE_RAW_JSON = "raw-json"
SOURCE_TYPE_KUSTOMIZE = "kustomize"
SOURCE_TYPE_HELM = "helm"
SUPPORTED_SOURCE_TYPES = {
    SOURCE_TYPE_RAW_JSON,
    SOURCE_TYPE_RAW_YAML,
    SOURCE_TYPE_KUSTOMIZE,
    SOURCE_TYPE_HELM,
}
KUSTOMIZATION_FILES = ("kustomization.yaml", "kustomization.yml", "Kustomization")
HELM_CHART_FILE = "Chart.yaml"
MANIFEST_EXTENSIONS = (".yaml", ".yml", ".json")
MAX_RENDER_ERROR_LENGTH = 2000
GIT_REPO_PATH_ENV = "GIT_REPO_PATH"
GIT_MANIFEST_PATH_ENV = "GIT_MANIFEST_PATH"
GIT_MANIFEST_SOURCE_TYPE_ENV = "GIT_MANIFEST_SOURCE_TYPE"
GIT_MANIFEST_SOURCE_MODE_ENV = "GIT_MANIFEST_SOURCE_MODE"
GIT_LOCAL_MANIFEST_ENABLED_ENV = "GIT_LOCAL_MANIFEST_ENABLED"
GIT_CHECKOUT_CACHE_ENABLED_ENV = "GIT_CHECKOUT_CACHE_ENABLED"
GIT_CHECKOUT_CACHE_REQUIRED_ENV = "GIT_CHECKOUT_CACHE_REQUIRED"
GIT_CACHE_DIR_ENV = "GIT_CACHE_DIR"
GIT_CACHE_REMOTE_URL_ENV = "GIT_CACHE_REMOTE_URL"
GIT_CACHE_MAX_BYTES_ENV = "GIT_CACHE_MAX_BYTES"
GIT_CACHE_MAX_REPOS_ENV = "GIT_CACHE_MAX_REPOS"
GIT_REMOTE_MANIFEST_ENABLED_ENV = "GIT_REMOTE_MANIFEST_ENABLED"
GIT_REMOTE_MANIFEST_REQUIRED_ENV = "GIT_REMOTE_MANIFEST_REQUIRED"
GITOPS_KUBECTL_BIN_ENV = "GITOPS_KUBECTL_BIN"
GITOPS_HELM_BIN_ENV = "GITOPS_HELM_BIN"
GITOPS_HELM_RELEASE_NAME_ENV = "GITOPS_HELM_RELEASE_NAME"
GITOPS_HELM_NAMESPACE_ENV = "GITOPS_HELM_NAMESPACE"
GITOPS_HELM_VALUES_FILES_ENV = "GITOPS_HELM_VALUES_FILES"
GITOPS_HELM_INCLUDE_CRDS_ENV = "GITOPS_HELM_INCLUDE_CRDS"
GITOPS_HELM_DEPENDENCY_BUILD_ENV = "GITOPS_HELM_DEPENDENCY_BUILD"
GITHUB_MANIFEST_TIMEOUT_SECONDS_ENV = "GITHUB_MANIFEST_TIMEOUT_SECONDS"
GIT_MANIFEST_COMMAND_TIMEOUT_SECONDS_ENV = "GIT_MANIFEST_COMMAND_TIMEOUT_SECONDS"
DEFAULT_GITHUB_MANIFEST_TIMEOUT_SECONDS = "5"
DEFAULT_GIT_MANIFEST_COMMAND_TIMEOUT_SECONDS = "5"
DEFAULT_GIT_CACHE_DIR = "/tmp/gitops-repo-cache"
MAX_REMOTE_RENDER_SOURCE_FILES = 500
MAX_REMOTE_RENDER_SOURCE_BYTES = 5 * 1_048_576
TRUTHY_VALUES = {"1", "true", "yes", "on"}
SOURCE_MODE_AUTO = "auto"
SOURCE_MODE_REMOTE = "remote"
SOURCE_MODE_LOCAL = "local"
# manifest 원천(local clone/파일/GitHub contents) 어디에서도 소스를 못 찾은 경우의 사유.
# 합성 manifest 생성 대신 manifest.invalid 로 정직하게 실패함.
MANIFEST_SOURCE_UNAVAILABLE_REASON = "manifest source unavailable"


class ManifestSourceError(Exception):
    """manifest 소스가 개념상 존재하지만 로드 불가한 경우."""


@dataclass(frozen=True)
class RenderSource:
    source_type: str
    manifest_path: str
    origin: str
    local_path: Path | None = None
    source_text: str | None = None


@dataclass(frozen=True)
class RenderResult:
    rendered_manifests: list[RenderedManifest]
    source_type: str
    source_origin: str
    source_is_file: bool
    source_document_count: int
    source_manifest_sha256: list[str]


@dataclass(frozen=True)
class CachedRenderedManifest:
    rendered: RenderedManifest
    artifact_id: str


def env_truthy(name: str, default: str = "") -> bool:
    return env(name, default).strip().lower() in TRUTHY_VALUES


def manifest_source_mode() -> str:
    mode = env(GIT_MANIFEST_SOURCE_MODE_ENV, SOURCE_MODE_AUTO).strip().lower()
    if mode in {SOURCE_MODE_AUTO, SOURCE_MODE_REMOTE, SOURCE_MODE_LOCAL}:
        return mode
    raise ManifestSourceError(f"{GIT_MANIFEST_SOURCE_MODE_ENV} must be one of auto, remote, local")


def remote_manifest_enabled() -> bool:
    return env_truthy(GIT_REMOTE_MANIFEST_ENABLED_ENV)


def checkout_cache_enabled() -> bool:
    return env_truthy(GIT_CHECKOUT_CACHE_ENABLED_ENV)


def checkout_cache_required() -> bool:
    return env_truthy(GIT_CHECKOUT_CACHE_REQUIRED_ENV)


def local_manifest_enabled(mode: str) -> bool:
    if mode == SOURCE_MODE_LOCAL:
        return True
    if mode == SOURCE_MODE_REMOTE:
        return False
    if env_truthy(GIT_LOCAL_MANIFEST_ENABLED_ENV):
        return True
    # 개발/테스트 편의를 위해 remote source가 꺼진 auto 모드에서만 local fallback 허용.
    return not remote_manifest_enabled()


def env_int(name: str, default: str = "0") -> int:
    try:
        return max(0, int(env(name, default)))
    except ValueError:
        return max(0, int(default))


def git_timeout_seconds() -> float:
    return float(
        env(
            GIT_MANIFEST_COMMAND_TIMEOUT_SECONDS_ENV,
            DEFAULT_GIT_MANIFEST_COMMAND_TIMEOUT_SECONDS,
        )
    )


def manifest_path_for(evt: GitChangedBody) -> str:
    return env(GIT_MANIFEST_PATH_ENV, evt.manifest_path)


def github_token() -> str:
    token_ref = env(GITHUB_TOKEN_REF_ENV, "").strip()
    if token_ref:
        return build_token_vault().read_token(SecretRef(token_ref))
    try:
        return build_token_vault("env").read_token(SecretRef(GITHUB_TOKEN_ENV))
    except SecretNotFound:
        return ""


def github_auth_header() -> str | None:
    try:
        token = github_token()
    except SecretNotFound:
        return None
    return f"Authorization: Bearer {token}" if token else None


def repo_remote_url(repo_ref: str) -> str:
    configured = env(GIT_CACHE_REMOTE_URL_ENV, "").strip()
    if configured:
        return configured
    normalized = repo_ref.strip()
    if not normalized:
        return ""
    if "://" in normalized or normalized.startswith("git@"):
        return normalized
    web_base = env(GITHUB_WEB_BASE_ENV, DEFAULT_GITHUB_WEB_BASE).rstrip("/")
    return f"{web_base}/{normalized.strip('/')}.git"


def source_type_override(event_source_type: str = "") -> str | None:
    raw = env(GIT_MANIFEST_SOURCE_TYPE_ENV, "").strip().lower() or event_source_type.strip().lower()
    if not raw:
        return None
    if raw not in SUPPORTED_SOURCE_TYPES:
        allowed = ", ".join(sorted(SUPPORTED_SOURCE_TYPES))
        raise ManifestSourceError(f"unsupported manifest source type: {raw}; allowed={allowed}")
    return raw


def kubectl_bin() -> str:
    return env(GITOPS_KUBECTL_BIN_ENV, "kubectl")


def helm_bin() -> str:
    return env(GITOPS_HELM_BIN_ENV, "helm")


def render_namespace() -> str:
    return env(GITOPS_HELM_NAMESPACE_ENV, Sandbox.NAMESPACE)


def detect_source_type(path: Path, override: str | None) -> str:
    if override:
        return override
    if path.is_dir():
        if (path / HELM_CHART_FILE).is_file():
            return SOURCE_TYPE_HELM
        if any((path / name).is_file() for name in KUSTOMIZATION_FILES):
            return SOURCE_TYPE_KUSTOMIZE
    return SOURCE_TYPE_RAW_YAML


def render_source_from_path(
    path: Path, manifest_path: str, origin: str, override: str | None = None
) -> RenderSource:
    return RenderSource(
        source_type=detect_source_type(path, override),
        manifest_path=manifest_path,
        origin=origin,
        local_path=path,
    )


def render_source_from_text(
    source: str, manifest_path: str, origin: str, override: str | None = None
) -> RenderSource:
    if override and override not in {SOURCE_TYPE_RAW_YAML, SOURCE_TYPE_RAW_JSON}:
        raise ManifestSourceError(
            f"{override} rendering requires a checked-out repo path, not a raw file response"
        )
    return RenderSource(
        source_type=override or SOURCE_TYPE_RAW_YAML,
        manifest_path=manifest_path,
        origin=origin,
        source_text=source,
    )


def export_checkout_cache_manifest_path(
    evt: GitChangedBody, manifest_path: str, destination: Path
) -> Path | None:
    if not checkout_cache_enabled():
        return None
    remote_url = repo_remote_url(evt.repo_ref)
    if not remote_url:
        return None
    cache = GitRepoCache(
        cache_dir=env(GIT_CACHE_DIR_ENV, DEFAULT_GIT_CACHE_DIR),
        remote_url=remote_url,
        timeout_seconds=git_timeout_seconds(),
        max_bytes=env_int(GIT_CACHE_MAX_BYTES_ENV),
        max_repos=env_int(GIT_CACHE_MAX_REPOS_ENV),
        http_extra_header=github_auth_header(),
    )
    try:
        return cache.export_path(evt.commit_sha, manifest_path, destination)
    except GitRepoCacheError:
        if checkout_cache_required():
            raise
        return None


def github_contents_url(repo_ref: str, commit_sha: str, manifest_path: str) -> str:
    api_base = env(GITHUB_API_BASE_ENV, DEFAULT_GITHUB_API_BASE).rstrip("/")
    encoded_repo = parse.quote(repo_ref.strip("/"), safe="/")
    encoded_path = parse.quote(manifest_path.lstrip("/"), safe="/")
    encoded_ref = parse.quote(commit_sha, safe="")
    return f"{api_base}/repos/{encoded_repo}/contents/{encoded_path}?ref={encoded_ref}"


def github_api_url(path: str, params: Mapping[str, str] | None = None) -> str:
    api_base = env(GITHUB_API_BASE_ENV, DEFAULT_GITHUB_API_BASE).rstrip("/")
    url = f"{api_base}/{path.lstrip('/')}"
    if params:
        url = f"{url}?{parse.urlencode(params)}"
    return url


def github_json(path: str, params: Mapping[str, str] | None = None) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        token = github_token()
    except SecretNotFound as exc:
        raise ManifestSourceError(f"failed to load {GITHUB_TOKEN_REF_ENV}") from exc
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = request.Request(github_api_url(path, params), headers=headers)
    timeout = float(
        env(GITHUB_MANIFEST_TIMEOUT_SECONDS_ENV, DEFAULT_GITHUB_MANIFEST_TIMEOUT_SECONDS)
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (error.HTTPError, error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ManifestSourceError(f"failed to load GitHub render source: {exc}") from exc


def github_commit_tree_sha(repo_ref: str, commit_sha: str) -> str:
    encoded_repo = parse.quote(repo_ref.strip("/"), safe="/")
    encoded_commit = parse.quote(commit_sha, safe="")
    data = github_json(f"/repos/{encoded_repo}/commits/{encoded_commit}")
    if not isinstance(data, Mapping):
        raise ManifestSourceError("GitHub commit response was invalid")
    commit = data.get("commit")
    commit_map = commit if isinstance(commit, Mapping) else {}
    tree = commit_map.get("tree")
    tree_map = tree if isinstance(tree, Mapping) else {}
    tree_sha = str(tree_map.get("sha") or "").strip()
    if not tree_sha:
        raise ManifestSourceError("GitHub commit response did not include a tree sha")
    return tree_sha


def github_tree(repo_ref: str, tree_sha: str) -> list[dict[str, Any]]:
    encoded_repo = parse.quote(repo_ref.strip("/"), safe="/")
    encoded_tree = parse.quote(tree_sha, safe="")
    data = github_json(
        f"/repos/{encoded_repo}/git/trees/{encoded_tree}",
        params={"recursive": "1"},
    )
    if not isinstance(data, Mapping):
        raise ManifestSourceError("GitHub tree response was invalid")
    raw_tree = data.get("tree")
    if not isinstance(raw_tree, list):
        raise ManifestSourceError("GitHub tree response was invalid")
    return [dict(item) for item in raw_tree if isinstance(item, Mapping)]


def read_github_content_bytes(repo_ref: str, commit_sha: str, path: str) -> bytes:
    encoded_repo = parse.quote(repo_ref.strip("/"), safe="/")
    encoded_path = parse.quote(path, safe="/")
    data = github_json(
        f"/repos/{encoded_repo}/contents/{encoded_path}",
        params={"ref": commit_sha},
    )
    if not isinstance(data, Mapping) or str(data.get("type") or "") != "file":
        raise ManifestSourceError(f"GitHub content is not a file: {path}")
    encoding = str(data.get("encoding") or "")
    raw_content = data.get("content")
    if encoding != "base64" or not isinstance(raw_content, str):
        raise ManifestSourceError(f"GitHub content response was invalid: {path}")
    try:
        return base64.b64decode(raw_content, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ManifestSourceError(f"GitHub content response was invalid: {path}") from exc


def export_github_render_source(
    repo_ref: str,
    commit_sha: str,
    manifest_path: str,
    source_type: str,
    destination: Path,
) -> Path | None:
    if not remote_manifest_enabled():
        return None
    if not repo_ref or not commit_sha or not manifest_path:
        return None

    source_dir = render_source_directory(manifest_path, source_type)
    try:
        tree = github_tree(repo_ref, github_commit_tree_sha(repo_ref, commit_sha))
        blob_paths = sorted(
            {
                path
                for item in tree
                if str(item.get("type") or "") == "blob"
                for path in [normalize_repository_path(str(item.get("path") or ""))]
                if path
            }
        )
        # Kustomize overlay 는 저장소 상대참조(예: resources: ../../base)를 쓴다.
        # 저장소 루트 전체를 내려받으면 관계없는 대형 monorepo 파일까지
        # 파일 상한에 산입되어 정상 overlay가 렌더 전에 거부된다.
        # Kustomization 그래프에서 직접 참조한 로컬 파일만 수집하고,
        # 그 의존성 집합에 대해 기존 파일/바이트 상한을 강제한다.
        has_kustomization = any(
            path_is_under_directory(path, source_dir)
            and path.rsplit("/", 1)[-1] in KUSTOMIZATION_FILES
            for path in blob_paths
        )
        if source_type == SOURCE_TYPE_KUSTOMIZE or has_kustomization:
            source_contents = collect_github_kustomize_source_contents(
                repo_ref,
                commit_sha,
                source_dir,
                set(blob_paths),
            )
            for path, content in sorted(source_contents.items()):
                write_render_source_file(destination, path, content)
        else:
            source_paths = [
                path for path in blob_paths if path_is_under_directory(path, source_dir)
            ]
            if not source_paths:
                raise ManifestSourceError(
                    f"{source_type} render source contains no files under {source_dir}"
                )
            if len(source_paths) > MAX_REMOTE_RENDER_SOURCE_FILES:
                raise ManifestSourceError(
                    f"{source_type} render source exceeds file limit "
                    f"({len(source_paths)} > {MAX_REMOTE_RENDER_SOURCE_FILES})"
                )

            total_bytes = 0
            for path in source_paths:
                content = read_github_content_bytes(repo_ref, commit_sha, path)
                total_bytes += len(content)
                if total_bytes > MAX_REMOTE_RENDER_SOURCE_BYTES:
                    raise ManifestSourceError(
                        f"{source_type} render source exceeds byte limit "
                        f"({total_bytes} > {MAX_REMOTE_RENDER_SOURCE_BYTES})"
                    )
                write_render_source_file(destination, path, content)
    except ManifestSourceError:
        if env_truthy(GIT_REMOTE_MANIFEST_REQUIRED_ENV, "1"):
            raise
        return None

    return destination if source_dir == "." else destination / source_dir


def collect_github_kustomize_source_contents(
    repo_ref: str,
    commit_sha: str,
    source_dir: str,
    blob_paths: set[str],
) -> dict[str, bytes]:
    """Fetch only the bounded local dependency graph for one Kustomize root."""

    selected_source_paths = {
        path for path in blob_paths if path_is_under_directory(path, source_dir)
    }
    if len(selected_source_paths) > MAX_REMOTE_RENDER_SOURCE_FILES:
        raise ManifestSourceError(
            "kustomize render source exceeds file limit "
            f"({len(selected_source_paths)} > {MAX_REMOTE_RENDER_SOURCE_FILES})"
        )

    directory_paths = repository_directories(blob_paths)
    source_contents: dict[str, bytes] = {}
    total_bytes = 0

    def fetch(path: str) -> bytes:
        nonlocal total_bytes
        cached = source_contents.get(path)
        if cached is not None:
            return cached
        if path not in blob_paths:
            raise ManifestSourceError(
                f"Kustomize local reference does not exist in repository: {path}"
            )
        if len(source_contents) >= MAX_REMOTE_RENDER_SOURCE_FILES:
            raise ManifestSourceError(
                "kustomize render source exceeds file limit "
                f"({len(source_contents) + 1} > {MAX_REMOTE_RENDER_SOURCE_FILES})"
            )
        content = read_github_content_bytes(repo_ref, commit_sha, path)
        total_bytes += len(content)
        if total_bytes > MAX_REMOTE_RENDER_SOURCE_BYTES:
            raise ManifestSourceError(
                "kustomize render source exceeds byte limit "
                f"({total_bytes} > {MAX_REMOTE_RENDER_SOURCE_BYTES})"
            )
        source_contents[path] = content
        return content

    pending_directories = [source_dir]
    visited_directories: set[str] = set()
    try:
        while pending_directories:
            directory = pending_directories.pop()
            if directory in visited_directories:
                continue
            visited_directories.add(directory)
            kustomization_path = find_kustomization_path(directory, blob_paths)
            document = parse_kustomization_document(fetch(kustomization_path), kustomization_path)
            for field, raw_reference, may_be_directory in kustomize_local_references(document):
                reference = normalize_kustomize_local_reference(
                    raw_reference,
                    field=field,
                    current_directory=directory,
                )
                if may_be_directory and reference in directory_paths:
                    pending_directories.append(reference)
                    continue
                fetch(reference)
    except ManifestRenderValidationError as exc:
        raise ManifestSourceError(str(exc)) from exc
    return source_contents


def render_source_directory(manifest_path: str, source_type: str) -> str:
    normalized = normalize_repository_path(manifest_path)
    if not normalized:
        raise ManifestSourceError("manifest_path must be a relative repository path")
    basename = normalized.rsplit("/", 1)[-1]
    if source_type == SOURCE_TYPE_HELM and basename == HELM_CHART_FILE:
        return parent_repository_path(normalized)
    if source_type == SOURCE_TYPE_KUSTOMIZE and basename in KUSTOMIZATION_FILES:
        return parent_repository_path(normalized)
    return normalized


def path_is_under_directory(path: str, directory: str) -> bool:
    return directory == "." or path == directory or path.startswith(f"{directory}/")


def normalize_repository_path(path: str) -> str:
    path = path.strip().strip("/")
    if not path or "\\" in path:
        return ""
    parts = [part for part in path.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        return ""
    return "/".join(parts)


def parent_repository_path(path: str) -> str:
    if "/" not in path:
        return "."
    return path.rsplit("/", 1)[0]


def write_render_source_file(checkout_root: Path, repository_path: str, content: bytes) -> None:
    normalized = normalize_repository_path(repository_path)
    if not normalized:
        raise ManifestSourceError("render source path is invalid")
    destination = checkout_root.joinpath(*normalized.split("/"))
    root = checkout_root.resolve()
    resolved = destination.resolve()
    if resolved != root and root not in resolved.parents:
        raise ManifestSourceError("render source path escapes checkout directory")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    except OSError as exc:
        message = exc.strerror or str(exc)
        raise ManifestSourceError(f"failed to write render source: {message}") from exc


def read_github_manifest_source(repo_ref: str, commit_sha: str, manifest_path: str) -> str | None:
    if not remote_manifest_enabled():
        return None
    if not repo_ref or not commit_sha or not manifest_path:
        return None

    headers = {"Accept": "application/vnd.github.raw"}
    try:
        token = github_token()
    except SecretNotFound as exc:
        raise ManifestSourceError(f"failed to load {GITHUB_TOKEN_REF_ENV}") from exc
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = request.Request(github_contents_url(repo_ref, commit_sha, manifest_path), headers=headers)
    timeout = float(
        env(GITHUB_MANIFEST_TIMEOUT_SECONDS_ENV, DEFAULT_GITHUB_MANIFEST_TIMEOUT_SECONDS)
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return response.read().decode("utf-8")
    except (error.HTTPError, error.URLError, TimeoutError) as exc:
        if env_truthy(GIT_REMOTE_MANIFEST_REQUIRED_ENV, "1"):
            raise ManifestSourceError(f"failed to load GitHub manifest: {exc}") from exc
        return None


def export_local_git_path(
    repo_path: str, commit_sha: str, manifest_path: str, destination: Path
) -> Path:
    result = subprocess.run(
        ["git", "-C", repo_path, "archive", "--format=tar", commit_sha, manifest_path],
        check=True,
        capture_output=True,
        timeout=git_timeout_seconds(),
    )
    return extract_git_archive(result.stdout, destination, manifest_path)


@contextmanager
def manifest_render_source(evt: GitChangedBody) -> Any:
    manifest_path = manifest_path_for(evt)
    if not manifest_path:
        yield None
        return

    mode = manifest_source_mode()
    override = source_type_override(evt.source_type)

    if mode != SOURCE_MODE_LOCAL:
        with TemporaryDirectory(prefix="gitops-render-") as tmp:
            cached_path = export_checkout_cache_manifest_path(evt, manifest_path, Path(tmp))
            if cached_path is not None:
                yield render_source_from_path(cached_path, manifest_path, "git_cache", override)
                return

            if override in {SOURCE_TYPE_KUSTOMIZE, SOURCE_TYPE_HELM}:
                remote_path = export_github_render_source(
                    evt.repo_ref,
                    evt.commit_sha,
                    manifest_path,
                    override,
                    Path(tmp),
                )
                if remote_path is not None:
                    yield render_source_from_path(
                        remote_path,
                        manifest_path,
                        "github_tree",
                        override,
                    )
                    return

        remote_source = read_github_manifest_source(evt.repo_ref, evt.commit_sha, manifest_path)
        if remote_source is not None:
            yield render_source_from_text(remote_source, manifest_path, "github_contents", override)
            return

    if local_manifest_enabled(mode):
        repo_path = env(GIT_REPO_PATH_ENV, "")
        if repo_path:
            with TemporaryDirectory(prefix="gitops-render-") as tmp:
                local_path = export_local_git_path(
                    repo_path, evt.commit_sha, manifest_path, Path(tmp)
                )
                yield render_source_from_path(local_path, manifest_path, "git_repo_path", override)
            return

        path = Path(manifest_path)
        if path.exists():
            yield render_source_from_path(path, manifest_path, "local_path", override)
            return

    yield None


def load_manifest_documents(source: str) -> list[Any]:
    try:
        payload = json.loads(source)
        return payload if isinstance(payload, list) else [payload]
    except json.JSONDecodeError:
        try:
            return list(yaml.safe_load_all(source))
        except yaml.YAMLError as exc:
            raise ValueError(f"invalid YAML manifest: {exc}") from exc


def render_source_documents(source: RenderSource) -> list[Any]:
    if source.source_type in {SOURCE_TYPE_RAW_YAML, SOURCE_TYPE_RAW_JSON}:
        return load_raw_yaml_documents(source)
    if source.source_type == SOURCE_TYPE_KUSTOMIZE:
        return load_manifest_documents(render_kustomize(source))
    if source.source_type == SOURCE_TYPE_HELM:
        return load_manifest_documents(render_helm(source))
    raise ManifestSourceError(f"unsupported manifest source type: {source.source_type}")


def load_raw_yaml_documents(source: RenderSource) -> list[Any]:
    if source.source_text is not None:
        return load_manifest_documents(source.source_text)
    if source.local_path is None:
        raise ManifestSourceError("raw YAML source has no local path")
    if source.local_path.is_file():
        return load_manifest_documents(source.local_path.read_text(encoding="utf-8"))
    if not source.local_path.is_dir():
        raise ManifestSourceError(f"manifest path does not exist: {source.manifest_path}")

    docs: list[Any] = []
    files = [
        item
        for item in sorted(source.local_path.rglob("*"))
        if item.is_file() and item.suffix.lower() in MANIFEST_EXTENSIONS
    ]
    if not files:
        raise ValueError("raw manifest directory did not contain YAML or JSON files")
    for item in files:
        docs.extend(load_manifest_documents(item.read_text(encoding="utf-8")))
    return docs


def render_kustomize(source: RenderSource) -> str:
    if source.local_path is None or not source.local_path.is_dir():
        raise ManifestSourceError("Kustomize rendering requires a directory source")
    if not any((source.local_path / name).is_file() for name in KUSTOMIZATION_FILES):
        raise ManifestSourceError("Kustomize source is missing kustomization.yaml")
    return run_render_command(
        [kubectl_bin(), "kustomize", str(source.local_path)],
        "kustomize render failed",
    )


def render_helm(source: RenderSource) -> str:
    if source.local_path is None or not source.local_path.is_dir():
        raise ManifestSourceError("Helm rendering requires a chart directory source")
    if not (source.local_path / HELM_CHART_FILE).is_file():
        raise ManifestSourceError("Helm source is missing Chart.yaml")
    if env_truthy(GITOPS_HELM_DEPENDENCY_BUILD_ENV):
        run_render_command(
            [helm_bin(), "dependency", "build", str(source.local_path)],
            "helm dependency build failed",
        )
    command = [
        helm_bin(),
        "template",
        helm_release_name(source),
        str(source.local_path),
        "--namespace",
        render_namespace(),
    ]
    if env_truthy(GITOPS_HELM_INCLUDE_CRDS_ENV, "1"):
        command.append("--include-crds")
    command.extend(helm_values_args(source.local_path))
    return run_render_command(command, "helm template failed")


def helm_release_name(source: RenderSource) -> str:
    configured = env(GITOPS_HELM_RELEASE_NAME_ENV, "").strip()
    if configured:
        return configured
    stem = source.local_path.name if source.local_path is not None else "release"
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in stem).strip("-")
    return (cleaned or "release")[:53]


def helm_values_args(chart_path: Path) -> list[str]:
    raw = env(GITOPS_HELM_VALUES_FILES_ENV, "").strip()
    if not raw:
        return []
    args: list[str] = []
    for value in [item.strip() for item in raw.split(",") if item.strip()]:
        values_path = safe_child_path(chart_path, value)
        if not values_path.is_file():
            raise ManifestSourceError(f"Helm values file does not exist: {value}")
        args.extend(["--values", str(values_path)])
    return args


def safe_child_path(root: Path, raw_path: str) -> Path:
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    if resolved_root != resolved and resolved_root not in resolved.parents:
        raise ManifestSourceError(f"path escapes Helm chart directory: {raw_path}")
    return resolved


def run_render_command(command: list[str], error_prefix: str) -> str:
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=git_timeout_seconds(),
        )
    except FileNotFoundError as exc:
        raise ManifestSourceError(f"{error_prefix}: executable not found: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ManifestSourceError(
            f"{error_prefix}: timed out after {git_timeout_seconds()}s"
        ) from exc
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise ManifestSourceError(f"{error_prefix}: {compact_render_error(message)}") from exc
    if not result.stdout.strip():
        raise ManifestSourceError(f"{error_prefix}: renderer produced empty output")
    return result.stdout


def compact_render_error(message: str) -> str:
    compact = " ".join(message.split())
    try:
        token = github_token()
    except SecretNotFound:
        token = ""
    if token:
        compact = compact.replace(token, "<redacted>")
    return compact[:MAX_RENDER_ERROR_LENGTH]


def render_manifest_payload(payload: dict[str, Any]) -> RenderedManifest:
    kind = str(payload.get("kind", ""))
    api_version = str(payload.get("apiVersion", ""))
    metadata = payload.get(METADATA_FIELD, {})
    if not isinstance(metadata, dict):
        raise ValueError("manifest metadata must be an object")
    name = str(metadata.get("name", ""))
    if not kind or not api_version or not name:
        raise ValueError("manifest must include apiVersion, kind, and metadata.name")

    namespace = str(metadata.get("namespace") or default_namespace_for_kind(kind))
    if namespace:
        payload = {
            **payload,
            METADATA_FIELD: {
                **metadata,
                "namespace": namespace,
            },
        }

    return RenderedManifest(
        api_version=api_version,
        kind=kind,
        metadata=RenderedMetadata(name=name, namespace=namespace),
        spec=rendered_spec_from_payload(kind, payload),
        manifest=payload,
        artifact_digest=manifest_artifact_digest(payload),
        declared_fields=extract_declared_field_paths(payload),
    )


def with_approved_snapshot(
    rendered: RenderedManifest,
    record: Mapping[str, object] | None,
) -> RenderedManifest:
    """Attach only the exact binding/resource snapshot to a fresh render."""

    if not isinstance(record, Mapping):
        return rendered
    raw_snapshot = record.get("snapshot")
    snapshot = raw_snapshot if isinstance(raw_snapshot, Mapping) else record
    expected_resource = resource_ref(rendered.kind, rendered.metadata.name)
    if (
        str(snapshot.get("resource") or "").casefold() != expected_resource
        or str(snapshot.get("namespace") or "") != rendered.metadata.namespace
    ):
        return rendered
    raw_fields = snapshot.get("fields")
    if not isinstance(raw_fields, Mapping) or not raw_fields:
        return rendered
    fields = dict(raw_fields)
    managed = record.get("managed_fields")
    configured_paths = managed.get("paths") if isinstance(managed, Mapping) else None
    paths = (
        [str(path) for path in configured_paths if isinstance(path, str)]
        if isinstance(configured_paths, list)
        else list(fields)
    )
    if set(paths) != set(fields):
        return rendered
    return replace(
        rendered,
        managed_fields=sorted(paths),
        last_approved_snapshot=fields,
    )


async def load_approved_snapshot(
    evt: GitChangedBody,
    rendered: RenderedManifest,
    db: RepoChangeStore,
) -> RenderedManifest:
    reader = getattr(db, "get_last_approved_resource_snapshot", None)
    if not callable(reader):
        return rendered
    record = await reader(
        evt.workspace_id,
        evt.binding_id,
        evt.cluster_id,
        rendered.metadata.namespace,
        resource_ref(rendered.kind, rendered.metadata.name),
    )
    return with_approved_snapshot(rendered, record)


def default_namespace_for_kind(kind: str) -> str:
    return Sandbox.NAMESPACE if kind in DEFAULT_NAMESPACED_KINDS else ""


def manifest_artifact_digest(payload: dict[str, Any]) -> str:
    return canonical_manifest_digest(payload)


def rendered_spec_from_payload(kind: str, payload: dict[str, Any]) -> RenderedSpec:
    spec = payload.get(SPEC_FIELD, {})
    if not isinstance(spec, dict):
        return RenderedSpec()
    if kind != MANIFEST_KIND:
        return RenderedSpec()
    return RenderedSpec(
        replicas=deployment_replicas(spec),
        image=deployment_image(spec),
    )


def deployment_replicas(spec: dict[str, Any]) -> int:
    raw = spec.get("replicas", 1)
    if raw is None:
        return 1
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError("deployment spec.replicas must be an integer")
    if raw < 0:
        raise ValueError("deployment spec.replicas must be a non-negative integer")
    return raw


def deployment_image(spec: dict[str, Any]) -> str:
    template = spec.get(TEMPLATE_FIELD, {})
    if not isinstance(template, dict):
        return ""
    pod_spec = template.get(SPEC_FIELD, {})
    if not isinstance(pod_spec, dict):
        return ""
    containers = pod_spec.get(CONTAINERS_FIELD, [])
    if not isinstance(containers, list) or not containers:
        return ""
    first = containers[0]
    if not isinstance(first, dict):
        return ""
    return str(first.get("image", ""))


def build_rendered_manifest_result(evt: GitChangedBody) -> RenderResult:
    with manifest_render_source(evt) as source:
        if source is not None:
            documents = render_source_documents(source)
            manifest_documents = [
                document for document in documents if isinstance(document, dict) and document
            ]
            return RenderResult(
                rendered_manifests=parse_rendered_manifest_payloads(manifest_documents),
                source_type=source.source_type,
                source_origin=source.origin,
                source_is_file=(
                    source.source_text is not None
                    or bool(source.local_path is not None and source.local_path.is_file())
                ),
                source_document_count=len(documents),
                source_manifest_sha256=[
                    canonical_manifest_digest(document) for document in manifest_documents
                ],
            )
    # 소스 없이 Deployment 를 합성하지 않음 — 정직한 실패 경로(manifest.invalid)로 보냄.
    raise ManifestSourceError(MANIFEST_SOURCE_UNAVAILABLE_REASON)


def parse_rendered_manifest_payloads(payloads: list[Any]) -> list[RenderedManifest]:
    rendered = [
        render_manifest_payload(payload)
        for payload in payloads
        if isinstance(payload, dict) and payload
    ]
    if not rendered:
        raise ValueError("manifest source did not contain a Kubernetes object")
    return rendered


def rendered_resource_suffix(rendered: RenderedManifest) -> str:
    return f"{rendered.kind.lower()}/{rendered.metadata.name}"


def artifact_manifest_path(evt: GitChangedBody, rendered: RenderedManifest | None) -> str:
    manifest_path = manifest_path_for(evt)
    if rendered is None:
        return manifest_path
    return f"{manifest_path}#{rendered_resource_suffix(rendered)}"


def artifact_payload(
    evt: GitChangedBody,
    status: str,
    rendered: RenderedManifest | None = None,
    reason: str | None = None,
    source: RenderResult | None = None,
    source_manifest_sha256: str | None = None,
) -> dict[str, object]:
    return {
        "workspace_id": evt.workspace_id,
        "repository_id": evt.repository_id,
        "watch_target_id": evt.watch_target_id,
        "binding_id": evt.binding_id,
        "commit_sha": evt.commit_sha,
        "manifest_path": artifact_manifest_path(evt, rendered),
        "status": status,
        "status_reason": reason,
        "artifact_digest": rendered.artifact_digest if rendered is not None else None,
        "rendered_manifest": rendered.to_body() if rendered is not None else None,
        "source_summary": {
            "repo_ref": evt.repo_ref,
            "branch": evt.branch,
            "manifest_path": manifest_path_for(evt),
            "resource": rendered_resource_suffix(rendered) if rendered is not None else None,
            "source_type": source.source_type if source is not None else None,
            "source_origin": source.source_origin if source is not None else None,
            "source_is_file": source.source_is_file if source is not None else None,
            "source_document_count": (source.source_document_count if source is not None else None),
            "source_manifest_sha256": source_manifest_sha256,
            "renderer_version": RENDERER_VERSION,
            "cluster_id": evt.cluster_id,
            "application_id": evt.application_id,
            "workflow_run_id": evt.workflow_run_id,
            "environment": evt.environment,
        },
    }


async def cached_rendered_manifests(
    evt: GitChangedBody, db: RepoChangeStore, manifest_path: str
) -> list[CachedRenderedManifest]:
    finder = getattr(db, "find_rendered_manifest_artifacts", None)
    if finder is None:
        return []
    artifacts = await finder(
        evt.workspace_id,
        evt.binding_id,
        evt.commit_sha,
        manifest_path,
        RENDERER_VERSION,
    )
    rendered: list[CachedRenderedManifest] = []
    source_signatures: set[tuple[str, str, bool, int]] = set()
    prefix = f"{manifest_path}#"
    for artifact in artifacts or []:
        artifact_path = str(artifact.get("manifest_path", ""))
        if artifact_path != manifest_path and not artifact_path.startswith(prefix):
            continue
        source_summary = artifact.get("source_summary", {})
        if (
            not isinstance(source_summary, dict)
            or source_summary.get("renderer_version") != RENDERER_VERSION
            or not isinstance(source_summary.get("source_type"), str)
            or not source_summary.get("source_type")
            or not isinstance(source_summary.get("source_origin"), str)
            or not source_summary.get("source_origin")
            or not isinstance(source_summary.get("source_is_file"), bool)
            or isinstance(source_summary.get("source_document_count"), bool)
            or not isinstance(source_summary.get("source_document_count"), int)
            or int(source_summary["source_document_count"]) < 1
            or not isinstance(source_summary.get("source_manifest_sha256"), str)
            or not str(source_summary["source_manifest_sha256"]).startswith("sha256:")
        ):
            continue
        raw = artifact.get("rendered_manifest")
        if isinstance(raw, dict):
            source_signatures.add(
                (
                    str(source_summary["source_type"]),
                    str(source_summary["source_origin"]),
                    bool(source_summary["source_is_file"]),
                    int(source_summary["source_document_count"]),
                )
            )
            rendered.append(
                CachedRenderedManifest(
                    rendered=RenderedManifest.from_body(raw),
                    artifact_id=str(artifact.get("artifact_id") or ""),
                )
            )
    if len(source_signatures) != 1:
        return []
    (_, _, _, source_document_count) = next(iter(source_signatures))
    return rendered if source_document_count == len(rendered) else []


@app.on(GitChangedBody)
async def on_git_changed(
    evt: GitChangedBody, ctx: EventContext[RepoChangeStore]
) -> AsyncIterator[EventBody]:
    # Git change를 Manifest/RenderedManifest 값 객체로 변환하고 render artifact를 저장함.
    # subject 발행은 yield된 이벤트를 런타임이 처리함.
    source_manifest_path = manifest_path_for(evt)
    event_manifest_path = evt.manifest_path
    cached = await cached_rendered_manifests(evt, ctx.db, source_manifest_path)
    if cached:
        for item in cached:
            rendered = await load_approved_snapshot(evt, item.rendered, ctx.db)
            await ctx.db.save_repo_change(
                ctx.correlation_id,
                evt.commit_sha,
                rendered.manifest or rendered.to_body(),
                evt.workspace_id,
                evt.repository_id,
                evt.watch_target_id,
                evt.binding_id,
                event_manifest_path,
            )
            yield ManifestRenderedBody(
                rendered_manifest=rendered,
                workspace_id=evt.workspace_id,
                repository_id=evt.repository_id,
                watch_target_id=evt.watch_target_id,
                binding_id=evt.binding_id,
                application_id=evt.application_id,
                workflow_run_id=evt.workflow_run_id,
                environment=evt.environment,
                cluster_id=evt.cluster_id,
                commit_sha=evt.commit_sha,
                manifest_path=event_manifest_path,
                repo_ref=evt.repo_ref,
                branch=evt.branch,
            )
        await ctx.db.mark_watch_observed(
            evt.watch_target_id,
            evt.commit_sha,
            evt.workspace_id,
            evt.repository_id,
            evt.branch,
            event_manifest_path,
        )
        return

    try:
        result = build_rendered_manifest_result(evt)
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        GitRepoCacheError,
        ManifestSourceError,
        ValueError,
    ) as exc:
        reason = str(exc)
        await ctx.db.record_manifest_artifact(
            artifact_payload(evt, ManifestArtifactStatus.INVALID_CONFIG.value, reason=reason)
        )
        if isinstance(exc, ValueError):
            await ctx.db.mark_watch_observed(
                evt.watch_target_id,
                evt.commit_sha,
                evt.workspace_id,
                evt.repository_id,
                evt.branch,
                evt.manifest_path,
            )
        yield ManifestInvalidBody(
            workspace_id=evt.workspace_id,
            repository_id=evt.repository_id,
            watch_target_id=evt.watch_target_id,
            binding_id=evt.binding_id,
            commit_sha=evt.commit_sha,
            manifest_path=event_manifest_path,
            reason=reason,
            application_id=evt.application_id,
            workflow_run_id=evt.workflow_run_id,
            environment=evt.environment,
            cluster_id=evt.cluster_id,
        )
        return

    for index, source_rendered in enumerate(result.rendered_manifests):
        rendered = await load_approved_snapshot(evt, source_rendered, ctx.db)
        await ctx.db.save_repo_change(
            ctx.correlation_id,
            evt.commit_sha,
            rendered.manifest or rendered.to_body(),
            evt.workspace_id,
            evt.repository_id,
            evt.watch_target_id,
            evt.binding_id,
            event_manifest_path,
        )
        await ctx.db.record_manifest_artifact(
            artifact_payload(
                evt,
                ManifestArtifactStatus.RENDERED.value,
                rendered=rendered,
                source=result,
                source_manifest_sha256=result.source_manifest_sha256[index],
            )
        )
        yield ManifestRenderedBody(
            rendered_manifest=rendered,
            workspace_id=evt.workspace_id,
            repository_id=evt.repository_id,
            watch_target_id=evt.watch_target_id,
            binding_id=evt.binding_id,
            application_id=evt.application_id,
            workflow_run_id=evt.workflow_run_id,
            environment=evt.environment,
            cluster_id=evt.cluster_id,
            commit_sha=evt.commit_sha,
            manifest_path=event_manifest_path,
            repo_ref=evt.repo_ref,
            branch=evt.branch,
        )
    await ctx.db.mark_watch_observed(
        evt.watch_target_id,
        evt.commit_sha,
        evt.workspace_id,
        evt.repository_id,
        evt.branch,
        event_manifest_path,
    )


if __name__ == "__main__":
    app.run()
