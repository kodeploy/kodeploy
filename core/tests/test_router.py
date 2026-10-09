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
from app.apps.model import App
from app.auth.deps import get_current_user_optional
from app.auth.model import User
from app.deploy import router as deploy_router
from app.deploy import status
from app.deploy.build import pipeline
from app.shared.db import get_db


APPS: dict[uuid.UUID, App] = {}   # 유저 id → 앱. fake_apps가 router의 앱 조회를 이걸로 대신한다


def make_user(app_name="foo", pipeline="v1"):
    """유저와 (app_name이 있으면) 그 유저의 앱. 앱은 APPS[user.id]."""
    user = User(id=uuid.uuid4())
    if app_name:
        APPS[user.id] = App(
            id=uuid.uuid4(), owner_id=user.id, name=app_name, namespace=f"tenant-{user.id.hex[:8]}",
            site_enabled=False, pipeline=pipeline,
        )
    return user


@pytest.fixture(autouse=True)
def fake_apps(monkeypatch):
    APPS.clear()
    monkeypatch.setattr(deploy_router.apps_service, "get_user_app", lambda db, owner_id: APPS.get(owner_id))
    yield
    APPS.clear()


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
    monkeypatch.setattr(status, "list_builds", lambda db, app_id=None: [])
    client = make_client(user=make_user())
    res = client.get("/deploy/commits")
    assert res.status_code == 200
    assert res.json() == []                    # 빌드 없으면 빈 리스트 (조기 반환)


def test_unknown_build_id_is_404(monkeypatch):
    # 정적 경로가 아닌 진짜 build_id 세그먼트는 get_state로 — 없으면 404 마스킹
    monkeypatch.setattr(status, "get_state", lambda db, bid, app_id=None: None)
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


# --- v2(빌더) 앱: 환경변수·도메인 변경은 빌더(config)로 반영한다 ---

@pytest.fixture
def cfg(monkeypatch):
    """환경변수·도메인 변경 경로의 바깥 호출을 가짜로 — 기록만 한다."""
    rec = SimpleNamespace(set_env=[], submitted=[], hostnames=[], domain=[], cleared=[], spawned=[], busy=False, builder_down=False, idle=[])

    monkeypatch.setattr(deploy_router.env, "get_env", lambda ns, name: {"OLD": "1"})
    monkeypatch.setattr(deploy_router.env, "set_env", lambda ns, name, e, restart=True: rec.set_env.append(restart))
    monkeypatch.setattr(pipeline, "spawn_background", lambda fn, *a: rec.spawned.append(fn))

    def ensure_idle(db, app, include_static=False):
        rec.idle.append(include_static)
        if rec.busy:
            raise ValueError("배포가 진행 중이에요. 끝난 뒤에 다시 시도해 주세요")

    async def submit_env(event, actor):
        if rec.builder_down:
            raise deploy_router.builder.BuilderError("connection refused")
        rec.submitted.append(event)

    async def apply_hostnames(app, actor):
        if rec.builder_down:
            raise deploy_router.builder.BuilderError("connection refused")
        rec.hostnames.append(app.name)

    monkeypatch.setattr(deploy_router.v2, "ensure_idle", ensure_idle)
    monkeypatch.setattr(deploy_router.v2, "submit_env_revision", submit_env)
    monkeypatch.setattr(deploy_router.v2, "apply_hostnames", apply_hostnames)
    monkeypatch.setattr(deploy_router.hostnames, "set_custom_domain",
                        lambda db, app, d, reconcile=True: rec.domain.append(reconcile) or {"domain": d, "status": "pending"})
    monkeypatch.setattr(deploy_router.hostnames, "clear_custom_domain",
                        lambda db, app, reconcile=True: rec.cleared.append(reconcile))
    return rec


def test_v2_env_change_writes_the_secret_then_asks_the_builder(cfg):
    client = make_client(make_user(pipeline="v2"))
    r = client.put("/deploy/env", json={"env": {"A": "1"}})
    assert r.status_code == 200
    assert cfg.set_env == [False]                                 # Deployment은 건드리지 않는다 (Argo가 다시 띄운다)
    assert cfg.idle == [False]                                    # 환경변수는 서버 배포만 기다린다 (정적 사이트와 상관없다)
    assert len(cfg.submitted) == 1
    ev = cfg.submitted[0]
    assert (ev.kind, ev.status, ev.last_event_seq) == ("env_change", "applied", 0)
    assert cfg.spawned == []                                      # v1의 롤아웃 감시 스레드는 안 뜬다


