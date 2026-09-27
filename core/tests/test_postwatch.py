"""v2 배포 뒤 초기 감시 — deployed 뒤 60초 안에 죽는 앱을 실패로 바꾼다.

데모 앱처럼 포트를 연 뒤 시작 코드에서 죽으면 Argo는 잠깐 Healthy가 되고 빌더는 deployed를 보낸다.
성공 표시는 그대로 두고, 뒤에서 재시작 1회부터 실패로 본다. 새 배포가 시작됐으면 옛 감시는 손대지 않는다.
"""

import asyncio
import uuid
from datetime import datetime
from types import SimpleNamespace as NS

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.deploy.build import postwatch
from app.deploy.model import Build, BuildRecord


@compiles(LONGTEXT, "sqlite")
def _longtext_sqlite(type_, compiler, **kw):
    return "TEXT"


UID = uuid.UUID("d6d8b759-8552-4d6f-9a90-00665e7ca0da")
IMAGE = "ghcr.io/yuntyu01/d6d8b759/demo:3f9a2c1d@sha256:" + "a" * 64


# --- 판정: 이번 배포 Pod만, 재시작 1회부터 ---

def _pod(image=IMAGE, restarts=0, exit_code=1, name="demo-1"):
    cs = NS(name="app", restart_count=restarts, state=NS(waiting=None),
            last_state=NS(terminated=NS(reason="Error", exit_code=exit_code)))
    return NS(metadata=NS(name=name, deletion_timestamp=None),
              spec=NS(containers=[NS(name="app", image=image)]),
              status=NS(container_statuses=[cs], conditions=[]))


def _mysql(ready=True):
    return NS(metadata=NS(name="mysql-0", deletion_timestamp=None),
              status=NS(conditions=[NS(type="Ready", status="True" if ready else "False")]))


@pytest.fixture
def cluster(monkeypatch):
    state = NS(app=[], deps=[_mysql()])

    def list_pods(namespace, label_selector):
        assert namespace == f"tenant-{UID.hex[:8]}"
        return NS(items=state.app if label_selector == "app=demo" else state.deps)

    monkeypatch.setattr(postwatch.k8s, "core_v1", lambda: NS(list_namespaced_pod=list_pods))
    return state


BUILD = Build(build_id="3f9a2c1d", app_name="demo", image=IMAGE, user_id=UID)


def test_one_restart_after_ready_is_a_problem(cluster):
    base = {}
    cluster.app = [_pod(restarts=1)]                  # Ready 전에 한 번 죽은 건 기준값
    assert postwatch._problem(BUILD, base) is None
    cluster.app = [_pod(restarts=2)]
    assert postwatch._problem(BUILD, base) == "앱이 시작 중에 종료됐어요 (종료 코드 1)."


def test_other_images_are_ignored(cluster):
    base = {}
    cluster.app = [_pod(image="ghcr.io/yuntyu01/d6d8b759/demo:old@sha256:" + "b" * 64, restarts=0)]
    postwatch._problem(BUILD, base)
    cluster.app[0].status.container_statuses[0].restart_count = 9
    assert postwatch._problem(BUILD, base) is None


def test_waits_for_deps_before_counting(cluster):
    base = {}
    cluster.deps = [_mysql(ready=False)]
    cluster.app = [_pod(restarts=0)]
    postwatch._problem(BUILD, base)
    cluster.app = [_pod(restarts=3)]
    assert postwatch._problem(BUILD, base) is None


# --- 감시 루프 ---

def test_watch_marks_failure_and_stops(monkeypatch):
    seen = iter([None, None, "앱이 시작 중에 종료됐어요 (종료 코드 1)."])
    marked = []
    monkeypatch.setattr(postwatch, "SessionLocal", lambda: _one_build_session())
    monkeypatch.setattr(postwatch, "_problem", lambda b, base: next(seen))
    monkeypatch.setattr(postwatch, "_mark_failed_after_deploy", lambda bid, r: marked.append((bid, r)))
    monkeypatch.setattr(postwatch, "POLL_SECONDS", 0)
    asyncio.run(postwatch.watch_after_deploy("3f9a2c1d"))
    assert marked == [("3f9a2c1d", "앱이 시작 중에 종료됐어요 (종료 코드 1).")]


def test_watch_ends_after_window(monkeypatch):
    calls = []
    monkeypatch.setattr(postwatch, "SessionLocal", lambda: _one_build_session())
    monkeypatch.setattr(postwatch, "_problem", lambda b, base: calls.append(1))
    monkeypatch.setattr(postwatch, "_mark_failed_after_deploy", lambda *a: pytest.fail("healthy app marked failed"))
    monkeypatch.setattr(postwatch, "WATCH_SECONDS", 0.05)
    monkeypatch.setattr(postwatch, "POLL_SECONDS", 0.01)
    asyncio.run(postwatch.watch_after_deploy("3f9a2c1d"))
    assert 2 <= len(calls) < 20


