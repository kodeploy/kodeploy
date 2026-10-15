"""커뮤니티 비밀글·비밀 댓글 가림 — 비밀글에 달린 답변이 다른 사람에게 새지 않아야 한다."""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.deps import get_current_user_optional
from app.auth.model import User
from app.community import router as community_router
from app.community.model import Comment, Post
from app.shared.db import get_db

HIDDEN = "비밀 댓글입니다."
ANSWER = "sentinel-answer-7f3a"


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for t in (User, Post, Comment):
        t.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


_n = [0]


def add_user(db, login, role="user"):
    _n[0] += 1
    u = User(id=uuid.uuid4(), github_id=_n[0], login=login, role=role, tier="basic")
    db.add(u)
    db.commit()
    return u


def add_post(db, author, secret):
    p = Post(user_id=author.id, content="질문", is_secret=secret)
    db.add(p)
    db.commit()
    return p


def add_comment(db, post, author, secret=False):
    c = Comment(post_id=post.id, user_id=author.id, content=ANSWER, is_secret=secret)
    db.add(c)
    db.commit()
    return c


def comment_seen_by(db, post, viewer):
    api = FastAPI()
    api.include_router(community_router.router)
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_current_user_optional] = lambda: viewer
    res = TestClient(api).get(f"/community/{post.id}")
    assert res.status_code == 200
    (c,) = res.json()["comments"]
    return c


@pytest.fixture
def people(db):
    return {
        "asker": add_user(db, "asker"),
        "admin": add_user(db, "admin", role="root"),
        "other": add_user(db, "other"),
    }


def test_plain_answer_on_secret_post_is_hidden_from_others(db, people):
    post = add_post(db, people["asker"], secret=True)
    add_comment(db, post, people["admin"])                     # 관리자가 비밀 체크 없이 답변
    for viewer in (people["other"], None):
        c = comment_seen_by(db, post, viewer)
        assert c["content"] == HIDDEN and ANSWER not in str(c)
        assert c["is_secret"] is True                          # 화면이 자물쇠를 그린다


def test_answer_on_secret_post_is_visible_to_asker_and_admin(db, people):
    post = add_post(db, people["asker"], secret=True)
    add_comment(db, post, people["admin"], secret=True)
    for viewer in (people["asker"], people["admin"]):
        assert comment_seen_by(db, post, viewer)["content"] == ANSWER


def test_comment_on_public_post_stays_public(db, people):
    post = add_post(db, people["asker"], secret=False)
    add_comment(db, post, people["admin"])
    c = comment_seen_by(db, post, None)
    assert c["content"] == ANSWER and c["is_secret"] is False


def test_secret_comment_on_public_post_hidden_from_others(db, people):
    post = add_post(db, people["asker"], secret=False)
    add_comment(db, post, people["other"], secret=True)
    assert comment_seen_by(db, post, None)["content"] == HIDDEN
    assert comment_seen_by(db, post, people["asker"])["content"] == ANSWER   # 글 작성자는 본다
