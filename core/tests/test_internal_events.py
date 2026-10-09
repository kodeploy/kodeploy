"""빌더 콜백 — 서명·게이트웨이 차단, seq 중복 무시, 이벤트별 상태·기록 반영.

SQLite에 builds·build_records를 실제로 만들어 라우터 → events.apply를 끝까지 돌린다.
"""

import json
import uuid
from datetime import datetime

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app import config
from app.builder import sign
from app.deploy.model import Build, BuildRecord
from app.internal import events
from app.internal import router as internal_router


@compiles(LONGTEXT, "sqlite")
def _longtext_sqlite(type_, compiler, **kw):
    return "TEXT"


SECRET = "hmac-secret"
UID = uuid.UUID("d6d8b759-8552-4d6f-9a90-00665e7ca0da")
BID = "3f9a2c1d"


@pytest.fixture
def env(monkeypatch):
    eng = sa.create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=sa.pool.StaticPool)
    Build.__table__.create(eng)
    BuildRecord.__table__.create(eng)
    Session = sessionmaker(bind=eng)
    monkeypatch.setattr(events, "SessionLocal", Session)
    monkeypatch.setattr(config, "BUILDER_HMAC_SECRET", SECRET)
    spawned = []
    dropped = []
    monkeypatch.setattr(events.pipeline, "spawn_background", lambda fn, *a: spawned.append((fn, a)))
    monkeypatch.setattr(events.v2_module, "_drop_git_auth", lambda bid: dropped.append(bid))   # 실제 K8s 호출 대신 기록
    monkeypatch.setattr(events.pipeline.diagnose, "is_configured", lambda: True)
    monkeypatch.setattr(events.pipeline, "_llm_budget_exceeded", lambda db, r: False)

    with Session() as s:
        s.add(Build(build_id=BID, repo_url="r", branch="main", image="ghcr.io/u/x/demo:3f9a2c1d", app_name="demo",
                    port=8080, runtime="java", status="building", user_id=UID, last_event_seq=0, logs=None))
        s.add(Build(build_id="v1v1v1v1", repo_url="r", branch="main", image="i", app_name="demo", port=8080,
                    runtime="java", status="building", user_id=UID, last_event_seq=None))
        s.add(BuildRecord(build_id=BID, user_id=UID, seq=1, app_name="demo", runtime="java", build_mode="dockerfile",
                          started_at=datetime(2026, 9, 1, 12, 0, 0)))
        s.commit()

    app = FastAPI()
    app.include_router(internal_router.router)
    client = TestClient(app)

    def post(ev, build_id=BID, headers=None, secret=SECRET, path=None):
        body = json.dumps(ev).encode()
        p = path or f"/internal/builds/{build_id}/events"
        h = sign.headers(secret.encode(), "POST", p, body)
        h.update(headers or {})
        return client.post(p, content=body, headers=h)

    def row():
        with Session() as s:
            return s.get(Build, BID), s.query(BuildRecord).filter_by(build_id=BID).one()

    return type("E", (), {"post": staticmethod(post), "row": staticmethod(row), "spawned": spawned, "dropped": dropped})


# --- 보호 ---

@pytest.mark.parametrize("header", ["X-Forwarded-For", "X-Envoy-External-Address", "X-Origin-Verify"])
def test_gateway_requests_are_404(env, header):
    r = env.post({"seq": 1, "type": "log", "lines": ["x"]}, headers={header: "1.2.3.4"})
    assert r.status_code == 404
    assert env.row()[0].last_event_seq == 0


def test_bad_signature_is_401(env):
    assert env.post({"seq": 1, "type": "log"}, secret="wrong").status_code == 401


def test_signature_covers_path(env):
    # 다른 빌드 경로로 서명한 본문을 이 빌드에 보내면 거부
    body = json.dumps({"seq": 1, "type": "log"}).encode()
    h = sign.headers(SECRET.encode(), "POST", "/internal/builds/aaaaaaaa/events", body)
    r = TestClient(_app()).post(f"/internal/builds/{BID}/events", content=body, headers=h)
    assert r.status_code == 401


def _app():
    app = FastAPI()
    app.include_router(internal_router.router)
    return app


def test_disabled_without_secret(env, monkeypatch):
    monkeypatch.setattr(config, "BUILDER_HMAC_SECRET", "")
    assert env.post({"seq": 1, "type": "log"}, secret="x").status_code == 404


def test_unknown_and_v1_builds_are_acknowledged_and_ignored(env):
    assert env.post({"seq": 1, "type": "log"}, build_id="ffffffff").json() == {"result": "ignored"}
    assert env.post({"seq": 1, "type": "deployed"}, build_id="v1v1v1v1").json() == {"result": "ignored"}


# --- 순서·중복 ---

def test_duplicate_and_gap(env):
    assert env.post({"seq": 1, "type": "log", "lines": ["a"]}).json()["result"] == "log"
    assert env.post({"seq": 1, "type": "log", "lines": ["a"]}).json()["result"] == "duplicate"
    assert env.post({"seq": 102, "type": "log", "lines": ["b", "", "c"]}).json()["result"] == "log"   # 재개로 건너뜀
    assert env.post({"seq": 50, "type": "log", "lines": ["late"]}).json()["result"] == "duplicate"
    b, _ = env.row()
    assert b.logs == "a\nb\n\nc" and b.last_event_seq == 102


