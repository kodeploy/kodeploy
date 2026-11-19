"""재배포 API — 라이브 빌드에서 start_deploy 인자를 다시 만들고, 커밋을 지정하고, 같은 키는 한 번만 시작한다.

SQLite 파일 DB에 builds·apps·deploy_requests를 실제로 만들고 진짜 start_deploy까지 돌린다.
바깥으로 나가는 것(빌더 제출 조건의 GitHub 확인, 백그라운드 spawn, R2 설정 확인)만 갈아끼운다.
"""

import asyncio
import json
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.apps import service as apps_service
from app.apps.model import App
from app.auth.deps import get_current_user_optional
from app.auth.model import User
from app.deploy import router as deploy_router
from app.deploy.build import pipeline, redeploy, v2
from app.deploy.model import Build, DeployRequestKey
from app.shared.db import get_db


@compiles(LONGTEXT, "sqlite")
def _longtext_sqlite(type_, compiler, **kw):
    return "TEXT"


OWNER = uuid.UUID("d6d8b759-8552-4d6f-9a90-00665e7ca0da")
APP_ID = uuid.UUID("11111111-2222-4333-8444-555555555555")
NS = "app-11111111"
API_REPO = "https://github.com/acme/api.git"
WEB_REPO = "https://github.com/acme/web.git"
SHA = "0123456789abcdef0123456789abcdef01234567"
T0 = datetime(2026, 11, 1, 12, 0, 0)

SERVER_FIELDS = ("repo_url", "branch", "runtime", "port", "build_mode", "dockerfile_path", "project_path",
                 "db_type", "use_redis", "use_storage", "volume_mount_path", "volume_storage_class", "volume_size",
                 "app_name", "namespace", "app_id")
STATIC_FIELDS = ("repo_url", "branch", "runtime", "port", "build_mode", "project_path", "build_cmd", "output_dir",
                 "build_env", "app_name", "namespace", "app_id", "db_type", "use_redis", "use_storage")


@pytest.fixture
def env(tmp_path, monkeypatch):
    eng = sa.create_engine(f"sqlite:///{tmp_path / 'core.sqlite'}")
    for table in (Build.__table__, App.__table__, DeployRequestKey.__table__):
        table.create(eng)
    Session = sessionmaker(bind=eng)

    state = SimpleNamespace(spawned=[], gate=None, submits=[], Session=Session)

    async def check_submit(user, *, build_mode, dockerfile_path, project_path="", **kw):
        state.submits.append(kw)
        if state.gate is not None:
            await state.gate.wait()
        return ("auto", project_path) if build_mode == "auto" else ("dockerfile", dockerfile_path or "Dockerfile")

    monkeypatch.setattr(pipeline, "spawn_background", lambda fn, *a: state.spawned.append((fn, a)))
    monkeypatch.setattr(v2, "check_submit", check_submit)
    monkeypatch.setattr(apps_service, "repo_installation_id", lambda db, app: None)
    monkeypatch.setattr(pipeline.r2, "is_configured", lambda: True)
    monkeypatch.setattr(redeploy, "REPLAY_WAIT_SECONDS", 0.01)
    return state


def make_app(s, site_enabled=False):
    app = App(id=APP_ID, owner_id=OWNER, name="demo", namespace=NS, site_enabled=site_enabled, pipeline="v2")
    s.add(app)
    s.commit()
    return app


def add_server(s, build_id, minute, **kw):
    fields = dict(
        build_id=build_id, repo_url=API_REPO, branch="main",
        image=f"ghcr.io/kodeploy/11111111/demo:{build_id}@sha256:" + "a" * 64,
        app_name="demo", port=8080, runtime="java", db_type="none", use_redis=False, use_storage=False,
        volume_mount_path="", volume_storage_class="local-path", volume_size="5Gi", kind="build",
        build_mode="dockerfile", dockerfile_path="Dockerfile", project_path="", build_cmd="", output_dir="",
        status="running", user_id=OWNER, app_id=APP_ID, namespace=NS, last_event_seq=5,
        created_at=T0 + timedelta(minutes=minute), updated_at=T0 + timedelta(minutes=minute),
    )
    fields.update(kw)
    s.add(Build(**fields))
    s.commit()


