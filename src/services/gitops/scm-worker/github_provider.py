"""GitHub REST 기반 safe PR provider — branch/commit/PR 생성을 실제로 수행함.

같은 workflow_run_id 이벤트 재전달(redelivery)에 멱등함:
- 브랜치 생성 422 → 기존 브랜치 재사용
- 변경 문서 PUT 422 → 기존 blob sha 로 갱신
- PR 생성 422 → head 브랜치의 기존 open PR URL 반환
"""

from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Mapping
from urllib.parse import quote

import httpx

from domains.gitops.kustomize_edit_source import resolve_unique_kustomize_edit_source
from domains.gitops.repository_discovery import MAX_MANIFEST_BYTES
from domains.gitops.source_patch import (
    DECLARED_SOURCE_STALE_VALUE_MESSAGE,
    DeclaredScalarPatch,
    ManifestImagePatchPlan,
    ManifestScalarPatchPlan,
    ManifestSourcePatchError,
    canonical_manifest_digest,
    declared_image_patch,
    declared_scalar_patch,
    materialize_declared_scalar_patch,
    parse_image_patch_plan,
    parse_scalar_patch_plan,
    scalar_patch_matches_manifest,
)
from domains.manifest_editor.validation import manifest_identity
from domains.scm.events import SafePrRequestedBody
from domains.scm.policy import (
    CHANGE_DOCUMENT_DIR,
    DefaultSafePrPreflightPolicy,
    normalize_repo_path,
    validate_request_paths,
)
from packages.config.logs import CONTEXT_KEY, get_logger
from packages.config.settings import env
from packages.contracts.gitops import (
    DEFAULT_GITHUB_API_BASE,
    GITHUB_API_BASE_ENV,
    GITHUB_TOKEN_ENV,
    GITHUB_TOKEN_REF_ENV,
)
from packages.contracts.remediation_source import (
    REMEDIATION_SOURCE_CONTRACT_PATH,
    RemediationSourceContract,
    RemediationSourceContractError,
    parse_remediation_source_contract,
)
from packages.contracts.scm.provider import ScmPullRequestResult
from packages.contracts.security import SecretRef, TokenVaultPort
from packages.contracts.stores import PullRequestStore
from packages.runtime.app import EventContext
from packages.security import SecretNotFound, build_token_vault

SCM_REPO_ENV = "SCM_REPO"  # PR 을 만들 저장소("owner/repo")
SCM_BASE_BRANCH_ENV = "SCM_BASE_BRANCH"  # PR base 브랜치(기본 main)
DEFAULT_SCM_BASE_BRANCH = "main"
SCM_HTTP_TIMEOUT_SECONDS_ENV = "SCM_HTTP_TIMEOUT_SECONDS"  # GitHub API 타임아웃 초(기본 10)
DEFAULT_SCM_HTTP_TIMEOUT_SECONDS = "10"
GITHUB_REPO_REF_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
GITHUB_BRANCH_REF_RE = re.compile(r"^[A-Za-z0-9._/-]+$")

PR_STATUS_CREATED = "created"
BRANCH_PREFIX = "gitops"
CONFLICT_STATUS = 422
OK_STATUS = 200
PATCH_COMMIT_MESSAGE_PREFIX = "Apply manifest patch"
INVALID_REPO_REF_MESSAGE = "safe pr repo_ref must be an owner/repo GitHub repository path"
INVALID_BRANCH_REF_MESSAGE = "safe pr branch must be a safe GitHub branch ref"
STALE_BASE_MESSAGE = "safe pr base branch no longer matches the approved commit"
STALE_TARGET_MESSAGE = "safe pr target manifest no longer matches the approved value"

# Safe PR 전달 방식 — 환경설정으로 선택한다(하드코딩 금지).
#   pull_request(기본): 브랜치 + PR 을 열어 사람이 머지한다(리뷰 게이트).
#   direct_commit     : 승인된 패치를 base 브랜치에 직접 커밋한다. 커밋은
#                       github-poll-worker 가 감지해 기존 GitOps 파이프라인
#                       (render→diff→policy→apply)으로 즉시 재배포된다.
SAFE_PR_DELIVERY_MODE_ENV = "SAFE_PR_DELIVERY_MODE"
SAFE_PR_DELIVERY_PULL_REQUEST = "pull_request"
SAFE_PR_DELIVERY_DIRECT_COMMIT = "direct_commit"


def safe_pr_delivery_mode() -> str:
    value = env(SAFE_PR_DELIVERY_MODE_ENV, SAFE_PR_DELIVERY_PULL_REQUEST).strip().lower()
    if value == SAFE_PR_DELIVERY_DIRECT_COMMIT:
        return SAFE_PR_DELIVERY_DIRECT_COMMIT
    return SAFE_PR_DELIVERY_PULL_REQUEST


def request_delivery_mode(request: SafePrRequestedBody) -> str:
    """요청별 전달 방식 — 발행자가 위험도 기준으로 지정한 값이 최우선,
    미지정이면 SAFE_PR_DELIVERY_MODE 기본값을 따른다."""
    value = (getattr(request, "delivery", None) or "").strip().lower()
    if value in (SAFE_PR_DELIVERY_DIRECT_COMMIT, SAFE_PR_DELIVERY_PULL_REQUEST):
        return value
    return safe_pr_delivery_mode()


# 직접 커밋은 "우리 시스템이 스스로 만든 커밋"이라는 특수 상황이다 — 폴러의
# 다음 주기를 기다리지 않도록 pg_notify 로 즉시 알려 버스트 폴링을 깨운다.
# 알림 실패는 경고만 남긴다(fail-open): 30초 주기 폴링이 정확성을 보장한다.
DIRECT_COMMIT_NOTIFY_CHANNEL = "gitops_direct_commit"
NOTIFY_DATABASE_URL_ENV = "COMMAND_NOTIFY_DATABASE_URL"


async def notify_direct_commit(repo: str, base_branch: str) -> None:
    notify_url = env(NOTIFY_DATABASE_URL_ENV, "").strip()
    if not notify_url:
        return
    try:
        import psycopg

        async with await psycopg.AsyncConnection.connect(
            notify_url, autocommit=True
        ) as conn:
            await conn.execute(
                "select pg_notify(%s, %s)",
                (DIRECT_COMMIT_NOTIFY_CHANNEL, f"{repo}|{base_branch}"),
            )
    except Exception as exc:
        LOGGER.warning(
            "direct_commit_notify_failed",
            extra={CONTEXT_KEY: {"exception_type": type(exc).__name__}},
        )
