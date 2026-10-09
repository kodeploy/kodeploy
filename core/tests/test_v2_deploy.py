"""v2(빌더) 제출 경로 — pipeline 플래그로 갈리고, v1은 그대로.

start_deploy가 v2 앱만 조건 검사 → run_v2_build로 보내는지, 조건이 안 맞으면 아무것도 바꾸기 전에
400(ValueError)인지, 빌더 요청 본문이 계약(3-1)대로인지, 준비 → 전송 순서와 실패 처리를 본다.
"""

import asyncio
import base64
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app import config
from app.auth.model import User
from app.builder import client as builder
from app.deploy.build import pipeline, v2
from app.deploy.model import Build
from app.apps.model import App
from tests.test_start_deploy import APPS, fake_apps, make_user, run_deploy, spawned, spawned_fns  # noqa: F401  (fixture)

UID = uuid.UUID("d6d8b759-8552-4d6f-9a90-00665e7ca0da")


APP_ID = uuid.UUID("11111111-2222-4333-8444-555555555555")


def v2_user(**kw):
    return make_user(user_id=UID, pipeline="v2", **kw)


@pytest.fixture
def github(monkeypatch):
    """GitHub 조회를 갈아끼운다: public 여부, detect 결과."""
    state = SimpleNamespace(public=True, detected=("dockerfile", "Dockerfile"), probes=[])
    monkeypatch.setattr(config, "BUILDER_HMAC_SECRET", "s")
    monkeypatch.setattr(v2, "_repo_is_public", lambda url: state.public)

    def detect(b):
        state.probes.append((b.repo_url, b.branch, b.runtime))
        return state.detected

    monkeypatch.setattr(v2, "_detect_build", detect)
    return state


# --- 갈림길 ---

def test_v1_user_is_unchanged(spawned, github):  # noqa: F811
    builds = run_deploy(MagicMock(), make_user())
    assert spawned_fns(spawned) == [pipeline._run_build, pipeline._teardown_static]
    assert builds[0].last_event_seq is None
    assert github.probes == []                      # v1은 GitHub 확인도 하지 않는다


def test_v2_detect_resolves_dockerfile_and_skips_v1(spawned, github):  # noqa: F811
    github.detected = ("dockerfile", "docker/Dockerfile.prod")
    builds = run_deploy(MagicMock(), v2_user(), runtime="java", port=8080, env_vars={"A": "1"}, build_mode="detect")
    b = builds[0]
    assert (b.build_mode, b.dockerfile_path, b.last_event_seq) == ("dockerfile", "docker/Dockerfile.prod", 0)
    assert spawned_fns(spawned) == [v2.run_v2_build]  # _run_build도, 정적 teardown(route reconcile)도 없다
    assert spawned[0][1] == (b.build_id, {"A": "1"})
    assert github.probes == [("https://github.com/u/repo.git", "main", "java")]


def test_v2_explicit_dockerfile_mode_skips_detect(spawned, github):  # noqa: F811
    builds = run_deploy(MagicMock(), v2_user(), build_mode="dockerfile", dockerfile_path="api/Dockerfile")
    assert builds[0].dockerfile_path == "api/Dockerfile" and github.probes == []


@pytest.mark.parametrize("kwargs,setup,reason", [
    ({"init_dump_token": "t"}, None, "초기 DB 복원"),
    ({}, lambda g: setattr(g, "public", False), "찾을 수 없거나 접근할 수 없어요"),   # 비공개인데 GitHub 연결에도 없다
    ({}, lambda g: setattr(g, "public", None), "확인하지 못했습니다"),
])
def test_v2_rejects_before_any_change(spawned, github, kwargs, setup, reason):  # noqa: F811
    if setup:
        setup(github)
    user = v2_user()
    db = MagicMock()
    with pytest.raises(ValueError, match=reason):
        run_deploy(db, user, **kwargs)
    assert spawned == [] and APPS[user.id].site_enabled is False and not db.commit.called


def test_v2_without_builder_secret(spawned, github, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "BUILDER_HMAC_SECRET", "")
    with pytest.raises(ValueError, match="빌더 연결"):
        run_deploy(MagicMock(), v2_user())


def test_stale_v2_build_is_cancelled_at_builder(monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline, "spawn_background", lambda fn, *a: calls.append((fn, a)))
    monkeypatch.setattr(pipeline, "_cleanup_build_job", lambda *a: calls.append(("job", a)))
    old_v2 = Build(build_id="aaaa1111", status="building", last_event_seq=5, user_id=UID, runtime="java")
    old_v1 = Build(build_id="bbbb2222", status="building", last_event_seq=None, user_id=UID, runtime="java")
    db = MagicMock()
    db.query.return_value.filter.return_value.filter.return_value.all.return_value = [old_v2, old_v1]
    pipeline._cancel_stale_builds(db, UID, slot="server")
    assert old_v2.status == old_v1.status == "cancelled"
    assert calls == [(v2.cancel_remote, ("aaaa1111",)), ("job", ("bbbb2222", UID.hex))]


# --- 요청 본문 ---