def test_v1_env_change_is_unchanged(cfg):
    r = make_client(make_user(pipeline="v1")).put("/deploy/env", json={"env": {"A": "1"}})
    assert r.status_code == 200
    assert cfg.set_env == [True] and cfg.submitted == [] and len(cfg.spawned) == 1


def test_v2_env_change_waits_while_a_deploy_is_running(cfg):
    cfg.busy = True
    r = make_client(make_user(pipeline="v2")).put("/deploy/env", json={"env": {"A": "1"}})
    assert r.status_code == 409 and "배포가 진행 중" in r.json()["detail"]
    assert cfg.set_env == [] and cfg.submitted == []              # Secret도 건드리지 않았다


def test_v2_env_change_reports_a_builder_failure(cfg):
    cfg.builder_down = True
    r = make_client(make_user(pipeline="v2")).put("/deploy/env", json={"env": {"A": "1"}})
    assert r.status_code == 502 and "저장했지만" in r.json()["detail"]
    assert cfg.set_env == [False]                                 # Secret은 이미 저장됐다


def test_v2_domain_goes_through_the_builder(cfg):
    client = make_client(make_user(pipeline="v2"))
    assert client.put("/deploy/domain", json={"domain": "www.example.com"}).status_code == 200
    assert cfg.domain == [False] and len(cfg.hostnames) == 1      # route를 직접 안 고치고 빌더로 반영한다
    assert client.delete("/deploy/domain").json() == {"status": "cleared"}
    assert cfg.idle == [True, True]                               # 도메인은 정적 사이트 배포까지 기다린다 (정적 슬롯 호스트도 바뀐다)
    assert cfg.cleared == [False] and len(cfg.hostnames) == 2


def test_v1_domain_is_unchanged(cfg):
    client = make_client(make_user(pipeline="v1"))
    assert client.put("/deploy/domain", json={"domain": "www.example.com"}).status_code == 200
    client.delete("/deploy/domain")
    assert cfg.domain == [True] and cfg.cleared == [True] and cfg.hostnames == []


def test_v2_domain_waits_while_a_deploy_is_running(cfg):
    cfg.busy = True
    client = make_client(make_user(pipeline="v2"))
    assert client.put("/deploy/domain", json={"domain": "www.example.com"}).status_code == 400
    assert client.delete("/deploy/domain").status_code == 409
    assert cfg.domain == [] and cfg.cleared == []


def test_v2_domain_reports_a_builder_failure(cfg):
    cfg.builder_down = True
    r = make_client(make_user(pipeline="v2")).put("/deploy/domain", json={"domain": "www.example.com"})
    assert r.status_code == 502 and "저장했지만" in r.json()["detail"]


def test_v1_app_delete_still_works(monkeypatch):
    called = []
    monkeypatch.setattr(status, "delete_app", lambda *a: called.append("delete_app"))
    user = make_user(pipeline="v1")
    assert make_client(user).delete("/deploy/app").status_code == 200 and called == ["delete_app"]


def test_v2_app_delete_waits_for_builder_then_deletes(monkeypatch):
    order = []

    async def request_delete(user, app):
        order.append("builder")

    monkeypatch.setattr(deploy_router.v2, "request_delete", request_delete)
    monkeypatch.setattr(status, "delete_app", lambda *a: order.append("delete_app"))
    user = make_user(pipeline="v2")
    assert make_client(user).delete("/deploy/app").status_code == 200
    assert order == ["builder", "delete_app"]          # Application이 사라진 뒤에야 ns를 지운다


def test_v2_app_delete_keeps_app_when_builder_fails(monkeypatch):
    async def request_delete(user, app):
        raise ValueError("삭제하지 못했습니다 (timeout: still exists)")

    called = []
    monkeypatch.setattr(deploy_router.v2, "request_delete", request_delete)
    monkeypatch.setattr(status, "delete_app", lambda *a: called.append("delete_app"))
    user = make_user(pipeline="v2")
    r = make_client(user).delete("/deploy/app")
    assert r.status_code == 400 and "삭제하지 못했습니다" in r.json()["detail"]
    assert called == []