def add_static(s, build_id, minute, **kw):
    fields = dict(
        build_id=build_id, repo_url=API_REPO, branch="main",
        image=f"ghcr.io/kodeploy/11111111/demo-static:{build_id}@sha256:" + "b" * 64,
        app_name="demo-static", port=8080, runtime="static", db_type="none", use_redis=False, use_storage=False,
        volume_mount_path="", volume_storage_class="local-path", volume_size="5Gi", kind="build",
        build_mode="static", dockerfile_path="Dockerfile", project_path="frontend",
        build_cmd="npm ci && npm run build", output_dir="dist", build_env=json.dumps({"VITE_API": "https://demo-api.kodeploy.com"}),
        status="running", user_id=OWNER, app_id=APP_ID, namespace=NS, last_event_seq=5,
        created_at=T0 + timedelta(minutes=minute), updated_at=T0 + timedelta(minutes=minute),
    )
    fields.update(kw)
    s.add(Build(**fields))
    s.commit()


def run(s, app, user=None, **kw):
    return asyncio.run(redeploy.start_redeploy(s, user or User(id=OWNER), app, **kw))


def pick(build, names):
    return {n: getattr(build, n) for n in names}


def new_rows(s, before):
    s.expire_all()
    return [b for b in s.query(Build).order_by(Build.created_at).all() if b.build_id not in before]


def ids(s):
    return {b.build_id for b in s.query(Build).all()}


# --- 설정 재구성: 다시 만든 행이 라이브 행과 같은 설정이어야 한다 ---

def _server_only(s):
    app = make_app(s)
    add_server(s, "a0000001", 0, db_type="mysql", use_redis=True, port=9000, branch="release",
               build_mode="auto", project_path="backend", runtime="python")
    return app, {"server": "a0000001"}


def _static_only(s):
    app = make_app(s, site_enabled=True)
    add_static(s, "c0000001", 0, repo_url=WEB_REPO, branch="gh-pages")
    return app, {"static": "c0000001"}


def _both(s):
    app = make_app(s, site_enabled=True)
    add_server(s, "a0000001", 0, use_storage=True)
    add_static(s, "c0000001", 0)
    return app, {"server": "a0000001", "static": "c0000001"}


def _two_repos(s):
    app = make_app(s, site_enabled=True)
    add_server(s, "a0000001", 0, volume_mount_path="/data", volume_size="10Gi", dockerfile_path="docker/Dockerfile")
    add_static(s, "c0000001", 0, repo_url=WEB_REPO, branch="develop", project_path="")
    return app, {"server": "a0000001", "static": "c0000001"}


def _failed_latest(s):
    # 최근 제출이 실패했으면 그 전의 성공한 배포가 기준이다
    app = make_app(s)
    add_server(s, "a0000001", 0, db_type="postgres")
    add_server(s, "a0000002", 5, db_type="mysql", port=1234, status="failed")
    return app, {"server": "a0000001"}


@pytest.mark.parametrize("setup", [_server_only, _static_only, _both, _two_repos, _failed_latest])
def test_rebuilt_rows_match_the_live_rows(env, setup):
    with env.Session() as s:
        app, live = setup(s)
        expected = {slot: pick(s.get(Build, bid), SERVER_FIELDS if slot == "server" else STATIC_FIELDS)
                    for slot, bid in live.items()}
        before = ids(s)
        result = run(s, app)
        rows = new_rows(s, before)

        got = {redeploy.slot_of(b): b for b in rows}
        assert set(got) == set(expected)
        for slot, fields in expected.items():
            assert pick(got[slot], SERVER_FIELDS if slot == "server" else STATIC_FIELDS) == fields
            assert got[slot].status == "queued" and got[slot].requested_sha is None
        assert [b.build_id for b in result.builds] == [b.build_id for b in rows]
        assert result.replayed is False and result.requested_sha is None
        s.refresh(app)
        assert app.site_enabled == ("static" in expected)       # 정적 선언은 그대로


def test_env_and_initial_dump_are_left_alone(env):
    with env.Session() as s:
        app, _ = _both(s)
        run(s, app)
    runs = [a for fn, a in env.spawned if fn is v2.run_v2_build]
    server_args = [a for a in runs if len(a) == 3]
    assert server_args and all(a[1] == {} and a[2] is None for a in server_args)   # 빈 env = Secret을 건드리지 않는다
    assert not any(fn is v2.remove_server for fn, _ in env.spawned)                # 서버를 내리지 않는다


