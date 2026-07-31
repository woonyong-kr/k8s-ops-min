"""GitHub App 자격증명 클라이언트.

PAT(개인 액세스 토큰)의 대체 경로다. 장기 비밀(개인키)은 서버 환경변수에서만
읽고 요청/로그로 절대 노출하지 않으며, 실제 git 작업에는 1시간짜리 단명
설치 토큰(installation access token)을 그때그때 발급해 쓴다.

App 미구성(개인키·App ID 없음) 시 ``is_configured()`` 가 False 를 돌려주어
상위 계층이 기존 PAT 흐름으로 자연스럽게 degrade 할 수 있게 한다.

역할:
  - ``app_jwt``                      : App 신원 증명용 단명 RS256 JWT(≤10분)
  - ``mint_installation_token``      : 설치 단위 1시간 토큰 발급
  - ``list_installation_repositories``: 설치가 접근 가능한 레포 목록(주소 불일치 감지용)
  - ``install_url``                  : 설치 리다이렉트 URL
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
import jwt

from packages.config.settings import env
from packages.contracts.gitops import DEFAULT_GITHUB_API_BASE, GITHUB_API_BASE_ENV

GITHUB_APP_ID_ENV = "GITHUB_APP_ID"
GITHUB_APP_PRIVATE_KEY_ENV = "GITHUB_APP_PRIVATE_KEY"  # PEM 원문 또는 base64(PEM)
GITHUB_APP_SLUG_ENV = "GITHUB_APP_SLUG"
GITHUB_APP_CLIENT_ID_ENV = "GITHUB_APP_CLIENT_ID"
GITHUB_APP_WEBHOOK_SECRET_ENV = "GITHUB_APP_WEBHOOK_SECRET"

# GitHub 상한은 10분. clock skew 여유(30s)를 빼고 9분으로 잡는다.
_JWT_TTL_SECONDS = 540
_JWT_BACKDATE_SECONDS = 30
_HTTP_TIMEOUT = 15.0
_MAX_REPO_PAGES = 20


class GithubAppNotConfigured(RuntimeError):
    """App 자격증명이 서버에 구성되지 않았을 때."""


def _load_private_key() -> str:
    raw = env(GITHUB_APP_PRIVATE_KEY_ENV, "").strip()
    if not raw:
        return ""
    if "BEGIN" in raw:
        return raw
    # 개행이 까다로운 배포 환경을 위해 base64(PEM) 도 허용한다.
    try:
        return base64.b64decode(raw).decode("utf-8")
    except Exception:  # noqa: BLE001 - 잘못된 base64 는 원문으로 취급
        return raw


@dataclass(frozen=True)
class GithubAppConfig:
    app_id: str
    private_key: str
    slug: str
    client_id: str
    api_base: str

    @property
    def configured(self) -> bool:
        return bool(self.app_id and self.private_key)


def load_github_app_config() -> GithubAppConfig:
    return GithubAppConfig(
        app_id=env(GITHUB_APP_ID_ENV, "").strip(),
        private_key=_load_private_key(),
        slug=env(GITHUB_APP_SLUG_ENV, "").strip(),
        client_id=env(GITHUB_APP_CLIENT_ID_ENV, "").strip(),
        api_base=env(GITHUB_API_BASE_ENV, DEFAULT_GITHUB_API_BASE).rstrip("/"),
    )


def is_configured() -> bool:
    return load_github_app_config().configured


def webhook_secret() -> str:
    return env(GITHUB_APP_WEBHOOK_SECRET_ENV, "")


class GithubAppClient:
    """App 개인키로 설치 토큰을 발급하는 클라이언트(단명 토큰만 다룬다)."""

    def __init__(self, config: GithubAppConfig | None = None) -> None:
        self._config = config or load_github_app_config()

    @property
    def config(self) -> GithubAppConfig:
        return self._config

    def _require(self) -> GithubAppConfig:
        if not self._config.configured:
            raise GithubAppNotConfigured("GitHub App 자격증명이 구성되지 않았습니다")
        return self._config

    def app_jwt(self, *, now: int | None = None) -> str:
        cfg = self._require()
        issued = int(now if now is not None else time.time()) - _JWT_BACKDATE_SECONDS
        payload = {"iat": issued, "exp": issued + _JWT_TTL_SECONDS, "iss": cfg.app_id}
        token = jwt.encode(payload, cfg.private_key, algorithm="RS256")
        # PyJWT<2 는 bytes 를 돌려주므로 방어적으로 디코드한다.
        return token.decode("utf-8") if isinstance(token, bytes) else token

    def install_url(self, *, state: str | None = None) -> str:
        cfg = self._require()
        if not cfg.slug:
            raise GithubAppNotConfigured("GITHUB_APP_SLUG 가 설정되지 않았습니다")
        base = f"https://github.com/apps/{cfg.slug}/installations/new"
        return f"{base}?state={quote(state)}" if state else base

    def _app_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.app_jwt()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def mint_installation_token(
        self,
        installation_id: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        """설치 단위 1시간 토큰을 발급한다.

        반환: ``{token, expires_at, permissions, repository_selection}``.
        """
        cfg = self._require()
        url = f"{cfg.api_base}/app/installations/{installation_id}/access_tokens"
        owns = client is None
        http = client or httpx.AsyncClient(timeout=_HTTP_TIMEOUT)
        try:
            resp = await http.post(url, headers=self._app_headers())
            resp.raise_for_status()
            return resp.json()
        finally:
            if owns:
                await http.aclose()

    def mint_installation_token_sync(
        self,
        installation_id: str,
        *,
        client: httpx.Client | None = None,
    ) -> dict[str, Any]:
        """``mint_installation_token`` 의 동기 버전(폴러 등 동기 경로용).

        폴러의 토큰 해석은 동기 함수라 async 를 호출할 수 없다. JWT 서명은 동일하게
        재사용하고 발급 POST 만 동기 httpx.Client 로 수행한다. 토큰은 1시간짜리라
        상위(캐시)에서 재사용되므로 이 블로킹 호출은 설치당 시간당 1회 수준이다.
        """
        cfg = self._require()
        url = f"{cfg.api_base}/app/installations/{installation_id}/access_tokens"
        owns = client is None
        http = client or httpx.Client(timeout=_HTTP_TIMEOUT)
        try:
            resp = http.post(url, headers=self._app_headers())
            resp.raise_for_status()
            return resp.json()
        finally:
            if owns:
                http.close()

    async def list_installation_repositories(
        self,
        installation_id: str,
        *,
        client: httpx.AsyncClient | None = None,
        token: str | None = None,
    ) -> list[dict[str, Any]]:
        """설치가 접근 가능한 레포 전체를 반환한다(주소 불일치 감지에 사용).

        ``token`` 을 주면 재발급 없이 그 설치 토큰을 재사용한다.
        """
        cfg = self._config
        owns = client is None
        http = client or httpx.AsyncClient(timeout=_HTTP_TIMEOUT)
        try:
            access_token = token or (await self.mint_installation_token(installation_id, client=http))["token"]
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            repos: list[dict[str, Any]] = []
            page = 1
            while page <= _MAX_REPO_PAGES:
                resp = await http.get(
                    f"{cfg.api_base}/installation/repositories?per_page=100&page={page}",
                    headers=headers,
                )
                resp.raise_for_status()
                batch = resp.json().get("repositories", [])
                repos.extend(batch)
                if len(batch) < 100:
                    break
                page += 1
            return repos
        finally:
            if owns:
                await http.aclose()