INVALID_SOURCE_RESPONSE_MESSAGE = "GitHub manifest source response is incomplete"
BRANCH_COLLISION_MESSAGE = "safe pr head branch already exists without a matching open PR"
AUTHORITY_MISMATCH_MESSAGE = "safe pr structured patch does not match workflow authority"
MANIFEST_EDIT_AUTHORITY_MISMATCH_MESSAGE = (
    "safe pr manifest edit does not match its granted human approval"
)
UNSUPPORTED_REMEDIATION_SOURCE_MESSAGE = "remediation source patch unsupported"
SAFE_PR_MANIFEST_EDIT_KIND = "safe_pr_manifest_edit"
KUSTOMIZE_SOURCE_TYPE = "kustomize"
MAX_KUSTOMIZE_TREE_ITEMS = 5000

# 자격 증명 부재는 부팅 실패가 아니라 요청 시점 실패 — 워커는 뜨고,
# 각 safe_pr.requested 는 safe_pr.failed 경로로 흐름.
MISSING_GITHUB_CONFIG_MESSAGE = (
    f"{GITHUB_TOKEN_REF_ENV}/{GITHUB_TOKEN_ENV}/{SCM_REPO_ENV} 미설정 — GitHub 자격 증명 없이는 safe PR 을 "
    "생성할 수 없음. deploy secret/env 에 토큰과 대상 저장소를 설정해야 함"
)
MISSING_EXISTING_PR_MESSAGE = (
    "GitHub 가 PR 생성을 거부(422)했지만 head 브랜치의 기존 open PR 을 찾지 못함"
)


LOGGER = get_logger(__name__)
StructuredPatchPlan = ManifestImagePatchPlan | ManifestScalarPatchPlan


class RemediationSourceContractMissing(RuntimeError):
    """The optional repository-owned source declaration is absent."""