def test_static_site_is_not_taken_down(env):
    with env.Session() as s:
        app, _ = _both(s)
        before = ids(s)
        run(s, app)
        rows = new_rows(s, before)
        assert sorted(redeploy.slot_of(b) for b in rows) == ["server", "static"]
        s.refresh(app)
        assert app.site_enabled is True


# --- 롤백이 라이브인 경우 ---

def test_rollback_live_uses_the_origin_code_and_the_current_dependencies(env):
    with env.Session() as s:
        app = make_app(s)
        # A: 처음 배포 (DB 없음) → B: MySQL·R2를 켠 배포 → A로 롤백 (R은 A의 칸을 복사해 둔다)
        add_server(s, "a0000001", 0, branch="stable", port=8080, db_type="none", use_storage=False)
        add_server(s, "b0000002", 5, branch="next", port=9000, db_type="mysql", use_storage=True, use_redis=True)
        add_server(s, "d0000003", 9, branch="stable", port=8080, db_type="none", use_storage=False, rollback_of="a0000001")
        before = ids(s)
        run(s, app)
        (row,) = new_rows(s, before)
        # 코드·빌드 설정은 롤백으로 되돌아간 원본(A) — 지금 떠 있는 이미지의 출처
        assert (row.branch, row.port) == ("stable", 8080)
        # DB·Redis·R2는 롤백이 values를 바꾸지 않아 B의 것이 살아 있다 — A의 값으로 보내면 DB를 내리고 R2를 회수한다
        assert (row.db_type, row.use_redis, row.use_storage) == ("mysql", True, True)


def test_rollback_chain_resolves_to_the_first_origin(env):
    with env.Session() as s:
        app = make_app(s)
        add_server(s, "a0000001", 0, branch="stable", dockerfile_path="ops/Dockerfile")
        add_server(s, "b0000002", 5, branch="next")
        add_server(s, "d0000003", 9, branch="stable", dockerfile_path="ops/Dockerfile", rollback_of="a0000001")
        add_server(s, "e0000004", 12, branch="stable", dockerfile_path="ops/Dockerfile", rollback_of="d0000003")
        live = redeploy.live_slots(s, app)
        assert (live.server.build_id, live.server_code.build_id, live.server_deps.build_id) == (
            "e0000004", "a0000001", "b0000002")


# --- 서버를 내린 앱 (정적만 남긴 제출) ---

def test_static_only_submission_after_the_server_drops_the_server(env):
    with env.Session() as s:
        app = make_app(s, site_enabled=True)
        add_server(s, "a0000001", 0)
        add_static(s, "c0000001", 0)          # 같은 제출의 짝 (같은 초)
        add_static(s, "c0000002", 7)          # 정적만 남긴 제출 — 서버 행 없음
        live = redeploy.live_slots(s, app)
        assert live.server is None and live.static.build_id == "c0000002"
        before = ids(s)
        run(s, app)
        rows = new_rows(s, before)
        assert [redeploy.slot_of(b) for b in rows] == ["static"]   # 내린 서버를 다시 올리지 않는다


def test_static_after_a_rollback_means_the_server_was_dropped(env):
    with env.Session() as s:
        app = make_app(s, site_enabled=True)
        add_server(s, "a0000001", 0)
        add_server(s, "d0000003", 5, rollback_of="a0000001")
        add_static(s, "c0000001", 8)          # 롤백에는 정적 짝이 없다
        assert redeploy.live_slots(s, app).server is None


def test_a_single_static_row_after_the_server_keeps_the_server(env):
    # 서버만 배포 → 정적만 켠 제출일 수도, 한 제출의 짝일 수도 있다 — 서버를 잘못 내리지 않는 쪽으로 본다
    with env.Session() as s:
        app = make_app(s, site_enabled=True)
        add_server(s, "a0000001", 0)
        add_static(s, "c0000001", 3)
        live = redeploy.live_slots(s, app)
        assert live.server.build_id == "a0000001" and live.static.build_id == "c0000001"


