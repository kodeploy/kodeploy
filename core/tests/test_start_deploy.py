"""start_deploy 검증·분기 고정 — 배포 제출 규칙의 안전망.

start_deploy는 슬롯 조합·의존성·저장소·예약 키 규칙의 진실원이라, 거절 분기와
슬롯별 spawn 결정을 현행 그대로 고정한다. 실제 빌드/K8s는 안 돌린다 —
spawn_background를 기록용 스텁으로 갈아끼우고 DB는 mock 세션.
"""

import asyncio
import uuid
from unittest.mock import MagicMock

import pytest

from app import config
from app.apps import service as apps_service
from app.apps.model import App
from app.auth.model import User
from app.deploy.build import pipeline

APPS: dict[uuid.UUID, App] = {}   # 유저 id → 앱. 아래 가짜 apps 서비스(fake_apps)가 읽고 쓴다


def make_user(app_name="foo", user_id=None, pipeline="v1"):
    """유저와 (app_name이 있으면) 그 유저의 앱. 앱은 APPS[user.id]로 꺼낸다."""
    # SQLAlchemy 컬럼 default는 INSERT 시점 적용이라 인스턴스 생성 시 명시 세팅 필수
    user = User(id=user_id or uuid.uuid4())
    if app_name:
        APPS[user.id] = App(
            id=uuid.uuid4(), owner_id=user.id, name=app_name, namespace=f"tenant-{user.id.hex[:8]}",
            site_enabled=False, pipeline=pipeline,
        )
    return user


@pytest.fixture
def fake_apps(monkeypatch):
    """DB 없이 앱 조회·생성 — start_deploy는 MagicMock 세션으로 도니 apps 서비스를 갈아끼운다."""
    APPS.clear()
    monkeypatch.setattr(apps_service, "get_user_app", lambda db, owner_id: APPS.get(owner_id))

    def create_app(db, owner_id, name):
        APPS[owner_id] = App(
            id=uuid.uuid4(), owner_id=owner_id, name=name, namespace=f"tenant-{owner_id.hex[:8]}",
            site_enabled=False, pipeline="v1",
        )
        return APPS[owner_id]

    monkeypatch.setattr(apps_service, "create_app", create_app)
    yield APPS
    APPS.clear()


def run_deploy(db, user, **kwargs):
    args = dict(repo_url="https://github.com/u/repo", runtime="python")
    args.update(kwargs)
    return asyncio.run(pipeline.start_deploy(db, user, **args))


@pytest.fixture
def spawned(monkeypatch, fake_apps):
    """spawn_background 호출을 (함수, 인자들)로 기록 — 실제 스레드/빌드는 안 뜸."""
    calls = []
    monkeypatch.setattr(
        pipeline, "spawn_background", lambda fn, *a: calls.append((fn, a))
    )
    monkeypatch.setattr(pipeline, "_cancel_stale_builds", lambda db, uid, slot: None)
    monkeypatch.setattr(pipeline, "crud", MagicMock(create_build=lambda db, b: b))
    return calls


def spawned_fns(calls):
    return [fn for fn, _args in calls]


# --- 거절 분기 (spawn 전에 ValueError) ---

def test_rejects_neither_server_nor_static(spawned):
    with pytest.raises(ValueError):
        run_deploy(MagicMock(), make_user(), runtime="none", use_static=False)
    assert spawned == []  # 거절이면 아무 것도 spawn되면 안 됨


@pytest.mark.parametrize("kwargs", [
    {"db_type": "mysql"},
    {"use_redis": True},
    {"storage": "object"},
    {"storage": "local", "volume_mount_path": "/data"},
])
def test_rejects_deps_without_server(spawned, kwargs):
    # DB·Redis·저장소는 서버 슬롯 선언 — 정적 단독 제출에 얹으면 거절
    with pytest.raises(ValueError):
        run_deploy(
            MagicMock(), make_user(), runtime="none", use_static=True, **kwargs
        )
    assert spawned == []


def test_rejects_object_storage_when_r2_unconfigured(spawned, monkeypatch):
    monkeypatch.setattr(pipeline.r2, "is_configured", lambda: False)
    with pytest.raises(ValueError):
        run_deploy(MagicMock(), make_user(), storage="object")


def test_rejects_invalid_volume_mount_path(spawned):
    # 상대경로는 _validate_volume_fields가 거절 — 빌드 시작 전에 표면화
    with pytest.raises(ValueError):
        run_deploy(
            MagicMock(), make_user(), storage="local", volume_mount_path="data"
        )


def test_rejects_reserved_env_key_collision(spawned):
    # mysql 시크릿이 주입하는 DB_HOST와 충돌 — 관리형 연결이 조용히 깨지는 것 방지
    with pytest.raises(ValueError) as exc:
        run_deploy(
            MagicMock(), make_user(), db_type="mysql", env_vars={"DB_HOST": "x"}
        )
    assert "DB_HOST" in str(exc.value)


def test_reserved_key_ok_when_dep_off(spawned):
    # 같은 키라도 그 dep이 꺼져 있으면 충돌 아님 (외부 DB 쓰는 경우)
    builds = run_deploy(
        MagicMock(), make_user(), db_type="none", env_vars={"DB_HOST": "external"}
    )
    assert len(builds) == 1


def test_rejects_invalid_static_env_key(spawned):
    with pytest.raises(ValueError):
        run_deploy(
            MagicMock(), make_user(), runtime="none", use_static=True,
            static_env={"bad-key": "v"},
        )


