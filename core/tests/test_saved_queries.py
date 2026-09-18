"""저장된 쿼리 API 계약 고정 — 스코프 격리와 인가가 이 기능의 전부다.

이 기능에서 조용히 깨질 수 있는 것 둘:
  1. 스코프 누락 — (user, app, db) 세 축 중 하나라도 WHERE에서 빠지면 다른 유저/앱/DB의
     쿼리가 목록에 섞이거나 남의 row가 수정·삭제된다. id는 연속 정수라 찍기도 쉽다.
  2. 라우트 순서 — /app/db/queries가 /{build_id}보다 아래로 내려가면 "app"이 build_id로
     잡혀 404가 난다 (router.py의 기존 순서 의존과 같은 함정).

sqlite에 saved_queries 테이블만 만들어 진짜 세션으로 돈다 — 스코프는 SQL이 거는 것이라
crud를 MagicMock으로 대신하면 정작 고정하려는 성질이 사라진다.
"""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.deps import get_current_user_optional
from app.auth.model import User
from app.deploy import crud
from app.deploy import router as deploy_router
from app.deploy.model import SavedQuery
from app.shared.db import get_db


@pytest.fixture
def db():
    """saved_queries 테이블만 있는 sqlite 세션 (Build는 LONGTEXT라 못 만든다).

    StaticPool — TestClient는 요청을 워커 스레드에서 돌린다. 기본 풀이면 스레드마다
    새 connection = 새 빈 :memory: DB가 잡혀 "no such table"이 난다.
    """
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    SavedQuery.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def make_user(app_name="foo"):
    return User(id=uuid.uuid4(), app_name=app_name, site_enabled=False)


def make_client(db, user=None, db_type="mysql", monkeypatch=None):
    """deploy router만 올린 테스트 앱.

    db_type은 최신 서버 빌드에서 나오는 값이라 crud.get_server_build를 대신 세운다
    (builds 테이블은 sqlite에 못 만든다). db_type=None이면 "DB 없는 앱".
    """
    app = FastAPI()
    app.include_router(deploy_router.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user_optional] = lambda: user

    fake_build = None if db_type is None else type("B", (), {"db_type": db_type})()
    monkeypatch.setattr(crud, "get_server_build", lambda _db, _uid: fake_build)
    return TestClient(app)


