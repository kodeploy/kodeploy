"""v2(빌더) 제출 경로 — pipeline 플래그로 갈리고, v1은 그대로.

start_deploy가 v2 앱만 조건 검사 → run_v2_build로 보내는지, 조건이 안 맞으면 아무것도 바꾸기 전에
400(ValueError)인지, 빌더 요청 본문이 계약(3-1)대로인지, 준비 → 전송 순서와 실패 처리를 본다.
"""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app import config
from app.auth.model import User
from app.builder import client as builder
from app.deploy.build import pipeline, v2
from app.deploy.model import Build
from tests.test_start_deploy import make_user, run_deploy, spawned, spawned_fns  # noqa: F401  (fixture)

UID = uuid.UUID("d6d8b759-8552-4d6f-9a90-00665e7ca0da")


def v2_user(**kw):
    u = make_user(user_id=UID, **kw)
    u.pipeline = "v2"
    return u


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
    ({"runtime": "none", "use_static": True}, None, "서버 없는 앱"),
    ({"use_static": True}, None, "정적 사이트"),
    ({"init_dump_token": "t"}, None, "초기 DB 복원"),
    ({"build_mode": "auto"}, None, "Dockerfile 빌드만"),
    ({"build_mode": "detect"}, lambda g: setattr(g, "detected", ("auto", "")), "Dockerfile을 찾지 못했습니다"),
    ({}, lambda g: setattr(g, "public", False), "public 저장소만"),
    ({}, lambda g: setattr(g, "public", None), "확인하지 못했습니다"),
])
def test_v2_rejects_before_any_change(spawned, github, kwargs, setup, reason):  # noqa: F811
    if setup:
        setup(github)
    user = v2_user(app_name=None)
    db = MagicMock()
    with pytest.raises(ValueError, match=reason):
        run_deploy(db, user, **kwargs)
    assert spawned == [] and user.app_name is None and not db.commit.called


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
              build_mode="dockerfile", dockerfile_path="docker/Dockerfile", user_id=UID, last_event_seq=0)
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
    db.query.return_value.filter_by.return_value.first.return_value = User(id=UID, app_name="demo", site_enabled=False)
    monkeypatch.setattr(v2, "SessionLocal", lambda: db)
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
    return SimpleNamespace(build=build, record=record, steps=steps, sent=sent, db=db, monkeypatch=monkeypatch)


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

def _delete_flow(monkeypatch, events, *, app_name="demo"):
    """request_delete를 돌린다. submit이 받아지면 events를 (build_id마다) 콜백처럼 흘려보낸다."""
    sent = []

    async def submit(payload):
        sent.append(payload)
        for ev in events:
            v2.on_delete_event(payload["build_id"], ev)

    monkeypatch.setattr(v2.builder, "submit", submit)
    user = v2_user()
    user.app_name = app_name
    return user, sent


def test_delete_payload_is_namespace_only():
    p = v2.delete_payload(v2_user(), "aabbccdd")
    assert p == {"build_id": "aabbccdd", "actor": "d6d8b759", "namespace": "tenant-d6d8b759", "kind": "delete"}


def test_request_delete_returns_when_builder_reports_deleted(monkeypatch):
    user, sent = _delete_flow(monkeypatch, [{"seq": 1, "type": "deleted"}])
    asyncio.run(v2.request_delete(user))
    assert len(sent) == 1 and sent[0]["kind"] == "delete"
    assert v2._deleting == {}


def test_request_delete_fails_when_builder_reports_failed(monkeypatch):
    user, _ = _delete_flow(monkeypatch, [{"seq": 1, "type": "failed", "stage": "timeout", "reason": "still exists"}])
    with pytest.raises(ValueError, match="timeout: still exists"):
        asyncio.run(v2.request_delete(user))
    assert v2._deleting == {}


def test_request_delete_times_out(monkeypatch):
    user, _ = _delete_flow(monkeypatch, [])
    monkeypatch.setattr(v2, "DELETE_TIMEOUT", 0.01)
    with pytest.raises(ValueError, match="다시 시도"):
        asyncio.run(v2.request_delete(user))
    assert v2._deleting == {}


def test_request_delete_rejected_by_builder(monkeypatch):
    user, _ = _delete_flow(monkeypatch, [])

    async def refuse(payload):
        raise builder.BuilderError("signature")

    monkeypatch.setattr(v2.builder, "submit", refuse)
    with pytest.raises(ValueError, match="받지 않았습니다"):
        asyncio.run(v2.request_delete(user))


def test_request_delete_without_app(monkeypatch):
    user, sent = _delete_flow(monkeypatch, [], app_name=None)
    with pytest.raises(ValueError, match="삭제할 앱이 없습니다"):
        asyncio.run(v2.request_delete(user))
    assert sent == []


def test_other_build_events_are_not_delete_events():
    assert v2.on_delete_event("3f9a2c1d", {"seq": 1, "type": "deleted"}) is False