def test_static_rows_are_ignored_when_the_site_is_disabled(env):
    with env.Session() as s:
        app = make_app(s, site_enabled=False)
        add_server(s, "a0000001", 0)
        add_static(s, "c0000001", 0)
        before = ids(s)
        run(s, app)
        assert [redeploy.slot_of(b) for b in new_rows(s, before)] == ["server"]


# --- 커밋 지정 범위 ---

def test_commit_pins_every_slot_on_the_same_repo(env):
    with env.Session() as s:
        app, _ = _both(s)                     # 두 슬롯이 같은 저장소
        before = ids(s)
        result = run(s, app, commit_sha=SHA)
        rows = new_rows(s, before)
        assert {redeploy.slot_of(b): b.requested_sha for b in rows} == {"server": SHA, "static": SHA}
        assert result.requested_sha == SHA


def test_commit_pins_only_the_server_repo_by_default(env):
    with env.Session() as s:
        app, _ = _two_repos(s)
        before = ids(s)
        run(s, app, commit_sha=SHA)
        got = {redeploy.slot_of(b): (b.requested_sha, b.branch) for b in new_rows(s, before)}
        # 다른 저장소의 정적 슬롯은 그 브랜치 최신으로 같이 다시 빌드된다
        assert got == {"server": (SHA, "main"), "static": (None, "develop")}


def test_repo_selects_which_slot_gets_the_commit(env):
    with env.Session() as s:
        app, _ = _two_repos(s)
        before = ids(s)
        run(s, app, commit_sha=SHA, repo="https://github.com/ACME/web")   # 대소문자·.git 차이는 같은 저장소
        got = {redeploy.slot_of(b): b.requested_sha for b in new_rows(s, before)}
        assert got == {"server": None, "static": SHA}


def test_commit_falls_back_to_the_static_repo_without_a_server(env):
    with env.Session() as s:
        app, _ = _static_only(s)
        before = ids(s)
        run(s, app, commit_sha=SHA.upper())   # 대문자는 소문자로 바꿔 받는다
        (row,) = new_rows(s, before)
        assert row.requested_sha == SHA


def test_pinned_rows_send_the_commit_as_the_builder_ref(env):
    with env.Session() as s:
        app, _ = _server_only(s)
        result = run(s, app, commit_sha=SHA)
        payload = v2.build_payload(result.builds[0], User(id=OWNER), ["demo.kodeploy.com"])
        assert payload["build"]["ref"] == SHA


# --- 오류 ---

@pytest.mark.parametrize("sha", ["abc1234", SHA[:39], SHA + "0", "g" * 40, "", " " + SHA])
def test_bad_commit_sha_is_400(env, sha):
    with env.Session() as s:
        app, _ = _server_only(s)
        with pytest.raises(redeploy.RedeployError) as e:
            run(s, app, commit_sha=sha)
        assert e.value.status == 400


@pytest.mark.parametrize("key", ["", "a" * 101, "has space", "콘솔", "a/b"])
def test_bad_idempotency_key_is_400(env, key):
    with env.Session() as s:
        app, _ = _server_only(s)
        with pytest.raises(redeploy.RedeployError) as e:
            run(s, app, key=key)
        assert e.value.status == 400
        assert s.query(DeployRequestKey).count() == 0


def test_repo_that_no_slot_uses_is_400(env):
    with env.Session() as s:
        app, _ = _server_only(s)
        with pytest.raises(redeploy.RedeployError) as e:
            run(s, app, commit_sha=SHA, repo="https://github.com/acme/other")
        assert e.value.status == 400


@pytest.mark.parametrize("seed", [
    lambda s: None,                                                   # 빌드가 하나도 없다 (첫 배포 전)
    lambda s: add_server(s, "a0000001", 0, status="failed"),           # 첫 배포가 실패했다
    lambda s: add_server(s, "a0000001", 0, status="building"),         # 첫 배포가 아직 돈다
])
def test_no_live_build_is_409(env, seed):
    with env.Session() as s:
        app = make_app(s)
        seed(s)
        before = ids(s)
        with pytest.raises(redeploy.RedeployError) as e:
            run(s, app, key="k-409")
        assert e.value.status == 409
        assert new_rows(s, before) == [] and env.spawned == []
        assert s.query(DeployRequestKey).count() == 0     # 실패한 요청은 키를 붙잡지 않는다