def seed(db, *, user_id, app_name, db_type, name, sql="SELECT 1"):
    row = SavedQuery(
        user_id=user_id, app_name=app_name, db_type=db_type, name=name, sql_text=sql,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# --- 인가 ---

def test_unauthenticated_is_401(db, monkeypatch):
    client = make_client(db, user=None, monkeypatch=monkeypatch)
    assert client.get("/deploy/app/db/queries").status_code == 401
    assert client.post("/deploy/app/db/queries", json={"name": "a", "sql": "SELECT 1"}).status_code == 401
    assert client.patch("/deploy/app/db/queries/1", json={"name": "b"}).status_code == 401
    assert client.delete("/deploy/app/db/queries/1").status_code == 401


def test_app_without_db_is_400(db, monkeypatch):
    client = make_client(db, user=make_user(), db_type=None, monkeypatch=monkeypatch)
    assert client.get("/deploy/app/db/queries").status_code == 400


def test_user_without_app_is_400(db, monkeypatch):
    client = make_client(db, user=make_user(app_name=None), monkeypatch=monkeypatch)
    assert client.get("/deploy/app/db/queries").status_code == 400


# --- 라우트 순서 (정적 경로가 /{build_id}에 잡히면 안 됨) ---

def test_queries_route_not_captured_by_build_id(db, monkeypatch):
    # /{build_id}로 잘못 매칭되면 get_state를 타서 404가 된다. 200 + 리스트면 제 핸들러.
    client = make_client(db, user=make_user(), monkeypatch=monkeypatch)
    res = client.get("/deploy/app/db/queries")
    assert res.status_code == 200
    assert res.json() == []


# --- CRUD 왕복 ---

def test_create_list_update_delete_roundtrip(db, monkeypatch):
    user = make_user()
    client = make_client(db, user=user, monkeypatch=monkeypatch)

    created = client.post(
        "/deploy/app/db/queries",
        json={"name": "  회원 목록  ", "sql": "  SELECT * FROM member;  "},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["name"] == "회원 목록"            # 앞뒤 공백은 서버가 정규화
    assert body["sql"] == "SELECT * FROM member;"
    qid = body["id"]

    assert [q["id"] for q in client.get("/deploy/app/db/queries").json()] == [qid]

    # 부분 수정 — 이름만 줘도 SQL은 그대로
    patched = client.patch(f"/deploy/app/db/queries/{qid}", json={"name": "회원"})
    assert patched.status_code == 200
    assert patched.json()["name"] == "회원"
    assert patched.json()["sql"] == "SELECT * FROM member;"

    assert client.delete(f"/deploy/app/db/queries/{qid}").status_code == 200
    assert client.get("/deploy/app/db/queries").json() == []


def test_list_is_newest_first(db, monkeypatch):
    user = make_user()
    client = make_client(db, user=user, monkeypatch=monkeypatch)
    for name in ("첫째", "둘째", "셋째"):
        client.post("/deploy/app/db/queries", json={"name": name, "sql": "SELECT 1"})
    assert [q["name"] for q in client.get("/deploy/app/db/queries").json()] == [
        "셋째", "둘째", "첫째",
    ]


# --- 스코프 격리 (이 기능의 핵심 계약) ---

def test_list_excludes_other_user_app_and_db(db, monkeypatch):
    me = make_user(app_name="foo")
    seed(db, user_id=me.id, app_name="foo", db_type="mysql", name="내 것")
    seed(db, user_id=uuid.uuid4(), app_name="foo", db_type="mysql", name="남의 유저")
    seed(db, user_id=me.id, app_name="bar", db_type="mysql", name="다른 앱")
    seed(db, user_id=me.id, app_name="foo", db_type="postgres", name="다른 DB")

    client = make_client(db, user=me, db_type="mysql", monkeypatch=monkeypatch)
    assert [q["name"] for q in client.get("/deploy/app/db/queries").json()] == ["내 것"]


@pytest.mark.parametrize("other", [
    {"user": True},                       # 다른 유저의 row
    {"app_name": "bar"},                  # 같은 유저, 다른 앱
    {"db_type": "postgres"},              # 같은 유저·앱, 다른 DB
])
def test_out_of_scope_row_is_404_on_patch_and_delete(db, monkeypatch, other):
    me = make_user(app_name="foo")
    row = seed(
        db,
        user_id=uuid.uuid4() if other.get("user") else me.id,
        app_name=other.get("app_name", "foo"),
        db_type=other.get("db_type", "mysql"),
        name="남의 쿼리",
    )
    client = make_client(db, user=me, db_type="mysql", monkeypatch=monkeypatch)

    assert client.patch(f"/deploy/app/db/queries/{row.id}", json={"name": "탈취"}).status_code == 404
    assert client.delete(f"/deploy/app/db/queries/{row.id}").status_code == 404
    # 실제로 안 바뀌고 안 지워졌는지 — 404만 보고 지나가면 "지워놓고 404"를 못 잡는다
    db.refresh(row)
    assert row.name == "남의 쿼리"


# --- 입력 검증 ---

@pytest.mark.parametrize("payload", [
    {"name": "   ", "sql": "SELECT 1"},                 # 빈 이름
    {"name": "a" * 101, "sql": "SELECT 1"},             # 이름 길이 초과
    {"name": "ok", "sql": "   "},                       # 빈 SQL
    {"name": "ok", "sql": "x" * 20001},                 # 실행 경로와 같은 상한
])
def test_invalid_input_is_400(db, monkeypatch, payload):
    client = make_client(db, user=make_user(), monkeypatch=monkeypatch)
    assert client.post("/deploy/app/db/queries", json=payload).status_code == 400


def test_empty_patch_is_400(db, monkeypatch):
    me = make_user()
    row = seed(db, user_id=me.id, app_name="foo", db_type="mysql", name="이름")
    client = make_client(db, user=me, monkeypatch=monkeypatch)
    assert client.patch(f"/deploy/app/db/queries/{row.id}", json={}).status_code == 400


def test_quota_caps_saved_queries(db, monkeypatch):
    me = make_user()
    for i in range(deploy_router.MAX_SAVED_QUERIES):
        seed(db, user_id=me.id, app_name="foo", db_type="mysql", name=f"q{i}")
    client = make_client(db, user=me, monkeypatch=monkeypatch)
    res = client.post("/deploy/app/db/queries", json={"name": "하나 더", "sql": "SELECT 1"})
    assert res.status_code == 400
    assert "최대" in res.json()["detail"]