def make_build(**kw):
    b = Build(build_id="3f9a2c1d", repo_url="https://github.com/u/repo.git", branch="main",
              image=f"ghcr.io/yuntyu01/{UID.hex[:8]}/demo:3f9a2c1d", app_name="demo", port=8080,
              runtime="java", db_type="mysql", use_redis=False, volume_mount_path="",
              build_mode="dockerfile", dockerfile_path="docker/Dockerfile", user_id=UID, app_id=APP_ID, last_event_seq=0)
    for k, v in kw.items():
        setattr(b, k, v)
    return b


def test_payload_matches_contract(monkeypatch):
    monkeypatch.setattr(config, "BUILD_REGISTRY_CACHE_ENABLED", False)
    owner = User(id=UID)
    p = v2.build_payload(make_build(), owner, ["demo.kodeploy.com", "demo-api.kodeploy.com"])
    assert p == {
        "build_id": "3f9a2c1d",
        "actor": UID.hex[:8],
        "namespace": f"tenant-{UID.hex[:8]}",
        "slot": "server",
        "kind": "build",
        "values": {
            "name": "demo", "userId": UID.hex, "db": "mysql", "redis": False,
            "volume": {"mountPath": ""},
            "hostnames": ["demo.kodeploy.com", "demo-api.kodeploy.com"],
            "static": {"enabled": False, "hostnames": []},
        },
        "unit": {"runtime": "java", "port": 8080},
        "build": {
            "repo": "https://github.com/u/repo.git", "ref": "main", "mode": "dockerfile",
            "dockerfile_dir": "docker", "dockerfile_name": "Dockerfile",
            "image_repo": f"ghcr.io/yuntyu01/{UID.hex[:8]}/demo", "image_tag": "3f9a2c1d",
        },
    }
    assert not {"image", "runtime", "port"} & set(p["values"])  # 빌더 소유 칸 (넣으면 400)


def test_payload_cache_ref_only_when_enabled(monkeypatch):
    monkeypatch.setattr(config, "BUILD_REGISTRY_CACHE_ENABLED", True)
    p = v2.build_payload(make_build(dockerfile_path="Dockerfile"), User(id=UID), ["demo.kodeploy.com"])
    assert p["build"]["cache_ref"] == f"ghcr.io/yuntyu01/{UID.hex[:8]}/demo:buildcache"
    assert p["build"]["dockerfile_dir"] == ""


# --- run_v2_build: 준비 → 전송 ---

@pytest.fixture
def runner(monkeypatch):
    build = make_build(status="queued")
    record = SimpleNamespace(started_at=__import__("datetime").datetime(2026, 9, 28), status=None, error=None,
                             finished_at=None, total_seconds=None)
    steps = []
    db = MagicMock()
    db.query.return_value.filter_by.return_value.first.return_value = User(id=UID)
    monkeypatch.setattr(v2, "SessionLocal", lambda: db)
    dropped = []
    monkeypatch.setattr(v2, "_drop_git_auth", lambda bid: dropped.append(bid))     # 실제 K8s 호출 대신 기록
    monkeypatch.setattr(v2.apps_service, "get_app", lambda d, app_id: App(
        id=app_id, owner_id=UID, name="demo", namespace=f"tenant-{UID.hex[:8]}", site_enabled=False, pipeline="v2"))
    monkeypatch.setattr(v2.crud, "get_build", lambda d, bid: build)
    monkeypatch.setattr(v2, "_new_record", lambda d, b: record)
    for name in ("_ensure_tenant_ns", "ensure_dep_secrets", "_apply_storage", "_apply_volume"):
        monkeypatch.setattr(v2, name, lambda b, _n=name: steps.append(_n))
    monkeypatch.setattr(v2.env_module, "set_env", lambda ns, app, env: steps.append(("env", ns, app, env)))
    monkeypatch.setattr(v2, "_slot_hostnames", lambda owner: (["demo.kodeploy.com"], []))
    sent = []

    async def submit(payload):
        steps.append("submit")
        sent.append((payload, build.status))

    monkeypatch.setattr(v2.builder, "submit", submit)
    return SimpleNamespace(build=build, record=record, steps=steps, sent=sent, db=db, monkeypatch=monkeypatch, dropped=dropped)


def test_run_provisions_then_submits(runner):
    asyncio.run(v2.run_v2_build("3f9a2c1d", {"A": "1"}))
    assert runner.steps == ["_ensure_tenant_ns", ("env", f"tenant-{UID.hex[:8]}", "demo", {"A": "1"}),
                            "ensure_dep_secrets", "_apply_storage", "_apply_volume", "submit"]
    payload, status_at_send = runner.sent[0]
    assert status_at_send == "building" and payload["values"]["hostnames"] == ["demo.kodeploy.com"]


def test_run_marks_failed_when_builder_refuses(runner):
    async def refuse(payload):
        raise builder.BuilderError("build.ref must not start with - or /")

    runner.monkeypatch.setattr(v2.builder, "submit", refuse)
    asyncio.run(v2.run_v2_build("3f9a2c1d"))
    assert runner.build.status == "failed"
    assert runner.build.error == "빌더가 받지 않았습니다: build.ref must not start with - or /"
    assert runner.record.status == "failed" and runner.record.total_seconds is not None


def test_run_stops_if_replaced_while_provisioning(runner):
    runner.db.refresh.side_effect = lambda b: setattr(b, "status", "cancelled")
    asyncio.run(v2.run_v2_build("3f9a2c1d"))
    assert "submit" not in runner.steps and runner.build.status == "cancelled"
    assert runner.record.status == "cancelled" and runner.record.finished_at is not None