def test_declared_static_without_a_live_row_is_409(env):
    # site_enabled인데 정적이 한 번도 성공하지 않았으면 그대로 다시 만들 수 없다 (빼고 보내면 정적을 내린다)
    with env.Session() as s:
        app = make_app(s, site_enabled=True)
        add_server(s, "a0000001", 0)
        add_static(s, "c0000001", 0, status="failed")
        with pytest.raises(redeploy.RedeployError) as e:
            run(s, app)
        assert e.value.status == 409


def test_editor_outside_the_allowed_repos_is_403(env):
    with env.Session() as s:
        app, _ = _two_repos(s)
        with pytest.raises(redeploy.RedeployError) as e:
            run(s, app, user=User(id=uuid.uuid4()), allowed_repos={"https://github.com/acme/api"})
        assert e.value.status == 403
        assert env.spawned == []


def test_editor_keeps_the_allowed_repos_rule(env, monkeypatch):
    seen = {}
    real = pipeline.start_deploy

    async def spy(db, user, **kw):
        seen.update(kw)
        return await real(db, user, **kw)

    monkeypatch.setattr(pipeline, "start_deploy", spy)
    with env.Session() as s:
        app, _ = _two_repos(s)
        allowed = {"https://github.com/acme/api", "https://github.com/acme/web"}
        editor = User(id=uuid.uuid4())
        result = run(s, app, user=editor, allowed_repos=allowed)
        assert seen["allowed_repos"] == allowed
        assert all(b.user_id == editor.id for b in result.builds)


# --- Idempotency-Key ---

def test_same_key_twice_returns_the_same_builds_without_cancelling(env):
    with env.Session() as s:
        app, _ = _both(s)
        first = run(s, app, key="deploy-1", commit_sha=SHA)
        spawned = len(env.spawned)
        second = run(s, app, key="deploy-1")
        assert [b.build_id for b in second.builds] == [b.build_id for b in first.builds]
        assert second.replayed is True and second.requested_sha == SHA
        assert len(env.spawned) == spawned                                  # 다시 빌드도, 취소도 없다
        assert all(b.status == "queued" for b in second.builds)


def test_replay_reports_the_current_status_even_if_it_failed(env):
    with env.Session() as s:
        app, _ = _server_only(s)
        first = run(s, app, key="deploy-1")
        bid = first.builds[0].build_id
        s.get(Build, bid).status = "failed"
        s.commit()
        again = run(s, app, key="deploy-1")
        assert [(b.build_id, b.status) for b in again.builds] == [(bid, "failed")]
        assert again.replayed is True


def test_a_new_key_starts_new_builds_and_cancels_the_previous_ones(env):
    with env.Session() as s:
        app, _ = _both(s)
        first = run(s, app, key="deploy-1")
        second = run(s, app, key="deploy-2")
        old, new = {b.build_id for b in first.builds}, {b.build_id for b in second.builds}
        assert old.isdisjoint(new) and len(new) == 2
        s.expire_all()
        assert {s.get(Build, bid).status for bid in old} == {"cancelled"}
        cancelled = {a[0] for fn, a in env.spawned if fn is v2.cancel_remote}
        assert cancelled == old


def test_without_a_key_every_call_is_a_new_request(env):
    with env.Session() as s:
        app, _ = _server_only(s)
        a = run(s, app)
        b = run(s, app)
        assert a.builds[0].build_id != b.builds[0].build_id
        assert s.query(DeployRequestKey).count() == 0