# --- 성공 흐름 ---

def test_success_flow(env):
    env.post({"seq": 1, "type": "log", "lines": ["=== clone (init) ==="]})
    env.post({"seq": 2, "type": "committed", "at": "2026-09-28T12:02:00.123456789Z",
              "image": "ghcr.io/u/x/demo:3f9a2c1d@sha256:" + "a" * 64, "commit_sha": "c" * 40,
              "push_done_at": "2026-09-28T12:01:50Z", "triggered_early": True})
    b, rec = env.row()
    assert b.status == "deploying" and b.image.endswith("@sha256:" + "a" * 64)
    assert rec.git_committed_at == datetime(2026, 9, 28, 12, 2, 0, 123456) and rec.triggered_early is True

    env.post({"seq": 3, "type": "deployed", "at": "2026-09-28T12:03:10Z",
              "synced_at": "2026-09-28T12:02:30Z", "healthy_at": "2026-09-28T12:03:10Z"})
    b, rec = env.row()
    assert b.status == "running"
    assert (rec.argo_synced_at, rec.deploy_ready_at) == (datetime(2026, 9, 28, 12, 2, 30), datetime(2026, 9, 28, 12, 3, 10))
    assert rec.finished_at is None                     # 기록 마감은 finished에서

    env.post({"seq": 4, "type": "finished", "job_ended_at": "2026-09-28T12:04:00Z", "job_succeeded": False,
              "export_failed": True})
    b, rec = env.row()
    assert b.status == "running"                       # export 실패는 배포 상태를 바꾸지 않는다
    assert rec.cache_export_failed is True and rec.job_ended_at == datetime(2026, 9, 28, 12, 4)
    assert rec.status == "running" and rec.finished_at is not None and rec.total_seconds > 0


# --- 실패·취소 ---

def test_health_failure_appends_app_logs_and_diagnoses(env):
    env.post({"seq": 1, "type": "log", "lines": ["BUILD SUCCESSFUL"]})
    env.post({"seq": 2, "type": "failed", "stage": "health", "reason": "the app exited during startup (exit code 1)",
              "last_lines": ["=== app logs (app-1, last terminated instance) ===", "Caused by: X"]})
    b, rec = env.row()
    assert b.status == "failed" and b.error == "health: the app exited during startup (exit code 1)"
    assert b.logs == "BUILD SUCCESSFUL\n\n=== app logs (app-1, last terminated instance) ===\nCaused by: X"
    assert b.ai_status == "pending" and rec.status == "failed" and rec.finished_at is not None
    assert env.spawned == [(events._diagnose, (BID, "rollout_failure"))]


def test_build_failure_does_not_repeat_build_log(env):
    env.post({"seq": 1, "type": "log", "lines": ["error: x"]})
    env.post({"seq": 2, "type": "failed", "stage": "build", "reason": "build failed", "last_lines": ["error: x"]})
    b, _ = env.row()
    assert b.logs == "error: x" and env.spawned == [(events._diagnose, (BID, "build_failure"))]


def test_commit_failure_is_not_diagnosed(env):
    env.post({"seq": 1, "type": "failed", "stage": "commit", "reason": "github 500"})
    b, _ = env.row()
    assert b.status == "failed" and b.error == "commit: github 500" and b.ai_status is None and env.spawned == []


def test_cancelled_event(env):
    env.post({"seq": 1, "type": "cancelled"})
    b, rec = env.row()
    assert b.status == "cancelled" and rec.status == "cancelled" and rec.finished_at is not None


def test_core_cancelled_build_ignores_late_events(env):
    with events.SessionLocal() as s:
        s.get(Build, BID).status = "cancelled"
        s.commit()
    env.post({"seq": 1, "type": "log", "lines": ["late"]})
    env.post({"seq": 2, "type": "deployed", "at": "2026-09-28T12:03:10Z"})
    b, rec = env.row()
    assert b.status == "cancelled" and b.logs is None and rec.deploy_ready_at is None and b.last_event_seq == 2
    env.post({"seq": 3, "type": "cancelled"})
    assert env.row()[1].status == "cancelled"


def test_invalid_event_body(env):
    r = env.post({"type": "log"})
    assert r.status_code == 400


def test_delete_request_events_skip_builds_table(env):
    from app.deploy.build import v2

    got = []

    class Fut:
        def done(self):
            return False

        def set_result(self, v):
            got.append(v)

    v2._deleting["dd44ee55"] = Fut()
    try:
        r = env.post({"seq": 1, "type": "deleted"}, build_id="dd44ee55")
    finally:
        v2._deleting.pop("dd44ee55", None)
    assert r.status_code == 200 and r.json() == {"result": "delete"}
    assert got == [None]


# --- 환경변수 변경 행 (v2: config 요청의 결과) ---