def test_run_refusal_after_replacement_stays_cancelled(runner):
    async def refuse(payload):
        runner.build.status = "cancelled"          # 전송 도중 재배포가 대체
        raise builder.BuilderError("conflict")

    runner.monkeypatch.setattr(v2.builder, "submit", refuse)
    asyncio.run(v2.run_v2_build("3f9a2c1d"))
    assert runner.build.status == "cancelled" and runner.build.error is None
    assert runner.record.status == "cancelled"


def test_run_orchestration_error(runner):
    runner.monkeypatch.setattr(v2, "_ensure_tenant_ns", lambda b: (_ for _ in ()).throw(RuntimeError("api down")))
    asyncio.run(v2.run_v2_build("3f9a2c1d"))
    assert runner.build.status == "failed" and "api down" in runner.build.error


# --- 보조 함수 ---

def test_repo_is_public(monkeypatch):
    import io
    import json
    import urllib.error

    from app.deploy.build import github as gh

    answers = {}

    def urlopen(req, timeout):
        a = answers["next"]
        if isinstance(a, Exception):
            raise a
        return io.BytesIO(json.dumps(a).encode())

    monkeypatch.setattr(gh.urllib.request, "urlopen", urlopen)
    answers["next"] = {"private": False}
    assert gh._repo_is_public("https://github.com/u/repo.git") is True
    answers["next"] = {"private": True}
    assert gh._repo_is_public("https://github.com/u/repo.git") is False
    answers["next"] = urllib.error.HTTPError("u", 404, "nf", {}, None)
    assert gh._repo_is_public("https://github.com/u/repo.git") is False
    answers["next"] = urllib.error.HTTPError("u", 403, "rate limited", {}, None)
    assert gh._repo_is_public("https://github.com/u/repo.git") is None


def test_ensure_dep_secrets_creates_only_secrets(monkeypatch):
    from app.deploy.stack import resources

    created = []
    core = MagicMock()
    core.create_namespaced_secret.side_effect = lambda namespace, body: created.append((namespace, body["metadata"]["name"]))
    monkeypatch.setattr(resources.k8s, "core_v1", lambda: core)
    apps = MagicMock()
    monkeypatch.setattr(resources.k8s, "apps_v1", lambda: apps)
    resources.ensure_dep_secrets(make_build(db_type="mysql", use_redis=True))
    ns = f"tenant-{UID.hex[:8]}"
    assert created == [(ns, "mysql-secret"), (ns, "redis-secret")]
    assert not core.create_namespaced_service.called and not apps.method_calls   # 워크로드는 차트가 그린다


# --- 앱 삭제: 빌더에 맡기고 deleted를 기다린다 ---

def _delete_flow(monkeypatch, events):
    """request_delete를 돌린다. submit이 받아지면 events를 (build_id마다) 콜백처럼 흘려보낸다."""
    sent = []

    async def submit(payload):
        sent.append(payload)
        for ev in events:
            v2.on_delete_event(payload["build_id"], ev)

    monkeypatch.setattr(v2.builder, "submit", submit)
    user = v2_user()
    return user, APPS[user.id], sent


def test_delete_payload_is_namespace_only():
    user = v2_user()
    p = v2.delete_payload(user, APPS[user.id], "aabbccdd")
    assert p == {"build_id": "aabbccdd", "actor": "d6d8b759", "namespace": "tenant-d6d8b759", "kind": "delete"}


def test_delete_payload_uses_app_namespace():
    user = v2_user()
    app = APPS[user.id]
    app.namespace = "app-9abcdef0"
    assert v2.delete_payload(user, app, "aabbccdd")["namespace"] == "app-9abcdef0"


def test_request_delete_returns_when_builder_reports_deleted(monkeypatch, fake_apps):  # noqa: F811
    user, app, sent = _delete_flow(monkeypatch, [{"seq": 1, "type": "deleted"}])
    asyncio.run(v2.request_delete(user, app))
    assert len(sent) == 1 and sent[0]["kind"] == "delete"
    assert v2._deleting == {}


def test_request_delete_fails_when_builder_reports_failed(monkeypatch, fake_apps):  # noqa: F811
    user, app, _ = _delete_flow(monkeypatch, [{"seq": 1, "type": "failed", "stage": "timeout", "reason": "still exists"}])
    with pytest.raises(ValueError, match="timeout: still exists"):
        asyncio.run(v2.request_delete(user, app))
    assert v2._deleting == {}


def test_request_delete_times_out(monkeypatch, fake_apps):  # noqa: F811
    user, app, _ = _delete_flow(monkeypatch, [])
    monkeypatch.setattr(v2, "DELETE_TIMEOUT", 0.01)
    with pytest.raises(ValueError, match="다시 시도"):
        asyncio.run(v2.request_delete(user, app))
    assert v2._deleting == {}


def test_request_delete_rejected_by_builder(monkeypatch, fake_apps):  # noqa: F811
    user, app, _ = _delete_flow(monkeypatch, [])

    async def refuse(payload):
        raise builder.BuilderError("signature")

    monkeypatch.setattr(v2.builder, "submit", refuse)
    with pytest.raises(ValueError, match="받지 않았습니다"):
        asyncio.run(v2.request_delete(user, app))


