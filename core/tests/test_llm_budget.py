"""일일 토큰 예산 가드레일 고정.

계측(build_records)만 있고 상한이 없으면 "얼마 썼는지는 아는데 못 막는" 상태가 된다.
실제로 그 상태에서 게이트웨이 크레딧이 소진돼 모든 진단이 api_error로 떨어진 적이 있고,
알람이 없어 한동안 아무도 몰랐다. 이 테스트는 그 재발을 막는 장치를 고정한다.

고정하는 성질 3가지:
  1. 예산 0(기본)이면 아무것도 바뀌지 않는다 — 기존 동작 무손상.
  2. 예산을 넘으면 diagnose를 **아예 호출하지 않는다** (돈이 나가지 않는다).
  3. 넘어서 안 부른 것과 애초에 안 부른 것(NULL)이 기록에서 구분된다 —
     이 구분이 없으면 대시보드에서 진단이 조용히 사라진 것처럼 보인다.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import config
from app.deploy.build import diagnose, pipeline
from app.deploy.model import BuildRecord


# --- _today_llm_tokens: 오늘 것만, NULL은 0으로 --------------------------------

@pytest.fixture
def db():
    """BuildRecord 테이블만 있는 sqlite 세션.

    BuildRecord는 MySQL 전용 타입(LONGTEXT 등)을 안 써서 그대로 sqlite에 만들어진다.
    (Build 쪽은 LONGTEXT라 안 됨 — 그래서 이 테이블만 만든다.)
    """
    engine = create_engine("sqlite://")
    BuildRecord.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _record(session, *, when, p=None, c=None, outcome="ok"):
    session.add(
        BuildRecord(
            build_id="b" + when.strftime("%H%M%S%f")[:7],
            user_id=None,
            seq=1,
            app_name="app",
            runtime="python",
            build_mode="dockerfile",
            started_at=when,
            status="failed",
            llm_outcome=outcome,
            llm_prompt_tokens=p,
            llm_completion_tokens=c,
        )
    )
    session.commit()


def _utc_naive_now():
    # 운영 DB는 naive UTC로 저장한다 — 테스트도 같은 규약을 쓴다.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_counts_only_today(db):
    now = _utc_naive_now()
    _record(db, when=now, p=100, c=10)
    _record(db, when=now - timedelta(days=1), p=9999, c=9999)   # 어제 — 안 세야 함
    assert pipeline._today_llm_tokens(db) == 110


def test_null_tokens_do_not_break_sum(db):
    # 게이트웨이가 usage를 안 줄 수 있다. 그때 합계가 NULL이 되어 int() 캐스팅이
    # 터지면 빌드 흐름 전체가 죽는다 — coalesce가 그걸 막는지 고정.
    now = _utc_naive_now()
    _record(db, when=now, p=None, c=None, outcome="api_error")
    _record(db, when=now, p=50, c=5)
    assert pipeline._today_llm_tokens(db) == 55


def test_empty_table_is_zero(db):
    assert pipeline._today_llm_tokens(db) == 0


# --- 정책 분기 ----------------------------------------------------------------

class _Rec:
    """BuildRecord 대역 — 예산 분기가 쓰는 필드만."""

    def __init__(self):
        self.build_id = "deadbeef"
        self.llm_model = None
        self.llm_outcome = None


class _Db:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


@pytest.fixture
def used(monkeypatch):
    """오늘 누적 토큰을 원하는 값으로 고정."""

    def _set(n):
        monkeypatch.setattr(pipeline, "_today_llm_tokens", lambda db: n)

    return _set


def test_budget_zero_means_unlimited(monkeypatch, used):
    monkeypatch.setattr(config, "LLM_DAILY_TOKEN_BUDGET", 0)
    used(10_000_000)                              # 아무리 썼어도
    assert pipeline._llm_budget_exceeded(_Db(), _Rec()) is False


def test_under_budget_proceeds(monkeypatch, used):
    monkeypatch.setattr(config, "LLM_DAILY_TOKEN_BUDGET", 1000)
    used(999)
    rec = _Rec()
    assert pipeline._llm_budget_exceeded(_Db(), rec) is False
    assert rec.llm_outcome is None                # 기록도 안 건드림


def test_at_budget_blocks_and_records(monkeypatch, used):
    # 경계값: 예산과 같아지는 순간 막는다 (>= 판정).
    monkeypatch.setattr(config, "LLM_DAILY_TOKEN_BUDGET", 1000)
    monkeypatch.setattr(config, "LLM_MODEL", "claude-sonnet-4-6")
    used(1000)
    rec, db = _Rec(), _Db()
    assert pipeline._llm_budget_exceeded(db, rec) is True
    assert rec.llm_outcome == "budget_exceeded"
    # 어느 단가 구간에서 상한에 닿았는지 — 정책 조정의 근거라 같이 남는다.
    assert rec.llm_model == "claude-sonnet-4-6"
    assert db.commits == 1


# --- 파이프라인 통합: 예산 초과면 돈이 안 나간다 ---------------------------------
# 부를지는 실패를 커밋하는 _mark_failed가 정하고(ai_status="pending"), _attach_diagnosis는
# pending인 빌드만 부른다. 두 단계를 실제 순서대로 태워 "호출 자체가 없다"를 고정한다.

def _failed_build():
    return SimpleNamespace(
        build_id="deadbeef", status="deploying", error=None,
        ai_analysis=None, ai_status=None,
    )


def test_over_budget_never_calls(monkeypatch):
    monkeypatch.setattr(diagnose, "is_configured", lambda: True)
    monkeypatch.setattr(config, "LLM_DAILY_TOKEN_BUDGET", 100)
    monkeypatch.setattr(pipeline, "_today_llm_tokens", lambda db: 500)

    called = []
    build, rec = _failed_build(), _Rec()
    pipeline._mark_failed(_Db(), build, rec, "빌드 실패")
    pipeline._attach_diagnosis(_Db(), build, rec, lambda b: called.append(b))
    assert called == []                           # ★ 호출 자체가 없어야 한다
    assert build.ai_analysis is None
    assert build.ai_status is None                # 화면이 "분석 중"으로 기다리지 않는다
    assert rec.llm_outcome == "budget_exceeded"


def test_under_budget_calls(monkeypatch):
    monkeypatch.setattr(diagnose, "is_configured", lambda: True)
    monkeypatch.setattr(config, "LLM_DAILY_TOKEN_BUDGET", 1000)
    monkeypatch.setattr(pipeline, "_today_llm_tokens", lambda db: 1)

    called = []

    def _fn(build):
        called.append(build)
        return diagnose.CallResult(
            outcome="ok", latency_ms=5, model="m", payload='{"a":1}',
            prompt_tokens=1, completion_tokens=2,
            cause_category="oom", inconsistent=False,
        )

    build, rec = _failed_build(), _Rec()
    pipeline._mark_failed(_Db(), build, rec, "빌드 실패")
    pipeline._attach_diagnosis(_Db(), build, rec, _fn)
    assert len(called) == 1
    assert rec.llm_outcome == "ok"
    assert build.ai_analysis == '{"a":1}'         # 유저 대면 진단문도 붙는다
    assert build.ai_status == "done"


def test_unconfigured_never_queries_budget(monkeypatch):
    # is_configured()=False면 예산 조회조차 하면 안 된다 (기능 OFF인데 DB만 때리는 일 방지).
    monkeypatch.setattr(diagnose, "is_configured", lambda: False)

    def _boom(db):
        raise AssertionError("예산 조회가 불렸다")

    monkeypatch.setattr(pipeline, "_today_llm_tokens", _boom)
    build = _failed_build()
    pipeline._mark_failed(_Db(), build, _Rec(), "빌드 실패")
    pipeline._attach_diagnosis(_Db(), build, _Rec(), lambda b: None)
    assert build.status == "failed"
    assert build.ai_status is None
