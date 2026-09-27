"""GitHub App (user-to-server OAuth) + DB 세션 오케스트레이션.

GitHub App을 OAuth provider로 사용. OAuth App과 흐름은 동일하지만 차이:
- authorize URL에 scope 안 넘김 (권한은 App 등록 페이지의 permissions로 고정)
- access_token이 8시간 만료 + refresh_token 동봉 (현재 코드는 즉시 user fetch 후 버려서 영향 X)
- callback에 `installation_id`/`setup_action`이 추가로 올 수 있음 (post-install) — 무시해도 무방

흐름:
    /auth/github/login  → state cookie 발급 + GitHub authorize redirect
    /auth/github/callback?code&state → token 교환 → user upsert → 세션 생성 + cookie
    /auth/me            → 현재 user
    /auth/logout        → 세션 revoke + cookie 삭제
"""

import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session as SASession

from app import config
from app.auth.model import User, UserSession

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_USER_EMAILS_URL = "https://api.github.com/user/emails"
GITHUB_USER_INSTALLATIONS_URL = "https://api.github.com/user/installations"
_INSTALLATIONS_MAX_PAGES = 10                     # 100개씩 — 한 유저가 접근하는 우리 App 설치 수로는 넉넉

# GitHub API에서 요구하는 헤더 베이스. Authorization은 호출부에서 추가.
_GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


# authorize URL 빌더 — state는 호출자가 랜덤 발급 후 cookie와 query 양쪽에 박음
def build_authorize_url(state: str) -> str:
    # GitHub App은 scope 파라미터 안 받음 — 권한은 App 등록 페이지의 permissions로 고정.
    # 현재 설정된 permissions: PR read / repo content read / user email read.
    params = {
        "client_id": config.GITHUB_CLIENT_ID,
        "redirect_uri": config.GITHUB_OAUTH_REDIRECT_URI,
        "state": state,
        "allow_signup": "true",
    }
    return f"{GITHUB_AUTHORIZE_URL}?{urlencode(params)}"


# code → access_token 교환 (server-to-server). client_secret이 노출되지 않게 백엔드에서만 호출.
async def exchange_code_for_token(code: str) -> str:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            GITHUB_TOKEN_URL,
            data={
                "client_id": config.GITHUB_CLIENT_ID,
                "client_secret": config.GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri": config.GITHUB_OAUTH_REDIRECT_URI,
            },
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise ValueError(f"GitHub token 응답에 access_token 없음: {data}")
        return token


# /user — primary identity. email이 private이면 /user/emails에서 primary+verified 한 건 보강.
async def fetch_github_user(access_token: str) -> dict:
    headers = {**_GITHUB_HEADERS, "Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=10) as client:
        u = await client.get(GITHUB_USER_URL, headers=headers)
        u.raise_for_status()
        user = u.json()

        email = user.get("email")
        if not email:
            e = await client.get(GITHUB_USER_EMAILS_URL, headers=headers)
            if e.status_code == 200:
                primary = next(
                    (
                        x
                        for x in e.json()
                        if x.get("primary") and x.get("verified")
                    ),
                    None,
                )
                if primary:
                    email = primary.get("email")
        user["_resolved_email"] = email
        return user


# 로그인한 유저가 이 App 설치에 접근할 수 있는지 — 유저 토큰으로 GitHub에 직접 묻는다.
# 콜백 query의 installation_id는 누구나 바꿔 보낼 수 있고, 서버는 App 자격증명으로 어느 설치든
# 토큰을 발급받을 수 있다. 확인 없이 저장하면 남의 설치를 자기 계정에 붙여 그 private repo를 빌드할 수 있다.
# /user/installations = 이 user 토큰이 접근 가능한 우리 App 설치 목록 (본인·소속 조직 설치 포함).
async def user_can_access_installation(access_token: str, installation_id: int) -> bool:
    headers = {**_GITHUB_HEADERS, "Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=10) as client:
        for page in range(1, _INSTALLATIONS_MAX_PAGES + 1):
            r = await client.get(
                GITHUB_USER_INSTALLATIONS_URL,
                headers=headers,
                params={"per_page": 100, "page": page},
            )
            r.raise_for_status()
            items = r.json().get("installations") or []
            if any(i.get("id") == installation_id for i in items):
                return True
            if len(items) < 100:
                return False
    return False


# github_id 기준 upsert. login/email/avatar는 매번 갱신 (GitHub 측 변경 추적).
def upsert_user(db: SASession, gh_user: dict) -> User:
    github_id = int(gh_user["id"])
    user = db.query(User).filter_by(github_id=github_id).first()
    if user is None:
        user = User(
            github_id=github_id,
            login=gh_user.get("login", ""),
            email=gh_user.get("_resolved_email"),
            avatar_url=gh_user.get("avatar_url"),
        )
        db.add(user)
    else:
        user.login = gh_user.get("login", user.login)
        user.email = gh_user.get("_resolved_email", user.email)
        user.avatar_url = gh_user.get("avatar_url", user.avatar_url)
    db.commit()
    db.refresh(user)
    return user


# 새 세션 발급 — secrets.token_urlsafe(48)는 ~64자 base64url. 추측 불가능 수준의 엔트로피.
def create_session(
    db: SASession,
    user: User,
    *,
    user_agent: str | None = None,
    ip: str | None = None,
) -> UserSession:
    sid = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(
        days=config.SESSION_LIFETIME_DAYS
    )
    sess = UserSession(
        id=sid,
        user_id=user.id,
        expires_at=expires_at,
        user_agent=(user_agent or "")[:255] or None,
        ip=(ip or "")[:45] or None,
    )
    db.add(sess)
    db.commit()
    return sess


# expires_at/revoked_at 검사를 한 곳에서 — 호출부가 "활성" 판별을 중복 안 하도록.
# DB에 저장된 datetime은 naive로 들어가지만 의미상 UTC. compare 위해 naive로 비교.
def get_active_session(db: SASession, sid: str) -> UserSession | None:
    sess = db.query(UserSession).filter_by(id=sid).first()
    if sess is None:
        return None
    if sess.revoked_at is not None:
        return None
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    if sess.expires_at < now_naive:
        return None
    return sess


def revoke_session(db: SASession, sid: str) -> None:
    sess = db.query(UserSession).filter_by(id=sid).first()
    if sess is None:
        return
    sess.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
