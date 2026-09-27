"""deploy router 계약 고정 — 라우트 순서·인가·에러 매핑의 안전망.

router.py는 /env·/commits·/reserved-keys 같은 정적 경로가 /{build_id}보다 먼저
등록돼야 하는 순서 의존이 있다 (핸들러 추가 순서 실수 = 조용한 라우팅 오동작).
TestClient로 그 계약을 고정한다. 도메인 함수는 monkeypatch — K8s/DB 안 감.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import config
from app.auth.deps import get_current_user_optional
from app.auth.model import User
from app.deploy import router as deploy_router
from app.deploy import status
from app.deploy.build import pipeline
from app.shared.db import get_db


def make_user(app_name="foo"):
    return User(id=uuid.uuid4(), app_name=app_name, site_enabled=False)


def make_client(user=None):
    """deploy router만 올린 테스트 앱. user=None이면 비로그인 상태."""
    app = FastAPI()
    app.include_router(deploy_router.router)

    def fake_db():
        yield MagicMock()

    app.dependency_overrides[get_db] = fake_db
    # 실서비스 인증(cookie→세션 조회)은 leaf dependency만 갈아끼워 우회 —
    # get_current_user의 401 분기는 그대로 살아서 비로그인 계약도 테스트된다.
    app.dependency_overrides[get_current_user_optional] = lambda: user
    return TestClient(app)


# --- 인가 ---

def test_unauthenticated_requests_get_401():
    client = make_client(user=None)
    assert client.get("/deploy/env").status_code == 401
    assert client.get("/deploy").status_code == 401
    assert client.post("/deploy", json={}).status_code == 401


# --- 라우트 순서 (정적 경로가 /{build_id}에 잡히면 안 됨) ---

def test_env_route_not_captured_by_build_id():
    # app_name 없는 유저 → env_get의 빈 dict 조기 반환.
    # /{build_id}로 잘못 매칭되면 이 응답 형태가 나올 수 없다.
    client = make_client(user=make_user(app_name=None))
    res = client.get("/deploy/env")
    assert res.status_code == 200
    assert res.json() == {"env": {}}


def test_reserved_keys_route_not_captured_by_build_id():
    client = make_client(user=make_user())
    res = client.get("/deploy/reserved-keys")
    assert res.status_code == 200
    keys_map = res.json()
    assert set(keys_map) == {"mysql", "postgres", "redis", "storage"}
    assert "DB_HOST" in keys_map["mysql"]      # dep 템플릿에서 파생된 실키


def test_commits_route_not_captured_by_build_id(monkeypatch):
    monkeypatch.setattr(status, "list_builds", lambda db, user_id=None: [])
    client = make_client(user=make_user())
    res = client.get("/deploy/commits")
    assert res.status_code == 200
    assert res.json() == []                    # 빌드 없으면 빈 리스트 (조기 반환)


def test_unknown_build_id_is_404(monkeypatch):
    # 정적 경로가 아닌 진짜 build_id 세그먼트는 get_state로 — 없으면 404 마스킹
    monkeypatch.setattr(status, "get_state", lambda db, bid, user_id=None: None)
    client = make_client(user=make_user())
    assert client.get("/deploy/zzzzzzzz").status_code == 404


# --- 에러 매핑 ---

def test_start_deploy_valueerror_maps_to_400(monkeypatch):
    async def boom(*a, **kw):
        raise ValueError("검증 실패 사유")

    monkeypatch.setattr(pipeline, "start_deploy", boom)
    client = make_client(user=make_user())
    res = client.post(
        "/deploy",
        json={"repo_url": "https://github.com/u/repo", "runtime": "python"},
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "검증 실패 사유"


def test_start_deploy_success_shape(monkeypatch):
    async def fake_start_deploy(*a, **kw):
        return [SimpleNamespace(build_id="abc12345", runtime="python", status="queued")]

    monkeypatch.setattr(pipeline, "start_deploy", fake_start_deploy)
    client = make_client(user=make_user(app_name="foo"))
    res = client.post(
        "/deploy",
        json={"repo_url": "https://github.com/u/repo", "runtime": "python"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["app_name"] == "foo"
    assert body["builds"] == [
        {"build_id": "abc12345", "runtime": "python", "status": "queued"}
    ]


def test_invalid_runtime_rejected_by_schema():
    client = make_client(user=make_user())
    res = client.post(
        "/deploy",
        json={"repo_url": "https://github.com/u/repo", "runtime": "cobol"},
    )
    assert res.status_code == 422              # Literal 스키마가 차단


# --- WebSocket Origin 검증 (CSWSH 방어) ---

@pytest.mark.parametrize("path", [
    "/deploy/app/terminal",
    "/deploy/app/db-terminal",
    "/deploy/app/redis-terminal",
])
def test_ws_rejects_bad_origin(path):
    client = make_client()
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(path, headers={"origin": "https://evil.example"}):
            pass
    assert exc.value.code == 4403


def test_ws_rejects_missing_cookie_with_allowed_origin():
    client = make_client()
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            "/deploy/app/terminal",
            headers={"origin": config.ALLOWED_ORIGINS[0]},
        ):
            pass
    assert exc.value.code == 4001              # Origin 통과 후 인증에서 거절


# --- v2(빌더) 앱: config·delete가 빌더에 연결되기 전까지 501 ---

@pytest.mark.parametrize("method,path,body,target", [
    ("put", "/deploy/env", {"env": {"A": "1"}}, "set_env"),
    ("put", "/deploy/domain", {"domain": "www.example.com"}, "set_custom_domain"),
    ("delete", "/deploy/domain", None, "clear_custom_domain"),
    ("delete", "/deploy/app", None, "delete_app"),
])
def test_v2_app_changes_are_501(monkeypatch, method, path, body, target):
    called = []
    monkeypatch.setattr(deploy_router.env, "set_env", lambda *a: called.append("set_env"))
    monkeypatch.setattr(deploy_router.hostnames, "set_custom_domain", lambda *a: called.append("set_custom_domain"))
    monkeypatch.setattr(deploy_router.hostnames, "clear_custom_domain", lambda *a: called.append("clear_custom_domain"))
    monkeypatch.setattr(status, "delete_app", lambda *a: called.append("delete_app"))
    user = make_user()
    user.pipeline = "v2"
    client = make_client(user)
    r = getattr(client, method)(path, json=body) if body else getattr(client, method)(path)
    assert r.status_code == 501 and "새 경로(v2)" in r.json()["detail"]
    assert called == []


def test_v1_app_delete_still_works(monkeypatch):
    called = []
    monkeypatch.setattr(status, "delete_app", lambda *a: called.append("delete_app"))
    user = make_user()
    user.pipeline = "v1"
    assert make_client(user).delete("/deploy/app").status_code == 200 and called == ["delete_app"]
