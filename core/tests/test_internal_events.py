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
    monkeypatch.setattr(events.pipeline, "spawn_background", lambda fn, *a: spawned.append((fn, a)))
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

    return type("E", (), {"post": staticmethod(post), "row": staticmethod(row), "spawned": spawned})


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
