"""배포(rollout) 조기 실패 판정 — _crash_reason 순수 함수.

크래시하는 앱을 타임아웃(15분)까지 "배포 중"으로 두지 않는 것이 목적이다.
다만 첫 배포에서 DB가 뜨기 전 앱이 몇 번 죽는 건 정상이라, 의존성이 준비되기 전 재시작은 세지 않는다.
"""

from types import SimpleNamespace as NS

from app.deploy.build.pipeline import CRASH_RESTART_LIMIT, _crash_reason


def _app_pod(restarts=0, waiting=None, exit_code=None, reason="Error", name="app-1", deleting=False):
    term = NS(reason=reason, exit_code=exit_code) if exit_code is not None else None
    cs = NS(
        name="app",
        restart_count=restarts,
        state=NS(waiting=NS(reason=waiting) if waiting else None),
        last_state=NS(terminated=term),
    )
    return NS(
        metadata=NS(name=name, deletion_timestamp="now" if deleting else None),
        status=NS(container_statuses=[cs], conditions=[]),
    )


def _dep_pod(ready):
    return NS(status=NS(conditions=[NS(type="Ready", status="True" if ready else "False")]))


def test_healthy_pod_no_problem():
    assert _crash_reason([_app_pod()], [], {}) is None


def test_image_pull_backoff_fails_immediately():
    msg = _crash_reason([_app_pod(waiting="ImagePullBackOff")], [], {})
    assert msg and "ImagePullBackOff" in msg


def test_crashes_counted_from_first_sight():
    base = {}
    assert _crash_reason([_app_pod(restarts=0)], [], base) is None
    assert _crash_reason([_app_pod(restarts=CRASH_RESTART_LIMIT - 1, exit_code=1)], [], base) is None
    msg = _crash_reason([_app_pod(restarts=CRASH_RESTART_LIMIT, exit_code=1)], [], base)
    assert msg and "종료 코드 1" in msg


def test_restarts_while_db_not_ready_are_not_counted():
    # MySQL이 뜨기 전 5번 죽어도 실패가 아니다. 준비된 뒤부터 센다.
    base = {}
    assert _crash_reason([_app_pod(restarts=5, exit_code=1)], [_dep_pod(False)], base) is None
    assert _crash_reason([_app_pod(restarts=5, exit_code=1)], [_dep_pod(True)], base) is None
    assert _crash_reason([_app_pod(restarts=6, exit_code=1)], [_dep_pod(True)], base) is None
    assert _crash_reason([_app_pod(restarts=5 + CRASH_RESTART_LIMIT, exit_code=1)], [_dep_pod(True)], base)


def test_image_pull_fails_even_while_db_not_ready():
    msg = _crash_reason([_app_pod(waiting="ImagePullBackOff")], [_dep_pod(False)], {})
    assert msg


def test_oom_message():
    base = {"app-1": 0}
    msg = _crash_reason([_app_pod(restarts=CRASH_RESTART_LIMIT, exit_code=137, reason="OOMKilled")], [], base)
    assert "OOMKilled" in msg


def test_probe_kill_message_mentions_port():
    base = {"app-1": 0}
    msg = _crash_reason([_app_pod(restarts=CRASH_RESTART_LIMIT, exit_code=143)], [], base)
    assert "포트" in msg


def test_terminating_pod_ignored():
    base = {"app-1": 0}
    assert _crash_reason([_app_pod(restarts=9, exit_code=1, deleting=True)], [], base) is None