def test_other_build_events_are_not_delete_events():
    assert v2.on_delete_event("3f9a2c1d", {"seq": 1, "type": "deleted"}) is False


# --- 롤백 (이력의 digest로 되돌린다, v2만) ---

DIGEST_IMAGE = f"ghcr.io/yuntyu01/{UID.hex[:8]}/demo:3f9a2c1d@sha256:" + "a" * 64


def rb_build(**kw):
    fields = dict(build_id="aa11bb22", status="running", image=DIGEST_IMAGE)
    fields.update(kw)                                       # 기본값을 테스트가 바꾼 값으로 덮는다
    return make_build(**fields)


def v2_app():
    return App(id=APP_ID, owner_id=UID, name="demo", namespace=f"tenant-{UID.hex[:8]}", pipeline="v2")


@pytest.mark.parametrize("change,ok", [
    ({}, True),
    ({"status": "failed"}, False),                          # 실패한 배포로는 못 돌아간다
    ({"status": "cancelled"}, False),
    ({"kind": "env_change"}, False),
    ({"runtime": "static"}, False),                         # 정적 슬롯은 v2 대상이 아니다
    ({"image": "ghcr.io/u/h/demo:3f9a2c1d"}, False),        # digest가 없는 이미지 (v1 빌드)
    ({"last_event_seq": None}, False),                      # v1 빌드
])
def test_which_builds_can_be_rolled_back_to(change, ok):
    assert v2.is_rollbackable(rb_build(**change)) is ok


def test_rollback_check_errors():
    app = v2_app()
    v2.check_rollback_target(app, rb_build())               # 통과
    v1 = v2_app()
    v1.pipeline = "v1"
    with pytest.raises(ValueError, match="v2"):
        v2.check_rollback_target(v1, rb_build())
    with pytest.raises(LookupError):
        v2.check_rollback_target(app, None)
    with pytest.raises(LookupError):
        v2.check_rollback_target(app, rb_build(app_id=uuid.uuid4()))   # 다른 앱의 배포는 없는 것처럼
    with pytest.raises(ValueError, match="되돌릴 수 없"):
        v2.check_rollback_target(app, rb_build(status="failed"))


def test_rollback_payload_is_a_set_image_without_values():
    p = v2.rollback_payload(Build(build_id="cc33dd44", image=DIGEST_IMAGE, runtime="java", port=8080, user_id=UID,
                                  namespace="app-9abcdef0"), User(id=UID))
    assert p == {
        "build_id": "cc33dd44", "actor": UID.hex[:8], "namespace": "app-9abcdef0", "slot": "server",
        "kind": "set-image", "unit": {"runtime": "java", "port": 8080}, "image": DIGEST_IMAGE,
    }
    assert "values" not in p and "build" not in p                # 지금 설정(DB·도메인 등)은 그대로 둔다


def test_start_rollback_records_a_new_deploy_and_submits(monkeypatch):
    import asyncio

    app = v2_app()
    target = rb_build(branch="release", port=9000)
    calls, created = [], []
    monkeypatch.setattr(pipeline, "spawn_background", lambda fn, *a: calls.append((fn, a)))
    monkeypatch.setattr(pipeline, "_cancel_stale_builds", lambda db, app_id, slot: calls.append(("cancel", slot)))
    monkeypatch.setattr(pipeline.crud, "get_build", lambda db, bid, app_id=None: target)
    monkeypatch.setattr(pipeline.crud, "create_build", lambda db, b: created.append(b) or b)

    new = asyncio.run(pipeline.start_rollback(MagicMock(), User(id=UID), app, "aa11bb22"))

    assert created == [new]
    assert (new.rollback_of, new.image, new.port, new.branch) == ("aa11bb22", DIGEST_IMAGE, 9000, "release")
    assert (new.status, new.last_event_seq, new.app_id, new.namespace) == ("deploying", 0, APP_ID, app.namespace)
    assert new.build_id != "aa11bb22" and new.kind == "build"
    assert calls == [("cancel", "server"), (v2.run_v2_rollback, (new.build_id,))]    # 진행 중이던 서버 배포는 대체된다


def test_start_rollback_rejects_without_touching_anything(monkeypatch):
    import asyncio

    calls = []
    monkeypatch.setattr(pipeline, "spawn_background", lambda fn, *a: calls.append(fn))
    monkeypatch.setattr(pipeline, "_cancel_stale_builds", lambda *a: calls.append("cancel"))
    monkeypatch.setattr(pipeline.crud, "get_build", lambda db, bid, app_id=None: rb_build(status="failed"))
    with pytest.raises(ValueError):
        asyncio.run(pipeline.start_rollback(MagicMock(), User(id=UID), v2_app(), "aa11bb22"))
    assert calls == []                                       # 거절이면 취소도 제출도 없다


def test_run_rollback_submits_set_image(runner):
    runner.build.rollback_of = "aa11bb22"
    runner.build.image = DIGEST_IMAGE
    asyncio.run(v2.run_v2_rollback("3f9a2c1d"))
    payload, status_at_send = runner.sent[0]
    assert payload["kind"] == "set-image" and payload["image"] == DIGEST_IMAGE
    assert status_at_send == "deploying"
    assert "_ensure_tenant_ns" not in runner.steps and "_apply_volume" not in runner.steps   # 준비 단계는 건너뛴다