def _one_build_session():
    q = NS(filter=lambda *a: NS(first=lambda: Build(build_id="3f9a2c1d", app_name="demo", image=IMAGE, user_id=UID)))
    return NS(query=lambda m: q, expunge=lambda b: None, close=lambda: None)


# --- 실패로 바꾸기 (SQLite) ---

@pytest.fixture
def db(monkeypatch):
    eng = sa.create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=sa.pool.StaticPool)
    Build.__table__.create(eng)
    BuildRecord.__table__.create(eng)
    Session = sessionmaker(bind=eng)
    monkeypatch.setattr(postwatch, "SessionLocal", Session)
    monkeypatch.setattr(postwatch.pipeline, "_app_log_tail", lambda b: "\n\n=== 앱 로그 (마지막으로 종료된 인스턴스) ===\nCaused by: X\n")
    monkeypatch.setattr(postwatch.pipeline.diagnose, "is_configured", lambda: True)
    monkeypatch.setattr(postwatch.pipeline, "_llm_budget_exceeded", lambda d, r: False)
    diagnosed = []
    monkeypatch.setattr(postwatch.pipeline, "_attach_diagnosis",
                        lambda d, b, r, fn: diagnosed.append((b.build_id, fn.__name__)))

    def add(build_id, status="running", created=datetime(2026, 9, 28, 12, 0)):
        with Session() as s:
            s.add(Build(build_id=build_id, repo_url="r", branch="main", image=IMAGE, app_name="demo", port=8080,
                        runtime="java", status=status, user_id=UID, last_event_seq=5, logs="BUILD OK",
                        kind="build", created_at=created))
            s.add(BuildRecord(build_id=build_id, user_id=UID, seq=1, app_name="demo", runtime="java",
                              build_mode="dockerfile", started_at=datetime(2026, 9, 28, 12, 0), status="running"))
            s.commit()

    def get(build_id):
        with Session() as s:
            return s.get(Build, build_id), s.query(BuildRecord).filter_by(build_id=build_id).one()

    return NS(add=add, get=get, diagnosed=diagnosed)


def test_marks_latest_running_build_failed(db):
    db.add("3f9a2c1d")
    assert postwatch._mark_failed_after_deploy("3f9a2c1d", "앱이 시작 중에 종료됐어요 (종료 코드 1).") is True
    b, r = db.get("3f9a2c1d")
    assert b.status == "failed"
    assert b.error == "배포 완료 후 초기 실행 오류 발생: 앱이 시작 중에 종료됐어요 (종료 코드 1)."
    assert b.logs.startswith("BUILD OK\n\n=== 앱 로그") and b.ai_status == "pending"
    assert r.status == "failed" and r.error == b.error
    assert db.diagnosed == [("3f9a2c1d", "rollout_failure")]


def test_newer_deploy_is_not_overwritten(db):
    db.add("3f9a2c1d", created=datetime(2026, 9, 28, 12, 0))
    db.add("bbbb2222", status="building", created=datetime(2026, 9, 28, 12, 1))
    assert postwatch._mark_failed_after_deploy("3f9a2c1d", "x") is False
    assert db.get("3f9a2c1d")[0].status == "running" and db.diagnosed == []


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_other_endings_are_left_alone(db, status):
    db.add("3f9a2c1d", status=status)
    assert postwatch._mark_failed_after_deploy("3f9a2c1d", "x") is False
    assert db.get("3f9a2c1d")[0].status == status


# --- 콜백에서 시작 ---

def test_deployed_event_starts_watch(monkeypatch):
    from app.internal import events

    started = []
    monkeypatch.setattr(events.pipeline, "spawn_background", lambda fn, *a: started.append((fn, a)))
    monkeypatch.setattr(events, "SessionLocal", lambda: _events_session())
    assert events.apply("3f9a2c1d", {"seq": 1, "type": "deployed", "at": "2026-09-28T12:03:10Z"}) == "deployed"
    assert started == [(postwatch.watch_after_deploy, ("3f9a2c1d",))]


def _events_session():
    build = Build(build_id="3f9a2c1d", status="deploying", last_event_seq=0)
    q = NS(filter=lambda *a: NS(with_for_update=lambda: NS(first=lambda: build),
                                order_by=lambda *a: NS(first=lambda: None)))
    return NS(query=lambda m: q, commit=lambda: None, close=lambda: None)
