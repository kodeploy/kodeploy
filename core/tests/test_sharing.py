"""앱 공유 — 초대(이메일·GitHub 아이디)·수락·거절, 멤버 관리, 그리고 단계(viewer/editor/owner)별 접근.

sqlite에 실제 테이블을 만들어 진짜 세션으로 돈다. 접근을 가르는 건 SQL과 의존성이 하는 일이라
crud를 대신 세우면 정작 고정하려는 성질이 사라진다. K8s·빌드는 monkeypatch.
"""

import importlib.util
from types import SimpleNamespace
import io
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.apps import sharing
from app.apps.model import App, AppInvite, AppMember, Tier
from app.apps.router import invites_router
from app.apps.router import router as apps_router
from app.auth.deps import get_current_user_optional
from app.auth.model import User
from app.deploy import router as deploy_router
from app.deploy.model import Build
from app.deploy import status
from app.deploy.build import pipeline
from app.shared.db import get_db


@compiles(LONGTEXT, "sqlite")
def _longtext_sqlite(type_, compiler, **kw):
    return "TEXT"


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for t in (App, Tier, AppMember, AppInvite, User, Build):
        t.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([Tier(name="basic", max_apps=1)])
    session.commit()
    yield session
    session.close()


_n = [0]


def add_user(db, login=None, email=None):
    _n[0] += 1
    u = User(id=uuid.uuid4(), github_id=_n[0], login=login or f"user{_n[0]}", email=email, tier="basic")
    db.add(u)
    db.commit()
    return u


def add_app(db, owner, name="shop"):
    a = App(id=uuid.uuid4(), owner_id=owner.id, name=name, namespace=f"app-{uuid.uuid4().hex[:8]}")
    db.add(a)
    db.commit()
    return a


def client(db, user):
    api = FastAPI()
    api.include_router(deploy_router.router)
    api.include_router(deploy_router.router, prefix="/apps/{app_id}")
    api.include_router(apps_router)
    api.include_router(invites_router)
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_current_user_optional] = lambda: user
    return TestClient(api)


def share(db, app, user, role):
    db.add(AppMember(app_id=app.id, user_id=user.id, role=role))
    db.commit()


# --- 초대 만들기 ---

def test_invite_by_email_and_by_login(db):
    owner, app = None, None
    owner = add_user(db, "owner")
    app = add_app(db, owner)
    c = client(db, owner)
    a = c.post(f"/apps/{app.id}/invites", json={"target": " Kim@Example.COM ", "role": "viewer"})
    b = c.post(f"/apps/{app.id}/invites", json={"target": "@Lee-Dev", "role": "editor"})
    assert a.status_code == b.status_code == 200
    assert (a.json()["email"], a.json()["github_login"]) == ("kim@example.com", None)   # 소문자로 저장
    assert (b.json()["email"], b.json()["github_login"]) == (None, "lee-dev")
    listing = c.get(f"/apps/{app.id}/members").json()
    assert [i["role"] for i in listing["invites"]] == ["viewer", "editor"] and listing["members"] == []


@pytest.mark.parametrize("target,role,msg", [
    ("", "viewer", "입력하세요"),
    ("not an email@", "viewer", "올바른"),
    ("bad name!", "viewer", "올바른"),
    ("kim@example.com", "owner", "권한은"),
    ("kim@example.com", "admin", "권한은"),
])
def test_invite_validation(db, target, role, msg):
    owner = add_user(db)
    app = add_app(db, owner)
    res = client(db, owner).post(f"/apps/{app.id}/invites", json={"target": target, "role": role})
    assert res.status_code == 400 and msg in res.json()["detail"]


def test_cannot_invite_owner_member_or_twice(db):
    owner = add_user(db, "owner", "owner@example.com")
    member = add_user(db, "member", "member@example.com")
    app = add_app(db, owner)
    share(db, app, member, "viewer")
    c = client(db, owner)
    for target, msg in [("owner@example.com", "앱 주인"), ("member", "이미 이 앱의 멤버")]:
        res = c.post(f"/apps/{app.id}/invites", json={"target": target, "role": "viewer"})
        assert res.status_code == 400 and msg in res.json()["detail"]
    assert c.post(f"/apps/{app.id}/invites", json={"target": "new@example.com", "role": "viewer"}).status_code == 200
    again = c.post(f"/apps/{app.id}/invites", json={"target": "NEW@example.com", "role": "editor"})
    assert again.status_code == 400 and "이미 초대" in again.json()["detail"]