def test_run_rollback_marks_failed_when_builder_refuses(runner):
    async def refuse(payload):
        raise builder.BuilderError("image must be <repo>:<tag>@sha256:<64hex>")

    runner.monkeypatch.setattr(v2.builder, "submit", refuse)
    asyncio.run(v2.run_v2_rollback("3f9a2c1d"))
    assert runner.build.status == "failed" and "빌더가 받지 않았습니다" in runner.build.error


# --- 설정 변경 (환경변수·도메인) ---

def test_config_payload_has_values_only():
    p = v2.config_payload("aabbccdd", User(id=UID), "tenant-d6d8b759", {"envRevision": 5})
    assert p == {
        "build_id": "aabbccdd", "actor": UID.hex[:8], "namespace": "tenant-d6d8b759",
        "slot": "server", "kind": "config", "values": {"envRevision": 5},
    }
    assert not {"unit", "build", "image"} & set(p)               # 값만 바꾼다 — 이미지·빌드는 그대로


def test_ensure_idle():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    v2.ensure_idle(db, v2_app())                                  # 진행 중인 서버 배포가 없다
    db.query.return_value.filter.return_value.first.return_value = Build(build_id="x", status="building")
    with pytest.raises(ValueError, match="배포가 진행 중"):
        v2.ensure_idle(db, v2_app())


class _SpyDb:
    """query().filter(...).first()에 쓰인 조건을 모은다 — 실제 DB 없이 어떤 빌드를 기다리는지 본다."""

    def __init__(self, busy=None):
        self.conds, self.busy = [], busy

    def query(self, *_):
        return self

    def filter(self, *conds):
        self.conds += conds
        return self

    def first(self):
        return self.busy


def test_ensure_idle_includes_static_builds_only_when_asked():
    # 환경변수는 서버 배포만 기다리고, 도메인은 정적 사이트 배포까지 기다린다 (정적 슬롯 호스트도 바뀌니까)
    def waits_on_static(**kw):
        spy = _SpyDb()
        v2.ensure_idle(spy, v2_app(), **kw)
        return not any("runtime" in str(c) for c in spy.conds)

    assert waits_on_static() is False
    assert waits_on_static(include_static=True) is True
    with pytest.raises(ValueError, match="배포가 진행 중"):
        v2.ensure_idle(_SpyDb(busy=Build(build_id="s", status="building", runtime="static")), v2_app(), include_static=True)


def test_env_revision_is_time_based_and_bumps_each_time(monkeypatch):
    sent = []

    async def submit(payload):
        sent.append(payload)

    monkeypatch.setattr(v2.builder, "submit", submit)
    ticks = iter([1_800_000_000, 1_800_000_001])
    monkeypatch.setattr(v2.time, "time", lambda: next(ticks))
    event = Build(build_id="ee11ff22", namespace="app-9abcdef0", user_id=UID)
    asyncio.run(v2.submit_env_revision(event, User(id=UID)))
    asyncio.run(v2.submit_env_revision(event, User(id=UID)))
    assert [p["values"]["envRevision"] for p in sent] == [1_800_000_000, 1_800_000_001]   # 매번 달라서 Pod이 다시 뜬다
    assert sent[0]["namespace"] == "app-9abcdef0" and sent[0]["build_id"] == "ee11ff22"


def test_apply_hostnames_sends_the_slot_rule_list(monkeypatch):
    sent = []

    async def submit(payload):
        sent.append(payload)

    monkeypatch.setattr(v2.builder, "submit", submit)
    app = v2_app()
    app.custom_domain = "www.example.com"
    app.site_enabled = False
    asyncio.run(v2.apply_hostnames(app, User(id=UID)))
    p = sent[0]
    assert p["kind"] == "config" and p["namespace"] == app.namespace
    assert p["values"] == {
        "hostnames": ["demo.kodeploy.com", "demo-api.kodeploy.com", "www.example.com"],
        "static": {"hostnames": []},                  # 정적 사이트가 꺼져 있으면 비운다 (켬/끔은 건드리지 않는다)
    }


def test_apply_hostnames_moves_the_custom_domain_to_the_static_slot(monkeypatch):
    sent = []

    async def submit(payload):
        sent.append(payload)

    monkeypatch.setattr(v2.builder, "submit", submit)
    app = v2_app()
    app.custom_domain = "www.example.com"
    app.site_enabled = True
    asyncio.run(v2.apply_hostnames(app, User(id=UID)))
    assert sent[0]["values"] == {
        "hostnames": ["demo-api.kodeploy.com"],
        "static": {"hostnames": ["demo.kodeploy.com", "www.example.com"]},
    }
    assert "enabled" not in sent[0]["values"]["static"]


# --- 비공개 저장소 ---

def test_private_repo_is_accepted_when_the_owners_installation_has_it(spawned, github, monkeypatch):  # noqa: F811
    github.public = False
    monkeypatch.setattr(v2.github_app, "list_installation_repos", lambda iid: [{"full_name": "U/Repo"}] if iid == 111 else [])
    monkeypatch.setattr(pipeline.apps_service, "repo_installation_id", lambda db, app: 111)
    builds = run_deploy(MagicMock(), v2_user(), runtime="java", port=8080, build_mode="dockerfile")
    assert len(builds) == 1 and spawned_fns(spawned) == [v2.run_v2_build]