def _add_env_change(status="applied", bid="e1e1e1e1"):
    with events.SessionLocal() as s:
        s.add(Build(build_id=bid, repo_url="r", branch="main", image="i", app_name="demo", port=8080, runtime="java",
                    kind="env_change", status=status, user_id=UID, last_event_seq=0))
        s.commit()


def _status(bid):
    with events.SessionLocal() as s:
        return s.get(Build, bid).status


def test_env_change_stays_applied_until_deployed(env):
    _add_env_change()
    assert env.post({"seq": 1, "type": "committed", "image": "i"}, build_id="e1e1e1e1").json() == {"result": "committed"}
    assert _status("e1e1e1e1") == "applied"                    # 환경변수 상태 문구에 "deploying"이 없다
    env.post({"seq": 2, "type": "deployed"}, build_id="e1e1e1e1")
    assert _status("e1e1e1e1") == "running"
    assert env.spawned == []                                   # 크래시 감시(postwatch)는 빌드에만 붙인다


def test_env_change_failure_is_marked_failed(env):
    _add_env_change()
    env.post({"seq": 1, "type": "failed", "stage": "sync", "reason": "boom"}, build_id="e1e1e1e1")
    assert _status("e1e1e1e1") == "failed"


def test_regular_build_still_goes_deploying_then_watched(env):
    env.post({"seq": 1, "type": "committed", "image": "ghcr.io/u/x/demo:3f9a2c1d@sha256:" + "a" * 64})
    assert env.row()[0].status == "deploying"
    env.post({"seq": 2, "type": "deployed"})
    assert env.row()[0].status == "running" and len(env.spawned) == 1


# --- 비공개 저장소 빌드의 토큰 Secret 정리 ---

@pytest.mark.parametrize("ev", [
    {"seq": 1, "type": "finished", "job_ended_at": None, "job_succeeded": True, "export_failed": False},
    {"seq": 1, "type": "failed", "stage": "build", "reason": "x"},
    {"seq": 1, "type": "cancelled"},
])
def test_git_auth_secret_is_dropped_when_the_build_ends(env, ev):
    env.post(ev)
    assert env.dropped == [BID]


@pytest.mark.parametrize("ev", [
    {"seq": 1, "type": "log", "lines": ["x"]},
    {"seq": 1, "type": "committed", "image": "i"},
    {"seq": 1, "type": "deployed"},
])
def test_git_auth_secret_is_kept_while_the_job_may_still_need_it(env, ev):
    env.post(ev)
    assert env.dropped == []


def test_env_change_rows_never_touch_build_secrets(env):
    _add_env_change()
    env.post({"seq": 1, "type": "failed", "stage": "sync", "reason": "x"}, build_id="e1e1e1e1")
    assert env.dropped == []


# --- 자동 빌드(nixpacks): 로그에서 Dockerfile 뽑기 ---

NIX_LOG = "\n".join([
    "=== nixpacks (init) ===", "[4/5] running nixpacks build...",
    "===KODEPLOY_DOCKERFILE_START===", "FROM ubuntu:jammy", "RUN echo hi", "===KODEPLOY_DOCKERFILE_END===",
    "===KODEPLOY_PLAN_START===", "{}", "===KODEPLOY_PLAN_END===",
])


def test_auto_build_dockerfile_is_extracted_from_the_logs(env):
    with events.SessionLocal() as s:
        b = s.get(Build, BID)
        b.build_mode = "auto"
        s.commit()
    env.post({"seq": 1, "type": "log", "lines": NIX_LOG.split("\n")})
    assert env.row()[0].dockerfile_content == "FROM ubuntu:jammy\nRUN echo hi"


def test_dockerfile_mode_builds_never_get_a_generated_dockerfile(env):
    env.post({"seq": 1, "type": "log", "lines": NIX_LOG.split("\n")})          # 같은 표지가 와도 dockerfile 모드는 건드리지 않는다
    assert env.row()[0].dockerfile_content is None


def test_static_site_deploy_is_not_crash_watched(env):
    # 크래시 감시(postwatch)는 서버 배포 기준(최신 서버 빌드)으로 판정한다. 정적 행에 붙이면 엉뚱한 빌드와 비교한다.
    with events.SessionLocal() as s:
        s.add(Build(build_id="5a6b7c8d", repo_url="r", branch="main", image="ghcr.io/u/x/demo-static:5a6b7c8d",
                    app_name="demo-static", port=8080, runtime="static", build_mode="static", status="building",
                    user_id=UID, last_event_seq=0))
        s.commit()
    env.post({"seq": 1, "type": "committed", "image": "ghcr.io/u/x/demo-static:5a6b7c8d@sha256:" + "ab" * 32}, build_id="5a6b7c8d")
    assert _status("5a6b7c8d") == "deploying"
    env.post({"seq": 2, "type": "deployed"}, build_id="5a6b7c8d")
    assert _status("5a6b7c8d") == "running"
    assert env.spawned == []
    # 서버 빌드는 그대로 감시한다
    env.post({"seq": 1, "type": "committed", "image": "i"})
    env.post({"seq": 2, "type": "deployed"})
    assert [fn.__name__ for fn, _ in env.spawned] == ["watch_after_deploy"]