def test_share_cap(db, monkeypatch):
    monkeypatch.setattr(sharing, "MAX_SHARES_PER_APP", 2)
    owner = add_user(db)
    app = add_app(db, owner)
    c = client(db, owner)
    assert c.post(f"/apps/{app.id}/invites", json={"target": "a@x.com", "role": "viewer"}).status_code == 200
    assert c.post(f"/apps/{app.id}/invites", json={"target": "b@x.com", "role": "viewer"}).status_code == 200
    third = c.post(f"/apps/{app.id}/invites", json={"target": "c@x.com", "role": "viewer"})
    assert third.status_code == 400 and "최대 2명" in third.json()["detail"]


def test_only_owner_manages_sharing(db):
    owner, editor, stranger = add_user(db), add_user(db), add_user(db)
    app = add_app(db, owner)
    share(db, app, editor, "editor")
    body = {"target": "x@x.com", "role": "viewer"}
    assert client(db, editor).post(f"/apps/{app.id}/invites", json=body).status_code == 403
    assert client(db, editor).get(f"/apps/{app.id}/members").status_code == 403
    assert client(db, stranger).post(f"/apps/{app.id}/invites", json=body).status_code == 404   # 존재를 알리지 않는다


# --- 받은 초대: 수락·거절 ---

def test_invitee_sees_and_accepts_invite_by_email(db):
    owner = add_user(db, "owner")
    kim = add_user(db, "kim", "Kim@Example.com")
    app = add_app(db, owner, "team-shop")
    client(db, owner).post(f"/apps/{app.id}/invites", json={"target": "kim@example.com", "role": "editor"})

    c = client(db, kim)
    inv = c.get("/invites").json()
    assert [(i["app_name"], i["owner_login"], i["role"]) for i in inv] == [("team-shop", "owner", "editor")]
    res = c.post(f"/invites/{inv[0]['id']}/accept")
    assert res.status_code == 200 and (res.json()["role"], res.json()["owner_login"]) == ("editor", "owner")
    assert c.get("/invites").json() == []
    assert [(a["name"], a["role"]) for a in c.get("/apps").json()] == [("team-shop", "editor")]
    assert client(db, owner).get(f"/apps/{app.id}/members").json()["members"][0]["login"] == "kim"


def test_invite_by_github_login_matches_after_signup(db):
    owner = add_user(db, "owner")
    app = add_app(db, owner)
    client(db, owner).post(f"/apps/{app.id}/invites", json={"target": "late-joiner", "role": "viewer"})
    late = add_user(db, "Late-Joiner")                     # 초대 뒤에 가입 — 로그인하면 보인다
    assert len(client(db, late).get("/invites").json()) == 1


def test_invite_is_invisible_and_unusable_to_others(db):
    owner, kim, other = add_user(db, "owner"), add_user(db, "kim", "kim@example.com"), add_user(db, "other", "o@example.com")
    app = add_app(db, owner)
    inv = client(db, owner).post(f"/apps/{app.id}/invites", json={"target": "kim@example.com", "role": "viewer"}).json()
    c = client(db, other)
    assert c.get("/invites").json() == []
    assert c.post(f"/invites/{inv['id']}/accept").status_code == 404
    assert c.post(f"/invites/{inv['id']}/decline").status_code == 404
    assert db.query(AppInvite).count() == 1 and db.query(AppMember).count() == 0


def test_decline_and_cancel_remove_the_invite(db):
    owner, kim = add_user(db, "owner"), add_user(db, "kim", "kim@example.com")
    app = add_app(db, owner)
    oc = client(db, owner)
    first = oc.post(f"/apps/{app.id}/invites", json={"target": "kim@example.com", "role": "viewer"}).json()
    assert client(db, kim).post(f"/invites/{first['id']}/decline").status_code == 200
    assert db.query(AppInvite).count() == 0 and db.query(AppMember).count() == 0
    second = oc.post(f"/apps/{app.id}/invites", json={"target": "kim@example.com", "role": "viewer"}).json()
    assert oc.delete(f"/apps/{app.id}/invites/{second['id']}").status_code == 200
    assert db.query(AppInvite).count() == 0