class GithubKustomizeSnapshotClient:
    """Commit-pinned, bounded adapter for the shared Kustomize graph resolver."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        context: dict[str, object] | None = None,
    ) -> None:
        self.client = client
        self.context = context

    async def tree_at_revision(
        self,
        repo_ref: str,
        revision: str,
    ) -> tuple[list[dict[str, object]], list[str]]:
        commit = await self.client.get(f"/repos/{repo_ref}/git/commits/{revision}")
        if self.context is not None:
            log_provider_response("github.get_kustomize_commit", commit, self.context)
        commit.raise_for_status()
        commit_payload = commit.json()
        tree = commit_payload.get("tree") if isinstance(commit_payload, Mapping) else None
        tree_sha = str(tree.get("sha") or "") if isinstance(tree, Mapping) else ""
        if not tree_sha:
            raise RuntimeError(
                f"{UNSUPPORTED_REMEDIATION_SOURCE_MESSAGE}: commit tree is missing"
            )
        response = await self.client.get(
            f"/repos/{repo_ref}/git/trees/{tree_sha}",
            params={"recursive": "1"},
        )
        if self.context is not None:
            log_provider_response("github.get_kustomize_tree", response, self.context)
        response.raise_for_status()
        payload = response.json()
        raw_tree = payload.get("tree") if isinstance(payload, Mapping) else None
        if (
            not isinstance(raw_tree, list)
            or payload.get("truncated") is True
            or len(raw_tree) > MAX_KUSTOMIZE_TREE_ITEMS
        ):
            raise RuntimeError(
                f"{UNSUPPORTED_REMEDIATION_SOURCE_MESSAGE}: repository tree is incomplete"
            )
        return (
            [dict(item) for item in raw_tree if isinstance(item, Mapping)],
            [],
        )

    async def content(
        self,
        repo_ref: str,
        revision: str,
        path: str,
    ) -> bytes:
        response = await self.client.get(
            contents_api_path(repo_ref, path),
            params={"ref": revision},
        )
        if self.context is not None:
            log_provider_response(
                "github.get_kustomize_source",
                response,
                self.context,
                path=path,
            )
        response.raise_for_status()
        payload = response.json()
        size = payload.get("size")
        if (
            payload.get("type") != "file"
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or size > MAX_MANIFEST_BYTES
            or payload.get("encoding") != "base64"
            or not isinstance(payload.get("content"), str)
        ):
            raise RuntimeError(INVALID_SOURCE_RESPONSE_MESSAGE)
        try:
            encoded = "".join(payload["content"].split())
            decoded = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise RuntimeError(INVALID_SOURCE_RESPONSE_MESSAGE) from exc
        if len(decoded) != size or len(decoded) > MAX_MANIFEST_BYTES:
            raise RuntimeError(INVALID_SOURCE_RESPONSE_MESSAGE)
        return decoded


def manifest_content_sha256(content: str) -> str:
    return f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"


def normalize_branch_ref(branch: str) -> str:
    ref = branch.strip()
    parts = ref.split("/")
    if (
        not ref
        or not GITHUB_BRANCH_REF_RE.match(ref)
        or ref.startswith("/")
        or ref.endswith("/")
        or "//" in ref
        or "\\" in ref
        or ".." in ref
        or "@{" in ref
        or ref.endswith(".")
        or ref.endswith(".lock")
        or any(part in {"", ".", ".."} or part.endswith(".lock") for part in parts)
    ):
        raise ValueError(INVALID_BRANCH_REF_MESSAGE)
    return ref


def branch_name(request: SafePrRequestedBody) -> str:
    # A workflow identifies the approved deployment authority, but a failed Safe PR
    # may be retried with a new approval.  Reusing only the workflow id makes every
    # retry target the abandoned branch from the first attempt and GitHub rejects
    # branch creation with 422 ("Reference already exists").  Scope the branch to
    # the approval attempt while keeping redelivery of the same approved request
    # idempotent.  Legacy requests without an approval keep their existing name.
    approval_ref = str(request.approval_ref or "").strip()
    if approval_ref:
        attempt = hashlib.sha256(approval_ref.encode("utf-8")).hexdigest()[:12]
        return normalize_branch_ref(
            f"{BRANCH_PREFIX}/{request.workflow_run_id}-{attempt}"
        )
    return normalize_branch_ref(f"{BRANCH_PREFIX}/{request.workflow_run_id}")


def change_document_path(request: SafePrRequestedBody) -> str:
    return f"{CHANGE_DOCUMENT_DIR}/{request.workflow_run_id}.md"


def change_document(request: SafePrRequestedBody) -> str:
    patch_rows = "\n".join(
        f"- `{patch.path}`: {patch.description or 'manifest patch'}" for patch in request.patches
    )
    patch_section = patch_rows if patch_rows else "- no file patches supplied"
    approval_rows = []
    if request.approval_ref:
        approval_rows.append(f"- approval_ref: `{request.approval_ref}`")
    if request.policy_decision_ref:
        approval_rows.append(f"- policy_decision_ref: `{request.policy_decision_ref}`")
    approval_section = "\n".join(approval_rows) if approval_rows else "- approval_ref: 없음"
    structured_plans = [
        patch.content.rstrip()
        for patch in request.patches
        if patch.path.startswith(".gitops/safe-pr/patches/")
    ]
    structured_section = (
        "\n\n## Structured Patch Plan\n\n"
        + "\n\n".join(f"```yaml\n{content}\n```" for content in structured_plans)
        if structured_plans
        else ""
    )
    return (
        f"# {request.title}\n\n"
        f"{request.body}\n\n"
        f"- manifest_path: `{request.manifest_path}`\n"
        f"- pr_kind: `{request.pr_kind}`\n"
        f"- workflow_run_id: `{request.workflow_run_id}`\n"
        f"- environment: `{request.environment}`\n\n"
        "## Evidence\n\n"
        f"- commit_sha: `{request.commit_sha}`\n"
        f"- patch_sha256: `{request.patch_sha256}`\n\n"
        "## Approval\n\n"
        f"{approval_section}\n\n"
        "## Files\n\n"
        f"{patch_section}\n"
        f"{structured_section}"
    )


def pull_request_body(request: SafePrRequestedBody) -> str:
    return f"{request.body}\n\n<!-- safe-pr-patch-sha256: {request.patch_sha256} -->"


def contents_api_path(repo: str, path: str) -> str:
    return f"/repos/{repo}/contents/{quote(normalize_repo_path(path), safe='/')}"


def request_repo(request: SafePrRequestedBody) -> str:
    repo = (request.repo_ref or env(SCM_REPO_ENV, "")).strip()
    if not repo:
        raise RuntimeError(MISSING_GITHUB_CONFIG_MESSAGE)
    if not GITHUB_REPO_REF_RE.match(repo):
        raise ValueError(INVALID_REPO_REF_MESSAGE)
    return repo


def request_base_branch(request: SafePrRequestedBody) -> str:
    return normalize_branch_ref(
        request.base_branch.strip()
        or env(SCM_BASE_BRANCH_ENV, DEFAULT_SCM_BASE_BRANCH).strip()
        or DEFAULT_SCM_BASE_BRANCH
    )


def safe_pr_provider_context(
    request: SafePrRequestedBody,
    ctx: EventContext[PullRequestStore],
    *,
    repo: str,
    branch: str,
    base_branch: str,
    operation: str,
    path: str | None = None,
    status_code: int | None = None,
) -> dict[str, object]:
    context: dict[str, object] = {
        "provider": request.provider,
        "operation": operation,
        "event_id": ctx.event_id,
        "correlation_id": ctx.correlation_id,
        "causation_id": ctx.causation_id,
        "workspace_id": request.workspace_id,
        "repository_id": request.repository_id,
        "binding_id": request.binding_id,
        "application_id": request.application_id,
        "workflow_run_id": request.workflow_run_id,
        "environment": request.environment,
        "manifest_path": request.manifest_path,
        "repo_ref": repo,
        "base_branch": base_branch,
        "head_branch": branch,
    }
    if path is not None:
        context["path"] = path
    if status_code is not None:
        context["status_code"] = status_code
    return context


def log_provider_response(
    operation: str,
    response: httpx.Response,
    context: dict[str, object],
    *,
    path: str | None = None,
) -> None:
    log_context = {**context, "operation": operation, "status_code": response.status_code}
    if path is not None:
        log_context["path"] = path
    level = LOGGER.warning if response.status_code >= 400 else LOGGER.info
    level("github_provider_response", extra={CONTEXT_KEY: log_context})


def pull_request_result(payload: Mapping[str, object]) -> ScmPullRequestResult:
    """GitHub PR response의 immutable identity와 생성 직후 head를 보존."""

    url = str(payload.get("html_url") or "").strip()
    if not url:
        raise RuntimeError("GitHub pull request response is missing html_url")
    raw_number = payload.get("number")
    number = (
        int(raw_number)
        if isinstance(raw_number, int) and not isinstance(raw_number, bool) and raw_number > 0
        else None
    )
    head = payload.get("head")
    head_mapping = head if isinstance(head, Mapping) else {}
    return ScmPullRequestResult(
        url=url,
        number=number,
        node_id=str(payload.get("node_id") or ""),
        head_ref=str(head_mapping.get("ref") or ""),
        head_sha=str(head_mapping.get("sha") or ""),
    )


class GithubScmProvider:
    """ScmProvider 구현 — GitHub REST API 호출로 PR html_url 을 반환함."""

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        token_vault: TokenVaultPort | None = None,
    ) -> None:
        self.transport = transport
        self.token_vault = token_vault or build_token_vault()

    async def create_pull_request(
        self, request: SafePrRequestedBody, ctx: EventContext[PullRequestStore]
    ) -> ScmPullRequestResult:
        preflight = DefaultSafePrPreflightPolicy().evaluate(request)
        if not preflight.allowed:
            raise ValueError(preflight.message)
        repo = request_repo(request)
        try:
            token = self.github_token()
        except SecretNotFound as exc:
            raise RuntimeError(MISSING_GITHUB_CONFIG_MESSAGE) from exc
        base_branch = request_base_branch(request)
        branch = branch_name(request)
        validate_request_paths(request)
        context = safe_pr_provider_context(
            request,
            ctx,
            repo=repo,
            branch=branch,
            base_branch=base_branch,
            operation="safe_pr.create",
        )
        LOGGER.info("github_provider_started", extra={CONTEXT_KEY: context})
        patch_plans = self.patch_plans(request)
        structured = any(plan is not None for plan in patch_plans)
        structured_authority: Mapping[str, object] | None = None
        manifest_edit_authority: Mapping[str, object] | None = None
        if structured and (
            any(plan is None for plan in patch_plans)
            or any(plan.expected_base_sha != request.commit_sha for plan in patch_plans if plan)
        ):
            raise RuntimeError(STALE_BASE_MESSAGE)
        if structured:
            structured_authority = await self.validate_structured_patch_authority(
                request,
                patch_plans,
                ctx,
            )
        elif request.pr_kind == SAFE_PR_MANIFEST_EDIT_KIND:
            manifest_edit_authority = await self.validate_manifest_edit_approval(request, ctx)

        async with self.client(token) as client:
            base_sha = await self.base_branch_sha(client, repo, base_branch, context)
            if request.pr_kind == SAFE_PR_MANIFEST_EDIT_KIND:
                await self.validate_manifest_edit_source(
                    client,
                    repo,
                    base_sha,
                    request,
                    manifest_edit_authority,
                    context,
                )
            direct_commit = request_delivery_mode(request) == SAFE_PR_DELIVERY_DIRECT_COMMIT
            if structured and direct_commit:
                expected_base_sha = patch_plans[0].expected_base_sha if patch_plans[0] else ""
                if expected_base_sha != base_sha:
                    raise RuntimeError(STALE_BASE_MESSAGE)
                patch_contents = await self.materialize_patch_contents(
                    client,
                    repo,
                    base_sha,
                    request,
                    patch_plans,
                    context,
                    authority=structured_authority,
                )
                await self.put_change_document(client, repo, base_branch, request, context)
                await self.put_manifest_patches(client, repo, base_branch, patch_contents, context)
                pr_url = await self.branch_head_commit_url(client, repo, base_branch, context)
                result = ScmPullRequestResult(url=pr_url)
                await notify_direct_commit(repo, base_branch)
            elif structured:
                existing = await self.find_existing_pr(
                    client,
                    repo,
                    branch,
                    base_branch,
                    request,
                    context,
                    require_request_match=True,
                )
                if existing is not None:
                    if not await self.verify_existing_structured_pr(
                        client,
                        repo,
                        request,
                        patch_plans,
                        existing,
                        base_sha,
                        context,
                        authority=structured_authority,
                    ):
                        raise RuntimeError(BRANCH_COLLISION_MESSAGE)
                    result = pull_request_result(existing)
                else:
                    expected_base_sha = patch_plans[0].expected_base_sha if patch_plans[0] else ""
                    patch_contents = await self.validate_structured_base_advance(
                        client,
                        repo,
                        expected_base_sha,
                        base_sha,
                        request,
                        patch_plans,
                        context,
                        authority=structured_authority,
                    )
                    await self.ensure_branch(
                        client,
                        repo,
                        branch,
                        base_sha,
                        context,
                        # Approval-scoped branches belong to exactly one Safe PR
                        # attempt.  If the same event is redelivered after a partial
                        # failure, resume that branch instead of failing on GitHub's
                        # 422 "Reference already exists" response.  Legacy branch
                        # names remain fail-closed because they are only workflow-
                        # scoped and could contain another attempt's changes.
                        allow_existing=bool(request.approval_ref),
                    )
                    await self.put_change_document(client, repo, branch, request, context)
                    await self.put_manifest_patches(
                        client,
                        repo,
                        branch,
                        patch_contents,
                        context,
                    )
                    result = await self.create_or_reuse_pr(
                        client, repo, branch, base_branch, request, context
                    )
            elif direct_commit:
                patch_contents = await self.materialize_patch_contents(
                    client, repo, base_sha, request, patch_plans, context,
                )
                await self.put_change_document(client, repo, base_branch, request, context)
                await self.put_manifest_patches(client, repo, base_branch, patch_contents, context)
                pr_url = await self.branch_head_commit_url(client, repo, base_branch, context)
                result = ScmPullRequestResult(url=pr_url)
                await notify_direct_commit(repo, base_branch)
            else:
                patch_contents = await self.materialize_patch_contents(
                    client,
                    repo,
                    base_sha,
                    request,
                    patch_plans,
                    context,
                )
                await self.ensure_branch(client, repo, branch, base_sha, context)
                await self.put_change_document(client, repo, branch, request, context)
                await self.put_manifest_patches(
                    client,
                    repo,
                    branch,
                    patch_contents,
                    context,
                )
                result = await self.create_or_reuse_pr(
                    client, repo, branch, base_branch, request, context
                )

        await ctx.db.save_pull_request(
            ctx.correlation_id, result.url, request.title, request.body, PR_STATUS_CREATED
        )
        LOGGER.info(
            "github_provider_completed",
            extra={CONTEXT_KEY: {**context, "pr_url": result.url}},
        )
        return result

    def github_token(self) -> str:
        token_ref = env(GITHUB_TOKEN_REF_ENV, GITHUB_TOKEN_ENV).strip() or GITHUB_TOKEN_ENV
        return self.token_vault.read_token(SecretRef(token_ref))

    def client(self, token: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=env(GITHUB_API_BASE_ENV, DEFAULT_GITHUB_API_BASE).rstrip("/"),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=float(env(SCM_HTTP_TIMEOUT_SECONDS_ENV, DEFAULT_SCM_HTTP_TIMEOUT_SECONDS)),
            transport=self.transport,
        )

    async def base_branch_sha(
        self,
        client: httpx.AsyncClient,
        repo: str,
        base_branch: str,
        context: dict[str, object] | None = None,
    ) -> str:
        response = await client.get(f"/repos/{repo}/git/ref/heads/{base_branch}")
        if context is not None:
            log_provider_response("github.base_ref", response, context)
        response.raise_for_status()
        return str(response.json()["object"]["sha"])

    async def ensure_branch(
        self,
        client: httpx.AsyncClient,
        repo: str,
        branch: str,
        base_sha: str,
        context: dict[str, object] | None = None,
        *,
        allow_existing: bool = True,
    ) -> None:
        response = await client.post(
            f"/repos/{repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": base_sha},
        )
        if context is not None:
            log_provider_response("github.ensure_branch", response, context)
        if response.status_code == CONFLICT_STATUS:
            if allow_existing:
                return  # legacy 요청은 기존 멱등 규약 유지
            raise RuntimeError(BRANCH_COLLISION_MESSAGE)
        response.raise_for_status()

    async def put_change_document(
        self,
        client: httpx.AsyncClient,
        repo: str,
        branch: str,
        request: SafePrRequestedBody,
        context: dict[str, object] | None = None,
    ) -> None:
        await self.put_content_file(
            client,
            repo,
            branch,
            path=change_document_path(request),
            message=request.title,
            content=change_document(request),
            context=context,
        )

    async def put_manifest_patches(
        self,
        client: httpx.AsyncClient,
        repo: str,
        branch: str,
        patch_contents: list[tuple[str, str]],
        context: dict[str, object] | None = None,
    ) -> None:
        for path, content in patch_contents:
            await self.put_content_file(
                client,
                repo,
                branch,
                path=path,
                message=f"{PATCH_COMMIT_MESSAGE_PREFIX}: {path}",
                content=content,
                context=context,
            )

    async def materialize_patch_contents(
        self,
        client: httpx.AsyncClient,
        repo: str,
        base_sha: str,
        request: SafePrRequestedBody,
        patch_plans: list[StructuredPatchPlan | None],
        context: dict[str, object] | None = None,
        *,
        declared_patches: list[DeclaredScalarPatch | None] | None = None,
        authority: Mapping[str, object] | None = None,
    ) -> list[tuple[str, str]]:
        if len(patch_plans) != len(request.patches):
            raise RuntimeError("safe pr patch plan count does not match file patches")
        resolved = declared_patches or await self.resolve_declared_patches(
            client,
            repo,
            base_sha,
            patch_plans,
            context,
            request=request,
            authority=authority,
        )
        if len(resolved) != len(patch_plans):
            raise RuntimeError("safe pr declared patch count does not match patch plans")
        contents: list[tuple[str, str]] = []
        for patch, plan, declared in zip(
            request.patches,
            patch_plans,
            resolved,
            strict=True,
        ):
            if plan is None:
                contents.append((patch.path, patch.content))
                continue
            if declared is None:
                raise RuntimeError(
                    f"{UNSUPPORTED_REMEDIATION_SOURCE_MESSAGE}: declaration is missing"
                )
            try:
                source = await self.declared_source_file_content(
                    client,
                    repo,
                    base_sha,
                    declared.source_path,
                    context,
                )
                content = materialize_declared_scalar_patch(
                    source,
                    declared,
                    allow_already_applied=True,
                )
                # The desired scalar may already be present on an advanced base.
                # Keep the evidence/change document PR, but do not ask GitHub to
                # rewrite an identical manifest blob.
                if content != source:
                    contents.append((declared.source_path, content))
            except ManifestSourcePatchError as exc:
                raise RuntimeError(str(exc)) from exc
        return contents

    async def validate_structured_base_advance(
        self,
        client: httpx.AsyncClient,
        repo: str,
        approved_base_sha: str,
        current_base_sha: str,
        request: SafePrRequestedBody,
        patch_plans: list[StructuredPatchPlan | None],
        context: dict[str, object] | None = None,
        *,
        authority: Mapping[str, object] | None = None,
    ) -> list[tuple[str, str]]:
        """Allow an advanced base only when the approved target is still unchanged.

        Before creating a recovery branch from the current base, validate that the
        base is a descendant of the approved commit, resolves the same
        repository-owned source, and still contains every approved
        ``currentValue``.  This is field-level optimistic concurrency: unrelated
        repository changes are preserved, while target drift fails closed.
        """

        if current_base_sha == approved_base_sha:
            return await self.materialize_patch_contents(
                client,
                repo,
                current_base_sha,
                request,
                patch_plans,
                context,
                authority=authority,
            )
        response = await client.get(
            f"/repos/{repo}/compare/{approved_base_sha}...{current_base_sha}"
        )
        if context is not None:
            log_provider_response("github.compare_current_base", response, context)
        response.raise_for_status()
        comparison = response.json()
        merge_base = (
            comparison.get("merge_base_commit")
            if isinstance(comparison, Mapping)
            else None
        )
        if (
            not isinstance(comparison, Mapping)
            or comparison.get("status") != "ahead"
            or not isinstance(merge_base, Mapping)
            or merge_base.get("sha") != approved_base_sha
        ):
            raise RuntimeError(STALE_BASE_MESSAGE)

        try:
            approved_declared = await self.resolve_declared_patches(
                client,
                repo,
                approved_base_sha,
                patch_plans,
                context,
                request=request,
                authority=authority,
            )
            current_declared = await self.resolve_declared_patches(
                client,
                repo,
                current_base_sha,
                patch_plans,
                context,
                request=request,
                authority=authority,
            )
            if (
                len(approved_declared) != len(patch_plans)
                or current_declared != approved_declared
                or any(
                    plan is not None and declared is None
                    for plan, declared in zip(
                        patch_plans,
                        current_declared,
                        strict=True,
                    )
                )
            ):
                raise RuntimeError(STALE_TARGET_MESSAGE)
            # Materialization parses the current source and accepts only the
            # approval's currentValue or desiredValue.  The latter produces a
            # document-only audit PR; any third value remains a stale conflict.
            return await self.materialize_patch_contents(
                client,
                repo,
                current_base_sha,
                request,
                patch_plans,
                context,
                declared_patches=current_declared,
                authority=authority,
            )
        except RuntimeError as exc:
            if str(exc) == STALE_TARGET_MESSAGE:
                raise
            if str(exc) == DECLARED_SOURCE_STALE_VALUE_MESSAGE:
                raise RuntimeError(STALE_TARGET_MESSAGE) from exc
            raise

    async def resolve_declared_patches(
        self,
        client: httpx.AsyncClient,
        repo: str,
        base_sha: str,
        patch_plans: list[StructuredPatchPlan | None],
        context: dict[str, object] | None = None,
        *,
        request: SafePrRequestedBody | None = None,
        authority: Mapping[str, object] | None = None,
    ) -> list[DeclaredScalarPatch | None]:
        if not any(plan is not None for plan in patch_plans):
            return [None for _ in patch_plans]
        try:
            contract = await self.remediation_source_contract(
                client,
                repo,
                base_sha,
                context,
            )
        except RemediationSourceContractMissing:
            return await self.resolve_kustomize_patches(
                client,
                repo,
                base_sha,
                patch_plans,
                request=request,
                authority=authority,
                context=context,
            )
        resolved: list[DeclaredScalarPatch | None] = []
        try:
            for plan in patch_plans:
                if plan is None:
                    resolved.append(None)
                elif isinstance(plan, ManifestScalarPatchPlan):
                    resolved.append(declared_scalar_patch(plan, contract))
                else:
                    resolved.append(declared_image_patch(plan, contract))
        except ManifestSourcePatchError as exc:
            raise RuntimeError(str(exc)) from exc
        return resolved

    async def resolve_kustomize_patches(
        self,
        client: httpx.AsyncClient,
        repo: str,
        base_sha: str,
        patch_plans: list[StructuredPatchPlan | None],
        *,
        request: SafePrRequestedBody | None,
        authority: Mapping[str, object] | None,
        context: dict[str, object] | None,
    ) -> list[DeclaredScalarPatch | None]:
        provenance = (
            authority.get("provenance") if isinstance(authority, Mapping) else None
        )
        desired_manifest = (
            authority.get("desired_manifest") if isinstance(authority, Mapping) else None
        )
        identity = (
            manifest_identity(desired_manifest)
            if isinstance(desired_manifest, Mapping)
            else None
        )
        if (
            request is None
            or not isinstance(provenance, Mapping)
            or str(provenance.get("source_type") or "") != KUSTOMIZE_SOURCE_TYPE
            or str(provenance.get("manifest_path") or "") != request.manifest_path
            or identity is None
        ):
            raise RuntimeError(
                f"{UNSUPPORTED_REMEDIATION_SOURCE_MESSAGE}: contract is missing"
            )
        resolved_source = await resolve_unique_kustomize_edit_source(
            GithubKustomizeSnapshotClient(client, context=context),
            repo_ref=repo,
            revision=base_sha,
            binding_manifest_path=request.manifest_path,
            selected_identity=identity,
            protected_field_paths=tuple(
                replacement.field_path
                for plan in patch_plans
                if isinstance(plan, ManifestScalarPatchPlan)
                for replacement in plan.replacements
            ),
        )
        if resolved_source is None:
            raise RuntimeError(
                f"{UNSUPPORTED_REMEDIATION_SOURCE_MESSAGE}: "
                "Kustomize resource source is missing or ambiguous"
            )
        resolved: list[DeclaredScalarPatch | None] = []
        for plan in patch_plans:
            if plan is None:
                resolved.append(None)
                continue
            if not isinstance(plan, ManifestScalarPatchPlan):
                raise RuntimeError(
                    f"{UNSUPPORTED_REMEDIATION_SOURCE_MESSAGE}: "
                    "Kustomize image recovery requires a repository declaration"
                )
            resolved.append(
                DeclaredScalarPatch(
                    source_type=resolved_source.source_type,
                    source_path=resolved_source.path,
                    replacements=plan.replacements,
                    document_identity=resolved_source.document_identity,
                )
            )
        return resolved

    async def remediation_source_contract(
        self,
        client: httpx.AsyncClient,
        repo: str,
        base_sha: str,
        context: dict[str, object] | None = None,
    ) -> RemediationSourceContract:
        try:
            content = await self.source_file_content(
                client,
                repo,
                base_sha,
                REMEDIATION_SOURCE_CONTRACT_PATH,
                context,
            )
            return parse_remediation_source_contract(content)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            raise RemediationSourceContractMissing(
                f"{UNSUPPORTED_REMEDIATION_SOURCE_MESSAGE}: contract is missing"
            ) from exc
        except (RemediationSourceContractError, RuntimeError) as exc:
            raise RuntimeError(
                f"{UNSUPPORTED_REMEDIATION_SOURCE_MESSAGE}: contract is missing or malformed"
            ) from exc

    async def declared_source_file_content(
        self,
        client: httpx.AsyncClient,
        repo: str,
        base_sha: str,
        path: str,
        context: dict[str, object] | None = None,
    ) -> str:
        try:
            return await self.source_file_content(
                client,
                repo,
                base_sha,
                path,
                context,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            raise RuntimeError(
                f"{UNSUPPORTED_REMEDIATION_SOURCE_MESSAGE}: declared source is missing"
            ) from exc
        except RuntimeError as exc:
            raise RuntimeError(
                f"{UNSUPPORTED_REMEDIATION_SOURCE_MESSAGE}: declared source is malformed"
            ) from exc

    async def source_file_content(
        self,
        client: httpx.AsyncClient,
        repo: str,
        base_sha: str,
        path: str,
        context: dict[str, object] | None = None,
    ) -> str:
        response = await client.get(contents_api_path(repo, path), params={"ref": base_sha})
        if context is not None:
            log_provider_response("github.get_source_content", response, context, path=path)
        response.raise_for_status()
        payload = response.json()
        if payload.get("encoding") != "base64" or not isinstance(payload.get("content"), str):
            raise RuntimeError(INVALID_SOURCE_RESPONSE_MESSAGE)
        try:
            encoded = "".join(payload["content"].split())
            return base64.b64decode(encoded, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise RuntimeError(INVALID_SOURCE_RESPONSE_MESSAGE) from exc

    def patch_plans(self, request: SafePrRequestedBody) -> list[StructuredPatchPlan | None]:
        try:
            plans: list[StructuredPatchPlan | None] = []
            for patch in request.patches:
                if not patch.path.startswith(".gitops/safe-pr/patches/"):
                    plans.append(None)
                    continue
                plan = parse_image_patch_plan(patch.content)
                if plan is None:
                    plan = parse_scalar_patch_plan(patch.content)
                if plan is None:
                    raise ManifestSourcePatchError("structured manifest patch document is invalid")
                plans.append(plan)
            return plans
        except ManifestSourcePatchError as exc:
            raise RuntimeError(str(exc)) from exc

    async def validate_manifest_edit_approval(
        self,
        request: SafePrRequestedBody,
        ctx: EventContext[PullRequestStore],
    ) -> Mapping[str, object]:
        load_approval = getattr(ctx.db, "get_workflow_approval", None)
        if not callable(load_approval) or not request.approval_ref:
            raise RuntimeError(MANIFEST_EDIT_AUTHORITY_MISMATCH_MESSAGE)
        approval = await load_approval(request.approval_ref, request.workspace_id)
        details = approval.get("details") if isinstance(approval, Mapping) else None
        expected = {
            "authority": SAFE_PR_MANIFEST_EDIT_KIND,
            "repository_id": request.repository_id,
            "repo_ref": request.repo_ref,
            "branch": request.base_branch,
            "manifest_path": request.manifest_path,
            "base_sha": request.commit_sha,
            "patch_sha256": request.patch_sha256,
        }
        if (
            not isinstance(approval, Mapping)
            or not isinstance(details, Mapping)
            or str(approval.get("status") or "") != "granted"
            or str(approval.get("workspace_id") or "") != request.workspace_id
            or str(approval.get("workflow_run_id") or "") != request.workflow_run_id
            or str(approval.get("application_id") or "") != request.application_id
            or str(approval.get("binding_id") or "") != request.binding_id
            or str(approval.get("environment") or "") != request.environment
            or not str(approval.get("requested_by") or "")
            or str(approval.get("requested_by") or "") != str(approval.get("decided_by") or "")
            or approval.get("decision") != "granted"
            or any(str(details.get(key) or "") != value for key, value in expected.items())
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(details.get("source_sha256") or ""))
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(details.get("desired_sha256") or ""))
            or len(request.patches) != 1
            or request.patches[0].path != request.manifest_path
            or manifest_content_sha256(request.patches[0].content)
            != str(details.get("desired_sha256") or "")
        ):
            raise RuntimeError(MANIFEST_EDIT_AUTHORITY_MISMATCH_MESSAGE)
        return details

    async def validate_manifest_edit_source(
        self,
        client: httpx.AsyncClient,
        repo: str,
        base_sha: str,
        request: SafePrRequestedBody,
        authority: Mapping[str, object] | None,
        context: dict[str, object] | None = None,
    ) -> None:
        if base_sha != request.commit_sha:
            raise RuntimeError(STALE_BASE_MESSAGE)
        source = await self.source_file_content(
            client, repo, base_sha, request.manifest_path, context
        )
        source_digest = manifest_content_sha256(source)
        desired_digest = manifest_content_sha256(request.patches[0].content)
        if (
            authority is None
            or source_digest != str(authority.get("source_sha256") or "")
            or desired_digest != str(authority.get("desired_sha256") or "")
            or source_digest == desired_digest
        ):
            raise RuntimeError(MANIFEST_EDIT_AUTHORITY_MISMATCH_MESSAGE)

    async def validate_structured_patch_authority(
        self,
        request: SafePrRequestedBody,
        patch_plans: list[StructuredPatchPlan | None],
        ctx: EventContext[PullRequestStore],
    ) -> Mapping[str, object]:
        load_run = getattr(ctx.db, "get_workflow_run", None)
        load_diff = getattr(ctx.db, "get_workflow_step_details", None)
        load_resource_diff = getattr(
            ctx.db,
            "get_completed_workload_resource_diff",
            None,
        )
        load_approval = getattr(ctx.db, "get_workflow_approval", None)
        load_provenance = getattr(ctx.db, "get_manifest_artifact_provenance", None)
        if not callable(load_run) or not callable(load_provenance):
            raise RuntimeError(AUTHORITY_MISMATCH_MESSAGE)
        run = await load_run(request.workflow_run_id)
        target_kind, separator, target_name = request.target_resource.partition("/")
        target_declared = any(
            (request.cluster_id, request.target_namespace, request.target_resource)
        )
        resource_record: Mapping[str, object] | None = None
        if target_declared:
            if (
                not all(
                    (
                        request.cluster_id,
                        request.target_namespace,
                        separator,
                        target_kind,
                        target_name,
                        request.target_authority,
                    )
                )
            ):
                raise RuntimeError(AUTHORITY_MISMATCH_MESSAGE)
            if request.target_authority == "completed_workload_change":
                if not callable(load_resource_diff):
                    raise RuntimeError(AUTHORITY_MISMATCH_MESSAGE)
                loaded = await load_resource_diff(
                    request.workspace_id,
                    request.workflow_run_id,
                    request.binding_id,
                    request.cluster_id,
                    request.target_namespace,
                    target_kind,
                    target_name,
                )
                resource_record = loaded if isinstance(loaded, Mapping) else None
                raw_diff = (
                    resource_record.get("diff_details")
                    if resource_record is not None
                    else None
                )
            elif request.target_authority == "policy_approval":
                if not callable(load_approval) or not request.approval_ref:
                    raise RuntimeError(AUTHORITY_MISMATCH_MESSAGE)
                approval = await load_approval(
                    request.approval_ref,
                    request.workspace_id,
                )
                details = (
                    approval.get("details")
                    if isinstance(approval, Mapping)
                    else None
                )
                if (
                    not isinstance(approval, Mapping)
                    or not isinstance(details, Mapping)
                    or str(approval.get("approval_id") or "")
                    != request.approval_ref
                    or str(approval.get("status") or "") != "granted"
                    or str(approval.get("workspace_id") or "")
                    != request.workspace_id
                    or str(approval.get("workflow_run_id") or "")
                    != request.workflow_run_id
                    or str(approval.get("application_id") or "")
                    != request.application_id
                    or str(approval.get("binding_id") or "")
                    != request.binding_id
                    or str(approval.get("environment") or "")
                    != request.environment
                ):
                    raise RuntimeError(AUTHORITY_MISMATCH_MESSAGE)
                raw_diff = details.get("diff")
            else:
                raise RuntimeError(AUTHORITY_MISMATCH_MESSAGE)
            diff = dict(raw_diff) if isinstance(raw_diff, Mapping) else None
        else:
            if not callable(load_diff):
                raise RuntimeError(AUTHORITY_MISMATCH_MESSAGE)
            diff = await load_diff(request.workflow_run_id, "diff")
        if not isinstance(run, Mapping) or not isinstance(diff, Mapping):
            raise RuntimeError(AUTHORITY_MISMATCH_MESSAGE)
        run_fields = {
            "workflow_run_id": request.workflow_run_id,
            "workspace_id": request.workspace_id,
            "application_id": request.application_id,
            "binding_id": request.binding_id,
            "environment": request.environment,
            "commit_sha": request.commit_sha,
        }
        diff_fields = {
            "workspace_id": request.workspace_id,
            "repository_id": request.repository_id,
            "binding_id": request.binding_id,
            "application_id": request.application_id,
            "workflow_run_id": request.workflow_run_id,
            "environment": request.environment,
            "manifest_path": request.manifest_path,
        }
        basis = diff.get("basis")
        desired_manifest = diff.get("desired_manifest")
        changes = diff.get("changes")
        plans = [plan for plan in patch_plans if plan is not None]
        resource = str(diff.get("resource") or "")
        artifact_digest = (
            str(basis.get("artifact_digest") or "") if isinstance(basis, Mapping) else ""
        )
        provenance = (
            await load_provenance(
                request.workspace_id,
                request.binding_id,
                request.commit_sha,
                request.manifest_path,
                resource,
                artifact_digest,
            )
            if resource and artifact_digest
            else None
        )
        if (
            any(str(run.get(key) or "") != value for key, value in run_fields.items())
            or any(str(diff.get(key) or "") != value for key, value in diff_fields.items())
            or (
                target_declared
                and (
                    str(diff.get("cluster_id") or "") != request.cluster_id
                    or str(diff.get("namespace") or "")
                    != request.target_namespace
                    or str(diff.get("resource") or "").casefold()
                    != request.target_resource.casefold()
                )
            )
            or (
                resource_record is not None
                and (
                    str(resource_record.get("workspace_id") or "")
                    != request.workspace_id
                    or str(resource_record.get("workflow_run_id") or "")
                    != request.workflow_run_id
                    or str(resource_record.get("binding_id") or "")
                    != request.binding_id
                    or str(resource_record.get("cluster_id") or "")
                    != request.cluster_id
                    or str(resource_record.get("namespace") or "")
                    != request.target_namespace
                    or str(resource_record.get("resource_kind") or "").casefold()
                    != target_kind.casefold()
                    or str(resource_record.get("resource_name") or "") != target_name
                    or str(resource_record.get("repository_id") or "")
                    != request.repository_id
                    or str(resource_record.get("manifest_path") or "")
                    != request.manifest_path
                    or str(resource_record.get("commit_sha") or "")
                    != request.commit_sha
                )
            )
            or not isinstance(basis, Mapping)
            or basis.get("old_desired_source") != "last_approved_snapshot"
            or not isinstance(desired_manifest, Mapping)
            or canonical_manifest_digest(desired_manifest)
            != str(basis.get("artifact_digest") or "")
            or not isinstance(changes, list)
            or len(request.patches) != 1
            or len(plans) != 1
            or plans[0].manifest_path != request.manifest_path
            or not request.patches[0].path.startswith(".gitops/safe-pr/patches/")
            or not isinstance(provenance, Mapping)
            or str(provenance.get("workspace_id") or "") != request.workspace_id
            or str(provenance.get("repository_id") or "") != request.repository_id
            or str(provenance.get("binding_id") or "") != request.binding_id
            or str(provenance.get("commit_sha") or "") != request.commit_sha
            or str(provenance.get("manifest_path") or "") != request.manifest_path
            or str(provenance.get("artifact_digest") or "") != artifact_digest
            or str(provenance.get("source_manifest_sha256") or "")
            != plans[0].source_manifest_sha256
            or str(provenance.get("repo_ref") or "") != request.repo_ref
            or str(provenance.get("branch") or "") != request.base_branch
            or not self.replacements_match_authority(plans[0], changes, desired_manifest)
        ):
            raise RuntimeError(AUTHORITY_MISMATCH_MESSAGE)
        return {
            "provenance": dict(provenance),
            "desired_manifest": dict(desired_manifest),
        }

    @staticmethod
    def replacements_match_authority(
        plan: StructuredPatchPlan,
        changes: list[object],
        desired_manifest: Mapping[str, object],
    ) -> bool:
        if isinstance(plan, ManifestScalarPatchPlan):
            return scalar_patch_matches_manifest(plan, desired_manifest)
        replacement = plan.replacements[0]
        expected_suffix = f"[name={replacement.container_name}].image"
        matches = [
            change
            for change in changes
            if isinstance(change, Mapping)
            and str(change.get("field_path") or "").endswith(expected_suffix)
            and change.get("old_desired") == replacement.previous_image
            and change.get("new_desired", change.get("after")) == replacement.current_image
        ]
        return len(matches) == 1

    async def branch_head_commit_url(
        self,
        client: httpx.AsyncClient,
        repo: str,
        branch: str,
        context: dict[str, object] | None = None,
    ) -> str:
        """direct_commit 전달 결과 링크 — base 브랜치 head 커밋의 실제 URL."""
        response = await client.get(f"/repos/{repo}/commits/{quote(branch, safe='')}")
        if context is not None:
            log_provider_response("github.head_commit", response, context)
        response.raise_for_status()
        html_url = response.json().get("html_url")
        if isinstance(html_url, str) and html_url:
            return html_url
        return f"https://github.com/{repo}/commits/{branch}"

    async def put_content_file(
        self,
        client: httpx.AsyncClient,
        repo: str,
        branch: str,
        *,
        path: str,
        message: str,
        content: str,
        context: dict[str, object] | None = None,
    ) -> None:
        url = contents_api_path(repo, path)
        payload = {
            "message": message,
            "content": base64.b64encode(content.encode()).decode(),
            "branch": branch,
        }
        response = await client.put(url, json=payload)
        if context is not None:
            log_provider_response("github.put_content", response, context, path=path)
        if response.status_code == CONFLICT_STATUS:
            # 재전달로 파일이 이미 있음 — 기존 blob sha 를 붙여 갱신(멱등)
            existing = await client.get(url, params={"ref": branch})
            if context is not None:
                log_provider_response("github.get_existing_content", existing, context, path=path)
            if existing.status_code == OK_STATUS:
                sha = str(existing.json().get("sha", ""))
                if sha:
                    response = await client.put(url, json={**payload, "sha": sha})
                    if context is not None:
                        log_provider_response("github.update_content", response, context, path=path)
        response.raise_for_status()

    async def create_or_reuse_pr(
        self,
        client: httpx.AsyncClient,
        repo: str,
        branch: str,
        base_branch: str,
        request: SafePrRequestedBody,
        context: dict[str, object] | None = None,
    ) -> ScmPullRequestResult:
        response = await client.post(
            f"/repos/{repo}/pulls",
            json={
                "title": request.title,
                "body": pull_request_body(request),
                "head": branch,
                "base": base_branch,
            },
        )
        if context is not None:
            log_provider_response("github.create_pr", response, context)
        if response.status_code == CONFLICT_STATUS:
            # 재전달로 PR 이 이미 있음 — head 브랜치의 open PR URL 재사용(멱등)
            existing = await self.find_existing_pr(
                client,
                repo,
                branch,
                base_branch,
                request,
                context,
                require_request_match=False,
            )
            if existing is not None:
                return pull_request_result(existing)
            raise RuntimeError(MISSING_EXISTING_PR_MESSAGE)
        response.raise_for_status()
        return pull_request_result(response.json())

    async def find_existing_pr(
        self,
        client: httpx.AsyncClient,
        repo: str,
        branch: str,
        base_branch: str,
        request: SafePrRequestedBody,
        context: dict[str, object] | None = None,
        *,
        require_request_match: bool,
    ) -> dict[str, object] | None:
        owner = repo.split("/", 1)[0]
        response = await client.get(
            f"/repos/{repo}/pulls",
            params={"head": f"{owner}:{branch}", "state": "open"},
        )
        if context is not None:
            log_provider_response("github.find_existing_pr", response, context)
        response.raise_for_status()
        pulls = response.json()
        if not isinstance(pulls, list):
            return None
        for pull in pulls:
            if not isinstance(pull, dict) or not isinstance(pull.get("html_url"), str):
                continue
            if require_request_match and not (
                pull.get("title") == request.title
                and pull.get("body") == pull_request_body(request)
                and isinstance(pull.get("base"), dict)
                and pull["base"].get("ref") == base_branch
                and isinstance(pull.get("head"), dict)
                and pull["head"].get("ref") == branch
            ):
                continue
            return dict(pull)
        return None

    async def verify_existing_structured_pr(
        self,
        client: httpx.AsyncClient,
        repo: str,
        request: SafePrRequestedBody,
        patch_plans: list[StructuredPatchPlan | None],
        pull: Mapping[str, object],
        current_base_sha: str,
        context: dict[str, object] | None = None,
        *,
        authority: Mapping[str, object] | None = None,
    ) -> bool:
        plans = [plan for plan in patch_plans if plan is not None]
        head = pull.get("head")
        if len(plans) != 1 or not isinstance(head, Mapping):
            return False
        head_sha = str(head.get("sha") or "")
        plan = plans[0]
        if not re.fullmatch(r"[0-9a-f]{40,64}", head_sha):
            return False
        response = await client.get(f"/repos/{repo}/compare/{current_base_sha}...{head_sha}")
        if context is not None:
            log_provider_response("github.compare_existing_pr", response, context)
        response.raise_for_status()
        comparison = response.json()
        files = comparison.get("files") if isinstance(comparison, Mapping) else None
        merge_base = (
            comparison.get("merge_base_commit") if isinstance(comparison, Mapping) else None
        )
        pr_base_sha = str(merge_base.get("sha") or "") if isinstance(merge_base, Mapping) else ""
        if not re.fullmatch(r"[0-9a-f]{40,64}", pr_base_sha):
            return False
        try:
            pr_base_contents = await self.validate_structured_base_advance(
                client,
                repo,
                plan.expected_base_sha,
                pr_base_sha,
                request,
                patch_plans,
                context,
                authority=authority,
            )
            if pr_base_sha != current_base_sha:
                await self.validate_structured_base_advance(
                    client,
                    repo,
                    pr_base_sha,
                    current_base_sha,
                    request,
                    patch_plans,
                    context,
                    authority=authority,
                )
        except RuntimeError:
            return False
        if len(pr_base_contents) > 1:
            return False
        target_path = pr_base_contents[0][0] if pr_base_contents else None
        expected_paths = {change_document_path(request)}
        if target_path is not None:
            expected_paths.add(target_path)
        file_by_name = {
            str(item.get("filename") or ""): item
            for item in files or []
            if isinstance(item, Mapping)
        }
        manifest_file = file_by_name.get(target_path) if target_path is not None else None
        change_file = file_by_name.get(change_document_path(request))
        if (
            not isinstance(files, list)
            or not isinstance(merge_base, Mapping)
            or comparison.get("status") not in {"ahead", "diverged"}
            or set(file_by_name) != expected_paths
            or (
                target_path is not None
                and (
                    not isinstance(manifest_file, Mapping)
                    or manifest_file.get("status") != "modified"
                    or manifest_file.get("previous_filename") is not None
                )
            )
            or not isinstance(change_file, Mapping)
            or change_file.get("status") not in {"added", "modified"}
            or change_file.get("previous_filename") is not None
        ):
            return False
        actual_change_document = await self.source_file_content(
            client,
            repo,
            head_sha,
            change_document_path(request),
            context,
        )
        if actual_change_document != change_document(request):
            return False
        if target_path is None:
            return True
        actual_manifest = await self.source_file_content(
            client,
            repo,
            head_sha,
            target_path,
            context,
        )
        return actual_manifest == pr_base_contents[0][1]
