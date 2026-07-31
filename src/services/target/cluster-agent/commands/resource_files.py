"""Read-only Pod and OCI image filesystem execution inside the outbound Agent."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import platform
import posixpath
import re
import shlex
import ssl
import tarfile
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from kubernetes_api import kubernetes_api_base_url, service_account_token

from commands.context import CommandContext, KubernetesClient
from commands.exec_transport import (
    STATUS_CHANNEL,
    STDERR_CHANNEL,
    STDOUT_CHANNEL,
    kubernetes_exec_connector,
    kubernetes_exit_code,
)
from commands.kubernetes import validate_exact_resource
from config import (
    KUBERNETES_SERVICEACCOUNT_CA_CERT_PATH,
    KUBERNETES_SERVICEACCOUNT_TOKEN_PATH,
)
from packages.config.settings import env
from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.resource_files import (
    MAX_RESOURCE_FILE_CHUNK_BYTES,
    MAX_RESOURCE_FILE_LIST_BYTES,
    MAX_RESOURCE_FILE_LIST_RECORDS,
    MAX_RESOURCE_FILE_PAGE_SIZE,
    MAX_RESOURCE_FILE_TOTAL_BYTES,
    MAX_RESOURCE_IMAGE_FILES,
    MAX_RESOURCE_IMAGE_LAYERS,
    ResourceFileCommandPayload,
    ResourceFileDirectoryResult,
    ResourceFileEntry,
    ResourceFileReadResult,
    ResourceImageMetadataResult,
    normalize_resource_file_path,
)

RESOURCE_FILES_CACHE_DIR_ENV = "RESOURCE_FILES_CACHE_DIR"
RESOURCE_FILES_CACHE_TTL_SECONDS_ENV = "RESOURCE_FILES_CACHE_TTL_SECONDS"
RESOURCE_FILES_MAX_CACHED_IMAGES_ENV = "RESOURCE_FILES_MAX_CACHED_IMAGES"
RESOURCE_FILES_REGISTRY_TIMEOUT_SECONDS_ENV = "RESOURCE_FILES_REGISTRY_TIMEOUT_SECONDS"
DEFAULT_RESOURCE_FILES_CACHE_DIR = "/tmp/target-agent/resource-files"
DEFAULT_RESOURCE_FILES_CACHE_TTL_SECONDS = 300
DEFAULT_RESOURCE_FILES_MAX_CACHED_IMAGES = 4
DEFAULT_RESOURCE_FILES_REGISTRY_TIMEOUT_SECONDS = 20.0
MAX_RESOURCE_IMAGE_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_RESOURCE_IMAGE_CONFIG_BYTES = 2 * 1024 * 1024

OCI_MANIFEST_TYPES = (
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
)
OCI_INDEX_TYPES = frozenset(
    {
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    }
)
OCI_MANIFEST_ACCEPT = ", ".join(OCI_MANIFEST_TYPES)
OCI_ARTIFACT_PREFIX = "artifact-"
WHITEOUT_PREFIX = ".wh."
OPAQUE_WHITEOUT = ".wh..wh..opq"


class ResourceFileExecutionError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class ExecConnection(Protocol):
    async def recv(self) -> str | bytes: ...


ExecConnector = Any


@dataclass(frozen=True)
class IndexedFile:
    entry: ResourceFileEntry
    layer_path: Path | None
    member_name: str | None


class ImageLayerIndex:
    """Merged OCI layer index. No archive member is extracted onto the host."""

    def __init__(self, records: Mapping[str, IndexedFile]) -> None:
        self.records = dict(records)

    @classmethod
    def from_layers(cls, layer_paths: Iterable[Path]) -> ImageLayerIndex:
        records: dict[str, IndexedFile] = {
            "/": IndexedFile(
                ResourceFileEntry(name="/", path="/", type="directory", permissions="drwxr-xr-x"),
                None,
                None,
            )
        }
        file_count = 0
        for layer_path in layer_paths:
            with tarfile.open(layer_path, mode="r:*") as archive:
                for member in archive:
                    safe_path = _safe_archive_path(member.name)
                    if safe_path is None:
                        continue
                    parent = posixpath.dirname(safe_path) or "/"
                    base = posixpath.basename(safe_path)
                    if base == OPAQUE_WHITEOUT:
                        _remove_descendants(records, parent)
                        continue
                    if base.startswith(WHITEOUT_PREFIX):
                        target = posixpath.join(parent, base.removeprefix(WHITEOUT_PREFIX))
                        _remove_path(records, target)
                        continue
                    _ensure_parent_directories(records, safe_path)
                    entry_type = (
                        "directory"
                        if member.isdir()
                        else "symlink"
                        if member.issym() or member.islnk()
                        else "file"
                    )
                    if entry_type != "directory":
                        _remove_descendants(records, safe_path)
                    records[safe_path] = IndexedFile(
                        entry=ResourceFileEntry(
                            name=base,
                            path=safe_path,
                            type=entry_type,
                            size=max(0, member.size) if entry_type == "file" else 0,
                            permissions=_mode_string(member.mode, entry_type),
                            modified_at=_iso_timestamp(member.mtime),
                            link_target=member.linkname if entry_type == "symlink" else None,
                        ),
                        layer_path=layer_path if entry_type == "file" else None,
                        member_name=member.name if entry_type == "file" else None,
                    )
                    file_count += 1
                    if file_count > MAX_RESOURCE_IMAGE_FILES:
                        raise ResourceFileExecutionError("image filesystem exceeds the file limit")
        return cls(records)

    def list_directory(self, path: str) -> list[ResourceFileEntry]:
        normalized = normalize_resource_file_path(path)
        current = self.records.get(normalized)
        if current is None or current.entry.type != "directory":
            raise ResourceFileExecutionError("image directory was not found")
        rows = [
            indexed.entry
            for key, indexed in self.records.items()
            if key != normalized and (posixpath.dirname(key) or "/") == normalized
        ]
        return sorted(rows, key=_entry_sort_key)

    def read_file(self, path: str, *, offset: int, limit: int) -> tuple[bytes, bool, int]:
        normalized = normalize_resource_file_path(path)
        indexed = self.records.get(normalized)
        if (
            indexed is None
            or indexed.entry.type != "file"
            or indexed.layer_path is None
            or indexed.member_name is None
        ):
            raise ResourceFileExecutionError("image file was not found")
        total_size = indexed.entry.size
        if offset > total_size:
            raise ResourceFileExecutionError("resource file offset exceeds file size")
        with tarfile.open(indexed.layer_path, mode="r:*") as archive:
            member = archive.getmember(indexed.member_name)
            stream = archive.extractfile(member)
            if stream is None:
                raise ResourceFileExecutionError("image file content is unavailable")
            if offset:
                _discard_exact(stream, offset)
            content = stream.read(limit)
        next_offset = offset + len(content)
        return content, next_offset >= total_size, total_size


def parse_find_records(output: bytes, directory: str) -> list[ResourceFileEntry]:
    if output.startswith(b"__OPSIA_LS__\n"):
        return parse_ls_records(output.split(b"\n", 1)[1], directory)
    normalized_directory = normalize_resource_file_path(directory)
    records: list[ResourceFileEntry] = []
    for raw in output.split(b"\0"):
        if not raw:
            continue
        fields = raw.decode("utf-8", errors="replace").split("\t", 5)
        if len(fields) < 5:
            continue
        kind, size_text, modified, mode, value = fields[:5]
        link_target = fields[5] if len(fields) == 6 and fields[5] else None
        try:
            path = normalize_resource_file_path(value)
        except ValueError:
            continue
        if path == normalized_directory or (posixpath.dirname(path) or "/") != normalized_directory:
            continue
        entry_type = "directory" if kind == "d" else "symlink" if kind == "l" else "file"
        try:
            size = max(0, int(size_text))
        except ValueError:
            size = 0
        records.append(
            ResourceFileEntry(
                name=posixpath.basename(path),
                path=path,
                type=entry_type,
                size=min(size, MAX_RESOURCE_FILE_TOTAL_BYTES),
                permissions=_mode_string(int(mode, 8), entry_type) if mode.isdigit() else mode[:16],
                modified_at=_iso_timestamp(float(modified)) if modified else None,
                link_target=link_target,
            )
        )
        if len(records) > MAX_RESOURCE_FILE_LIST_RECORDS:
            raise ResourceFileExecutionError("Pod directory exceeds the bounded listing limit")
    return sorted(records, key=_entry_sort_key)


def parse_ls_records(output: bytes, directory: str) -> list[ResourceFileEntry]:
    normalized_directory = normalize_resource_file_path(directory)
    records: list[ResourceFileEntry] = []
    for line in output.decode("utf-8", errors="replace").splitlines():
        fields = line.split(maxsplit=8)
        if len(fields) < 9 or fields[0].casefold() == "total":
            continue
        permissions, raw_size, raw_name = fields[0], fields[4], fields[8]
        name, separator, link_target = raw_name.partition(" -> ")
        if name in {".", ".."}:
            continue
        path = normalize_resource_file_path(posixpath.join(normalized_directory, name))
        entry_type = (
            "directory"
            if permissions.startswith("d")
            else "symlink"
            if permissions.startswith("l")
            else "file"
        )
        try:
            size = max(0, int(raw_size))
        except ValueError:
            size = 0
        records.append(
            ResourceFileEntry(
                name=name,
                path=path,
                type=entry_type,
                size=min(size, MAX_RESOURCE_FILE_TOTAL_BYTES),
                permissions=permissions[:16],
                link_target=link_target if separator else None,
            )
        )
        if len(records) > MAX_RESOURCE_FILE_LIST_RECORDS:
            raise ResourceFileExecutionError("Pod directory exceeds the bounded listing limit")
    return sorted(records, key=_entry_sort_key)


def paginate_entries(
    entries: list[ResourceFileEntry], *, cursor: int, limit: int
) -> tuple[list[ResourceFileEntry], int | None]:
    if cursor < 0 or limit < 1 or limit > MAX_RESOURCE_FILE_PAGE_SIZE:
        raise ValueError("resource file page is out of bounds")
    page = entries[cursor : cursor + limit]
    next_cursor = cursor + len(page)
    return page, next_cursor if next_cursor < len(entries) else None


def pod_directory_list_command(path: str) -> str:
    normalized = normalize_resource_file_path(path)
    quoted = shlex.quote(normalized)
    return (
        f"(find {quoted} -mindepth 1 -maxdepth 1 "
        "-printf '%y\\t%s\\t%T@\\t%m\\t%p\\t%l\\0') 2>/dev/null || "
        f"{{ printf '__OPSIA_LS__\\n'; LC_ALL=C ls -lan {quoted}; }}"
    )


def pod_file_read_command(path: str, *, offset: int, limit: int) -> str:
    normalized = normalize_resource_file_path(path)
    if offset < 0 or limit < 1 or limit > MAX_RESOURCE_FILE_CHUNK_BYTES:
        raise ValueError("resource file byte range is out of bounds")
    quoted = shlex.quote(normalized)
    return (
        f"(dd if={quoted} bs=1 skip={offset} count={limit} 2>/dev/null) || "
        f"(tail -c +{offset + 1} {quoted} | head -c {limit})"
    )


class PodFileReader:
    def __init__(
        self,
        *,
        connector: ExecConnector | None = None,
        token_path: str = KUBERNETES_SERVICEACCOUNT_TOKEN_PATH,
        ca_cert_path: str = KUBERNETES_SERVICEACCOUNT_CA_CERT_PATH,
        base_url: str | None = None,
    ) -> None:
        self.connector = connector or kubernetes_exec_connector
        self.token_path = token_path
        self.ca_cert_path = ca_cert_path
        self.base_url = base_url

    async def list_directory(
        self,
        *,
        namespace: str,
        pod: str,
        container: str,
        path: str,
    ) -> list[ResourceFileEntry]:
        stdout = await self._exec(
            namespace=namespace,
            pod=pod,
            container=container,
            command=pod_directory_list_command(path),
            max_bytes=MAX_RESOURCE_FILE_LIST_BYTES,
        )
        return parse_find_records(stdout, path)

    async def read_file(
        self,
        *,
        namespace: str,
        pod: str,
        container: str,
        path: str,
        offset: int,
        limit: int,
    ) -> tuple[bytes, bool, int | None]:
        content = await self._exec(
            namespace=namespace,
            pod=pod,
            container=container,
            command=pod_file_read_command(path, offset=offset, limit=limit),
            max_bytes=limit,
        )
        # A short dd read proves EOF. Exact total size is known only on that final chunk.
        eof = len(content) < limit
        return content, eof, offset + len(content) if eof else None

    async def _exec(
        self,
        *,
        namespace: str,
        pod: str,
        container: str,
        command: str,
        max_bytes: int,
    ) -> bytes:
        stdout = bytearray()
        stderr = bytearray()
        async with self.connector(
            self._exec_url(namespace, pod, container, command),
            {"Authorization": f"Bearer {self._token()}"},
            self._ssl_context(),
        ) as connection:
            while True:
                raw = await connection.recv()
                frame = raw.encode("utf-8") if isinstance(raw, str) else raw
                if not frame:
                    continue
                channel, content = frame[0], frame[1:]
                if channel == STDOUT_CHANNEL:
                    stdout.extend(content)
                    if len(stdout) > max_bytes:
                        raise ResourceFileExecutionError(
                            "resource file output exceeds the byte limit"
                        )
                elif channel == STDERR_CHANNEL:
                    stderr.extend(content)
                    if len(stderr) > 8_192:
                        raise ResourceFileExecutionError(
                            "resource file command error exceeds the limit"
                        )
                elif channel == STATUS_CHANNEL:
                    exit_code = kubernetes_exit_code(content)
                    if exit_code not in (None, 0):
                        detail = stderr.decode("utf-8", errors="replace").strip()
                        raise ResourceFileExecutionError(
                            detail[:240] or "Pod filesystem command failed"
                        )
                    return bytes(stdout)

    def _exec_url(self, namespace: str, pod: str, container: str, command: str) -> str:
        base_url = (self.base_url or kubernetes_api_base_url() or "").rstrip("/")
        if not base_url:
            raise ResourceFileExecutionError("Kubernetes API is not configured", retryable=True)
        parsed = urlsplit(base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        query = urlencode(
            [
                ("container", container),
                ("command", "/bin/sh"),
                ("command", "-lc"),
                ("command", command),
                ("stdin", "false"),
                ("stdout", "true"),
                ("stderr", "true"),
                ("tty", "false"),
            ]
        )
        path = f"/api/v1/namespaces/{namespace}/pods/{pod}/exec"
        return urlunsplit((scheme, parsed.netloc, path, query, ""))

    def _token(self) -> str:
        token = (
            service_account_token()
            if self.token_path == KUBERNETES_SERVICEACCOUNT_TOKEN_PATH
            else Path(self.token_path).read_text(encoding="utf-8").strip()
        )
        if not token:
            raise ResourceFileExecutionError("Kubernetes service account token is unavailable")
        return token

    def _ssl_context(self) -> ssl.SSLContext:
        return ssl.create_default_context(cafile=self.ca_cert_path)


@dataclass(frozen=True)
class RegistryReference:
    registry: str
    repository: str
    reference: str

    @classmethod
    def parse(cls, image: str) -> RegistryReference:
        value = image.strip()
        if not value or any(character.isspace() for character in value):
            raise ResourceFileExecutionError("container image reference is invalid")
        digest_separator = value.rfind("@")
        if digest_separator >= 0:
            name, reference = value[:digest_separator], value[digest_separator + 1 :]
        else:
            last_slash = value.rfind("/")
            tag_separator = value.rfind(":")
            if tag_separator > last_slash:
                name, reference = value[:tag_separator], value[tag_separator + 1 :]
            else:
                name, reference = value, "latest"
        segments = name.split("/")
        explicit_registry = len(segments) > 1 and (
            "." in segments[0] or ":" in segments[0] or segments[0] == "localhost"
        )
        if explicit_registry:
            registry, repository = segments[0], "/".join(segments[1:])
        else:
            registry, repository = "registry-1.docker.io", name
            if "/" not in repository:
                repository = f"library/{repository}"
        if not registry or not repository or not reference:
            raise ResourceFileExecutionError("container image reference is invalid")
        return cls(registry=registry, repository=repository, reference=reference)


@dataclass(frozen=True)
class RegistryCredentials:
    username: str
    password: str


@dataclass(frozen=True)
class ImageManifest:
    image: str
    reference: RegistryReference
    digest: str
    platform: str
    total_size: int
    layers: tuple[JsonObject, ...]
    auth_method: str

    @property
    def artifact_id(self) -> str:
        identity = f"{self.reference.registry}/{self.reference.repository}@{self.digest}"
        return f"{OCI_ARTIFACT_PREFIX}{hashlib.sha256(identity.encode()).hexdigest()}"


class OciRegistryClient:
    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def manifest(
        self,
        image: str,
        *,
        credentials: RegistryCredentials | None = None,
    ) -> ImageManifest:
        reference = RegistryReference.parse(image)
        client = self._client or httpx.AsyncClient(
            timeout=float(
                env(
                    RESOURCE_FILES_REGISTRY_TIMEOUT_SECONDS_ENV,
                    str(DEFAULT_RESOURCE_FILES_REGISTRY_TIMEOUT_SECONDS),
                )
            ),
            follow_redirects=True,
        )
        owns_client = self._client is None
        try:
            manifest, headers, auth_method = await self._manifest_request(
                client, reference, reference.reference, credentials
            )
            media_type = str(manifest.get("mediaType") or headers.get("content-type") or "")
            if media_type.split(";", 1)[0] in OCI_INDEX_TYPES:
                descriptor = _select_platform_manifest(manifest)
                digest = str(descriptor.get("digest") or "")
                manifest, headers, auth_method = await self._manifest_request(
                    client, reference, digest, credentials
                )
            digest = str(headers.get("docker-content-digest") or "")
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                encoded = json.dumps(
                    manifest, ensure_ascii=True, separators=(",", ":"), sort_keys=True
                ).encode()
                digest = f"sha256:{hashlib.sha256(encoded).hexdigest()}"
            layers = manifest.get("layers")
            if not isinstance(layers, list) or len(layers) > MAX_RESOURCE_IMAGE_LAYERS:
                raise ResourceFileExecutionError("image manifest layer count is invalid")
            normalized_layers = tuple(dict(item) for item in layers if isinstance(item, dict))
            if len(normalized_layers) != len(layers):
                raise ResourceFileExecutionError("image manifest contains invalid layers")
            total_size = sum(max(0, int(item.get("size") or 0)) for item in normalized_layers)
            if total_size > MAX_RESOURCE_FILE_TOTAL_BYTES:
                raise ResourceFileExecutionError("image layers exceed the configured byte limit")
            config = manifest.get("config")
            config_descriptor = config if isinstance(config, dict) else {}
            image_platform = await self._config_platform(
                client, reference, str(config_descriptor.get("digest") or ""), credentials
            )
            return ImageManifest(
                image=image,
                reference=reference,
                digest=digest,
                platform=image_platform,
                total_size=total_size,
                layers=normalized_layers,
                auth_method=auth_method,
            )
        finally:
            if owns_client:
                await client.aclose()

    async def download_layers(
        self,
        manifest: ImageManifest,
        destination: Path,
        *,
        credentials: RegistryCredentials | None = None,
    ) -> tuple[Path, ...]:
        destination.mkdir(parents=True, exist_ok=True)
        client = self._client or httpx.AsyncClient(
            timeout=float(
                env(
                    RESOURCE_FILES_REGISTRY_TIMEOUT_SECONDS_ENV,
                    str(DEFAULT_RESOURCE_FILES_REGISTRY_TIMEOUT_SECONDS),
                )
            ),
            follow_redirects=True,
        )
        owns_client = self._client is None
        total = 0
        paths: list[Path] = []
        try:
            for index, descriptor in enumerate(manifest.layers):
                digest = str(descriptor.get("digest") or "")
                if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                    raise ResourceFileExecutionError("image layer digest is invalid")
                response, _auth_method = await self._authorized_request(
                    client,
                    "GET",
                    _registry_url(manifest.reference, f"blobs/{digest}"),
                    manifest.reference,
                    credentials,
                    stream=True,
                )
                try:
                    response.raise_for_status()
                    path = destination / f"layer-{index}.tar"
                    hasher = hashlib.sha256()
                    with path.open("wb") as output:
                        async for chunk in response.aiter_bytes():
                            total += len(chunk)
                            if total > MAX_RESOURCE_FILE_TOTAL_BYTES:
                                raise ResourceFileExecutionError(
                                    "image layers exceed the configured byte limit"
                                )
                            hasher.update(chunk)
                            output.write(chunk)
                finally:
                    await response.aclose()
                if f"sha256:{hasher.hexdigest()}" != digest:
                    raise ResourceFileExecutionError("downloaded image layer digest mismatch")
                paths.append(path)
            return tuple(paths)
        finally:
            if owns_client:
                await client.aclose()

    async def _manifest_request(
        self,
        client: httpx.AsyncClient,
        reference: RegistryReference,
        manifest_ref: str,
        credentials: RegistryCredentials | None,
    ) -> tuple[JsonObject, httpx.Headers, str]:
        response, auth_method = await self._authorized_request(
            client,
            "GET",
            _registry_url(reference, f"manifests/{manifest_ref}"),
            reference,
            credentials,
            headers={"Accept": OCI_MANIFEST_ACCEPT},
            stream=True,
        )
        try:
            if response.status_code in {401, 403}:
                raise ResourceFileExecutionError("image registry authentication failed")
            response.raise_for_status()
            body = await _bounded_json(response, MAX_RESOURCE_IMAGE_MANIFEST_BYTES)
            if not isinstance(body, dict):
                raise ResourceFileExecutionError("image manifest response is invalid")
            return body, httpx.Headers(response.headers), auth_method
        finally:
            await response.aclose()

    async def _authorized_request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        reference: RegistryReference,
        credentials: RegistryCredentials | None,
        *,
        headers: Mapping[str, str] | None = None,
        stream: bool = False,
    ) -> tuple[httpx.Response, str]:
        auth = (
            httpx.BasicAuth(credentials.username, credentials.password)
            if credentials is not None
            else None
        )
        response = await client.send(
            client.build_request(method, url, headers=headers, auth=auth),
            stream=stream,
        )
        challenge = response.headers.get("www-authenticate", "")
        if response.status_code != 401 or not challenge.lower().startswith("bearer "):
            return response, "pull-secret" if credentials is not None else "anonymous"
        parameters = _bearer_parameters(challenge)
        realm = parameters.get("realm")
        if not realm:
            return response, "pull-secret" if credentials is not None else "anonymous"
        await response.aclose()
        token_response = await client.get(
            realm,
            params={
                "service": parameters.get("service", ""),
                "scope": parameters.get("scope", f"repository:{reference.repository}:pull"),
            },
            auth=auth,
        )
        token_response.raise_for_status()
        token_body = token_response.json()
        token = token_body.get("token") or token_body.get("access_token")
        if not isinstance(token, str) or not token:
            raise ResourceFileExecutionError("image registry token response is invalid")
        return (
            await client.send(
                client.build_request(
                    method,
                    url,
                    headers={**dict(headers or {}), "Authorization": f"Bearer {token}"},
                ),
                stream=stream,
            ),
            "pull-secret" if credentials is not None else "anonymous",
        )

    async def _config_platform(
        self,
        client: httpx.AsyncClient,
        reference: RegistryReference,
        digest: str,
        credentials: RegistryCredentials | None,
    ) -> str:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            return _local_platform()
        response, _auth_method = await self._authorized_request(
            client,
            "GET",
            _registry_url(reference, f"blobs/{digest}"),
            reference,
            credentials,
            stream=True,
        )
        try:
            if not response.is_success:
                return _local_platform()
            body = await _bounded_json(response, MAX_RESOURCE_IMAGE_CONFIG_BYTES)
            if not isinstance(body, dict):
                return _local_platform()
            os_name = str(body.get("os") or "linux")
            architecture = str(body.get("architecture") or platform.machine())
            return f"{os_name}/{_canonical_architecture(architecture)}"
        finally:
            await response.aclose()


class ImageArtifactCache:
    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or Path(
            env(RESOURCE_FILES_CACHE_DIR_ENV, DEFAULT_RESOURCE_FILES_CACHE_DIR)
        )
        self.ttl_seconds = max(
            30,
            int(
                env(
                    RESOURCE_FILES_CACHE_TTL_SECONDS_ENV,
                    str(DEFAULT_RESOURCE_FILES_CACHE_TTL_SECONDS),
                )
            ),
        )
        self.max_images = max(
            1,
            int(
                env(
                    RESOURCE_FILES_MAX_CACHED_IMAGES_ENV,
                    str(DEFAULT_RESOURCE_FILES_MAX_CACHED_IMAGES),
                )
            ),
        )
        self.lock = asyncio.Lock()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def is_cached(self, artifact_id: str) -> bool:
        try:
            self._metadata(artifact_id)
            return True
        except ResourceFileExecutionError:
            return False

    async def ensure(
        self,
        manifest: ImageManifest,
        registry: OciRegistryClient,
        *,
        credentials: RegistryCredentials | None,
    ) -> tuple[ImageLayerIndex, bool]:
        async with self.lock:
            try:
                return self.load(manifest.artifact_id), True
            except ResourceFileExecutionError:
                pass
            self._evict()
            target = self._artifact_path(manifest.artifact_id)
            temporary = self.cache_dir / f".{manifest.artifact_id}-{os.getpid()}-{time.time_ns()}"
            try:
                layers = await registry.download_layers(
                    manifest, temporary / "layers", credentials=credentials
                )
                index = ImageLayerIndex.from_layers(layers)
                metadata = {
                    "artifact_id": manifest.artifact_id,
                    "image": manifest.image,
                    "digest": manifest.digest,
                    "platform": manifest.platform,
                    "created_at": time.time(),
                    "layers": [path.name for path in layers],
                }
                (temporary / "metadata.json").write_text(
                    json.dumps(metadata, ensure_ascii=True, separators=(",", ":")),
                    encoding="utf-8",
                )
                if target.exists():
                    _remove_tree(target)
                temporary.rename(target)
                return index, False
            except Exception:
                _remove_tree(temporary)
                raise

    def load(self, artifact_id: str) -> ImageLayerIndex:
        metadata = self._metadata(artifact_id)
        base = self._artifact_path(artifact_id) / "layers"
        layer_names = metadata.get("layers")
        if not isinstance(layer_names, list):
            raise ResourceFileExecutionError("image artifact metadata is invalid", retryable=True)
        paths = tuple(base / str(name) for name in layer_names)
        if not paths or any(not path.is_file() for path in paths):
            raise ResourceFileExecutionError(
                "image artifact layers are unavailable", retryable=True
            )
        return ImageLayerIndex.from_layers(paths)

    def _metadata(self, artifact_id: str) -> JsonObject:
        path = self._artifact_path(artifact_id) / "metadata.json"
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ResourceFileExecutionError(
                "image artifact is unavailable", retryable=True
            ) from exc
        if not isinstance(body, dict) or body.get("artifact_id") != artifact_id:
            raise ResourceFileExecutionError("image artifact identity is invalid")
        created_at = body.get("created_at")
        if not isinstance(created_at, (int, float)) or time.time() - created_at >= self.ttl_seconds:
            _remove_tree(path.parent)
            raise ResourceFileExecutionError("image artifact expired", retryable=True)
        return body

    def _artifact_path(self, artifact_id: str) -> Path:
        if not re.fullmatch(r"artifact-[0-9a-f]{64}", artifact_id):
            raise ResourceFileExecutionError("image artifact handle is invalid")
        return self.cache_dir / artifact_id

    def _evict(self) -> None:
        artifacts: list[tuple[float, Path]] = []
        for child in self.cache_dir.iterdir():
            if not child.is_dir() or not child.name.startswith(OCI_ARTIFACT_PREFIX):
                continue
            try:
                created = (child / "metadata.json").stat().st_mtime
            except OSError:
                created = 0.0
            artifacts.append((created, child))
        for _created, path in sorted(artifacts)[: max(0, len(artifacts) - self.max_images + 1)]:
            _remove_tree(path)


class ResourceFileExecutor:
    def __init__(
        self,
        *,
        pod_reader: PodFileReader | None = None,
        registry: OciRegistryClient | None = None,
        cache: ImageArtifactCache | None = None,
    ) -> None:
        self.pod_reader = pod_reader or PodFileReader()
        self.registry = registry or OciRegistryClient()
        self.cache = cache or ImageArtifactCache()

    async def execute(
        self,
        ctx: CommandContext[ResourceFileCommandPayload],
    ) -> JsonObject:
        payload = ctx.payload
        try:
            pod = await ctx.kubernetes.get_namespaced_resource(
                api_group="",
                version="v1",
                namespace=payload.resource.namespace or "",
                resource="pods",
                name=payload.resource.name,
            )
            validate_exact_resource(pod, payload.resource, payload.pod_resource_version)
            image = _container_image(pod, payload.container or "")
            if payload.operation.startswith("image."):
                result = await self._image(payload, pod, image, ctx.kubernetes)
            else:
                result = await self._pod(payload)
            return ctx.ok(
                "resource filesystem read completed",
                resource_file=result.model_dump(mode="json"),
            )
        except ResourceFileExecutionError as exc:
            return ctx.fail(str(exc), retryable=exc.retryable)
        except (httpx.HTTPError, OSError, tarfile.TarError, ValueError) as exc:
            return ctx.fail(str(exc)[:240], retryable=isinstance(exc, httpx.TransportError))

    async def _image(
        self,
        payload: ResourceFileCommandPayload,
        pod: JsonObject,
        image: str,
        kubernetes: KubernetesClient,
    ) -> ResourceImageMetadataResult | ResourceFileDirectoryResult | ResourceFileReadResult:
        credentials = await _registry_credentials(kubernetes, payload, pod, image)
        manifest = await self.registry.manifest(image, credentials=credentials)
        if payload.artifact_id is not None and payload.artifact_id != manifest.artifact_id:
            raise ResourceFileExecutionError("image artifact no longer matches the Pod image")
        if payload.operation == "image.metadata":
            return ResourceImageMetadataResult(
                image=image,
                digest=manifest.digest,
                platform=manifest.platform,
                total_size=manifest.total_size,
                layer_count=len(manifest.layers),
                cached=self.cache.is_cached(manifest.artifact_id),
                artifact_id=manifest.artifact_id,
                auth_method=(
                    "cached" if self.cache.is_cached(manifest.artifact_id) else manifest.auth_method
                ),
            )
        if payload.operation == "image.list":
            index, _cached = await self.cache.ensure(
                manifest, self.registry, credentials=credentials
            )
            entries = index.list_directory(payload.path or "/")
            page, next_cursor = paginate_entries(
                entries, cursor=payload.cursor or 0, limit=payload.limit or 1
            )
            return ResourceFileDirectoryResult(
                operation="image.list",
                path=payload.path or "/",
                entries=tuple(page),
                cursor=payload.cursor or 0,
                next_cursor=next_cursor,
                total_entries=len(entries),
                truncated=next_cursor is not None,
                artifact_id=manifest.artifact_id,
            )
        index = self.cache.load(manifest.artifact_id)
        content, eof, total = index.read_file(
            payload.path or "/", offset=payload.offset or 0, limit=payload.limit or 1
        )
        return ResourceFileReadResult.from_bytes(
            operation="image.read",
            path=payload.path or "/",
            offset=payload.offset or 0,
            content=content,
            eof=eof,
            total_size=total,
            artifact_id=manifest.artifact_id,
        )

    async def _pod(
        self,
        payload: ResourceFileCommandPayload,
    ) -> ResourceFileDirectoryResult | ResourceFileReadResult:
        target = {
            "namespace": payload.resource.namespace or "",
            "pod": payload.resource.name,
            "container": payload.container or "",
            "path": payload.path or "/",
        }
        if payload.operation == "pod.list":
            entries = await self.pod_reader.list_directory(**target)
            page, next_cursor = paginate_entries(
                entries, cursor=payload.cursor or 0, limit=payload.limit or 1
            )
            return ResourceFileDirectoryResult(
                operation="pod.list",
                path=payload.path or "/",
                entries=tuple(page),
                cursor=payload.cursor or 0,
                next_cursor=next_cursor,
                total_entries=len(entries),
                truncated=next_cursor is not None,
            )
        content, eof, total = await self.pod_reader.read_file(
            **target,
            offset=payload.offset or 0,
            limit=payload.limit or 1,
        )
        return ResourceFileReadResult.from_bytes(
            operation="pod.read",
            path=payload.path or "/",
            offset=payload.offset or 0,
            content=content,
            eof=eof,
            total_size=total,
        )


async def _registry_credentials(
    kubernetes: KubernetesClient,
    payload: ResourceFileCommandPayload,
    pod: JsonObject,
    image: str,
) -> RegistryCredentials | None:
    spec = pod.get("spec")
    spec_body = spec if isinstance(spec, dict) else {}
    pull_secrets = spec_body.get("imagePullSecrets")
    registry = RegistryReference.parse(image).registry
    for item in pull_secrets if isinstance(pull_secrets, list) else []:
        name = item.get("name") if isinstance(item, dict) else None
        if not isinstance(name, str) or not name:
            continue
        secret = await kubernetes.get_namespaced_resource(
            api_group="",
            version="v1",
            namespace=payload.resource.namespace or "",
            resource="secrets",
            name=name,
        )
        credentials = _docker_config_credentials(secret, registry)
        if credentials is not None:
            return credentials
    return None


def _docker_config_credentials(
    secret: Mapping[str, object], registry: str
) -> RegistryCredentials | None:
    data = secret.get("data")
    encoded = data.get(".dockerconfigjson") if isinstance(data, dict) else None
    if not isinstance(encoded, str) or not encoded:
        return None
    try:
        config = json.loads(base64.b64decode(encoded, validate=True))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    auths = config.get("auths") if isinstance(config, dict) else None
    for authority, raw in auths.items() if isinstance(auths, dict) else ():
        if not isinstance(authority, str) or not isinstance(raw, dict):
            continue
        if not _registry_authority_matches(authority, registry):
            continue
        username = raw.get("username")
        password = raw.get("password")
        if isinstance(username, str) and isinstance(password, str):
            return RegistryCredentials(username=username, password=password)
        auth = raw.get("auth")
        if not isinstance(auth, str):
            continue
        try:
            decoded = base64.b64decode(auth, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        username, separator, password = decoded.partition(":")
        if separator and username:
            return RegistryCredentials(username=username, password=password)
    return None


def _registry_authority_matches(authority: str, registry: str) -> bool:
    value = authority.strip().rstrip("/")
    parsed = urlsplit(value if "://" in value else f"https://{value}")
    host = (parsed.netloc or parsed.path.split("/", 1)[0]).casefold()
    expected = registry.casefold()
    if {host, expected} <= {"index.docker.io", "registry-1.docker.io"}:
        return True
    return host == expected


def _container_image(pod: Mapping[str, object], container: str) -> str:
    spec = pod.get("spec")
    spec_body = spec if isinstance(spec, dict) else {}
    for key in ("containers", "initContainers", "ephemeralContainers"):
        rows = spec_body.get(key)
        for item in rows if isinstance(rows, list) else []:
            if isinstance(item, dict) and item.get("name") == container:
                image = item.get("image")
                if isinstance(image, str) and image:
                    return image
    raise ResourceFileExecutionError("selected Pod container image is unavailable")


def _safe_archive_path(name: str) -> str | None:
    value = name.replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    if not value or value.startswith("/"):
        return None
    segments = [segment for segment in value.split("/") if segment not in ("", ".")]
    if not segments or any(segment == ".." for segment in segments):
        return None
    try:
        return normalize_resource_file_path("/" + "/".join(segments))
    except ValueError:
        return None


def _ensure_parent_directories(records: dict[str, IndexedFile], path: str) -> None:
    parent = posixpath.dirname(path) or "/"
    missing: list[str] = []
    while parent not in records and parent != "/":
        missing.append(parent)
        parent = posixpath.dirname(parent) or "/"
    for directory in reversed(missing):
        records[directory] = IndexedFile(
            ResourceFileEntry(
                name=posixpath.basename(directory),
                path=directory,
                type="directory",
                permissions="drwxr-xr-x",
            ),
            None,
            None,
        )


def _remove_path(records: dict[str, IndexedFile], path: str) -> None:
    records.pop(path, None)
    _remove_descendants(records, path)


def _remove_descendants(records: dict[str, IndexedFile], path: str) -> None:
    prefix = path.rstrip("/") + "/"
    for key in tuple(records):
        if key.startswith(prefix):
            records.pop(key, None)


def _entry_sort_key(entry: ResourceFileEntry) -> tuple[int, str, str]:
    rank = 0 if entry.type == "directory" else 1 if entry.type == "symlink" else 2
    return rank, entry.name.casefold(), entry.name


def _mode_string(mode: int, entry_type: str) -> str:
    prefix = "d" if entry_type == "directory" else "l" if entry_type == "symlink" else "-"
    bits = ""
    for shift in (6, 3, 0):
        value = (mode >> shift) & 0o7
        bits += "r" if value & 0o4 else "-"
        bits += "w" if value & 0o2 else "-"
        bits += "x" if value & 0o1 else "-"
    return prefix + bits


def _iso_timestamp(value: float | int) -> str | None:
    try:
        return datetime.fromtimestamp(float(value), tz=UTC).isoformat().replace("+00:00", "Z")
    except (OSError, OverflowError, ValueError):
        return None


def _discard_exact(stream: Any, length: int) -> None:
    remaining = length
    while remaining:
        chunk = stream.read(min(remaining, 64 * 1_024))
        if not chunk:
            raise ResourceFileExecutionError("resource file offset exceeds file size")
        remaining -= len(chunk)


def _registry_url(reference: RegistryReference, suffix: str) -> str:
    return f"https://{reference.registry}/v2/{reference.repository}/{suffix}"


async def _bounded_json(response: httpx.Response, limit: int) -> object:
    content = bytearray()
    async for chunk in response.aiter_bytes():
        content.extend(chunk)
        if len(content) > limit:
            raise ResourceFileExecutionError("image registry response exceeds the byte limit")
    try:
        return json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ResourceFileExecutionError("image registry response is invalid") from exc


def _bearer_parameters(challenge: str) -> dict[str, str]:
    value = challenge.split(" ", 1)[1] if " " in challenge else ""
    return {
        match.group(1).lower(): match.group(2) for match in re.finditer(r'(\w+)="([^"]*)"', value)
    }


def _local_platform() -> str:
    return f"linux/{_canonical_architecture(platform.machine())}"


def _canonical_architecture(value: str) -> str:
    normalized = value.casefold()
    return {"x86_64": "amd64", "aarch64": "arm64"}.get(normalized, normalized)


def _select_platform_manifest(index: Mapping[str, object]) -> JsonObject:
    manifests = index.get("manifests")
    rows = manifests if isinstance(manifests, list) else []
    local_os, local_arch = _local_platform().split("/", 1)
    for row in rows:
        if not isinstance(row, dict):
            continue
        descriptor_platform = row.get("platform")
        descriptor = descriptor_platform if isinstance(descriptor_platform, dict) else {}
        if (
            str(descriptor.get("os") or "") == local_os
            and _canonical_architecture(str(descriptor.get("architecture") or "")) == local_arch
        ):
            return dict(row)
    raise ResourceFileExecutionError("image does not contain a compatible platform manifest")


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_symlink() or child.is_file():
            child.unlink(missing_ok=True)
        elif child.is_dir():
            child.rmdir()
    path.rmdir()