# --- 멤버 관리 ---

def test_owner_changes_role_and_removes_member(db):
    owner, kim = add_user(db, "owner"), add_user(db, "kim")
    app = add_app(db, owner)
    share(db, app, kim, "viewer")
    oc = client(db, owner)
    assert oc.patch(f"/apps/{app.id}/members/{kim.id}", json={"role": "editor"}).status_code == 200
    assert db.get(AppMember, (app.id, kim.id)).role == "editor"
    assert oc.patch(f"/apps/{app.id}/members/{kim.id}", json={"role": "owner"}).status_code == 400
    assert oc.delete(f"/apps/{app.id}/members/{kim.id}").status_code == 200
    assert db.query(AppMember).count() == 0


def test_member_can_leave_but_not_remove_others(db):
    owner, kim, lee = add_user(db), add_user(db), add_user(db)
    app = add_app(db, owner)
    share(db, app, kim, "editor")
    share(db, app, lee, "viewer")
    kc = client(db, kim)
    assert kc.delete(f"/apps/{app.id}/members/{lee.id}").status_code == 403
    assert kc.patch(f"/apps/{app.id}/members/{lee.id}", json={"role": "editor"}).status_code == 403
    assert kc.delete(f"/apps/{app.id}/members/{kim.id}").status_code == 200       # 스스로 나간다
    assert db.get(AppMember, (app.id, kim.id)) is None and db.get(AppMember, (app.id, lee.id)) is not None


def test_purge_removes_members_and_invites(db):
    owner, kim = add_user(db), add_user(db)
    app = add_app(db, owner)
    share(db, app, kim, "viewer")
    client(db, owner).post(f"/apps/{app.id}/invites", json={"target": "x@x.com", "role": "viewer"})
    sharing.purge(db, app.id)
    db.commit()
    assert db.query(AppMember).count() == 0 and db.query(AppInvite).count() == 0


# --- 단계별 접근: viewer < editor < owner ---

@pytest.fixture
def roles(db, monkeypatch):
    owner, editor, viewer, stranger = add_user(db, "o"), add_user(db, "e"), add_user(db, "v"), add_user(db, "s")
    app = add_app(db, owner)
    share(db, app, editor, "editor")
    share(db, app, viewer, "viewer")
    monkeypatch.setattr(status, "get_app_status", lambda a: {"status": "running"})
    monkeypatch.setattr(status, "list_builds", lambda d, app_id=None: [])
    monkeypatch.setattr(status, "get_state", lambda d, bid, app_id=None: None)
    monkeypatch.setattr(deploy_router.env, "get_env", lambda ns, name: {"SECRET": "x"})
    monkeypatch.setattr(deploy_router.env, "set_env", lambda ns, name, e: None)
    monkeypatch.setattr(deploy_router.logs, "fetch_app_logs", lambda ns, name: {"current": ""})
    seen = {}

    async def fake_start(db_, **kw):
        seen.update(kw)
        return []

    monkeypatch.setattr(pipeline, "start_deploy", fake_start)
    monkeypatch.setattr(pipeline, "spawn_background", lambda fn, *a: None)   # env 변경 뒤 롤아웃 감시 스레드는 안 띄운다
    return type("R", (), dict(app=app, owner=owner, editor=editor, viewer=viewer, stranger=stranger, seen=seen))


def code(db, user, method, path, **kw):
    return getattr(client(db, user), method)(path, **kw).status_code


DEPLOY = {"repo_url": "https://github.com/u/repo", "runtime": "python"}


def test_viewer_can_look_but_not_touch(db, roles):
    a = roles.app.id
    for path in (f"/apps/{a}/deploy/app/status", f"/apps/{a}/deploy", f"/apps/{a}/deploy/app/logs", f"/apps/{a}/deploy/commits"):
        assert code(db, roles.viewer, "get", path) == 200, path
    assert code(db, roles.viewer, "get", f"/apps/{a}/deploy/env") == 403            # 환경변수 값은 비밀 — editor부터
    assert code(db, roles.viewer, "put", f"/apps/{a}/deploy/env", json={"env": {}}) == 403
    assert code(db, roles.viewer, "post", f"/apps/{a}/deploy", json=DEPLOY) == 403


