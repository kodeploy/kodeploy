"""관리자가 남의 앱에 들어가기 — root는 모든 앱에서 주인과 같고 admin은 보기만, 바꾼 것은 기록이 남는다.

test_sharing과 같은 방식(sqlite 실제 테이블 + 실제 라우터)으로 돈다. 접근을 가르는 건 SQL과 의존성이다.
"""

import importlib.util
import io
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.admin import router as admin_router
from app.admin import service as admin_service
from app.admin.model import AdminAction
from app.apps import sharing
from app.apps.model import App, AppInvite, AppMember, Tier
from app.auth.deps import get_current_user_optional
from app.auth.model import User
from app.deploy import router as deploy_router
from app.deploy import status
from app.deploy.build import pipeline
from app.deploy.model import Build
from app.shared.db import get_db
from tests.test_sharing import DEPLOY, add_app, add_user, client, share  # noqa: F401  (LONGTEXT sqlite 컴파일도 같이)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for t in (App, Tier, AppMember, AppInvite, User, Build, AdminAction):
        t.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([Tier(name="basic", max_apps=1)])
    session.commit()
    yield session
    session.close()


def with_role(db, user, role):
    user.role = role
    db.commit()
    return user


@pytest.fixture
def world(db, monkeypatch):
    owner = add_user(db, "owner")
    root = with_role(db, add_user(db, "root"), "root")
    admin = with_role(db, add_user(db, "admin"), "admin")
    stranger = add_user(db, "stranger")
    app = add_app(db, owner)
    monkeypatch.setattr(status, "get_app_status", lambda a: {"status": "running"})
    monkeypatch.setattr(status, "list_builds", lambda d, app_id=None: [])
    monkeypatch.setattr(deploy_router.env, "get_env", lambda ns, name: {"SECRET": "x"})
    monkeypatch.setattr(deploy_router.env, "set_env", lambda ns, name, e, restart=True: None)

    async def fake_start(db_, **kw):
        return []

    monkeypatch.setattr(pipeline, "start_deploy", fake_start)
    monkeypatch.setattr(pipeline, "spawn_background", lambda fn, *a: None)
    return SimpleNamespace(app=app, owner=owner, root=root, admin=admin, stranger=stranger)


def code(db, user, method, path, **kw):
    return getattr(client(db, user), method)(path, **kw).status_code


def actions(db):
    return [(a.actor_login, a.app_name, a.target_login, a.action) for a in db.query(AdminAction).order_by(AdminAction.id)]


# --- 단계 판정 ---

def test_access_for_adds_the_admin_floor(db, world):
    app = world.app
    assert sharing.access_for(db, world.owner, app.id)[1:] == ("owner", False)
    assert sharing.access_for(db, world.root, app.id)[1:] == ("owner", True)
    assert sharing.access_for(db, world.admin, app.id)[1:] == ("viewer", True)
    assert sharing.access_for(db, world.stranger, app.id) is None
    share(db, app, world.admin, "editor")                       # 멤버 단계가 더 높으면 그쪽 — 관리자 권한이 아니다
    assert sharing.access_for(db, world.admin, app.id)[1:] == ("editor", False)
    assert sharing.access_for(db, world.root, uuid.uuid4()) is None


# --- 앱 안의 동작 ---

def test_root_acts_as_owner_and_changes_are_recorded(db, world):
    a = world.app.id
    assert code(db, world.root, "get", f"/apps/{a}/deploy/env") == 200       # 보기는 기록하지 않는다
    assert code(db, world.root, "put", f"/apps/{a}/deploy/env", json={"env": {"A": "1"}}) == 200
    assert code(db, world.root, "post", f"/apps/{a}/deploy", json=DEPLOY) == 200
    assert actions(db) == [
        ("root", "shop", "owner", "PUT /deploy/env"),
        ("root", "shop", "owner", "POST /deploy"),
    ]


def test_admin_can_only_look(db, world):
    a = world.app.id
    assert code(db, world.admin, "get", f"/apps/{a}/deploy/app/status") == 200
    assert code(db, world.admin, "get", f"/apps/{a}/deploy/env") == 403            # 환경변수 값은 editor부터
    assert code(db, world.admin, "put", f"/apps/{a}/deploy/env", json={"env": {}}) == 403
    assert code(db, world.admin, "post", f"/apps/{a}/deploy", json=DEPLOY) == 403
    assert code(db, world.admin, "delete", f"/apps/{a}/deploy/app") == 403
    assert actions(db) == []


