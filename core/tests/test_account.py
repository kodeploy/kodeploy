"""회원 탈퇴 — 소유 앱을 먼저 지우고, 앱 밖의 개인 데이터와 계정을 지운다. 남의 데이터는 그대로다.

sqlite에 실제 테이블을 만들어 진짜 세션으로 돈다. K8s·R2·빌더는 monkeypatch.
"""

import uuid
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.apps.model import App, AppInvite, AppMember, Tier
from app.auth import account
from app.auth import router as auth_router
from app.auth.deps import get_current_user_optional
from app.auth.model import User, UserSession
from app.community.model import Comment, Post
from app.deploy import status
from app.deploy.build import v2
from app.deploy.model import Build, BuildRecord, SavedQuery
from app.shared.db import get_db


@compiles(LONGTEXT, "sqlite")
def _longtext_sqlite(type_, compiler, **kw):
    return "TEXT"


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for t in (User, UserSession, App, AppMember, AppInvite, Tier, Post, Comment, Build, BuildRecord, SavedQuery):
        t.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    session.add(Tier(name="basic", max_apps=1))
    session.commit()
    yield session
    session.close()


_n = [0]


def add_user(db, login=None, email=None, role="user"):
    _n[0] += 1
    u = User(id=uuid.uuid4(), github_id=_n[0], login=login or f"user{_n[0]}", email=email, role=role, tier="basic")
    db.add(u)
    db.commit()
    return u


def add_app(db, owner, name, pipeline="v1"):
    a = App(id=uuid.uuid4(), owner_id=owner.id, name=name, namespace=f"app-{uuid.uuid4().hex[:8]}", pipeline=pipeline)
    db.add(a)
    db.commit()
    return a


def seed_footprint(db, user):
    """이 유저가 앱 밖에 남기는 모든 흔적."""
    now = datetime(2026, 10, 10)
    db.add(UserSession(id=f"s{user.id.hex}", user_id=user.id, expires_at=now))
    post = Post(user_id=user.id, content="내 글")
    db.add(post)
    db.flush()
    db.add(Comment(post_id=post.id, user_id=user.id, content="내 댓글"))
    db.add(BuildRecord(build_id="b1", user_id=user.id, seq=1, app_name="x", runtime="python", build_mode="auto", started_at=now))
    db.commit()


def counts(db):
    return {m.__tablename__: db.query(m).count() for m in (User, UserSession, Post, Comment, BuildRecord, AppMember, AppInvite)}


# --- 앱 밖 데이터 ---

def test_purge_removes_the_user_and_everything_that_names_them(db):
    me = add_user(db, "me", "me@example.com")
    other = add_user(db, "other", "other@example.com")
    seed_footprint(db, me)
    seed_footprint(db, other)
    their_app = add_app(db, other, "theirs")
    db.add(AppMember(app_id=their_app.id, user_id=me.id, role="viewer"))                 # 내가 멤버인 남의 앱
    db.add(AppInvite(app_id=their_app.id, role="viewer", invited_by=other.id, email="ME@example.com".lower()))   # 나에게 온 초대
    db.add(AppInvite(app_id=their_app.id, role="viewer", invited_by=other.id, github_login="me"))
    db.add(AppInvite(app_id=their_app.id, role="viewer", invited_by=other.id, email="friend@example.com"))        # 남의 초대는 그대로
    db.add(Build(build_id="e1", repo_url="r", branch="b", image="i", app_name="theirs", port=1, runtime="python",
                 user_id=me.id, app_id=their_app.id, status="running"))                  # 남의 앱에서 내가 한 배포
    db.commit()

    account.purge_user_rows(db, me)

    assert db.get(User, me.id) is None
    assert counts(db) == {"users": 1, "sessions": 1, "posts": 1, "comments": 1, "build_records": 1,
                          "app_members": 0, "app_invites": 1}                      # 남의 것은 한 건씩 남는다
    assert db.query(AppInvite).one().email == "friend@example.com"
    kept = db.get(Build, "e1")
    assert kept is not None and kept.user_id is None                               # 그 앱의 이력은 남기고 나를 가리키는 칸만 비운다


def test_comments_on_my_posts_go_with_them(db):
    me, other = add_user(db), add_user(db)
    post = Post(user_id=me.id, content="내 글")
    db.add(post)
    db.flush()
    db.add(Comment(post_id=post.id, user_id=other.id, content="남이 단 댓글"))
    db.commit()
    account.purge_user_rows(db, me)
    assert db.query(Post).count() == 0 and db.query(Comment).count() == 0


# --- 앱을 먼저 지운다 ---

def run(coro):
    import asyncio
    return asyncio.run(coro)


def test_apps_are_deleted_before_the_account(db, monkeypatch):
    me = add_user(db, "me")
    a1, a2 = add_app(db, me, "one"), add_app(db, me, "two")
    order = []

    def fake_delete(db_, app):
        order.append(("app", app.name, db_.get(User, me.id) is not None))   # 이 시점엔 계정이 아직 있다
        db_.delete(app)
        db_.commit()

    monkeypatch.setattr(status, "delete_app", fake_delete)
    run(account.delete_account(db, me))
    assert order == [("app", "one", True), ("app", "two", True)]
    assert db.get(User, me.id) is None and db.query(App).count() == 0


def test_v2_app_waits_for_the_builder_first(db, monkeypatch):
    me = add_user(db, "me")
    app = add_app(db, me, "fast", pipeline="v2")
    order = []

    async def request_delete(user, a):
        order.append("builder")

    monkeypatch.setattr(v2, "request_delete", request_delete)
    monkeypatch.setattr(status, "delete_app", lambda d, a: (order.append("delete_app"), d.delete(a), d.commit()))
    run(account.delete_account(db, me))
    assert order == ["builder", "delete_app"]


def test_failure_keeps_the_account_so_it_can_be_retried(db, monkeypatch):
    me = add_user(db, "me")
    add_app(db, me, "one")

    def boom(db_, app):
        raise ValueError("삭제하지 못했습니다")

    monkeypatch.setattr(status, "delete_app", boom)
    with pytest.raises(ValueError):
        run(account.delete_account(db, me))
    assert db.get(User, me.id) is not None


def test_root_cannot_leave(db):
    root = add_user(db, "root", role="root")
    with pytest.raises(ValueError, match="root"):
        run(account.delete_account(db, root))
    assert db.get(User, root.id) is not None


# --- API ---

def client(db, user):
    api = FastAPI()
    api.include_router(auth_router.router)
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_current_user_optional] = lambda: user
    return TestClient(api)


def test_endpoint_requires_matching_login(db, monkeypatch):
    me = add_user(db, "me")
    monkeypatch.setattr(status, "delete_app", lambda d, a: None)
    c = client(db, me)
    assert c.request("DELETE", "/auth/me", json={"confirm": "someone-else"}).status_code == 400
    assert db.get(User, me.id) is not None
    assert client(db, None).request("DELETE", "/auth/me", json={"confirm": "me"}).status_code == 401


def test_endpoint_deletes_and_clears_cookie(db):
    me = add_user(db, "me")
    seed_footprint(db, me)
    res = client(db, me).request("DELETE", "/auth/me", json={"confirm": "me"})
    assert res.status_code == 200 and res.json() == {"status": "deleted"}
    assert db.query(User).count() == 0 and db.query(UserSession).count() == 0
    assert "kd_session" in res.headers.get("set-cookie", "")        # 세션 쿠키를 거둔다