def test_concurrent_requests_with_the_same_key_start_once(env):
    env.gate = asyncio.Event()

    async def scenario():
        sa_, sb = env.Session(), env.Session()
        try:
            app_a, app_b = sa_.get(App, APP_ID), sb.get(App, APP_ID)
            first = asyncio.create_task(redeploy.start_redeploy(sa_, User(id=OWNER), app_a, key="dbl"))
            while not env.submits:                     # 첫 요청이 키를 잡고 빌더 조건 검사에서 멈출 때까지
                await asyncio.sleep(0.001)
            second = asyncio.create_task(redeploy.start_redeploy(sb, User(id=OWNER), app_b, key="dbl"))
            await asyncio.sleep(0.05)                  # 두 번째는 키 충돌로 기다리는 중
            assert not second.done()
            env.gate.set()
            a, b = await first, await second
            return ([x.build_id for x in a.builds], a.replayed), ([x.build_id for x in b.builds], b.replayed)
        finally:
            sa_.close()
            sb.close()

    with env.Session() as s:
        _both(s)
        before = ids(s)
    (a_ids, a_replayed), (b_ids, b_replayed) = asyncio.run(scenario())
    assert a_ids == b_ids and len(a_ids) == 2
    assert (a_replayed, b_replayed) == (False, True)
    assert len(env.submits) == 1                       # 빌드 시작은 한 번
    with env.Session() as s:
        assert len(new_rows(s, before)) == 2


def test_key_still_in_progress_is_409(env, monkeypatch):
    monkeypatch.setattr(redeploy, "REPLAY_WAIT_TRIES", 2)
    with env.Session() as s:
        app, _ = _server_only(s)
        s.add(DeployRequestKey(app_id=APP_ID, key="busy", build_ids=None))   # 다른 요청이 선점하고 아직 처리 중
        s.commit()
        before = ids(s)
        with pytest.raises(redeploy.RedeployError) as e:
            run(s, app, key="busy")
        assert e.value.status == 409
        assert new_rows(s, before) == []


def test_failed_start_releases_the_key_for_a_retry(env, monkeypatch):
    real = pipeline.start_deploy

    async def refuse(*a, **kw):
        raise ValueError("오브젝트 스토리지(R2)가 서버에 설정되지 않았습니다")

    with env.Session() as s:
        app, _ = _server_only(s)
        monkeypatch.setattr(pipeline, "start_deploy", refuse)
        with pytest.raises(redeploy.RedeployError) as e:
            run(s, app, key="retry-me")
        assert e.value.status == 400
        assert s.query(DeployRequestKey).count() == 0
        monkeypatch.setattr(pipeline, "start_deploy", real)
        again = run(s, app, key="retry-me")
        assert again.replayed is False and len(again.builds) == 1


