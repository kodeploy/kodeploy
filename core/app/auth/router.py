"""auth 도메인 엔드포인트 (GitHub App user-to-server OAuth).

GET  /auth/github/login    — state cookie 발급 + GitHub authorize로 302
GET  /auth/github/callback — code/state 검증 → 세션 발급 + WEB_BASE_URL로 302
GET  /auth/me              — 현재 user (401 if 미로그인)
POST /auth/logout          — 세션 revoke + cookie 삭제
"""

import logging
import secrets

import httpx
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session as SASession

from app import config
from app.apps import service as apps_service
from app.auth import account, service as auth_service
from app.auth.deps import get_current_user
from app.auth.model import User
from app.auth.schemas import UserOut
from app.shared.db import get_db

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger(__name__)


# 세션 cookie 설정 — 정책(secure/samesite/domain)은 config에서.
# httponly=True 고정 — JS에서 cookie 읽지 못하게(XSS 시 탈취 방지).
def _set_session_cookie(response: Response, sid: str) -> None:
    response.set_cookie(
        key=config.SESSION_COOKIE_NAME,
        value=sid,
        max_age=config.SESSION_LIFETIME_DAYS * 86400,
        httponly=True,
        secure=config.SESSION_COOKIE_SECURE,
        samesite=config.SESSION_COOKIE_SAMESITE,
        domain=config.SESSION_COOKIE_DOMAIN,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=config.SESSION_COOKIE_NAME,
        domain=config.SESSION_COOKIE_DOMAIN,
        path="/",
    )


@router.get("/github/login")
def github_login():
    if not config.GITHUB_CLIENT_ID or not config.GITHUB_CLIENT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="GitHub App 미구성 (GITHUB_CLIENT_ID/SECRET)",
        )
    state = secrets.token_urlsafe(32)
    response = RedirectResponse(
        url=auth_service.build_authorize_url(state), status_code=302
    )
    # OAuth state cookie — callback에서 query state와 일치 확인. SameSite=Lax 고정:
    # top-level GET navigation은 Lax에서도 cookie 첨부됨. 짧은 만료(10분).
    response.set_cookie(
        key=config.OAUTH_STATE_COOKIE_NAME,
        value=state,
        max_age=config.OAUTH_STATE_TTL_SECONDS,
        httponly=True,
        secure=config.SESSION_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    return response


# private repo 연결 — GitHub App 설치 페이지로 redirect (유저가 repo 선택해서 설치).
# App 설정 "Request user authorization (OAuth) during installation" ON이면, 설치 후
# 콜백에 code+state+installation_id가 함께 와서 기존 /github/callback이 로그인+installation_id
# 저장을 한 흐름으로 처리한다. (full-page navigation — login과 동일하게 location 이동으로 호출)
@router.get("/github/install")
def github_install():
    if not config.GITHUB_APP_SLUG:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="GitHub App slug 미구성 (GITHUB_APP_SLUG)",
        )
    state = secrets.token_urlsafe(32)
    response = RedirectResponse(
        url=f"https://github.com/apps/{config.GITHUB_APP_SLUG}/installations/new?state={state}",
        status_code=302,
    )
    response.set_cookie(
        key=config.OAUTH_STATE_COOKIE_NAME,
        value=state,
        max_age=config.OAUTH_STATE_TTL_SECONDS,
        httponly=True,
        secure=config.SESSION_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/github/callback")
async def github_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    installation_id: int | None = None,   # App 설치를 통해 들어오면 GitHub이 함께 보냄 (private repo clone용)
    setup_action: str | None = None,      # "install"/"update" 등 — 현재는 installation_id 저장에만 관심
    db: SASession = Depends(get_db),
):
    # 사용자가 GitHub 동의 거부 시 — 그냥 web으로 돌려보냄 (UI에서 알림)
    if error:
        return RedirectResponse(
            url=f"{config.WEB_BASE_URL}/?login=denied", status_code=302
        )
    if not code or not state:
        raise HTTPException(status_code=400, detail="code/state 누락")

    cookie_state = request.cookies.get(config.OAUTH_STATE_COOKIE_NAME)
    if not cookie_state or not secrets.compare_digest(cookie_state, state):
        raise HTTPException(status_code=400, detail="state 검증 실패 (CSRF 방지)")

    try:
        access_token = await auth_service.exchange_code_for_token(code)
        gh_user = await auth_service.fetch_github_user(access_token)
        # installation_id는 query라 누구나 바꿔 보낼 수 있다 — 이 유저가 접근 가능한 설치일 때만 받는다.
        install_ok = installation_id is not None and await auth_service.user_can_access_installation(
            access_token, installation_id
        )
    except (httpx.HTTPError, ValueError) as e:
        raise HTTPException(status_code=502, detail=f"GitHub 인증 실패: {e}")

    user = auth_service.upsert_user(db, gh_user)
    if installation_id is not None and not install_ok:
        # 로그인은 그대로 하고 연결만 거절 (남의 설치 번호거나 GitHub 반영 지연 — 다시 연결하면 된다)
        log.warning("installation %s is not accessible to github user %s; not saved", installation_id, gh_user.get("id"))

    # App 설치 경유면 installation_id 저장 — 빌드가 이 installation의 토큰으로 private repo를 clone.
    # 일반 로그인(installation_id 없음)에선 기존 값 보존 (덮어쓰지 않음).
    if install_ok:
        user.github_installation_id = installation_id
        db.commit()

    sess = auth_service.create_session(
        db,
        user,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )

    login = "ok&install=denied" if installation_id is not None and not install_ok else "ok"
    response = RedirectResponse(
        url=f"{config.WEB_BASE_URL}/?login={login}", status_code=302
    )
    _set_session_cookie(response, sess.id)
    response.delete_cookie(config.OAUTH_STATE_COOKIE_NAME, path="/")
    return response


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user), db: SASession = Depends(get_db)) -> UserOut:
    # 앱 이름·정적 슬롯 선언은 앱의 속성이라 앱에서 채운다 (users의 같은 이름 칸은 더 이상 읽지 않는다)
    app = apps_service.get_user_app(db, user.id)
    return UserOut.model_validate(user).model_copy(update={
        "app_name": app.name if app else None,
        "site_enabled": bool(app and app.site_enabled),
        "max_apps": apps_service.app_limit(db, user),
        "app_count": len(apps_service.list_user_apps(db, user.id)),
    })


class DeleteAccountRequest(BaseModel):
    confirm: str    # 내 GitHub 아이디를 그대로 — 실수로 탈퇴하지 않게


# 회원 탈퇴 — 소유한 앱(K8s·DB·저장소·도메인)과 개인 데이터를 모두 지우고 계정을 없앤다. 되돌릴 수 없다.
@router.delete("/me")
async def delete_me(
    req: DeleteAccountRequest,
    response: Response,
    db: SASession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    if req.confirm != user.login:
        raise HTTPException(status_code=400, detail="GitHub 아이디가 일치하지 않습니다")
    try:
        await account.delete_account(db, user)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _clear_session_cookie(response)
    return {"status": "deleted"}


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    db: SASession = Depends(get_db),
):
    sid = request.cookies.get(config.SESSION_COOKIE_NAME)
    if sid:
        auth_service.revoke_session(db, sid)
    _clear_session_cookie(response)
    return {"status": "ok"}