def test_private_repo_is_rejected_when_the_installation_lacks_it(spawned, github, monkeypatch):  # noqa: F811
    github.public = False
    monkeypatch.setattr(v2.github_app, "list_installation_repos", lambda iid: [{"full_name": "u/other"}])
    monkeypatch.setattr(pipeline.apps_service, "repo_installation_id", lambda db, app: 111)
    with pytest.raises(ValueError, match="접근할 수 없어요"):
        run_deploy(MagicMock(), v2_user(), runtime="java", port=8080, build_mode="dockerfile")
    assert spawned == []


def test_private_repo_is_rejected_without_any_github_connection(spawned, github, monkeypatch):  # noqa: F811
    github.public = False
    monkeypatch.setattr(pipeline.apps_service, "repo_installation_id", lambda db, app: None)
    with pytest.raises(ValueError, match="접근할 수 없어요"):
        run_deploy(MagicMock(), v2_user(), runtime="java", port=8080, build_mode="dockerfile")


def test_installation_has_repo_matches_in_any_spelling(monkeypatch):
    monkeypatch.setattr(v2.github_app, "list_installation_repos", lambda iid: [{"full_name": "Me/Shop"}])
    assert v2._installation_has_repo(1, "https://github.com/me/shop.git")
    assert not v2._installation_has_repo(1, "https://github.com/me/shop-two")
    assert not v2._installation_has_repo(None, "https://github.com/me/shop")


def test_payload_names_the_git_auth_secret_only_for_private_builds(monkeypatch):
    monkeypatch.setattr(config, "BUILD_REGISTRY_CACHE_ENABLED", False)
    plain = v2.build_payload(make_build(), User(id=UID), ["demo.kodeploy.com"])
    assert "git_auth_secret" not in plain["build"]
    private = v2.build_payload(make_build(), User(id=UID), ["demo.kodeploy.com"], "git-auth-3f9a2c1d")
    assert private["build"]["git_auth_secret"] == "git-auth-3f9a2c1d"
    assert "GIT_AUTH_TOKEN" not in str(private)                 # 토큰 값 자체는 요청에 안 싣는다 (Secret 이름만)


def test_run_provisions_the_token_secret_and_sends_its_name(runner):
    import app.deploy.build.pipeline as pl

    runner.monkeypatch.setattr(pl, "_provision_git_auth", lambda b: f"git-auth-{b.build_id}")
    asyncio.run(v2.run_v2_build("3f9a2c1d"))
    assert runner.sent[0][0]["build"]["git_auth_secret"] == "git-auth-3f9a2c1d"
    assert runner.dropped == []                                  # 성공 제출이면 빌드가 끝날 때(콜백)까지 둔다


def test_run_drops_the_token_secret_when_submit_fails(runner):
    import app.deploy.build.pipeline as pl

    runner.monkeypatch.setattr(pl, "_provision_git_auth", lambda b: "git-auth-3f9a2c1d")

    async def refuse(payload):
        raise builder.BuilderError("down")

    runner.monkeypatch.setattr(v2.builder, "submit", refuse)
    asyncio.run(v2.run_v2_build("3f9a2c1d"))
    assert runner.dropped == ["3f9a2c1d"]                        # 제출이 실패하면 토큰이 남지 않게 바로 지운다


# --- 자동 빌드(nixpacks) ---

def test_v2_accepts_auto_mode_with_a_project_path(spawned, github):  # noqa: F811
    builds = run_deploy(MagicMock(), v2_user(), runtime="java", port=8080, build_mode="auto", project_path="/backend/")
    b = builds[0]
    assert (b.build_mode, b.project_path, b.last_event_seq) == ("auto", "backend", 0)
    assert spawned_fns(spawned) == [v2.run_v2_build]
    assert github.probes == []                                  # 방식을 직접 골랐으면 GitHub를 조회하지 않는다


def test_v2_detect_falls_back_to_nixpacks_when_there_is_no_dockerfile(spawned, github):  # noqa: F811
    github.detected = ("auto", "services/api")                  # Dockerfile이 없고 services/api에서 프로젝트가 보인다
    builds = run_deploy(MagicMock(), v2_user(), runtime="java", port=8080, build_mode="detect")
    b = builds[0]
    assert (b.build_mode, b.project_path) == ("auto", "services/api")


def test_v2_rejects_a_bad_project_path_before_any_change(spawned, github):  # noqa: F811
    with pytest.raises(ValueError):
        run_deploy(MagicMock(), v2_user(), runtime="java", port=8080, build_mode="auto", project_path="../etc")
    assert spawned == []


def test_auto_payload_has_no_dockerfile_fields(monkeypatch):
    monkeypatch.setattr(config, "BUILD_REGISTRY_CACHE_ENABLED", False)
    p = v2.build_payload(make_build(build_mode="auto", project_path="backend", dockerfile_path="Dockerfile"),
                         User(id=UID), ["demo.kodeploy.com"])
    spec = p["build"]
    assert (spec["mode"], spec["project_path"]) == ("auto", "backend")
    assert not {"dockerfile_dir", "dockerfile_name"} & set(spec)     # 방식마다 쓰는 칸이 다르다 (빌더가 엉뚱한 칸을 거절한다)


