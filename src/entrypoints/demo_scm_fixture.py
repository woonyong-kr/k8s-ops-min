"""Demo-only GitHub-compatible SCM fixture backed by a real Git repository.

This server exists solely for the local ``make demo`` journey.  The production
SCM writer remains ``GithubScmProvider``; the fixture only supplies the small
GitHub REST surface that provider calls, and keeps every branch/file/merge as
real Git commits for third-party inspection.
"""

from __future__ import annotations

import base64
import os
import secrets
import shutil
import subprocess
import threading
from pathlib import Path, PurePosixPath
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request

DEMO_SCM_TOKEN_ENV = "DEMO_SCM_TOKEN"
DEMO_SCM_ADMIN_TOKEN_ENV = "DEMO_SCM_ADMIN_TOKEN"
DEMO_SCM_REVIEWER_TOKEN_ENV = "DEMO_SCM_REVIEWER_TOKEN"
DEMO_SCM_REPOSITORY_ENV = "DEMO_SCM_REPOSITORY"
DEMO_SCM_REPO_REF_ENV = "DEMO_SCM_REPO_REF"
DEMO_SCM_PUBLIC_URL_ENV = "DEMO_SCM_PUBLIC_URL"


class DemoScmRepository:
    """A single demo repository with GitHub-compatible branch and PR operations."""

    def __init__(
        self,
        path: Path,
        *,
        repo_ref: str,
        public_url: str = "http://demo-scm.local",
    ) -> None:
        self.path = path.resolve()
        if self.path == Path(self.path.anchor) or self.path.name.casefold() == ".git":
            raise ValueError("unsafe demo repository root")
        self.repo_ref = repo_ref
        self.public_url = public_url.rstrip("/")
        self._lock = threading.RLock()
        self._pull_requests: dict[int, dict[str, Any]] = {}

    def reset(self, files: dict[str, str]) -> str:
        """Replace the demo repository and return the authoritative main commit."""
        with self._lock:
            if self.path.exists():
                shutil.rmtree(self.path)
            self.path.mkdir(parents=True)
            self._run("init", "--initial-branch=main")
            self._run("config", "user.name", "Opsia Demo SCM")
            self._run("config", "user.email", "demo-scm@opsia.local")
            for relative, content in files.items():
                target = self._target(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            self._run("add", "--all")
            self._run("commit", "--allow-empty", "-m", "Seed demo incident revision")
            self._pull_requests.clear()
            return self.branch_sha("main")

    def ensure_initialized(self) -> None:
        with self._lock:
            if (self.path / ".git").is_dir():
                return
            self.reset({"README.md": "# Opsia demo repository\n"})

    def branch_sha(self, branch: str) -> str:
        with self._lock:
            return self._run("rev-parse", self._branch(branch)).strip()

    def create_branch(self, branch: str, base_sha: str) -> bool:
        with self._lock:
            normalized = self._branch(branch)
            if self.branch_exists(normalized):
                return False
            self._run("cat-file", "-e", f"{base_sha}^{{commit}}")
            self._run("branch", normalized, base_sha)
            return True

    def branch_exists(self, branch: str) -> bool:
        normalized = self._branch(branch)
        result = self._run_result("show-ref", "--verify", "--quiet", f"refs/heads/{normalized}")
        return result.returncode == 0

    def file_metadata(self, branch: str, relative: str) -> dict[str, str]:
        with self._lock:
            normalized = self._branch(branch)
            path = self._relative(relative)
            result = self._run_result("rev-parse", f"{normalized}:{path}")
            if result.returncode != 0:
                raise FileNotFoundError(path)
            return {
                "sha": result.stdout.strip(),
                "content": self.file_content(normalized, path),
            }

    def file_content(self, branch: str, relative: str) -> str:
        with self._lock:
            return self._run("show", f"{self._branch(branch)}:{self._relative(relative)}")

    def put_file(
        self,
        branch: str,
        relative: str,
        content: str,
        *,
        message: str,
        expected_blob_sha: str | None,
    ) -> tuple[str, bool]:
        """Create/update one file and return ``(blob_sha, created)``."""
        with self._lock:
            normalized = self._branch(branch)
            path = self._relative(relative)
            if not self.branch_exists(normalized):
                raise KeyError(normalized)
            try:
                current = self.file_metadata(normalized, path)
            except FileNotFoundError:
                current = None
            if current is not None and expected_blob_sha is None:
                raise FileExistsError(path)
            if expected_blob_sha is not None and (
                current is None or current["sha"] != expected_blob_sha
            ):
                raise ValueError("blob sha does not match current branch content")

            self._run("switch", normalized)
            target = self._target(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            self._run("add", "--", path)
            if self._run_result("diff", "--cached", "--quiet").returncode != 0:
                self._run("commit", "-m", message)
            blob_sha = self._run("rev-parse", f"{normalized}:{path}").strip()
            return blob_sha, current is None

    def commit_files(self, branch: str, files: dict[str, str], *, message: str) -> str:
        """Commit an authoritative demo GitOps revision without creating a PR."""
        with self._lock:
            normalized = self._branch(branch)
            if not self.branch_exists(normalized):
                raise KeyError(normalized)
            self._run("switch", normalized)
            for relative, content in files.items():
                target = self._target(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            self._run("add", "--all")
            if self._run_result("diff", "--cached", "--quiet").returncode != 0:
                self._run("commit", "-m", message)
            return self.branch_sha(normalized)

    def create_pull_request(
        self,
        *,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            normalized_head = self._branch(head)
            normalized_base = self._branch(base)
            if not self.branch_exists(normalized_head) or not self.branch_exists(normalized_base):
                raise KeyError("pull request branch is missing")
            for pull in self._pull_requests.values():
                if (
                    pull["state"] == "open"
                    and pull["head_ref"] == normalized_head
                    and pull["base_ref"] == normalized_base
                ):
                    return None
            number = len(self._pull_requests) + 1
            self._pull_requests[number] = {
                "number": number,
                "title": title,
                "body": body,
                "state": "open",
                "head_ref": normalized_head,
                "base_ref": normalized_base,
                "merged": False,
                "merge_commit_sha": None,
            }
            return self.pull_request(number)

    def list_pull_requests(
        self, *, head: str | None = None, state: str = "open"
    ) -> list[dict[str, Any]]:
        with self._lock:
            normalized_head = head.split(":", 1)[-1] if head else None
            return [
                self.pull_request(number)
                for number, pull in sorted(self._pull_requests.items())
                if (not state or pull["state"] == state)
                and (normalized_head is None or pull["head_ref"] == normalized_head)
            ]

    def pull_request(self, number: int) -> dict[str, Any]:
        with self._lock:
            try:
                pull = self._pull_requests[number]
            except KeyError as exc:
                raise KeyError(f"pull request {number} not found") from exc
            head_ref = str(pull["head_ref"])
            base_ref = str(pull["base_ref"])
            return {
                "number": number,
                "html_url": f"{self.public_url}/demo/pulls/{number}",
                "title": pull["title"],
                "body": pull["body"],
                "state": pull["state"],
                "merged": pull["merged"],
                "merge_commit_sha": pull["merge_commit_sha"],
                "head": {"ref": head_ref, "sha": self.branch_sha(head_ref)},
                "base": {"ref": base_ref, "sha": self.branch_sha(base_ref)},
            }

    def merge_pull_request(
        self,
        number: int,
        *,
        expected_base_sha: str,
        expected_head_sha: str,
    ) -> str:
        with self._lock:
            pull = self._pull_requests.get(number)
            if pull is None:
                raise KeyError(f"pull request {number} not found")
            if pull["state"] != "open":
                return str(pull["merge_commit_sha"] or "")
            base = str(pull["base_ref"])
            head = str(pull["head_ref"])
            if self.branch_sha(base) != expected_base_sha:
                raise ValueError("reviewed base revision changed")
            if self.branch_sha(head) != expected_head_sha:
                raise ValueError("reviewed head revision changed")
            self._run("switch", base)
            self._run("merge", "--no-ff", head, "-m", f"Merge demo pull request #{number}")
            sha = self.branch_sha(base)
            pull["state"] = "closed"
            pull["merged"] = True
            pull["merge_commit_sha"] = sha
            return sha

    def _target(self, relative: str) -> Path:
        target = (self.path / self._relative(relative)).resolve()
        if not target.is_relative_to(self.path):
            raise ValueError("repository path escapes demo root")
        return target

    @staticmethod
    def _relative(value: str) -> str:
        raw = value.strip()
        path = PurePosixPath(raw)
        if not raw or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError(f"unsafe repository path: {value}")
        if any(part.casefold() == ".git" for part in path.parts):
            raise ValueError("repository metadata paths are forbidden")
        return str(path)

    def _branch(self, value: str) -> str:
        branch = value.removeprefix("refs/heads/").strip()
        if not branch:
            raise ValueError("branch is required")
        result = self._run_result("check-ref-format", "--branch", branch)
        if result.returncode != 0:
            raise ValueError(f"unsafe branch: {value}")
        return branch

    def _run(self, *args: str) -> str:
        result = self._run_result(*args)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
        return result.stdout

    def _run_result(self, *args: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", "-C", str(self.path), *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("demo Git operation timed out") from exc


def create_app(
    repository: DemoScmRepository,
    *,
    token: str,
    admin_token: str,
    reviewer_token: str,
) -> FastAPI:
    if len({token, admin_token, reviewer_token}) != 3:
        raise ValueError("writer, admin, and reviewer tokens must differ")
    app = FastAPI(title="Opsia demo SCM fixture", docs_url=None, redoc_url=None)

    def authorize(request: Request) -> None:
        supplied = request.headers.get("authorization", "")
        if not secrets.compare_digest(supplied, f"Bearer {token}"):
            raise HTTPException(status_code=401, detail="demo SCM token required")

    def authorize_reviewer(request: Request) -> None:
        supplied = request.headers.get("authorization", "")
        if not secrets.compare_digest(supplied, f"Bearer {reviewer_token}"):
            raise HTTPException(status_code=401, detail="demo reviewer token required")

    def authorize_admin(request: Request) -> None:
        supplied = request.headers.get("authorization", "")
        if not secrets.compare_digest(supplied, f"Bearer {admin_token}"):
            raise HTTPException(status_code=401, detail="demo harness admin token required")

    def require_repo(owner: str, name: str) -> None:
        if f"{owner}/{name}" != repository.repo_ref:
            raise HTTPException(status_code=404, detail="repository not found")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/repos/{owner}/{name}/git/ref/heads/{branch:path}")
    def get_ref(owner: str, name: str, branch: str, request: Request) -> dict[str, Any]:
        authorize(request)
        require_repo(owner, name)
        try:
            sha = repository.branch_sha(branch)
        except RuntimeError as exc:
            raise HTTPException(status_code=404, detail="branch not found") from exc
        return {"ref": f"refs/heads/{branch}", "object": {"sha": sha}}

    @app.post("/repos/{owner}/{name}/git/refs", status_code=201)
    async def create_ref(owner: str, name: str, request: Request) -> dict[str, str]:
        authorize(request)
        require_repo(owner, name)
        payload = await request.json()
        ref = str(payload.get("ref") or "")
        sha = str(payload.get("sha") or "")
        try:
            created = repository.create_branch(ref, sha)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not created:
            raise HTTPException(status_code=422, detail="Reference already exists")
        return {"ref": ref}

    @app.get("/repos/{owner}/{name}/contents/{path:path}")
    def get_content(
        owner: str,
        name: str,
        path: str,
        request: Request,
        ref: str = Query(default="main"),
    ) -> dict[str, str]:
        authorize(request)
        require_repo(owner, name)
        try:
            item = repository.file_metadata(ref, path)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="content not found") from exc
        return {
            "sha": item["sha"],
            "encoding": "base64",
            "content": base64.b64encode(item["content"].encode()).decode(),
        }

    @app.put("/repos/{owner}/{name}/contents/{path:path}")
    async def put_content(owner: str, name: str, path: str, request: Request) -> dict[str, Any]:
        authorize(request)
        require_repo(owner, name)
        payload = await request.json()
        try:
            content = base64.b64decode(str(payload.get("content") or ""), validate=True).decode()
            blob_sha, created = repository.put_file(
                str(payload.get("branch") or "main"),
                path,
                content,
                message=str(payload.get("message") or f"Update {path}"),
                expected_blob_sha=str(payload["sha"]) if payload.get("sha") else None,
            )
        except FileExistsError as exc:
            raise HTTPException(status_code=422, detail="sha required for update") from exc
        except (KeyError, RuntimeError, UnicodeDecodeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"content": {"sha": blob_sha}, "created": created}

    @app.post("/repos/{owner}/{name}/pulls", status_code=201)
    async def create_pull(owner: str, name: str, request: Request) -> dict[str, Any]:
        authorize(request)
        require_repo(owner, name)
        payload = await request.json()
        try:
            pull = repository.create_pull_request(
                title=str(payload.get("title") or ""),
                body=str(payload.get("body") or ""),
                head=str(payload.get("head") or ""),
                base=str(payload.get("base") or "main"),
            )
        except (KeyError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if pull is None:
            raise HTTPException(status_code=422, detail="A pull request already exists")
        return pull

    @app.get("/repos/{owner}/{name}/pulls")
    def list_pulls(
        owner: str,
        name: str,
        request: Request,
        head: str | None = Query(default=None),
        state: str = Query(default="open"),
    ) -> list[dict[str, Any]]:
        authorize(request)
        require_repo(owner, name)
        return repository.list_pull_requests(head=head, state=state)

    @app.post("/demo/reset")
    async def reset(request: Request) -> dict[str, str]:
        authorize_admin(request)
        payload = await request.json()
        files = payload.get("files")
        if not isinstance(files, dict) or not all(
            isinstance(path, str) and isinstance(content, str) for path, content in files.items()
        ):
            raise HTTPException(status_code=422, detail="files must be a string map")
        return {"main_sha": repository.reset(dict(files))}

    @app.post("/demo/commits")
    async def commit_demo_revision(request: Request) -> dict[str, str]:
        authorize_admin(request)
        payload = await request.json()
        files = payload.get("files")
        if not isinstance(files, dict) or not all(
            isinstance(path, str) and isinstance(content, str) for path, content in files.items()
        ):
            raise HTTPException(status_code=422, detail="files must be a string map")
        try:
            sha = repository.commit_files(
                str(payload.get("branch") or "main"),
                dict(files),
                message=str(payload.get("message") or "Commit demo revision"),
            )
        except (KeyError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"commit_sha": sha}

    @app.get("/demo/pulls/{number}")
    def get_demo_pull(number: int, request: Request) -> dict[str, Any]:
        authorize(request)
        try:
            return repository.pull_request(number)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/demo/pulls/{number}/merge")
    async def merge_demo_pull(number: int, request: Request) -> dict[str, Any]:
        authorize_reviewer(request)
        payload = await request.json()
        try:
            sha = repository.merge_pull_request(
                number,
                expected_base_sha=str(payload.get("expected_base_sha") or ""),
                expected_head_sha=str(payload.get("expected_head_sha") or ""),
            )
        except (KeyError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"merged": True, "merge_commit_sha": sha}

    @app.get("/demo/files/{path:path}")
    def get_demo_file(
        path: str,
        request: Request,
        ref: str = Query(default="main"),
    ) -> dict[str, str]:
        authorize(request)
        try:
            return {"ref": ref, "path": path, "content": repository.file_content(ref, path)}
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="content not found") from exc

    return app


def main() -> None:
    token = os.environ.get(DEMO_SCM_TOKEN_ENV, "").strip()
    if not token:
        raise RuntimeError(f"{DEMO_SCM_TOKEN_ENV} is required")
    admin_token = os.environ.get(DEMO_SCM_ADMIN_TOKEN_ENV, "").strip()
    if not admin_token:
        raise RuntimeError(f"{DEMO_SCM_ADMIN_TOKEN_ENV} is required")
    reviewer_token = os.environ.get(DEMO_SCM_REVIEWER_TOKEN_ENV, "").strip()
    if not reviewer_token:
        raise RuntimeError(f"{DEMO_SCM_REVIEWER_TOKEN_ENV} is required")
    repository = DemoScmRepository(
        Path(os.environ.get(DEMO_SCM_REPOSITORY_ENV, "/tmp/opsia-demo-repository")),
        repo_ref=os.environ.get(DEMO_SCM_REPO_REF_ENV, "opsia/demo"),
        public_url=os.environ.get(
            DEMO_SCM_PUBLIC_URL_ENV,
            "http://opsia-demo-scm.opsia-system.svc:8080",
        ),
    )
    repository.ensure_initialized()
    uvicorn.run(
        create_app(
            repository,
            token=token,
            admin_token=admin_token,
            reviewer_token=reviewer_token,
        ),
        host="0.0.0.0",
        port=8080,
    )


if __name__ == "__main__":
    main()
