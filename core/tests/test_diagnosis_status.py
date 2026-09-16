"""AI 진단 진행 상태(builds.ai_status) 고정 — 화면이 "분석 중"을 믿고 기다리는 근거.

프론트는 status가 failed가 되는 순간 빌드 폴링을 멈추고, ai_status가 "pending"일 때만
이어 간다. 그래서 두 성질이 깨지면 화면이 조용히 틀어진다:
  1. failed와 pending이 **같은 커밋**에 실려야 한다 — failed만 먼저 보이면 폴링이 끝나
     진단이 영영 안 뜬다.
  2. pending은 어떤 경로로 끝나든(성공·LLM 실패·재료 수집 예외·기록 실패) 닫혀야 한다 —
     남으면 화면이 "분석 중"에서 못 벗어난다.
"""

from types import SimpleNamespace

import pytest

from app import config
from app.deploy.build import diagnose, pipeline


class _Rec:
    def __init__(self):
        self.build_id = "deadbeef"
        self.llm_model = None
        self.llm_outcome = None


class _SnapDb:
    """커밋 순간의 build 필드를 찍어 두는 세션 대역 — "같은 커밋" 성질을 재는 계측기."""

    def __init__(self, build, fail_commit_at=None):
        self.build = build
        self.snaps = []
        self.rollbacks = 0
        self._fail_at = fail_commit_at

    def commit(self):
        if self._fail_at is not None and len(self.snaps) == self._fail_at:
            self.snaps.append("boom")
            raise RuntimeError("commit 실패")
        self.snaps.append((self.build.status, self.build.ai_status))

    def rollback(self):
        self.rollbacks += 1


def _build():
    return SimpleNamespace(
        build_id="deadbeef", status="building", error=None,
        ai_analysis=None, ai_status=None,
    )


def _ok(payload='{"cause":"x"}'):
    return lambda b: diagnose.CallResult(
        outcome="ok", latency_ms=1, model="m", payload=payload,
        cause_category="oom", inconsistent=False,
    )


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(diagnose, "is_configured", lambda: True)
    monkeypatch.setattr(config, "LLM_DAILY_TOKEN_BUDGET", 0)


def test_failed_and_pending_land_in_one_commit(configured):
    build = _build()
    db = _SnapDb(build)
    pipeline._mark_failed(db, build, _Rec(), "빌드 실패")
    # 계측기 선검증: 커밋이 실제로 찍혔는지부터 (0건이면 아래 단언이 공허하게 통과한다)
    assert db.snaps, "commit 스냅샷이 하나도 없다"
    # ★ failed가 처음 보이는 커밋에서 이미 pending이어야 한다
    first_failed = next(s for s in db.snaps if s[0] == "failed")
    assert first_failed == ("failed", "pending")
    assert build.error == "빌드 실패"


def test_budget_commit_does_not_leak_failed_early(monkeypatch):
    # 예산 초과 판정은 record를 커밋한다 — 그 커밋에 failed가 pending 없이 실리면 안 된다.
    monkeypatch.setattr(diagnose, "is_configured", lambda: True)
    monkeypatch.setattr(config, "LLM_DAILY_TOKEN_BUDGET", 10)
    monkeypatch.setattr(pipeline, "_today_llm_tokens", lambda db: 5)   # 예산 안
    build = _build()
    db = _SnapDb(build)
    pipeline._mark_failed(db, build, _Rec(), "빌드 실패")
    assert [s for s in db.snaps if s[0] == "failed"] == [("failed", "pending")]


def test_success_closes_with_analysis(configured):
    build = _build()
    pipeline._mark_failed(_SnapDb(build), build, _Rec(), "빌드 실패")
    db = _SnapDb(build)
    pipeline._attach_diagnosis(db, build, _Rec(), _ok())
    assert build.ai_status == "done"
    assert build.ai_analysis == '{"cause":"x"}'
    assert db.snaps[-1] == ("failed", "done")


def test_llm_failure_closes_without_analysis(configured):
    # _call이 흡수한 실패(api_error 등)는 payload 없이 온다 — 닫히되 진단문은 없다.
    build = _build()
    pipeline._mark_failed(_SnapDb(build), build, _Rec(), "빌드 실패")
    rec = _Rec()
    pipeline._attach_diagnosis(
        _SnapDb(build), build, rec,
        lambda b: diagnose.CallResult(outcome="api_error", latency_ms=1, model="m"),
    )
    assert build.ai_status == "done"
    assert build.ai_analysis is None
    assert rec.llm_outcome == "api_error"


def test_material_exception_still_closes(configured):
    build = _build()
    pipeline._mark_failed(_SnapDb(build), build, _Rec(), "빌드 실패")

    def _boom(b):
        raise RuntimeError("K8s 조회 실패")

    db = _SnapDb(build)
    pipeline._attach_diagnosis(db, build, _Rec(), _boom)
    assert db.rollbacks == 1
    assert build.ai_status == "done"


def test_record_commit_failure_still_closes(configured):
    build = _build()
    pipeline._mark_failed(_SnapDb(build), build, _Rec(), "빌드 실패")
    db = _SnapDb(build, fail_commit_at=0)          # 결과 기록 커밋이 터진다
    pipeline._attach_diagnosis(db, build, _Rec(), _ok())
    assert build.ai_status == "done"
    assert db.snaps[-1] == ("failed", "done")      # 마감 커밋은 따로 성공


def test_not_pending_is_never_called(configured):
    # 기능 OFF·예산 초과로 pending이 안 붙은 빌드 — 부르지 않는다.
    build = _build()
    build.status = "failed"
    called = []
    pipeline._attach_diagnosis(_SnapDb(build), build, _Rec(), lambda b: called.append(b))
    assert called == []
    assert build.ai_status is None
