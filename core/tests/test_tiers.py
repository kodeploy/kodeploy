"""등급 — 관리자 API(목록·한도 조절·유저 등급)와 /auth/me의 등급 정보, 024 마이그레이션."""

import importlib.util
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
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.admin.router import router as admin_router
from app.apps.model import App, Tier
from app.auth import router as auth_router
from app.auth.deps import get_current_user_optional
from app.auth.model import User
from app.shared.db import get_db


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for t in (App.__table__, Tier.__table__, User.__table__):
        t.create(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([Tier(name="basic", max_apps=1), Tier(name="standard", max_apps=3), Tier(name="master", max_apps=None)])
    session.commit()
    yield session
    session.close()


def add_user(db, role="user", tier="basic", gid=[0]):
    gid[0] += 1
    u = User(id=uuid.uuid4(), github_id=gid[0], login=f"u{gid[0]}", role=role, tier=tier)
    db.add(u)
    db.commit()
    return u


def client_for(db, user):
    api = FastAPI()
    api.include_router(admin_router)
    api.include_router(auth_router.router)
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_current_user_optional] = lambda: user
    return TestClient(api)


# --- 관리자 API ---

def test_list_tiers_shows_counts_and_unlimited(db):
    add_user(db, tier="basic")
    add_user(db, tier="basic")
    root = add_user(db, role="root", tier="master")
    body = client_for(db, root).get("/admin/tiers").json()
    assert body == [
        {"name": "basic", "max_apps": 1, "users": 2},
        {"name": "standard", "max_apps": 3, "users": 0},
        {"name": "master", "max_apps": None, "users": 1},
    ]


def test_only_root_can_change_limits(db):
    admin, plain = add_user(db, role="admin"), add_user(db)
    assert client_for(db, admin).put("/admin/tiers/basic", json={"max_apps": 2}).status_code == 403
    assert client_for(db, plain).get("/admin/tiers").status_code == 403
    assert db.get(Tier, "basic").max_apps == 1


def test_root_adjusts_limit(db):
    root = add_user(db, role="root", tier="master")
    client = client_for(db, root)
    assert client.put("/admin/tiers/basic", json={"max_apps": 2}).json() == {"name": "basic", "max_apps": 2}
    assert client.put("/admin/tiers/standard", json={"max_apps": None}).json()["max_apps"] is None   # 무제한으로
    assert db.get(Tier, "standard").max_apps is None


@pytest.mark.parametrize("body,name", [({"max_apps": 0}, "basic"), ({"max_apps": -1}, "basic"), ({"max_apps": 2}, "ghost")])
def test_bad_limit_changes_are_400(db, body, name):
    root = add_user(db, role="root", tier="master")
    assert client_for(db, root).put(f"/admin/tiers/{name}", json=body).status_code == 400


def test_root_sets_user_tier(db):
    root, target = add_user(db, role="root", tier="master"), add_user(db)
    res = client_for(db, root).put(f"/admin/users/{target.id}/tier", json={"tier": "standard"})
    assert res.status_code == 200 and res.json()["tier"] == "standard"
    db.refresh(target)
    assert target.tier == "standard"
    assert client_for(db, root).put(f"/admin/users/{target.id}/tier", json={"tier": "ghost"}).status_code == 400
    assert client_for(db, add_user(db, role="admin")).put(f"/admin/users/{target.id}/tier", json={"tier": "pro"}).status_code == 403


# --- /auth/me ---

def test_me_reports_tier_and_app_counts(db):
    me = add_user(db, tier="standard")
    for n in ("a1", "a2"):
        db.add(App(id=uuid.uuid4(), owner_id=me.id, name=n, namespace=f"ns-{n}"))
    db.commit()
    body = client_for(db, me).get("/auth/me").json()
    assert (body["tier"], body["max_apps"], body["app_count"]) == ("standard", 3, 2)


def test_me_for_master_has_no_limit(db):
    body = client_for(db, add_user(db, role="root", tier="master")).get("/auth/me").json()
    assert body["max_apps"] is None


# --- 024 마이그레이션 ---

PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "024_add_tiers.py"


def load():
    spec = importlib.util.spec_from_file_location("m024", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_mysql_sql():
    buf = io.StringIO()
    ctx = MigrationContext.configure(dialect_name="mysql", opts={"as_sql": True, "output_buffer": buf})
    mod = load()
    with Operations.context(ctx):
        mod.upgrade()
        mod.downgrade()
    sql = buf.getvalue()
    assert "CREATE TABLE tiers" in sql and "INSERT INTO tiers" in sql
    assert "ALTER TABLE users ADD COLUMN tier VARCHAR(20) NOT NULL DEFAULT 'basic'" in sql
    assert "FOREIGN KEY(tier) REFERENCES tiers (name)" in sql
    assert "DROP TABLE tiers" in sql


def test_sqlite_creates_tiers_and_moves_root_to_master():
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY, role TEXT)")
        conn.exec_driver_sql("INSERT INTO users VALUES (1, 'user'), (2, 'admin'), (3, 'root')")
        mod = load()
        with Operations.context(MigrationContext.configure(conn)):
            mod.upgrade()
        assert dict(conn.exec_driver_sql("SELECT name, max_apps FROM tiers").fetchall()) == {
            "basic": 1, "standard": 3, "pro": 5, "master": None,
        }
        assert dict(conn.exec_driver_sql("SELECT id, tier FROM users").fetchall()) == {1: "basic", 2: "basic", 3: "master"}
        with Operations.context(MigrationContext.configure(conn)):
            mod.downgrade()
        assert "tiers" not in sa.inspect(conn).get_table_names()
        assert [c["name"] for c in sa.inspect(conn).get_columns("users")] == ["id", "role"]