def test_own_apps_and_memberships_are_not_recorded(db, world):
    mine = add_app(db, world.root, name="mine")
    assert code(db, world.root, "put", f"/apps/{mine.id}/deploy/env", json={"env": {}}) == 200
    share(db, world.app, world.admin, "editor")
    assert code(db, world.admin, "put", f"/apps/{world.app.id}/deploy/env", json={"env": {}}) == 200
    assert actions(db) == []


def test_strangers_still_get_404(db, world):
    assert code(db, world.stranger, "get", f"/apps/{world.app.id}/deploy/app/status") == 404


# --- 앱 하나 조회 (앱 화면이 처음 띄울 때) ---

def test_get_app_tells_the_screen_who_is_looking(db, world):
    a = world.app.id

    def body(user):
        return client(db, user).get(f"/apps/{a}").json()

    mine = body(world.owner)
    assert (mine["role"], mine["admin"], mine["owner_login"]) == ("owner", False, None)
    as_root = body(world.root)
    assert (as_root["role"], as_root["admin"], as_root["owner_login"]) == ("owner", True, "owner")
    as_admin = body(world.admin)
    assert (as_admin["role"], as_admin["admin"]) == ("viewer", True)
    member = add_user(db, "member")
    share(db, world.app, member, "editor")
    shared = body(member)
    assert (shared["role"], shared["admin"], shared["owner_login"]) == ("editor", False, "owner")
    assert client(db, world.stranger).get(f"/apps/{a}").status_code == 404


def test_root_manages_members_of_any_app_and_it_is_recorded(db, world):
    a = world.app.id
    r = client(db, world.root).post(f"/apps/{a}/invites", json={"target": "someone", "role": "viewer"})
    assert r.status_code == 200
    assert client(db, world.admin).post(f"/apps/{a}/invites", json={"target": "x", "role": "viewer"}).status_code == 403
    assert actions(db) == [("root", "shop", "owner", "POST /apps/{app_id}/invites")]


# --- 터미널 WebSocket (주인 전용) ---

def _ws(app_id):
    return SimpleNamespace(
        path_params={"app_id": str(app_id)},
        scope={"type": "websocket", "route": SimpleNamespace(path="/apps/{app_id}/deploy/app/terminal")},
        url=SimpleNamespace(path=f"/apps/{app_id}/deploy/app/terminal"),
    )


def test_terminal_opens_for_root_and_owner_but_not_admin(db, world):
    a = world.app.id
    assert deploy_router._ws_app(db, _ws(a), world.owner.id).id == a
    assert actions(db) == []
    assert deploy_router._ws_app(db, _ws(a), world.root.id).id == a
    assert actions(db) == [("root", "shop", "owner", "WS /deploy/app/terminal")]
    assert deploy_router._ws_app(db, _ws(a), world.admin.id) is None           # 보기 권한으로는 터미널 X
    assert deploy_router._ws_app(db, _ws(a), world.stranger.id) is None
    assert deploy_router._ws_app(db, _ws("not-a-uuid"), world.root.id) is None


# --- 관리자 화면 API ---

def admin_client(db, user):
    api = FastAPI()
    api.include_router(admin_router.router)
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_current_user_optional] = lambda: user
    return TestClient(api)


def test_apps_list_shows_owner_members_and_last_deploy(db, world):
    share(db, world.app, world.stranger, "viewer")
    db.add_all([
        Build(build_id="b0000001", repo_url="r", branch="main", image="i", app_name="shop", port=8080, runtime="java",
              status="failed", user_id=world.owner.id, app_id=world.app.id, kind="build"),
        Build(build_id="b0000002", repo_url="r", branch="main", image="i", app_name="shop", port=8080, runtime="java",
              status="running", user_id=world.owner.id, app_id=world.app.id, kind="env_change"),
    ])
    db.commit()
    rows = admin_client(db, world.admin).get("/admin/apps").json()
    row = next(r for r in rows if r["id"] == str(world.app.id))
    assert (row["owner_login"], row["member_count"], row["pipeline"]) == ("owner", 1, "v1")
    assert row["last_build"]["status"] == "failed"                   # 환경변수 변경 행은 배포가 아니다
    assert admin_client(db, world.stranger).get("/admin/apps").status_code == 403