# --- 슬롯별 spawn 결정 ---

def test_server_only_spawns_build_and_static_teardown(spawned):
    user = make_user()
    builds = run_deploy(MagicMock(), user, use_static=False)

    assert [b.runtime for b in builds] == ["python"]
    assert spawned_fns(spawned) == [pipeline._run_build, pipeline._teardown_static]
    assert APPS[user.id].site_enabled is False  # 슬롯 선언 저장 (라우팅 진실원)


def test_static_only_spawns_server_teardown_and_build(spawned):
    user = make_user()
    builds = run_deploy(MagicMock(), user, runtime="none", use_static=True)

    assert [b.runtime for b in builds] == ["static"]
    assert spawned_fns(spawned) == [pipeline._teardown_server, pipeline._run_build]
    assert APPS[user.id].site_enabled is True


def test_both_slots_spawn_two_builds(spawned):
    builds = run_deploy(MagicMock(), make_user(), use_static=True)
    assert [b.runtime for b in builds] == ["python", "static"]
    assert spawned_fns(spawned) == [pipeline._run_build, pipeline._run_build]


# --- Build row 내용 ---

def test_server_build_fields(spawned):
    user = make_user()
    builds = run_deploy(
        MagicMock(), user, repo_url="https://github.com/u/repo", port=8000,
        project_path="/backend/",
    )
    b = builds[0]
    assert b.repo_url == "https://github.com/u/repo.git"   # .git 접미사 정규화
    assert b.app_name == "foo"
    assert b.port == 8000
    assert b.project_path == "backend"                     # 앞뒤 슬래시 정리
    assert b.image.startswith(
        f"ghcr.io/{config.GHCR_USER}/{user.id.hex[:8]}/foo:"
    )


def test_builds_carry_app_id_and_namespace(spawned):
    user = make_user()
    app = APPS[user.id]
    app.namespace = "app-9abcdef0"             # 두 번째 앱 이후의 ns 규칙
    builds = run_deploy(MagicMock(), user, use_static=True)
    assert [(b.app_id, b.namespace) for b in builds] == [(app.id, "app-9abcdef0")] * 2
    assert all(b.tenant_id == "app-9abcdef0" for b in builds)
    # 이미지 경로의 hex8은 ns 기준 — 빌더 검증이 ns의 hex8과 맞춘다
    assert builds[0].image.startswith(f"ghcr.io/{config.GHCR_USER}/9abcdef0/foo:")


def test_deploy_to_given_app_ignores_users_first_app(spawned):
    user = make_user(app_name="first")
    second = App(id=uuid.uuid4(), owner_id=user.id, name="second", namespace="app-9abcdef0", site_enabled=False, pipeline="v1")
    builds = run_deploy(MagicMock(), user, name="ignored", app=second)
    assert [(b.app_name, b.app_id, b.namespace) for b in builds] == [("second", second.id, "app-9abcdef0")]
    assert second.site_enabled is False and APPS[user.id].name == "first"     # 첫 앱은 건드리지 않는다


def test_static_build_falls_back_to_server_repo_and_branch(spawned):
    builds = run_deploy(
        MagicMock(), make_user(), branch="dev", use_static=True,
        static_repo_url="", static_branch="",
    )
    static = builds[1]
    assert static.app_name == "foo-static"     # K8s 리소스 이름 (호스트는 슬롯 규칙)
    assert static.port == 8080                 # nginx-unprivileged 고정
    assert static.build_mode == "static"
    assert static.repo_url == "https://github.com/u/repo.git"
    assert static.branch == "dev"


def test_static_build_cmd_defaults_output_dir(spawned):
    builds = run_deploy(
        MagicMock(), make_user(), runtime="none", use_static=True,
        build_cmd="npm run build", output_dir="",
    )
    assert builds[0].output_dir == "dist"      # build_cmd 있고 output_dir 비면 dist


def test_volume_fields_normalized_into_build(spawned):
    builds = run_deploy(
        MagicMock(), make_user(), storage="local", volume_mount_path="/data/",
    )
    b = builds[0]
    assert b.volume_mount_path == "/data"      # 뒤 슬래시 정리
    assert b.use_storage is False              # local과 object는 상호배타


def test_object_storage_sets_use_storage(spawned, monkeypatch):
    monkeypatch.setattr(pipeline.r2, "is_configured", lambda: True)
    builds = run_deploy(MagicMock(), make_user(), storage="object")
    b = builds[0]
    assert b.use_storage is True
    assert b.volume_mount_path == ""


# --- 앱 이름 고정 (유저당 앱 하나인 동안) ---

def _db_with_no_name_conflict():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    return db


def test_first_deploy_fixes_app_name(spawned):
    user = make_user(app_name=None)
    builds = run_deploy(_db_with_no_name_conflict(), user, name="myapp")
    assert APPS[user.id].name == "myapp"
    assert builds[0].app_name == "myapp"


def test_first_deploy_rejects_taken_name(spawned):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = make_user("myapp")
    with pytest.raises(ValueError):
        run_deploy(db, make_user(app_name=None), name="myapp")


def test_app_name_immutable_after_first_deploy(spawned):
    user = make_user(app_name="fixed")
    builds = run_deploy(MagicMock(), user, name="other")
    assert APPS[user.id].name == "fixed"       # 이름 변경 무시 — 첫 배포에 고정
    assert builds[0].app_name == "fixed"