def test_auto_payload_without_project_path_lets_the_builder_detect_it(monkeypatch):
    monkeypatch.setattr(config, "BUILD_REGISTRY_CACHE_ENABLED", False)
    spec = v2.build_payload(make_build(build_mode="auto", project_path=""), User(id=UID), ["demo.kodeploy.com"])["build"]
    assert spec["mode"] == "auto" and "project_path" not in spec


def test_dockerfile_payload_is_unchanged(monkeypatch):
    monkeypatch.setattr(config, "BUILD_REGISTRY_CACHE_ENABLED", False)
    spec = v2.build_payload(make_build(), User(id=UID), ["demo.kodeploy.com"])["build"]
    assert spec["mode"] == "dockerfile" and spec["dockerfile_dir"] == "docker" and "project_path" not in spec


# --- 정적 사이트 ---

def static_build(**kw):
    b = make_build(runtime="static", app_name="demo-static", port=8080, db_type="none", use_redis=False,
                   volume_mount_path="", build_mode="static", dockerfile_path="", project_path="",
                   build_cmd="npm ci && npm run build", output_dir="dist",
                   image=f"ghcr.io/yuntyu01/{UID.hex[:8]}/demo-static:5a6b7c8d", build_id="5a6b7c8d")
    for k, v in kw.items():
        setattr(b, k, v)
    return b


def test_static_payload_matches_contract(monkeypatch):
    monkeypatch.setattr(config, "BUILD_REGISTRY_CACHE_ENABLED", True)
    b = static_build(project_path="site", build_env='{"VITE_API": "https://api.example.com"}')
    p = v2.build_payload(b, User(id=UID), ["demo-api.kodeploy.com"], static_hosts=["demo.kodeploy.com"])
    assert p["slot"] == "static" and p["kind"] == "build" and "unit" not in p
    assert p["values"] == {
        "name": "demo", "userId": UID.hex,                       # 정적 행 이름(demo-static)이 아니라 앱 이름
        "hostnames": ["demo-api.kodeploy.com"],
        "static": {"enabled": True, "hostnames": ["demo.kodeploy.com"]},
    }
    spec = p["build"]
    assert spec["mode"] == "static" and spec["project_path"] == "site"
    assert spec["image_repo"] == f"ghcr.io/yuntyu01/{UID.hex[:8]}/demo-static" and spec["image_tag"] == "5a6b7c8d"
    assert spec["cache_ref"] == spec["image_repo"] + ":buildcache"
    assert not {"dockerfile_dir", "dockerfile_name"} & set(spec)  # 정적은 repo의 Dockerfile을 쓰지 않는다
    # Dockerfile은 v1과 같은 템플릿으로 core가 만든다 — 빌더는 받은 그대로 쓴다
    text = base64.b64decode(spec["dockerfile_b64"]).decode("utf-8")
    assert text == v2.static_dockerfile_text(b)
    assert "RUN npm ci && npm run build" in text and "/app/dist/" in text and 'ENV VITE_API="https://api.example.com"' in text


def test_static_payload_never_carries_server_settings(monkeypatch):
    # 정적 행은 서버 설정을 모른다 (db none · 볼륨 없음). 보내면 배포된 서버의 DB·볼륨 설정을 지워 버린다.
    monkeypatch.setattr(config, "BUILD_REGISTRY_CACHE_ENABLED", False)
    p = v2.build_payload(static_build(), User(id=UID), ["demo-api.kodeploy.com"], static_hosts=["demo.kodeploy.com"])
    assert not {"db", "redis", "volume", "image", "runtime", "port"} & set(p["values"])
    assert "project_path" not in p["build"] and "cache_ref" not in p["build"]


def test_static_without_build_command_serves_the_repo_as_is():
    text = v2.static_dockerfile_text(static_build(build_cmd="", output_dir=""))
    assert "FROM node" not in text and "COPY . /usr/share/nginx/html/" in text


def test_server_payload_carries_the_static_slot_declaration(monkeypatch):
    monkeypatch.setattr(config, "BUILD_REGISTRY_CACHE_ENABLED", False)
    p = v2.build_payload(make_build(), User(id=UID), ["demo-api.kodeploy.com"],
                         static_enabled=True, static_hosts=["demo.kodeploy.com"])
    assert p["values"]["static"] == {"enabled": True, "hostnames": ["demo.kodeploy.com"]}   # {앱}이 정적으로 넘어간다
    assert p["slot"] == "server" and p["unit"] == {"runtime": "java", "port": 8080}


def test_v2_static_with_server_builds_both_slots_on_the_builder(spawned, github):  # noqa: F811
    builds = run_deploy(MagicMock(), v2_user(), runtime="java", port=8080, env_vars={"A": "1"}, use_static=True,
                        build_cmd="npm run build")
    server, site = builds
    assert (server.runtime, site.runtime) == ("java", "static")
    assert (server.last_event_seq, site.last_event_seq) == (0, 0)             # 둘 다 콜백으로 상태를 받는다
    assert (site.build_mode, site.app_name, site.port) == ("static", "foo-static", 8080)
    assert site.image.rsplit("/", 1)[1].startswith("foo-static:")
    assert spawned_fns(spawned) == [v2.run_v2_build, v2.run_v2_build]          # v1 빌드·정적 teardown 없음
    assert spawned[0][1] == (server.build_id, {"A": "1"})
    assert spawned[1][1] == (site.build_id,)                                    # 서버 환경변수는 정적 사이트와 상관없다
    assert APPS[server.user_id].site_enabled is True


