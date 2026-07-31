from __future__ import annotations

import io
import os
import re
import shutil
import stat
import subprocess
import tarfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path


class GitRepoCacheError(RuntimeError):
    """로컬 git object cache 가 commit/path 읽기를 처리하지 못할 때 발생."""


class GitRepoCache:
    def __init__(
        self,
        *,
        cache_dir: str,
        remote_url: str,
        timeout_seconds: float,
        max_bytes: int = 0,
        max_repos: int = 0,
        http_extra_header: str | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.remote_url = remote_url
        self.timeout_seconds = timeout_seconds
        self.repo_dir = self.cache_dir / f"{safe_cache_key(remote_url)}.git"
        self.lock_path = self.cache_dir / f"{safe_cache_key(remote_url)}.lock"
        self.max_bytes = max(0, max_bytes)
        self.max_repos = max(0, max_repos)
        self.http_extra_header = http_extra_header

    def read_file(self, commit_sha: str, manifest_path: str) -> str:
        with self._repo_lock():
            self._evict_cache()
            self._ensure_repo()
            if not self._has_commit(commit_sha):
                self._fetch()
            if not self._has_commit(commit_sha):
                raise GitRepoCacheError(f"git cache does not contain commit: {commit_sha}")
            output = self._git("show", f"{commit_sha}:{manifest_path}").stdout
            self._touch_repo()
            self._evict_cache()
            return output

    def export_path(self, commit_sha: str, manifest_path: str, destination: Path) -> Path:
        with self._repo_lock():
            self._evict_cache()
            self._ensure_repo()
            if not self._has_commit(commit_sha):
                self._fetch()
            if not self._has_commit(commit_sha):
                raise GitRepoCacheError(f"git cache does not contain commit: {commit_sha}")
            archive = self._git_bytes("archive", "--format=tar", commit_sha, manifest_path)
            self._touch_repo()
            self._evict_cache()
        return extract_git_archive(archive, destination, manifest_path)

    @contextmanager
    def _repo_lock(self) -> Iterator[None]:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as handle:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                handle.write(b"0")
                handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                return

            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def _try_repo_lock(self, lock_path: Path) -> Iterator[bool]:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                handle.write(b"0")
                handle.flush()
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    yield False
                    return
                try:
                    yield True
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                return

            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError):
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _ensure_repo(self) -> None:
        if self.repo_dir.exists():
            return
        self.repo_dir.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "clone", "--bare", self.remote_url, str(self.repo_dir)],
            check=False,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            env=self._git_env(),
        )
        if result.returncode != 0:
            raise GitRepoCacheError(result.stderr.strip() or "git clone failed")

    def _has_commit(self, commit_sha: str) -> bool:
        result = subprocess.run(
            [
                "git",
                f"--git-dir={self.repo_dir}",
                "cat-file",
                "-e",
                f"{commit_sha}^{{commit}}",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            env=self._git_env(),
        )
        return result.returncode == 0

    def _fetch(self) -> None:
        self._git("fetch", "--prune", "origin")

    def _touch_repo(self) -> None:
        if self.repo_dir.exists():
            os.utime(self.repo_dir, None)

    def _evict_cache(self) -> None:
        if not self.max_bytes and not self.max_repos:
            return
        repos = self._cache_repos()
        candidates = [repo for repo in repos if repo != self.repo_dir]
        if self.max_repos:
            while len(repos) > self.max_repos and candidates:
                victim = candidates.pop(0)
                if self._evict_repo(victim) is not None:
                    repos = [repo for repo in repos if repo != victim]
        if self.max_bytes:
            total = sum(dir_size(repo) for repo in repos if repo.exists())
            while total > self.max_bytes and candidates:
                victim = candidates.pop(0)
                size = self._evict_repo(victim)
                if size is None:
                    continue
                total -= size

    def _evict_repo(self, path: Path) -> int | None:
        with self._try_repo_lock(path.with_suffix(".lock")) as locked:
            if not locked:
                return None
            size = dir_size(path)
            self._safe_rmtree(path)
            return size

    def _cache_repos(self) -> list[Path]:
        repos: list[tuple[float, str, Path]] = []
        for path in self.cache_dir.glob("*.git"):
            try:
                stat_result = path.stat()
            except OSError:
                continue
            if path.is_dir():
                repos.append((stat_result.st_mtime, path.name, path))
        return [path for _mtime, _name, path in sorted(repos)]

    def _safe_rmtree(self, path: Path) -> None:
        if not path.exists():
            return

        def remove_readonly(func: Callable[[str], object], target: str, _exc_info: object) -> None:
            os.chmod(target, stat.S_IWRITE)
            func(target)

        shutil.rmtree(path, onerror=remove_readonly)

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", f"--git-dir={self.repo_dir}", *args],
                check=True,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                env=self._git_env(),
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise GitRepoCacheError(str(exc)) from exc

    def _git_bytes(self, *args: str) -> bytes:
        try:
            return subprocess.run(
                ["git", f"--git-dir={self.repo_dir}", *args],
                check=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                env=self._git_env(),
            ).stdout
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise GitRepoCacheError(str(exc)) from exc

    # git subprocess 에 전달할 환경변수 화이트리스트.
    # os.environ 전체를 상속하면 LLM/API 키 등 무관한 시크릿이 자식 프로세스에 노출되고,
    # 실패 시 진단 덤프에 딸려 나갈 수 있다 → 필요한 것만 명시적으로 전달.
    _GIT_ENV_ALLOWLIST = (
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
        "GIT_SSL_CAINFO",
        "GIT_SSL_CAPATH",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    )

    def _git_env(self) -> dict[str, str]:
        base = {key: os.environ[key] for key in self._GIT_ENV_ALLOWLIST if key in os.environ}
        # 자격증명 미설정 시 프롬프트 대기로 hang 하지 않고 즉시 실패하도록 고정.
        base["GIT_TERMINAL_PROMPT"] = "0"
        if not self.http_extra_header:
            return base
        return {
            **base,
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.extraHeader",
            "GIT_CONFIG_VALUE_0": self.http_extra_header,
        }


def safe_cache_key(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "repo"


def dir_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def extract_git_archive(archive: bytes, destination: Path, manifest_path: str) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    destination_root = destination.resolve()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as tar:
        for member in tar.getmembers():
            if member.issym() or member.islnk():
                raise GitRepoCacheError(f"git archive contains unsupported link: {member.name}")
            target = (destination / member.name).resolve()
            if destination_root != target and destination_root not in target.parents:
                raise GitRepoCacheError(f"git archive escapes destination: {member.name}")
        tar.extractall(destination, filter="data")
    normalized = manifest_path.strip("/\\").replace("\\", "/")
    exported = destination / normalized
    if not exported.exists():
        raise GitRepoCacheError(f"git archive did not contain path: {manifest_path}")
    return exported
