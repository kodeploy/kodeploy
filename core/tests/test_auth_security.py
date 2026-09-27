"""로그인 보안 계약 — 세션 쿠키 범위와 GitHub App 설치 번호 확인.

- 세션 쿠키에 Domain이 붙으면 {앱}.kodeploy.com(유저 앱)에도 전송돼 그 앱 서버가 세션을 가져갈 수 있다.
  운영 ConfigMap에 SESSION_COOKIE_DOMAIN을 다시 넣지 못하게 고정한다.
- 콜백 query의 installation_id는 위조할 수 있다. 로그인한 유저가 접근 가능한 설치일 때만 저장한다.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config
from app.auth import router as auth_router
from app.auth import service as auth_service
from app.auth.model import User
from app.shared.db import get_db

CONFIGMAP = Path(__file__).resolve().parents[2] / "deploy" / "k8s" / "configMap" / "kodeploy-core-env.yaml"


# --- 세션 쿠키 범위 ---

def test_configmap_has_no_session_cookie_domain():
    data = yaml.safe_load(CONFIGMAP.read_text())["data"]
    assert "SESSION_COOKIE_DOMAIN" not in data


def test_session_cookie_is_host_only(monkeypatch):
    monkeypatch.setattr(config, "SESSION_COOKIE_DOMAIN", None)
    resp = auth_router.Response()
    auth_router._set_session_cookie(resp, "sid")
    header = resp.headers["set-cookie"]
    assert "kd_session=sid" in header and "domain=" not in header.lower()


# --- /user/installations 조회 ---

def _mock_github(monkeypatch, pages):
    """pages[n] = n+1 페이지의 installation id 목록. 호출된 page 번호를 기록한다."""
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/user/installations"
        assert req.headers["authorization"] == "Bearer user-token"
        page = int(req.url.params["page"])
        seen.append(page)
        ids = pages[page - 1] if page <= len(pages) else []
        return httpx.Response(200, json={"total_count": 0, "installations": [{"id": i} for i in ids]})

    real = httpx.AsyncClient
    monkeypatch.setattr(auth_service.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return seen


def test_installation_found_on_later_page(monkeypatch):
    seen = _mock_github(monkeypatch, [list(range(1, 101)), [111]])
    assert asyncio.run(auth_service.user_can_access_installation("user-token", 111)) is True
    assert seen == [1, 2]


def test_installation_not_accessible(monkeypatch):
    seen = _mock_github(monkeypatch, [[222, 333]])
    assert asyncio.run(auth_service.user_can_access_installation("user-token", 111)) is False
    assert seen == [1]


def test_installation_lookup_error_raises(monkeypatch):
    real = httpx.AsyncClient
    monkeypatch.setattr(auth_service.httpx, "AsyncClient", lambda **kw: real(
        transport=httpx.MockTransport(lambda r: httpx.Response(401)), **kw))
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(auth_service.user_can_access_installation("user-token", 111))


# --- 콜백 ---

@pytest.fixture
def callback(monkeypatch):
    user = User(github_installation_id=None)
    checked = []

    async def can_access(token, iid):
        checked.append(iid)
        return iid == 111

    async def token(code):
        return "user-token"

    async def gh_user(t):
        return {"id": 42, "login": "chulsoo"}

    monkeypatch.setattr(auth_service, "exchange_code_for_token", token)
    monkeypatch.setattr(auth_service, "fetch_github_user", gh_user)
    monkeypatch.setattr(auth_service, "user_can_access_installation", can_access)
    monkeypatch.setattr(auth_service, "upsert_user", lambda db, g: user)
    monkeypatch.setattr(auth_service, "create_session", lambda db, u, **kw: SimpleNamespace(id="sid"))

    app = FastAPI()
    app.include_router(auth_router.router)

    def fake_db():
        yield MagicMock()

    app.dependency_overrides[get_db] = fake_db
    client = TestClient(app, follow_redirects=False)
    client.cookies.set(config.OAUTH_STATE_COOKIE_NAME, "st")

    def call(**params):
        return client.get("/auth/github/callback", params={"code": "c", "state": "st", **params})

    return SimpleNamespace(call=call, user=user, checked=checked)


def test_callback_saves_accessible_installation(callback):
    r = callback.call(installation_id=111)
    assert r.status_code == 302 and r.headers["location"].endswith("/?login=ok")
    assert callback.user.github_installation_id == 111


def test_callback_rejects_someone_elses_installation(callback):
    r = callback.call(installation_id=222)
    assert r.status_code == 302 and r.headers["location"].endswith("/?login=ok&install=denied")
    assert callback.user.github_installation_id is None
    assert "kd_session=sid" in r.headers["set-cookie"]      # 로그인 자체는 된다


def test_plain_login_skips_installation_check(callback):
    callback.user.github_installation_id = 111               # 기존 연결은 그대로
    r = callback.call()
    assert r.headers["location"].endswith("/?login=ok")
    assert callback.checked == [] and callback.user.github_installation_id == 111
