"""앱 서비스 — ns 이름 규칙(첫 앱은 기존 tenant-<hex8>, 이후는 app-<id hex8>)과 조회, 이름 해소."""

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.apps import service
from app.apps.model import App
from app.auth.model import User
from app.deploy.build import naming

OWNER = uuid.UUID("d6d8b759-8552-4d6f-9a90-00665e7ca0da")
OTHER = uuid.UUID("1e202690-0000-4000-8000-000000000002")


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    App.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_first_app_keeps_legacy_namespace(db):
    app = service.create_app(db, OWNER, "demo")
    assert app.namespace == "tenant-d6d8b759"
    assert service.get_user_app(db, OWNER).id == app.id


def test_later_apps_get_their_own_namespace(db):
    first = service.create_app(db, OWNER, "demo")
    second = service.create_app(db, OWNER, "demo2")
    assert second.namespace == f"app-{second.id.hex[:8]}" and second.namespace != first.namespace
    assert service.get_user_app(db, OWNER).id == first.id        # 유저당 첫 앱


def test_recreated_first_app_reuses_legacy_namespace(db):
    first = service.create_app(db, OWNER, "demo")
    db.delete(first)
    db.commit()
    assert service.create_app(db, OWNER, "again").namespace == "tenant-d6d8b759"


def test_namespace_hex_matches_image_path_rule():
    assert service.namespace_hex("tenant-d6d8b759") == "d6d8b759"
    assert service.namespace_hex("app-9abcdef0") == "9abcdef0"


def test_users_do_not_see_each_others_app(db):
    service.create_app(db, OWNER, "demo")
    assert service.get_user_app(db, OTHER) is None


def test_resolve_app_creates_on_first_deploy_and_reuses_after(db):
    user = User(id=OWNER)
    first = naming._resolve_app("myapp", "https://github.com/u/repo", user, db)
    assert (first.name, first.namespace) == ("myapp", "tenant-d6d8b759")
    again = naming._resolve_app("other", "https://github.com/u/repo", user, db)
    assert again.id == first.id                                   # 이름은 첫 배포에 고정


def test_resolve_app_rejects_taken_name(db):
    service.create_app(db, OTHER, "taken")
    with pytest.raises(ValueError, match="이미 사용 중인 이름"):
        naming._resolve_app("taken", "https://github.com/u/repo", User(id=OWNER), db)