def test_editor_can_deploy_and_edit_env(db, roles):
    a = roles.app.id
    assert code(db, roles.editor, "get", f"/apps/{a}/deploy/env") == 200
    assert code(db, roles.editor, "put", f"/apps/{a}/deploy/env", json={"env": {"A": "1"}}) == 200
    assert code(db, roles.editor, "post", f"/apps/{a}/deploy", json=DEPLOY) == 200
    assert roles.seen["app"].id == a and roles.seen["user"].id == roles.editor.id   # 앱은 그 앱, 배포한 사람은 editor


@pytest.mark.parametrize("method,path,kw", [
    ("delete", "/deploy/app", {}),                                        # 앱 삭제
    ("put", "/deploy/domain", {"json": {"domain": "www.example.com"}}),   # 커스텀 도메인
    ("delete", "/deploy/domain", {}),
    ("post", "/deploy/app/db/query", {"json": {"sql": "select 1"}}),      # DB 콘솔
    ("get", "/deploy/app/db/queries", {}),
    ("get", "/deploy/db/export", {}),                                     # DB 내려받기
    ("get", "/deploy/app/storage/objects", {}),                           # 저장소
])
def test_owner_only_actions_are_closed_to_editor(db, roles, method, path, kw):
    full = f"/apps/{roles.app.id}{path}"
    assert code(db, roles.editor, method, full, **kw) == 403
    assert code(db, roles.viewer, method, full, **kw) == 403


def test_stranger_gets_404_on_everything(db, roles):
    a = roles.app.id
    for method, path, kw in [("get", "/deploy/app/status", {}), ("get", "/deploy", {}), ("post", "/deploy", {"json": DEPLOY}),
                             ("get", "/deploy/env", {}), ("delete", "/deploy/app", {})]:
        assert code(db, roles.stranger, method, f"/apps/{a}{path}", **kw) == 404, path


def test_owner_still_does_everything(db, roles):
    a = roles.app.id
    assert code(db, roles.owner, "get", f"/apps/{a}/deploy/env") == 200
    assert code(db, roles.owner, "post", f"/apps/{a}/deploy", json=DEPLOY) == 200


def test_legacy_paths_never_reach_a_shared_app(db, roles, monkeypatch):
    # 옛 /deploy/... 경로는 "내 첫 앱"만 본다 — 공유받은 앱만 있는 유저에게는 앱이 없는 상태다
    seen = []
    monkeypatch.setattr(status, "get_app_status", lambda a: seen.append(a) or {"status": "missing"})
    client(db, roles.editor).get("/deploy/app/status")
    assert seen == [None]


# --- 025 마이그레이션 ---

PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "025_add_app_sharing.py"


