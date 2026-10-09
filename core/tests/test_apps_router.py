"""앱 API — 목록·생성, 그리고 /apps/{app_id}/deploy/... 가 가리키는 앱 선택과 접근 격리.

sqlite에 apps 테이블만 만들어 진짜 세션으로 돈다 (남의 앱을 가리는 건 SQL의 WHERE가 하는 일이라).
"""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.apps import service
from app.apps.model import App, Tier
from app.apps.router import router as apps_router
from app.auth.deps import get_current_user_optional
from app.auth.model import User
from app.deploy import router as deploy_router
from app.deploy import status
from app.shared.db import get_db


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    App.__table__.create(engine)
    Tier.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([Tier(name="basic", max_apps=1), Tier(name="standard", max_apps=3), Tier(name="master", max_apps=None)])
    session.commit()
    yield session
    session.close()


def make_client(db, user):
    api = FastAPI()
    api.include_router(deploy_router.router)
    api.include_router(deploy_router.router, prefix="/apps/{app_id}")   # main.py와 같은 붙임
    api.include_router(apps_router)
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_current_user_optional] = lambda: user
    return TestClient(api)


def user(tier="basic"):
    return User(id=uuid.uuid4(), tier=tier)


def add_app(db, owner, name, ns=None):
    a = App(id=uuid.uuid4(), owner_id=owner.id, name=name, namespace=ns or f"app-{uuid.uuid4().hex[:8]}")
    db.add(a)
    db.commit()
    return a


# --- 목록·생성 ---

def test_list_is_mine_only(db):
    me, other = user(), user()
    add_app(db, me, "mine")
    add_app(db, other, "theirs")
    body = make_client(db, me).get("/apps").json()
    assert [a["name"] for a in body] == ["mine"] and body[0]["role"] == "owner"


def test_unauthenticated_is_401(db):
    assert make_client(db, None).get("/apps").status_code == 401
    assert make_client(db, None).post("/apps", json={"name": "x"}).status_code == 401


def test_create_app(db):
    me = user()
    res = make_client(db, me).post("/apps", json={"name": "myapp"})
    assert res.status_code == 200
    assert res.json()["name"] == "myapp"
    assert service.get_user_app(db, me.id).namespace == f"tenant-{me.id.hex[:8]}"   # 첫 앱은 기존 ns 규칙


@pytest.mark.parametrize("name", ["API", "foo-api", "www", "a" * 60, "1abc"])
def test_create_rejects_bad_names(db, name):
    assert make_client(db, user()).post("/apps", json={"name": name}).status_code == 400


def test_create_rejects_taken_name(db):
    add_app(db, user(), "taken")
    res = make_client(db, user()).post("/apps", json={"name": "taken"})
    assert res.status_code == 400 and "이미 사용 중" in res.json()["detail"]


def test_basic_tier_allows_one_app(db):
    me = user("basic")
    client = make_client(db, me)
    assert client.post("/apps", json={"name": "one"}).status_code == 200
    second = client.post("/apps", json={"name": "two"})
    assert second.status_code == 400 and "최대 1개" in second.json()["detail"]
    assert len(service.list_user_apps(db, me.id)) == 1


def test_standard_tier_allows_three(db):
    me = user("standard")
    client = make_client(db, me)
    for name in ("one", "two", "three"):
        assert client.post("/apps", json={"name": name}).status_code == 200
    fourth = client.post("/apps", json={"name": "four"})
    assert fourth.status_code == 400 and "최대 3개" in fourth.json()["detail"]


def test_master_tier_is_unlimited(db):
    client = make_client(db, user("master"))
    for i in range(8):
        assert client.post("/apps", json={"name": f"app{i}x"}).status_code == 200


def test_changing_tier_limit_takes_effect(db):
    me = user("basic")
    client = make_client(db, me)
    client.post("/apps", json={"name": "one"})
    assert client.post("/apps", json={"name": "two"}).status_code == 400
    db.get(Tier, "basic").max_apps = 2                                  # 등급은 데이터라 DB에서 바로 조절된다
    db.commit()
    assert client.post("/apps", json={"name": "two"}).status_code == 200


def test_second_app_gets_its_own_namespace(db):
    me = user("standard")
    client = make_client(db, me)
    client.post("/apps", json={"name": "one"})
    client.post("/apps", json={"name": "two"})
    namespaces = [a.namespace for a in service.list_user_apps(db, me.id)]
    assert namespaces[0] == f"tenant-{me.id.hex[:8]}" and namespaces[1].startswith("app-")


# --- /apps/{app_id}/deploy: 앱 선택과 격리 ---

@pytest.fixture
def seen(monkeypatch):
    calls = []
    monkeypatch.setattr(status, "get_app_status", lambda app: calls.append(app) or {"status": "running"})
    return calls


def test_app_path_selects_that_app(db, seen):
    me = user()
    first, second = add_app(db, me, "first"), add_app(db, me, "second")
    client = make_client(db, me)
    assert client.get(f"/apps/{second.id}/deploy/app/status").status_code == 200
    assert seen[-1].id == second.id
    assert client.get("/deploy/app/status").status_code == 200       # 옛 경로 = 첫 앱
    assert seen[-1].id == first.id


def test_other_users_app_is_404_not_403(db, seen):
    me, other = user(), user()
    add_app(db, me, "mine")
    theirs = add_app(db, other, "theirs")
    res = make_client(db, me).get(f"/apps/{theirs.id}/deploy/app/status")
    assert res.status_code == 404 and seen == []                      # 존재 여부를 알리지 않는다


def test_unknown_app_is_404(db, seen):
    me = user()
    add_app(db, me, "mine")
    assert make_client(db, me).get(f"/apps/{uuid.uuid4()}/deploy/app/status").status_code == 404


def test_malformed_app_id_is_422(db, seen):
    assert make_client(db, user()).get("/apps/not-a-uuid/deploy/app/status").status_code == 422


def test_unauthenticated_app_path_is_401(db, seen):
    assert make_client(db, None).get(f"/apps/{uuid.uuid4()}/deploy/app/status").status_code == 401


def test_deploy_goes_to_selected_app(db, monkeypatch):
    from app.deploy.build import pipeline

    me = user()
    add_app(db, me, "first")
    second = add_app(db, me, "second")
    got = {}

    async def fake_start(db_, **kw):
        got.update(kw)
        return []

    monkeypatch.setattr(pipeline, "start_deploy", fake_start)
    res = make_client(db, me).post(
        f"/apps/{second.id}/deploy", json={"repo_url": "https://github.com/u/repo", "runtime": "python"},
    )
    assert res.status_code == 200 and got["app"].id == second.id
    assert res.json()["app_name"] == "second"