def test_app_detail_reads_the_apps_namespace(db, world, monkeypatch):
    seen = []
    pod = SimpleNamespace(
        metadata=SimpleNamespace(name="shop-1", labels={"app": "shop"}),
        status=SimpleNamespace(phase="Running", conditions=[SimpleNamespace(type="Ready", status="True")],
                               container_statuses=[SimpleNamespace(restart_count=2)], start_time=None),
    )
    fake = SimpleNamespace(list_namespaced_pod=lambda namespace: seen.append(namespace) or SimpleNamespace(items=[pod]))
    monkeypatch.setattr(admin_service.k8s, "core_v1", lambda: fake)
    body = admin_client(db, world.admin).get(f"/admin/apps/{world.app.id}").json()
    assert seen == [world.app.namespace]
    assert body["pods"] == [{"name": "shop-1", "component": "shop", "phase": "Running", "ready": True,
                             "restarts": 2, "started_at": None}]
    assert body["config"] is None                                    # 배포한 적 없는 앱
    assert admin_client(db, world.admin).get(f"/admin/apps/{uuid.uuid4()}").status_code == 404


def test_root_can_remove_an_account_and_it_is_recorded_first(db, world, monkeypatch):
    removed = []

    async def fake_delete(db_, user):
        assert actions(db_)[-1][2] == user.login                     # 지우기 전에 남긴다
        removed.append(user.login)

    monkeypatch.setattr(admin_router.account, "delete_account", fake_delete)
    r = admin_client(db, world.root).delete(f"/admin/users/{world.owner.id}")
    assert r.status_code == 200 and removed == ["owner"]
    assert actions(db) == [("root", None, "owner", "DELETE /admin/users/{user_id}")]


def test_account_removal_guards(db, world, monkeypatch):
    async def refuse(db_, user):
        raise ValueError("root 계정은 탈퇴할 수 없습니다")

    monkeypatch.setattr(admin_router.account, "delete_account", refuse)
    root_client = admin_client(db, world.root)
    assert root_client.delete(f"/admin/users/{world.root.id}").status_code == 400          # 자기 계정
    assert root_client.delete(f"/admin/users/{uuid.uuid4()}").status_code == 404
    other_root = with_role(db, add_user(db, "root2"), "root")
    r = root_client.delete(f"/admin/users/{other_root.id}")
    assert r.status_code == 400 and "root" in r.json()["detail"]
    assert admin_client(db, world.admin).delete(f"/admin/users/{world.owner.id}").status_code == 403   # admin은 못 한다


def test_actions_list_is_newest_first(db, world):
    a = world.app.id
    client(db, world.root).put(f"/apps/{a}/deploy/env", json={"env": {}})
    client(db, world.root).post(f"/apps/{a}/deploy", json=DEPLOY)
    rows = admin_client(db, world.admin).get("/admin/actions").json()
    assert [r["action"] for r in rows] == ["POST /deploy", "PUT /deploy/env"]
    assert rows[0]["app_id"] == str(a) and rows[0]["target_login"] == "owner"


# --- 028 마이그레이션 ---

def test_migration_028_creates_and_drops_admin_actions():
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "028_admin_actions.py"
    spec = importlib.util.spec_from_file_location("m028", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    buf = io.StringIO()
    with Operations.context(MigrationContext.configure(dialect_name="mysql", opts={"as_sql": True, "output_buffer": buf})):
        mod.upgrade()
        mod.downgrade()
    sql = buf.getvalue()
    assert "CREATE TABLE admin_actions" in sql and "DROP TABLE admin_actions" in sql
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            mod.upgrade()
        cols = {c["name"] for c in sa.inspect(conn).get_columns("admin_actions")}
    assert {"actor_id", "actor_login", "app_id", "app_name", "target_login", "action", "created_at"} <= cols