def test_v2_static_only_app_has_no_server_build_and_nothing_to_tear_down(spawned, github, monkeypatch):  # noqa: F811
    monkeypatch.setattr(v2, "has_server_deployed", lambda db, app: False)
    builds = run_deploy(MagicMock(), v2_user(), runtime="none", use_static=True)
    assert [b.runtime for b in builds] == ["static"]
    assert spawned_fns(spawned) == [v2.run_v2_build]                           # _teardown_server·_teardown_static 없음
    assert github.probes == []                                                  # 서버 빌드가 없으니 Dockerfile 감지도 없다


def test_v2_dropping_the_static_site_leaves_it_to_the_server_build(spawned, github):  # noqa: F811
    # 정적을 끄는 배포는 서버 빌드의 values가 static.enabled=false를 싣는다 — 따로 보낼 요청이 없다
    user = v2_user()
    APPS[user.id].site_enabled = True
    builds = run_deploy(MagicMock(), user, runtime="java", port=8080)
    assert [b.runtime for b in builds] == ["java"] and APPS[user.id].site_enabled is False
    assert spawned_fns(spawned) == [v2.run_v2_build]


def test_v2_cannot_drop_a_deployed_server(spawned, github, monkeypatch):  # noqa: F811
    monkeypatch.setattr(v2, "has_server_deployed", lambda db, app: True)
    user = v2_user()
    db = MagicMock()
    with pytest.raises(ValueError, match="서버를 쓰던 앱에서 서버를 빼는 것"):
        run_deploy(db, user, runtime="none", use_static=True)
    assert spawned == [] and APPS[user.id].site_enabled is False and not db.commit.called


def test_v2_static_repo_is_checked_too(spawned, github, monkeypatch):  # noqa: F811
    # 정적 사이트를 다른 저장소에서 받으면 그 저장소도 접근할 수 있어야 한다 (비공개면 GitHub 연결에 있어야)
    monkeypatch.setattr(v2, "_repo_is_public", lambda url: "site" not in url)
    user = v2_user()
    with pytest.raises(ValueError, match="찾을 수 없거나 접근할 수 없어요"):
        run_deploy(MagicMock(), user, runtime="java", use_static=True, static_repo_url="https://github.com/u/site")
    assert spawned == []


def test_v2_static_only_checks_just_the_static_repo(spawned, github, monkeypatch):  # noqa: F811
    seen = []
    monkeypatch.setattr(v2, "_repo_is_public", lambda url: seen.append(url) or True)
    monkeypatch.setattr(v2, "has_server_deployed", lambda db, app: False)
    run_deploy(MagicMock(), v2_user(), runtime="none", use_static=True, static_repo_url="https://github.com/u/site")
    assert seen == ["https://github.com/u/site"]


def test_has_server_deployed_looks_for_a_successful_v2_server_build():
    db = MagicMock()
    q = db.query.return_value.filter.return_value
    q.first.return_value = ("3f9a2c1d",)
    assert v2.has_server_deployed(db, v2_app()) is True
    q.first.return_value = None
    assert v2.has_server_deployed(db, v2_app()) is False


@pytest.fixture
def static_runner(runner):
    site = static_build(status="queued")
    runner.monkeypatch.setattr(v2.crud, "get_build", lambda d, bid: site)
    runner.monkeypatch.setattr(v2, "_slot_hostnames", lambda a: (["demo-api.kodeploy.com"], ["demo.kodeploy.com"]))
    runner.monkeypatch.setattr(v2.apps_service, "get_app", lambda d, app_id: App(
        id=app_id, owner_id=UID, name="demo", namespace=f"tenant-{UID.hex[:8]}", site_enabled=True, pipeline="v2"))
    runner.site = site
    return runner


def test_run_static_prepares_only_the_namespace_and_sends_the_static_slot(static_runner):
    asyncio.run(v2.run_v2_build("5a6b7c8d", {"IGNORED": "1"}))
    assert static_runner.steps == ["_ensure_tenant_ns", "submit"]            # 환경변수·DB·저장소·볼륨은 서버 슬롯의 몫
    payload = static_runner.sent[0][0]
    assert static_runner.site.status == "building" and payload["slot"] == "static"   # 전송 전에 바꿔 둔다
    assert payload["values"]["static"] == {"enabled": True, "hostnames": ["demo.kodeploy.com"]}
    assert payload["values"]["hostnames"] == ["demo-api.kodeploy.com"]
    assert static_runner.site.dockerfile_content == v2.static_dockerfile_text(static_runner.site)   # 빌드 전에 보존


def test_run_server_build_declares_the_static_slot_from_the_app(runner):
    runner.monkeypatch.setattr(v2, "_slot_hostnames", lambda a: (["demo-api.kodeploy.com"], ["demo.kodeploy.com"]))
    runner.monkeypatch.setattr(v2.apps_service, "get_app", lambda d, app_id: App(
        id=app_id, owner_id=UID, name="demo", namespace=f"tenant-{UID.hex[:8]}", site_enabled=True, pipeline="v2"))
    asyncio.run(v2.run_v2_build("3f9a2c1d"))
    assert runner.sent[0][0]["values"]["static"] == {"enabled": True, "hostnames": ["demo.kodeploy.com"]}
