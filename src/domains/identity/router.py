"""identity 도메인 HTTP 라우터 — 세션과 내부 로그인 경계."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse

from domains.identity.dependencies import (
    get_password_auth,
    require_admin_session,
    require_session,
)
from domains.mail.events import EmailVerificationRequestedBody
from packages.config.constants import Auth
from packages.config.settings import env
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.gateway.requests import (
    EmailCheckRequest,
    LoginRequest,
    ResendEmailVerificationRequest,
    SignupRequest,
    WorkspaceSwitchRequest,
)
from packages.contracts.gateway.responses import (
    AuthLogoutCapability,
    AuthSessionResponse,
    AuthWorkspaceItem,
    AuthWorkspaceListResponse,
    EmailCheckResponse,
    EmailVerificationResponse,
    LogoutResponse,
    UserApprovalResponse,
)
from packages.contracts.identity import DEFAULT_WORKSPACE_ID
from packages.runtime.dependencies import get_events
from packages.security.trusted_proxy import TRUSTED_PROXY_SESSION_TOKEN

router = APIRouter()
PUBLIC_BASE_URL_ENV = "PUBLIC_BASE_URL"
PUBLIC_API_BASE_URL_ENV = "PUBLIC_API_BASE_URL"
EMAIL_VERIFICATION_SUCCESS_REDIRECT = "/login?verified=1"
EMAIL_VERIFICATION_PENDING_APPROVAL_REDIRECT = "/login?verified=1&approval=pending"


FALSE_COOKIE_SECURE_VALUES = {"0", "false", "no", "off"}


def _cookie_secure() -> bool:
    """COOKIE_SECURE 판정 — http 배포에서 Secure 쿠키는 브라우저가 버려 로그인 직후 세션이 사라진다.

    "0" 외에 false/no/off 도 비보안(http) 신호로 인정해 설정 실수를 줄인다. 기본은 Secure.
    """
    return env(Auth.COOKIE_SECURE_ENV, "1").strip().lower() not in FALSE_COOKIE_SECURE_VALUES


def _set_session_cookie(response: Response, session: Any) -> None:
    # 토큰을 JSON 으로 돌려주지 않고 httpOnly 쿠키로 심음 → JS 가 못 읽어 XSS 탈취 차단.
    secure = _cookie_secure()
    response.set_cookie(
        key=Auth.SESSION_COOKIE_NAME,
        value=session.token,
        httponly=True,
        secure=secure,
        samesite=Auth.COOKIE_SAMESITE,
        max_age=int(env(Auth.SESSION_TTL_ENV, Auth.DEFAULT_SESSION_TTL_SECONDS)),
    )


def _clear_session_cookie(response: Response) -> None:
    secure = _cookie_secure()
    response.delete_cookie(
        key=Auth.SESSION_COOKIE_NAME,
        httponly=True,
        secure=secure,
        samesite=Auth.COOKIE_SAMESITE,
    )


def _authenticated_body(session: Any, password_auth: Any | None = None) -> AuthSessionResponse:
    auth_mode = getattr(
        session,
        "auth_mode",
        "trusted_proxy" if session.token == TRUSTED_PROXY_SESSION_TOKEN else "password",
    )
    display_name = getattr(session, "display_name", None)
    email = getattr(session, "email", None)
    groups: list[str] = []
    roles = list(session.roles)
    if auth_mode == "password":
        identity = (
            password_auth.session_identity(session.user_id, session.workspace_id)
            if password_auth is not None
            else None
        )
        if identity is None:
            raise HTTPException(status_code=401, detail="authentication required")
        display_name = identity.get("display_name")
        email = identity.get("email")
        groups = [str(group) for group in identity.get("groups", [])]
        roles = [str(role) for role in identity.get("roles", [])]
    logout = (
        AuthLogoutCapability(
            action="upstream_identity_required",
            supported=False,
            reauthentication_expected=True,
        )
        if auth_mode == "trusted_proxy"
        else AuthLogoutCapability(
            action="end_session",
            supported=True,
            reauthentication_expected=False,
        )
    )
    return AuthSessionResponse(
        authenticated=True,
        auth_enabled=True,
        auth_mode=auth_mode,
        display_name=display_name,
        email=email,
        user_id=session.user_id,
        groups=groups,
        roles=roles,
        workspace_id=session.workspace_id,
        logout=logout,
    )


def _verification_url(request: Request, token: str) -> str:
    query = urlencode({"token": token})
    public_api_base_url = env(PUBLIC_API_BASE_URL_ENV, "").rstrip("/")
    if public_api_base_url:
        return f"{public_api_base_url}{gateway_routes.AUTH_VERIFY_EMAIL_PATH}?{query}"
    public_base_url = env(PUBLIC_BASE_URL_ENV, "").rstrip("/")
    if public_base_url:
        return f"{public_base_url}/api{gateway_routes.AUTH_VERIFY_EMAIL_PATH}?{query}"
    return f"{request.url_for('verify_email')}?{query}"


def _safe_redirect_path(path: str | None) -> str:
    if not path:
        return EMAIL_VERIFICATION_SUCCESS_REDIRECT
    if not path.startswith("/") or path.startswith("//") or "://" in path:
        return EMAIL_VERIFICATION_SUCCESS_REDIRECT
    return path


TRUST_PROXY_ENV = "TRUST_PROXY"


def _client_key(request: Request) -> str:
    # X-Forwarded-For 는 신뢰 프록시(TRUST_PROXY=1) 뒤에서만 신뢰 — 아니면 헤더 스푸핑으로
    # 레이트리밋을 우회할 수 있음. 기본은 소켓 peer IP 사용.
    if env(TRUST_PROXY_ENV, "") == "1":
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
    if request.client is not None:
        return request.client.host
    return "unknown"


async def _request_email_verification(request: Request, events: Any, challenge: Any) -> None:
    await events.accept_body(
        EmailVerificationRequestedBody(
            email=challenge.email,
            verification_url=_verification_url(request, challenge.token),
            expires_in_seconds=challenge.expires_in_seconds,
        )
    )


@router.get(gateway_routes.AUTH_SESSION_PATH, response_model=AuthSessionResponse)
async def session(
    current: Any = Depends(require_session),
    password_auth: Any = Depends(get_password_auth),
) -> AuthSessionResponse:
    return _authenticated_body(current, password_auth)


@router.post(gateway_routes.AUTH_SESSION_REFRESH_PATH, response_model=AuthSessionResponse)
async def refresh_session(
    response: Response,
    current: Any = Depends(require_session),
    password_auth: Any = Depends(get_password_auth),
) -> AuthSessionResponse:
    if current.token == TRUSTED_PROXY_SESSION_TOKEN:
        return _authenticated_body(current, password_auth)
    if not await password_auth.sessions.touch_session(current.token):
        raise HTTPException(status_code=401, detail="authentication required")
    _set_session_cookie(response, current)
    return _authenticated_body(current, password_auth)


@router.get(
    gateway_routes.AUTH_WORKSPACES_PATH,
    response_model=AuthWorkspaceListResponse,
)
async def list_workspaces(
    current: Any = Depends(require_session),
    password_auth: Any = Depends(get_password_auth),
) -> AuthWorkspaceListResponse:
    return AuthWorkspaceListResponse(
        current_workspace_id=current.workspace_id,
        items=[
            AuthWorkspaceItem.model_validate(item)
            for item in password_auth.list_authorized_workspaces(current)
        ],
    )


@router.post(
    gateway_routes.AUTH_WORKSPACE_SWITCH_PATH,
    response_model=AuthSessionResponse,
)
async def switch_workspace(
    payload: WorkspaceSwitchRequest,
    response: Response,
    current: Any = Depends(require_session),
    password_auth: Any = Depends(get_password_auth),
) -> AuthSessionResponse:
    next_session = await password_auth.switch_workspace(current, payload.workspace_id)
    _set_session_cookie(response, next_session)
    return _authenticated_body(next_session, password_auth)


@router.post(gateway_routes.AUTH_SIGNUP_PATH, response_model=EmailVerificationResponse)
async def signup(
    payload: SignupRequest,
    request: Request,
    password_auth: Any = Depends(get_password_auth),
    events: Any = Depends(get_events),
) -> EmailVerificationResponse:
    challenge = await password_auth.signup(
        payload.email,
        payload.password,
        payload.password_confirm,
        _client_key(request),
    )
    await _request_email_verification(request, events, challenge)
    return EmailVerificationResponse(
        accepted=True,
        verification_required=True,
        email=challenge.email,
    )


@router.post(gateway_routes.AUTH_CHECK_EMAIL_PATH, response_model=EmailCheckResponse)
async def check_email(
    payload: EmailCheckRequest,
    request: Request,
    password_auth: Any = Depends(get_password_auth),
) -> EmailCheckResponse:
    try:
        available = await password_auth.check_email_available(payload.email, _client_key(request))
    except HTTPException as exc:
        if exc.status_code == 429 and isinstance(exc.detail, dict):
            raise HTTPException(
                status_code=429,
                detail={
                    "code": str(exc.detail.get("code") or "rate_limited"),
                    "detail": str(exc.detail.get("detail") or "요청이 너무 많습니다."),
                    "retry_after": exc.detail.get("retry_after"),
                },
            ) from exc
        raise
    if available:
        return EmailCheckResponse(available=True)
    return EmailCheckResponse(
        available=False,
        reason_code="already_registered",
        detail="이미 가입된 이메일입니다.",
    )


@router.post(
    gateway_routes.AUTH_RESEND_VERIFICATION_PATH,
    response_model=EmailVerificationResponse,
)
async def resend_verification(
    payload: ResendEmailVerificationRequest,
    request: Request,
    password_auth: Any = Depends(get_password_auth),
    events: Any = Depends(get_events),
) -> EmailVerificationResponse:
    challenge = await password_auth.resend_email_verification(
        payload.email, payload.password, _client_key(request)
    )
    if challenge is None:
        return EmailVerificationResponse(accepted=True, verification_required=False)
    await _request_email_verification(request, events, challenge)
    return EmailVerificationResponse(
        accepted=True,
        verification_required=True,
        email=challenge.email,
    )


@router.post(gateway_routes.AUTH_LOGIN_PATH, response_model=AuthSessionResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    password_auth: Any = Depends(get_password_auth),
) -> AuthSessionResponse:
    current = await password_auth.login(payload.email, payload.password, _client_key(request))
    _set_session_cookie(response, current)
    return _authenticated_body(current, password_auth)


@router.get(gateway_routes.AUTH_VERIFY_EMAIL_PATH)
async def verify_email(
    token: str = Query(min_length=1),
    redirect: str | None = None,
    password_auth: Any = Depends(get_password_auth),
) -> RedirectResponse:
    result = await password_auth.verify_email(token)
    if result.session is None:
        return RedirectResponse(url=EMAIL_VERIFICATION_PENDING_APPROVAL_REDIRECT, status_code=303)
    response = RedirectResponse(url=_safe_redirect_path(redirect), status_code=303)
    _set_session_cookie(response, result.session)
    return response


@router.post(gateway_routes.AUTH_APPROVE_USER_PATH, response_model=UserApprovalResponse)
async def approve_user(
    user_id: str,
    current: Any = Depends(require_admin_session),
    password_auth: Any = Depends(get_password_auth),
) -> UserApprovalResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    user = await password_auth.approve_user(user_id, workspace_id)
    return UserApprovalResponse(
        accepted=True,
        user_id=str(user["user_id"]),
        status=str(user["status"]),
        role=str(user["role"]),
        workspace_id=str(user.get("workspace_id", workspace_id)),
    )


@router.post(gateway_routes.AUTH_LOGOUT_PATH, response_model=LogoutResponse)
async def logout(
    response: Response,
    current: Any = Depends(require_session),
    password_auth: Any = Depends(get_password_auth),
) -> LogoutResponse:
    await password_auth.logout(current.token)
    _clear_session_cookie(response)
    return LogoutResponse(authenticated=False)
