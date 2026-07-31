"""github-poll-worker — GitHub 주기 polling, 새 commit을 webhook 입구로 전달

GitOps 컨트롤러류와 같은 방향: "polling 기본 + webhook 가속(옵션)".
외부 endpoint를 못 여는 환경이나 webhook 누락 보정용 polling.

cluster-agent와 같은 timer producer 형태: 주기마다 외부 호출 후
api-gateway의 /github/webhook으로 POST. 이후 경로는 webhook과 동일
(outbox → NATS → git-pull-worker → pipeline).

현재 구현은 최신 commit 1건 조회, 같은 commit 반복은 메모리 가드와
ledger dedup으로 흡수. ETag/cursor 기반 incremental 조회는 provider
adapter 내부 최적화로 추가 가능.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import random
import time
from dataclasses import dataclass
from typing import Any

import httpx
import psycopg
from settings import Settings

from domains.scm.github_app import GithubAppNotConfigured
from domains.scm.github_app_credentials import (
    is_app_installation_ref,
    parse_app_installation_ref,
    resolve_installation_token_sync,
)
from packages.config.logs import CONTEXT_KEY, get_logger
from packages.config.settings import env
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.gitops import PUBLIC_GITHUB_CREDENTIAL_REF
from packages.contracts.security import SecretRef
from packages.security import SecretNotFound, build_token_vault
from packages.security.credentials import (
    CredentialEncryptionError,
    decrypt_credential,
    parse_credential_ref,
)

LOGGER = get_logger(__name__)
TRUTHY_VALUES = {"1", "true", "yes", "on"}


def env_truthy(name: str) -> bool:
    return env(name, "").strip().lower() in TRUTHY_VALUES


def gitops_correlation_id(target: GitHubPollTarget, commit_sha: str) -> str:
    raw = "|".join(
        [
            target.workspace_id,
            target.repository_id,
            target.repo_ref,
            target.branch,
            target.watch_target_id,
            target.binding_id,
            commit_sha,
        ]
    )
    return f"gitops-{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


@dataclass(frozen=True)
class GitHubPollTarget:
    workspace_id: str
    repository_id: str
    repo_ref: str
    branch: str
    watch_target_id: str
    binding_id: str
    application_id: str
    environment: str
    cluster_id: str
    manifest_path: str
    source_type: str = ""
    credential_ref: str = ""
    database_managed: bool = False

    @property
    def key(self) -> str:
        return "|".join(
            (
                self.workspace_id,
                self.repository_id,
                self.watch_target_id,
                self.binding_id,
                self.application_id,
                self.environment,
                self.repo_ref,
                self.branch,
                self.manifest_path,
                self.source_type,
            )
        )

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> GitHubPollTarget:
        return cls(
            workspace_id=str(row.get("workspace_id") or Settings.DEFAULT_WORKSPACE_ID),
            repository_id=str(row.get("repository_id") or ""),
            repo_ref=str(row.get("repo_ref") or ""),
            branch=str(row.get("branch") or Settings.DEFAULT_GITHUB_BRANCH),
            watch_target_id=str(row.get("watch_target_id") or ""),
            binding_id=str(row.get("binding_id") or ""),
            application_id=str(row.get("application_id") or Settings.DEFAULT_APPLICATION_ID),
            environment=str(row.get("environment") or Settings.DEFAULT_ENVIRONMENT),
            cluster_id=str(row.get("cluster_id") or Settings.DEFAULT_TARGET_CLUSTER_ID),
            manifest_path=str(row.get("manifest_path") or Settings.DEFAULT_MANIFEST_PATH),
            source_type=str(row.get("source_type") or ""),
            credential_ref=str(row.get("credential_ref") or ""),
            database_managed=True,
        )


class GitHubPoller:
    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        db: Any | None = None,
        token_vault: Any | None = None,
    ) -> None:
        self.base_url = env(
            Settings.MANAGEMENT_BASE_URL_ENV, Settings.DEFAULT_MANAGEMENT_BASE_URL
        ).rstrip("/")
        self.repo = env(Settings.GITHUB_REPO_ENV, Settings.DEFAULT_GITHUB_REPO)
        self.branch = env(Settings.GITHUB_BRANCH_ENV, Settings.DEFAULT_GITHUB_BRANCH)
        self.workspace_id = env(Settings.WORKSPACE_ID_ENV, Settings.DEFAULT_WORKSPACE_ID)
        self.repository_id = env(Settings.REPOSITORY_ID_ENV, Settings.DEFAULT_REPOSITORY_ID)
        self.watch_target_id = env(Settings.WATCH_TARGET_ID_ENV, Settings.DEFAULT_WATCH_TARGET_ID)
        self.binding_id = env(
            Settings.DEPLOYMENT_BINDING_ID_ENV,
            Settings.DEFAULT_DEPLOYMENT_BINDING_ID,
        )
        self.cluster_id = env(Settings.TARGET_CLUSTER_ID_ENV, Settings.DEFAULT_TARGET_CLUSTER_ID)
        self.manifest_path = env(Settings.MANIFEST_PATH_ENV, Settings.DEFAULT_MANIFEST_PATH)
        self.source_type = env(Settings.MANIFEST_SOURCE_TYPE_ENV, "")
        self.interval = int(env(Settings.POLL_INTERVAL_ENV, Settings.DEFAULT_POLL_INTERVAL_SECONDS))
        self.token_ref = env(Settings.GITHUB_TOKEN_REF_ENV, "").strip()
        self.token = env(Settings.GITHUB_TOKEN_ENV, "")
        self.github_api_base = env(
            Settings.GITHUB_API_BASE_ENV, Settings.DEFAULT_GITHUB_API_BASE
        ).rstrip("/")
        self.webhook_secret = env(Settings.WEBHOOK_SECRET_ENV, "")  # webhook 입구 HMAC 서명 키.
        self.image = env(Settings.WEBHOOK_IMAGE_ENV, Settings.DEFAULT_IMAGE)
        self.once = env_truthy(Settings.POLL_ONCE_ENV)  # CronJob 호환 모드면 1회 후 종료.
        self._client = client
        self.db = db
        self.token_vault = token_vault or build_token_vault()
        self._last_sha_by_target: dict[str, str] = {}
        # 직접 커밋 웨이크업 — NOTIFY 수신 시 set 되어 유휴 대기를 즉시 깨운다.
        self._burst_wake = asyncio.Event()
        self._notify_task: asyncio.Task | None = None
        self.notify_url = env(Settings.NOTIFY_DATABASE_URL_ENV, "").strip()
        self.burst_interval = float(
            env(Settings.BURST_POLL_INTERVAL_SECONDS_ENV, Settings.DEFAULT_BURST_POLL_INTERVAL_SECONDS)
        )
        self.burst_window = float(
            env(Settings.BURST_POLL_WINDOW_SECONDS_ENV, Settings.DEFAULT_BURST_POLL_WINDOW_SECONDS)
        )
        # ETag 조건부 요청 — 변경 없으면 304 로 응답받아 GitHub rate limit 을 소모하지 않음
        # (SCM provider 를 압박하지 않는 폴링 원칙).
        self._etag_by_target: dict[str, str] = {}

    async def run(self) -> None:
        if self._client is not None:
            await self.drive(self._client)
            return
        async with httpx.AsyncClient(timeout=Settings.HTTP_TIMEOUT_SECONDS) as client:
            await self.drive(client)

    async def drive(self, client: httpx.AsyncClient) -> None:
        # 기본 배포: Deployment 1개가 interval 루프를 돈다. POLL_ONCE 는 CronJob 호환 진입점이다.
        if self.once:
            await self.poll_once_with_retry(client)
            return
        if self.notify_url:
            self._notify_task = asyncio.create_task(self._listen_direct_commits())
        try:
            await self.loop(client)
        finally:
            if self._notify_task is not None:
                self._notify_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._notify_task

    async def poll_once_with_retry(self, client: httpx.AsyncClient) -> None:
        # once 모드는 곧 프로세스가 끝나므로 일시 네트워크 오류만 짧게 자체 재시도.
        max_attempts = max(1, Settings.POLL_ONCE_MAX_ATTEMPTS)
        for attempt in range(1, max_attempts + 1):
            try:
                await self.poll_once(client)
                return
            except Exception as exc:
                if attempt >= max_attempts or not self.is_transient_poll_error(exc):
                    raise
                backoff = self.retry_backoff_seconds(attempt)
                LOGGER.warning(
                    "github_poll_retrying",
                    extra={
                        CONTEXT_KEY: {
                            "exception_type": type(exc).__name__,
                            "status_code": self.http_status_code(exc),
                            "attempt": attempt,
                            "max_attempts": max_attempts,
                            "backoff_seconds": round(backoff, 1),
                        }
                    },
                )
                await asyncio.sleep(backoff)

    async def loop(self, client: httpx.AsyncClient) -> None:
        failures = 0
        while True:
            try:
                await self.poll_once(client)
            except Exception as exc:
                failures += 1
                # 지수 백오프(+지터, 상한) — 연속 실패 시 GitHub/게이트웨이를 두드리지 않게.
                backoff = self.retry_backoff_seconds(failures)
                LOGGER.warning(
                    "github_poll_failed",
                    extra={
                        CONTEXT_KEY: {
                            "exception_type": type(exc).__name__,
                            "failures": failures,
                            "backoff_seconds": round(backoff, 1),
                        }
                    },
                )
                await asyncio.sleep(backoff)
                continue
            failures = 0  # 성공 → 백오프 리셋
            await self._idle_until_next_cycle(client)

    async def _idle_until_next_cycle(self, client: httpx.AsyncClient) -> None:
        """주기 대기 — 직접 커밋 알림이 오면 즉시 깨어나 버스트 폴링으로 전환."""
        try:
            await asyncio.wait_for(self._burst_wake.wait(), timeout=self.interval)
        except TimeoutError:
            return  # 일반 주기 도래
        self._burst_wake.clear()
        await self.burst_poll(client)

    async def burst_poll(self, client: httpx.AsyncClient) -> None:
        """직접 커밋 직후의 특수 구간 — 창(기본 30초) 동안 짧은 간격(기본 0.5초)으로
        조건부(ETag) 폴링해 우리 스스로 만든 커밋을 즉시 감지한다. 새 커밋을
        반영한 순간 종료하고, 실패하면 일반 주기 루프의 백오프에 맡긴다."""
        deadline = time.monotonic() + self.burst_window
        LOGGER.info(
            "github_burst_poll_started",
            extra={CONTEXT_KEY: {
                "interval_seconds": self.burst_interval,
                "window_seconds": self.burst_window,
            }},
        )
        while time.monotonic() < deadline:
            seen_before = dict(self._last_sha_by_target)
            try:
                await self.poll_once(client)
            except Exception as exc:
                LOGGER.warning(
                    "github_burst_poll_failed",
                    extra={CONTEXT_KEY: {"exception_type": type(exc).__name__}},
                )
                return  # 일반 루프 주기·백오프로 복귀(fail-open)
            if self._last_sha_by_target != seen_before:
                LOGGER.info("github_burst_poll_detected")
                return  # 목적 달성 — 새 커밋 감지·전달 완료
            self._burst_wake.clear()  # 창 내 추가 알림은 현재 버스트가 흡수
            await asyncio.sleep(self.burst_interval)
        LOGGER.info("github_burst_poll_window_elapsed")

    async def _listen_direct_commits(self) -> None:
        """scm-worker 의 pg_notify(direct commit) 수신 루프 — command_wakeup 과 동일한
        fail-open 원칙: 리스너 장애는 경고 후 재접속만 시도, 폴링 정확성엔 영향 없음."""
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(
                    self.notify_url, autocommit=True
                ) as conn:
                    await conn.execute(f"LISTEN {Settings.DIRECT_COMMIT_NOTIFY_CHANNEL}")
                    LOGGER.info("github_direct_commit_listening")
                    async for notification in conn.notifies():
                        LOGGER.info(
                            "github_direct_commit_notified",
                            extra={CONTEXT_KEY: {"payload": str(notification.payload)[:200]}},
                        )
                        self._burst_wake.set()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.warning(
                    "github_direct_commit_listener_failed",
                    extra={CONTEXT_KEY: {"exception_type": type(exc).__name__}},
                )
                await asyncio.sleep(5)

    @staticmethod
    def retry_backoff_seconds(failures: int) -> float:
        backoff = min(
            Settings.POLL_RETRY_DELAY_SECONDS * (2 ** (failures - 1)),
            Settings.POLL_MAX_BACKOFF_SECONDS,
        )
        return backoff + random.uniform(0, Settings.POLL_BACKOFF_JITTER_SECONDS)

    @staticmethod
    def is_transient_poll_error(exc: Exception) -> bool:
        if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in Settings.TRANSIENT_RETRY_STATUS_CODES
        return False

    @staticmethod
    def http_status_code(exc: Exception) -> int | None:
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code
        return None

    @staticmethod
    def is_target_status_error(exc: Exception) -> bool:
        if not isinstance(exc, httpx.HTTPStatusError):
            return False
        return exc.response.status_code in (
            Settings.ACCESS_ERROR_STATUS_CODES | Settings.RATE_LIMIT_STATUS_CODES
        )

    @staticmethod
    def github_error_kind(status_code: int) -> str:
        if status_code in Settings.RATE_LIMIT_STATUS_CODES:
            return "rate_limited"
        return "access_denied"

    async def poll_once(self, client: httpx.AsyncClient) -> None:
        target_errors: list[httpx.HTTPStatusError] = []
        for target in self.poll_targets():
            try:
                commit_sha = await self.latest_commit_sha(client, target)
            except httpx.HTTPStatusError as exc:
                if not self.is_target_status_error(exc):
                    raise
                target_errors.append(exc)
                self.record_poll_result(target, ok=False, exc=exc)
                self.log_target_status_error(target, exc)
                continue
            self.record_poll_result(target, ok=True)
            if commit_sha is None or commit_sha == self._last_sha_by_target.get(target.key):
                continue  # 새 커밋 없음 → webhook 안 쏨(dedup 은 ledger 가 최종 보장).
            correlation_id = gitops_correlation_id(target, commit_sha)
            await self.emit_webhook(client, target, commit_sha, correlation_id)
            self._last_sha_by_target[target.key] = commit_sha
            LOGGER.info(
                "github_change_detected",
                extra={
                    CONTEXT_KEY: {
                        "repo": target.repo_ref,
                        "branch": target.branch,
                        "watch_target_id": target.watch_target_id,
                        "binding_id": target.binding_id,
                        "application_id": target.application_id,
                        "commit_sha": commit_sha,
                        "correlation_id": correlation_id,
                    }
                },
            )
        if self.once and target_errors:
            raise target_errors[0]

    def log_target_status_error(self, target: GitHubPollTarget, exc: httpx.HTTPStatusError) -> None:
        status_code = exc.response.status_code
        LOGGER.warning(
            "github_poll_target_unavailable",
            extra={
                CONTEXT_KEY: {
                    "repo": target.repo_ref,
                    "branch": target.branch,
                    "watch_target_id": target.watch_target_id,
                    "binding_id": target.binding_id,
                    "application_id": target.application_id,
                    "status_code": status_code,
                    "kind": self.github_error_kind(status_code),
                    "hint": "check GitHub token permissions, repository access, and API rate limits",
                }
            },
        )

    def record_poll_result(
        self,
        target: GitHubPollTarget,
        *,
        ok: bool,
        exc: httpx.HTTPStatusError | None = None,
    ) -> None:
        record = getattr(self.db, "record_watch_poll_result", None)
        if not callable(record):
            return
        try:
            record(
                target.watch_target_id,
                workspace_id=target.workspace_id,
                repository_id=target.repository_id,
                branch=target.branch,
                manifest_path=target.manifest_path,
                ok=ok,
                status_code=exc.response.status_code if exc is not None else None,
                error_kind=self.github_error_kind(exc.response.status_code)
                if exc is not None
                else "",
                error=str(exc) if exc is not None else "",
            )
        except Exception as record_exc:
            LOGGER.warning(
                "github_poll_status_record_failed",
                extra={
                    CONTEXT_KEY: {
                        "repo": target.repo_ref,
                        "branch": target.branch,
                        "watch_target_id": target.watch_target_id,
                        "exception_type": type(record_exc).__name__,
                    }
                },
            )

    def poll_targets(self) -> list[GitHubPollTarget]:
        db_targets = self.db_poll_targets()
        if db_targets:
            return db_targets
        if self.repo:
            return [
                GitHubPollTarget(
                    workspace_id=self.workspace_id,
                    repository_id=self.repository_id,
                    repo_ref=self.repo,
                    branch=self.branch,
                    watch_target_id=self.watch_target_id,
                    binding_id=self.binding_id,
                    application_id=Settings.DEFAULT_APPLICATION_ID,
                    environment=Settings.DEFAULT_ENVIRONMENT,
                    cluster_id=self.cluster_id,
                    manifest_path=self.manifest_path,
                    source_type=self.source_type,
                )
            ]
        return []

    def db_poll_targets(self) -> list[GitHubPollTarget]:
        list_targets = getattr(self.db, "list_active_github_poll_targets", None)
        if not callable(list_targets):
            return []
        rows = list_targets()
        return [GitHubPollTarget.from_row(dict(row)) for row in rows]

    async def latest_commit_sha(
        self, client: httpx.AsyncClient, target: GitHubPollTarget
    ) -> str | None:
        self.require_poll_config(target)
        response = await client.get(
            f"{self.github_api_base}/repos/{target.repo_ref}/commits",
            params={"per_page": 1, "sha": target.branch},
            headers=self._github_headers(target),
        )
        if response.status_code == Settings.NOT_MODIFIED_STATUS_CODE:
            return None  # ETag 일치 — 새 커밋 없음(rate limit 미소모).
        response.raise_for_status()
        etag = response.headers.get("etag")
        if etag:
            self._etag_by_target[target.key] = etag
        commits = response.json()
        return commits[0]["sha"] if commits else None

    async def emit_webhook(
        self,
        client: httpx.AsyncClient,
        target: GitHubPollTarget,
        commit_sha: str,
        correlation_id: str,
    ) -> None:
        if not self.image:
            raise ValueError(f"{Settings.WEBHOOK_IMAGE_ENV} is required")
        # 서명은 전송 바이트와 정확히 일치 필요 → json= 대신 직접 직렬화한 content 전송
        body = json.dumps(
            {
                "correlation_id": correlation_id,
                "commit_sha": commit_sha,
                "image": self.image,
                "replicas": Settings.DEFAULT_REPLICAS,
                "workspace_id": target.workspace_id,
                "repository_id": target.repository_id,
                "repo_ref": target.repo_ref,
                "branch": target.branch,
                "watch_target_id": target.watch_target_id,
                "binding_id": target.binding_id,
                "application_id": target.application_id,
                "environment": target.environment,
                "cluster_id": target.cluster_id,
                "manifest_path": target.manifest_path,
                "source_type": target.source_type,
            }
        ).encode()
        response = await client.post(
            f"{self.base_url}{gateway_routes.GITHUB_WEBHOOK_PATH}",
            content=body,
            headers=self._webhook_headers(body, correlation_id),
        )
        response.raise_for_status()

    def require_poll_config(self, target: GitHubPollTarget) -> None:
        if not target.repo_ref or "/" not in target.repo_ref:
            raise ValueError(f"{Settings.GITHUB_REPO_ENV} must be set to owner/repo")

    def _webhook_headers(self, body: bytes, correlation_id: str) -> dict[str, str]:
        headers = {"content-type": "application/json", "x-correlation-id": correlation_id}
        if self.webhook_secret:  # 시크릿 있으면 HMAC 서명 첨부(없으면 입구가 거부 → fail-closed).
            digest = hmac.new(self.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
            headers[Settings.SIGNATURE_HEADER] = f"{Settings.SIGNATURE_PREFIX}{digest}"
        return headers

    def _github_headers(self, target: GitHubPollTarget) -> dict[str, str]:
        headers: dict[str, str] = {}
        token = self._github_token(target)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        etag = self._etag_by_target.get(target.key)
        if etag:  # 조건부 요청 — 변경 없으면 304.
            headers["If-None-Match"] = etag
        return headers

    def _github_token(self, target: GitHubPollTarget) -> str:
        if target.credential_ref:
            return self._read_token_ref(target.credential_ref, target)
        if target.database_managed:
            return ""
        if self.token_ref:
            return self._read_token_ref(self.token_ref, target)
        return self.token

    def _read_token_ref(self, ref: str, target: GitHubPollTarget) -> str:
        if ref == PUBLIC_GITHUB_CREDENTIAL_REF:
            return ""
        if is_app_installation_ref(ref):
            return self._read_app_installation_token(ref, target)
        if ref.startswith("db:"):
            return self._read_db_token_ref(ref, target)
        try:
            return self.token_vault.read_token(SecretRef(ref))
        except SecretNotFound:
            LOGGER.warning(
                "github_poll_token_ref_not_found",
                extra={
                    CONTEXT_KEY: {
                        "repo": target.repo_ref,
                        "branch": target.branch,
                        "watch_target_id": target.watch_target_id,
                    }
                },
            )
            return ""

    def _read_db_token_ref(self, ref: str, target: GitHubPollTarget) -> str:
        get_credential = getattr(self.db, "get_workspace_credential", None)
        if not callable(get_credential):
            LOGGER.warning(
                "github_poll_db_credential_store_unavailable",
                extra={
                    CONTEXT_KEY: {
                        "repo": target.repo_ref,
                        "branch": target.branch,
                        "watch_target_id": target.watch_target_id,
                    }
                },
            )
            return ""
        try:
            provider, scope = parse_credential_ref(ref)
            row = get_credential(target.workspace_id, provider, scope)
            encrypted = str((row or {}).get("encrypted_value") or "")
            if not encrypted:
                raise CredentialEncryptionError("credential not found")
            return decrypt_credential(encrypted)
        except CredentialEncryptionError as exc:
            LOGGER.warning(
                "github_poll_db_credential_not_readable",
                extra={
                    CONTEXT_KEY: {
                        "repo": target.repo_ref,
                        "branch": target.branch,
                        "watch_target_id": target.watch_target_id,
                        "credential_ref": ref,
                        "reason": str(exc),
                    }
                },
            )
            return ""

    def _read_app_installation_token(self, ref: str, target: GitHubPollTarget) -> str:
        """App 설치 참조(``github-app-installation:{id}``)면 단명 설치 토큰을 발급.

        기존 PAT/vault/public 경로는 손대지 않는 additive 분기다. 발급 실패(미구성·
        네트워크·권한)면 빈 토큰으로 degrade 하고, 이후 폴 응답의 401/403 을
        record_poll_result 가 기존대로 기록한다(폴러가 죽지 않음).
        """
        try:
            installation_id = parse_app_installation_ref(ref)
            return resolve_installation_token_sync(self.db, target.workspace_id, installation_id)
        except GithubAppNotConfigured:
            LOGGER.warning(
                "github_poll_app_not_configured",
                extra={
                    CONTEXT_KEY: {
                        "repo": target.repo_ref,
                        "branch": target.branch,
                        "watch_target_id": target.watch_target_id,
                    }
                },
            )
            return ""
        except Exception as exc:  # noqa: BLE001 - 발급 실패는 degrade(폴러 지속)
            LOGGER.warning(
                "github_poll_app_token_mint_failed",
                extra={
                    CONTEXT_KEY: {
                        "repo": target.repo_ref,
                        "branch": target.branch,
                        "watch_target_id": target.watch_target_id,
                        "reason": str(exc),
                    }
                },
            )
            return ""