def test_unexpected_error_also_releases_the_key(env, monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("db went away")

    monkeypatch.setattr(pipeline, "start_deploy", boom)
    with env.Session() as s:
        app, _ = _server_only(s)
        with pytest.raises(RuntimeError):
            run(s, app, key="k")
        assert s.query(DeployRequestKey).count() == 0


# --- HTTP (라우터) ---

@pytest.fixture
def http(env, monkeypatch):
    access = {}

    def access_for(db, user, app_id):
        role = access.get(user.id)
        app = db.get(App, app_id)
        return (app, role, False) if app is not None and role else None

    monkeypatch.setattr(deploy_router.sharing, "access_for", access_for)

    def client(user):
        api = FastAPI()
        api.include_router(deploy_router.router)
        api.include_router(deploy_router.router, prefix="/apps/{app_id}")

        def db():
            s = env.Session()
            try:
                yield s
            finally:
                s.close()

        api.dependency_overrides[get_db] = db
        api.dependency_overrides[get_current_user_optional] = lambda: user
        return TestClient(api)

    return SimpleNamespace(access=access, client=client)


def test_http_new_then_replayed(env, http):
    with env.Session() as s:
        _both(s)
    owner = User(id=OWNER)
    http.access[OWNER] = "owner"
    c = http.client(owner)
    r1 = c.post(f"/apps/{APP_ID}/deploy/redeploy", json={"commit_sha": SHA}, headers={"Idempotency-Key": "abc-1"})
    assert r1.status_code == 202
    body = r1.json()
    assert body["app_id"] == str(APP_ID) and body["requested_sha"] == SHA and body["replayed"] is False
    assert [(b["slot"], b["status"], b["ref"]) for b in body["builds"]] == [("server", "queued", SHA), ("static", "queued", SHA)]
    assert all(len(b["build_id"]) == 8 for b in body["builds"])

    r2 = c.post(f"/deploy/redeploy?app_id={APP_ID}", json={"commit_sha": SHA}, headers={"Idempotency-Key": "abc-1"})
    assert r2.status_code == 200
    assert r2.json()["replayed"] is True and r2.json()["builds"] == body["builds"]


def test_http_without_body_redeploys_the_branch_tip(env, http):
    with env.Session() as s:
        _server_only(s)
    http.access[OWNER] = "owner"
    r = http.client(User(id=OWNER)).post(f"/apps/{APP_ID}/deploy/redeploy")
    assert r.status_code == 202
    assert r.json()["requested_sha"] is None and r.json()["builds"][0]["ref"] == "release"


@pytest.mark.parametrize("who,role,path,body,headers,code", [
    ("owner", "owner", "/deploy/redeploy", {}, {}, 400),                                   # app_id 없음 — 첫 앱으로 가지 않는다
    ("owner", "owner", "/apps/{app}/deploy/redeploy", {"commit_sha": "abc1234"}, {}, 400),
    ("owner", "owner", "/apps/{app}/deploy/redeploy", {}, {"Idempotency-Key": "bad key"}, 400),
    ("owner", "owner", "/apps/{app}/deploy/redeploy", {"commit_sha": SHA, "repo": "https://github.com/x/y"}, {}, 400),
    ("viewer", "viewer", "/apps/{app}/deploy/redeploy", {}, {}, 403),                      # 보기 권한
    ("stranger", None, "/apps/{app}/deploy/redeploy", {}, {}, 404),                        # 접근 권한 없음 = 없는 앱
    ("owner", "owner", f"/apps/{uuid.uuid4()}/deploy/redeploy", {}, {}, 404),             # 없는 앱
    ("owner", "owner", "/apps/{app}/deploy/redeploy", {"env": {"A": "1"}}, {}, 422),       # 환경변수는 받지 않는다
    ("owner", "owner", "/apps/{app}/deploy/redeploy", {"init_dump_token": "t"}, {}, 422),  # 초기 덤프도
])
def test_http_error_codes(env, http, who, role, path, body, headers, code):
    with env.Session() as s:
        _server_only(s)
    user = User(id=OWNER) if who == "owner" else User(id=uuid.uuid4())
    if role:
        http.access[user.id] = role
    r = http.client(user).post(path.replace("{app}", str(APP_ID)), json=body, headers=headers)
    assert r.status_code == code, r.text
    with env.Session() as s:
        assert s.query(Build).count() == 1          # 아무것도 시작하지 않았다


def test_http_no_live_build_is_409(env, http):
    with env.Session() as s:
        make_app(s)
    http.access[OWNER] = "owner"
    r = http.client(User(id=OWNER)).post(f"/apps/{APP_ID}/deploy/redeploy")
    assert r.status_code == 409
    assert "배포 폼" in r.json()["detail"]


def test_http_editor_gets_the_allowed_repos_rule(env, http, monkeypatch):
    seen = {}

    async def spy(db, user, app, **kw):
        seen.update(kw)
        return redeploy.RedeployResult()

    monkeypatch.setattr(redeploy, "start_redeploy", spy)
    with env.Session() as s:
        _two_repos(s)
    editor = User(id=uuid.uuid4())
    http.access[editor.id] = "editor"
    assert http.client(editor).post(f"/apps/{APP_ID}/deploy/redeploy").status_code == 202
    assert seen["allowed_repos"] == {"https://github.com/acme/api", "https://github.com/acme/web"}

    http.access[OWNER] = "owner"
    http.client(User(id=OWNER)).post(f"/apps/{APP_ID}/deploy/redeploy")
    assert seen["allowed_repos"] is None                                    # 주인은 제한 없음


# --- 배포하기 커밋 고르기 (GET /redeploy/commits) ---

def _commit(i):
    return {"sha": f"{i:040x}", "message": f"commit {i}", "body": "", "author": "a",
            "date": f"2026-11-{20 - i:02d}T00:00:00Z", "url": f"https://github.com/acme/api/commit/{i:040x}"}


@pytest.fixture
def gh(monkeypatch):
    state = SimpleNamespace(recent=[_commit(i) for i in range(10)], single={}, calls=[])

    def recent(repo_url, branch, per_page=10, installation_id=None):
        state.calls.append(("recent", repo_url, branch, installation_id))
        return state.recent

    def one(repo_url, sha, installation_id=None):
        state.calls.append(("one", repo_url, sha, installation_id))
        return state.single.get(sha)

    monkeypatch.setattr(deploy_router.github, "fetch_recent_commits", recent)
    monkeypatch.setattr(deploy_router.github, "fetch_commit", one)
    monkeypatch.setattr(apps_service, "repo_installation_id", lambda db, app: 42)
    return state


def _picker(http, path=None):
    http.access[OWNER] = "owner"
    return http.client(User(id=OWNER)).get(path or f"/apps/{APP_ID}/deploy/redeploy/commits")


def test_picker_lists_three_commits_of_the_server_branch(env, http, gh):
    with env.Session() as s:
        _two_repos(s)
        s.query(Build).filter_by(build_id="a0000001").update({"source_sha": _commit(1)["sha"]})
        s.commit()
    r = _picker(http)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["repo"], body["branch"]) == (API_REPO, "main")
    assert [c["sha"] for c in body["commits"]] == [_commit(i)["sha"] for i in range(3)]
    assert body["current_sha"] == _commit(1)["sha"] and body["current"] is None   # 목록 안에 있으면 따로 안 준다
    assert gh.calls == [("recent", API_REPO, "main", 42)]                          # 비공개 저장소는 주인의 연결로


