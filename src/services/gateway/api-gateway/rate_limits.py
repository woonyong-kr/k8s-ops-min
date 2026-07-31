from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException, Request
from passwords import normalize_email
from settings import Settings

from packages.contracts.interfaces import SessionStore
from packages.storage.sessions import RateLimitExceeded

AuthenticatedRequestClass = Literal["read", "mutation"]
READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class AuthRateLimitPolicy:
    scope: str
    email_limit: int
    client_limit: int
    window_seconds: int
    lock_steps_seconds: tuple[int, ...]
    strike_ttl_seconds: int

    def email_key(self, email: str) -> str:
        return f"auth:{self.scope}:email:{stable_rate_key(normalize_email(email))}"

    def client_key(self, client_key: str) -> str:
        return f"auth:{self.scope}:client:{stable_rate_key(client_key)}"


class AuthRateLimiter:
    def __init__(self, sessions: SessionStore) -> None:
        self.sessions = sessions

    async def check(self, policy: AuthRateLimitPolicy, email: str, client_key: str) -> None:
        try:
            await self.sessions.check_escalating_rate_limit(
                policy.email_key(email),
                policy.email_limit,
                policy.window_seconds,
                policy.lock_steps_seconds,
                policy.strike_ttl_seconds,
            )
            await self.sessions.check_escalating_rate_limit(
                policy.client_key(client_key),
                policy.client_limit,
                policy.window_seconds,
                policy.lock_steps_seconds,
                policy.strike_ttl_seconds,
            )
        except RateLimitExceeded as exc:
            raise _rate_limited_http_exception(exc) from None


@dataclass(frozen=True)
class AuthenticatedRequestRateLimitPolicy:
    request_class: AuthenticatedRequestClass
    session_limit: int
    user_limit: int | None
    window_seconds: int


class AuthenticatedRequestRateLimiter:
    def __init__(self, sessions: SessionStore) -> None:
        self.sessions = sessions

    async def check(self, request: Request, *, token: str, user_id: str) -> None:
        policy = authenticated_request_rate_limit_policy(str(request.scope.get("method", "GET")))
        session_key = _session_request_key(policy.request_class, token)
        try:
            await self.sessions.check_rate_limit(
                session_key,
                policy.session_limit,
                policy.window_seconds,
            )
            if policy.user_limit is not None:
                await self.sessions.check_rate_limit(
                    _user_request_key(policy.request_class, user_id),
                    policy.user_limit,
                    policy.window_seconds,
                )
        except RateLimitExceeded as exc:
            raise _rate_limited_http_exception(exc, policy.request_class) from None


def authenticated_request_rate_limit_policy(
    method: str,
) -> AuthenticatedRequestRateLimitPolicy:
    if method.upper() in READ_ONLY_METHODS:
        return AuthenticatedRequestRateLimitPolicy(
            request_class="read",
            session_limit=Settings.AUTHENTICATED_READ_RATE_LIMIT,
            user_limit=None,
            window_seconds=Settings.AUTHENTICATED_READ_RATE_WINDOW_SECONDS,
        )
    return AuthenticatedRequestRateLimitPolicy(
        request_class="mutation",
        session_limit=Settings.AUTHENTICATED_MUTATION_SESSION_RATE_LIMIT,
        user_limit=Settings.AUTHENTICATED_MUTATION_USER_RATE_LIMIT,
        window_seconds=Settings.AUTHENTICATED_MUTATION_RATE_WINDOW_SECONDS,
    )


def _session_request_key(request_class: AuthenticatedRequestClass, token: str) -> str:
    return f"authenticated:{request_class}:session:{stable_rate_key(token)}"


def _user_request_key(request_class: AuthenticatedRequestClass, user_id: str) -> str:
    return f"authenticated:{request_class}:user:{stable_rate_key(user_id)}"


def _rate_limited_http_exception(
    exc: RateLimitExceeded,
    request_class: AuthenticatedRequestClass | None = None,
) -> HTTPException:
    retry_after = exc.retry_after_seconds or 1
    detail: dict[str, str | int] = {
        "code": "rate_limited",
        "detail": Settings.RATE_LIMIT_EXCEEDED_MESSAGE,
        "retry_after": retry_after,
    }
    if request_class is not None:
        detail["request_class"] = request_class
    return HTTPException(
        status_code=429,
        detail=detail,
        headers={"Retry-After": str(retry_after)},
    )


def signup_rate_limit_policy() -> AuthRateLimitPolicy:
    return _auth_policy(
        scope="signup",
        email_limit=Settings.SIGNUP_EMAIL_RATE_LIMIT,
        client_limit=Settings.SIGNUP_IP_RATE_LIMIT,
    )


def login_rate_limit_policy() -> AuthRateLimitPolicy:
    return _auth_policy(
        scope="login",
        email_limit=Settings.LOGIN_EMAIL_RATE_LIMIT,
        client_limit=Settings.LOGIN_IP_RATE_LIMIT,
    )


def resend_verification_rate_limit_policy() -> AuthRateLimitPolicy:
    return _auth_policy(
        scope="resend",
        email_limit=Settings.RESEND_EMAIL_RATE_LIMIT,
        client_limit=Settings.RESEND_IP_RATE_LIMIT,
    )


def check_email_rate_limit_policy() -> AuthRateLimitPolicy:
    return _auth_policy(
        scope="check_email",
        email_limit=Settings.CHECK_EMAIL_EMAIL_RATE_LIMIT,
        client_limit=Settings.CHECK_EMAIL_IP_RATE_LIMIT,
    )


def resend_verification_cooldown_policy() -> AuthRateLimitPolicy:
    return AuthRateLimitPolicy(
        scope="resend_cooldown",
        email_limit=1,
        client_limit=Settings.RESEND_IP_RATE_LIMIT,
        window_seconds=Settings.RESEND_EMAIL_COOLDOWN_SECONDS,
        lock_steps_seconds=(Settings.RESEND_EMAIL_COOLDOWN_SECONDS,),
        strike_ttl_seconds=Settings.RESEND_EMAIL_COOLDOWN_SECONDS,
    )


def _auth_policy(scope: str, email_limit: int, client_limit: int) -> AuthRateLimitPolicy:
    return AuthRateLimitPolicy(
        scope=scope,
        email_limit=email_limit,
        client_limit=client_limit,
        window_seconds=Settings.AUTH_ABUSE_RATE_WINDOW_SECONDS,
        lock_steps_seconds=(
            Settings.AUTH_ABUSE_FIRST_LOCK_SECONDS,
            Settings.AUTH_ABUSE_SECOND_LOCK_SECONDS,
            Settings.AUTH_ABUSE_THIRD_LOCK_SECONDS,
        ),
        strike_ttl_seconds=Settings.AUTH_ABUSE_STRIKE_TTL_SECONDS,
    )


def stable_rate_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