def _load():
    spec = importlib.util.spec_from_file_location("m025", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_creates_and_drops_tables():
    buf = io.StringIO()
    ctx = MigrationContext.configure(dialect_name="mysql", opts={"as_sql": True, "output_buffer": buf})
    mod = _load()
    with Operations.context(ctx):
        mod.upgrade()
        mod.downgrade()
    sql = buf.getvalue()
    assert "CREATE TABLE app_members" in sql and "CREATE TABLE app_invites" in sql
    assert "PRIMARY KEY (app_id, user_id)" in sql
    assert "DROP TABLE app_invites" in sql and "DROP TABLE app_members" in sql
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            mod.upgrade()
        assert {"app_members", "app_invites"} <= set(sa.inspect(conn).get_table_names())


# --- 비공개 저장소: 편집 권한 멤버는 주인의 연결로, 앱이 쓰던 저장소에 한해서만 ---

def add_build(db, app, repo, user=None):
    db.add(Build(build_id=uuid.uuid4().hex[:8], repo_url=repo, branch="main", image="i", app_name=app.name, port=1,
                 runtime="python", user_id=(user or app).owner_id if user is None else user.id, app_id=app.id, status="running"))
    db.commit()


def test_repo_key_ignores_case_scheme_suffix_and_slash():
    from app.deploy.build.naming import repo_key

    keys = {repo_key(u) for u in ("https://github.com/Me/Shop.git", "http://github.com/me/shop/", " https://github.com/me/shop ")}
    assert keys == {"https://github.com/me/shop"}


def test_editor_is_limited_to_repos_the_app_already_uses(db, roles):
    add_build(db, roles.app, "https://github.com/o/shop.git")
    assert code(db, roles.editor, "post", f"/apps/{roles.app.id}/deploy", json=DEPLOY) == 200
    assert roles.seen["allowed_repos"] == {"https://github.com/o/shop"}
    assert code(db, roles.owner, "post", f"/apps/{roles.app.id}/deploy", json=DEPLOY) == 200
    assert roles.seen["allowed_repos"] is None                      # 주인은 제한 없음


def test_github_lookups_use_owner_connection_only_for_the_apps_repos(db):
    from app.deploy.router import _github_installation

    owner = add_user(db, "owner")
    owner.github_installation_id = 111
    editor = add_user(db, "editor")
    editor.github_installation_id = 222
    db.commit()
    app = add_app(db, owner)
    share(db, app, editor, "editor")
    add_build(db, app, "https://github.com/o/shop.git")

    assert _github_installation(db, owner, app, "https://github.com/anything/else") == 111       # 주인은 자기 연결
    assert _github_installation(db, editor, app, "https://github.com/O/Shop") == 111              # 앱 저장소 → 주인의 연결
    assert _github_installation(db, editor, app, "https://github.com/o/other-private") is None   # 다른 저장소는 못 연다
    assert _github_installation(db, editor, None, "https://github.com/x/y") == 222                # 앱 밖(옛 경로)은 자기 연결


def test_build_clones_with_the_app_owners_installation(db, monkeypatch):
    from app.deploy.build import github as gh

    owner = add_user(db, "owner")
    owner.github_installation_id = 111
    editor = add_user(db, "editor")
    editor.github_installation_id = 222
    db.commit()
    app = add_app(db, owner)
    share(db, app, editor, "editor")
    monkeypatch.setattr(gh, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    build = Build(build_id="b1", repo_url="r", branch="main", image="i", app_name="shop", port=1, runtime="python",
                  user_id=editor.id, app_id=app.id)          # 배포한 사람은 editor, 앱 주인은 owner
    assert gh._installation_id_for(build) == 111


# --- 롤백 API 권한 (편집 권한 이상, v2 앱) ---

def test_rollback_endpoint_requires_editor_and_maps_errors(db, roles, monkeypatch):
    a = roles.app.id
    started = []

    async def fake_rollback(db_, user, app, build_id):
        started.append((user.id, app.id, build_id))
        return SimpleNamespace(build_id="new12345", runtime="python", status="deploying")

    monkeypatch.setattr(pipeline, "start_rollback", fake_rollback)
    path = f"/apps/{a}/deploy/aa11bb22/rollback"
    assert code(db, roles.viewer, "post", path) == 403
    assert code(db, roles.stranger, "post", path) == 404
    res = client(db, roles.editor).post(path)
    assert res.status_code == 200 and res.json() == {"build_id": "new12345", "runtime": "python", "status": "deploying"}
    assert started == [(roles.editor.id, a, "aa11bb22")]
    assert code(db, roles.owner, "post", path) == 200

    async def missing(*args):
        raise LookupError("배포를 찾을 수 없습니다")

    async def refused(*args):
        raise ValueError("새 경로(v2) 앱만 되돌릴 수 있습니다")

    monkeypatch.setattr(pipeline, "start_rollback", missing)
    assert code(db, roles.owner, "post", path) == 404
    monkeypatch.setattr(pipeline, "start_rollback", refused)
    res = client(db, roles.owner).post(path)
    assert res.status_code == 400 and "v2" in res.json()["detail"]


def test_rollback_endpoint_without_an_app_is_404(db, roles):
    assert code(db, roles.stranger, "post", "/deploy/aa11bb22/rollback") == 404       # 옛 경로: 내 앱이 없다