def test_picker_takes_an_older_current_commit_from_the_recent_list(env, http, gh):
    with env.Session() as s:
        _server_only(s)
        s.query(Build).update({"source_sha": _commit(7)["sha"]})
        s.commit()
    body = _picker(http).json()
    assert body["branch"] == "release" and len(body["commits"]) == 3
    assert body["current"]["sha"] == _commit(7)["sha"] and body["current"]["date"] == _commit(7)["date"]
    assert [c[0] for c in gh.calls] == ["recent"]


def test_picker_looks_up_a_current_commit_beyond_the_recent_list(env, http, gh):
    old = "f" * 40
    gh.single[old] = {**_commit(0), "sha": old, "message": "old one"}
    with env.Session() as s:
        _server_only(s)
        s.query(Build).update({"source_sha": old})
        s.commit()
    body = _picker(http).json()
    assert body["current"]["sha"] == old and body["current"]["message"] == "old one"
    assert gh.calls[-1] == ("one", API_REPO, old, 42)


def test_picker_keeps_the_current_sha_when_github_fails(env, http, gh):
    gh.recent = []
    with env.Session() as s:
        _server_only(s)
        s.query(Build).update({"source_sha": SHA})
        s.commit()
    body = _picker(http).json()
    assert body["commits"] == [] and body["current"] == {"sha": SHA, "message": "", "date": None, "url": None}


def test_picker_reads_the_origin_commit_for_an_old_rollback_row(env, http, gh):
    with env.Session() as s:
        app = make_app(s)
        add_server(s, "a0000001", 0, source_sha=_commit(4)["sha"], branch="stable")
        add_server(s, "a0000002", 5, status="superseded")
        add_server(s, "a0000003", 10, rollback_of="a0000001", source_sha=None)   # 기록 전에 만든 롤백 행
        base, current = redeploy.commit_base(s, app)
        assert (base.build_id, current) == ("a0000001", _commit(4)["sha"])
    assert _picker(http).json()["branch"] == "stable"


def test_picker_uses_the_static_repo_without_a_server(env, http, gh):
    with env.Session() as s:
        _static_only(s)
    body = _picker(http).json()
    assert (body["repo"], body["branch"], body["current_sha"]) == (WEB_REPO, "gh-pages", None)


@pytest.mark.parametrize("role,path,code", [
    ("owner", "/deploy/redeploy/commits", 400),        # app_id 없음
    ("viewer", "/apps/{app}/deploy/redeploy/commits", 403),
    (None, "/apps/{app}/deploy/redeploy/commits", 404),
])
def test_picker_access(env, http, gh, role, path, code):
    with env.Session() as s:
        _server_only(s)
    user = User(id=OWNER) if role == "owner" else User(id=uuid.uuid4())
    if role:
        http.access[user.id] = role
    assert http.client(user).get(path.replace("{app}", str(APP_ID))).status_code == code
    assert gh.calls == []


def test_picker_without_a_live_build_is_409(env, http, gh):
    with env.Session() as s:
        make_app(s)
    r = _picker(http)
    assert r.status_code == 409 and "배포 폼" in r.json()["detail"]
