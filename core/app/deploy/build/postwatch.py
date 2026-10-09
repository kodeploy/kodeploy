"""v2 배포 뒤 초기 감시 — deployed를 받은 뒤 60초 동안 이번 배포의 Pod을 본다.

Argo는 Pod이 한 번 Ready가 되면 Healthy로 보고, 롤아웃이 끝난 뒤의 크래시는 Degraded가 아니라
Progressing으로 둔다. 그래서 포트를 연 뒤 시작 코드에서 죽는 앱은 빌더가 deployed를 보낸다.
성공 표시는 그대로 두고(사용자 대기 없음) 뒤에서 60초 더 보다가, 재시작·OOM·이미지 문제가 보이면
빌드를 실패로 바꾸고 앱 로그와 진단을 붙인다. 60초가 지나면 끝 — 이후는 앱 상태 조회가 보여준다.

판정은 v1 _crash_reason을 그대로 쓰되, 이미 Ready였으니 재시작 1회부터 비정상으로 본다.
이번 배포 Pod = 앱 컨테이너 이미지가 이 빌드 이미지(committed의 repo:tag@digest)와 같은 Pod.
core가 재시작되면 진행 중인 감시는 사라진다 (배포 뒤 확인이라 best-effort).
"""

import asyncio
import logging
import time

from kubernetes.client.exceptions import ApiException

from app.deploy.build import diagnose, pipeline
from app.deploy.model import Build, BuildRecord
from app.shared import k8s
from app.shared.db import SessionLocal

logger = logging.getLogger(__name__)

WATCH_SECONDS = 60
POLL_SECONDS = 5
RESTART_LIMIT = 1
ERROR_PREFIX = "배포 완료 후 초기 실행 오류 발생"


def _problem(build: Build, crash_base: dict[str, int]) -> str | None:
    core = k8s.core_v1()
    pods = [
        p for p in core.list_namespaced_pod(namespace=build.tenant_id, label_selector=f"app={build.app_name}").items
        if any(c.name == "app" and c.image == build.image for c in (p.spec.containers or []))
    ]
    dep_pods = core.list_namespaced_pod(namespace=build.tenant_id, label_selector=pipeline._DEP_SELECTOR).items
    return pipeline._crash_reason(pods, dep_pods, crash_base, limit=RESTART_LIMIT, message=pipeline._crash_head)


async def watch_after_deploy(build_id: str) -> None:
    db = SessionLocal()
    try:
        build = db.query(Build).filter(Build.build_id == build_id).first()
        if build is None:
            return
        db.expunge(build)   # 이미지·ns만 읽는다. 상태 판단은 실패로 바꿀 때 새로 읽는다
    finally:
        db.close()

    crash_base: dict[str, int] = {}
    deadline = time.monotonic() + WATCH_SECONDS
    while True:
        try:
            problem = _problem(build, crash_base)
        except ApiException as e:
            logger.warning("build %s: post-deploy pod check failed — %s", build_id, e)
            problem = None
        if problem:
            _mark_failed_after_deploy(build_id, problem)
            return
        if time.monotonic() >= deadline:
            return
        await asyncio.sleep(POLL_SECONDS)


# 실패로 바꾸기 — 그사이 새 배포가 시작됐으면(이 빌드가 최신 서버 빌드가 아니면) 또는 이미 다른 결말이면
# 건드리지 않는다. 옛 감시가 새 배포의 상태를 덮지 않게.
def _mark_failed_after_deploy(build_id: str, reason: str) -> bool:
    db = SessionLocal()
    try:
        build = db.query(Build).filter(Build.build_id == build_id).with_for_update().first()
        if build is None or build.status != "running":
            return False
        latest = (
            db.query(Build.build_id)
            .filter(
                Build.app_id == build.app_id if build.app_id is not None else Build.user_id == build.user_id,
                Build.runtime != "static", Build.kind == "build",
            )
            .order_by(Build.created_at.desc())
            .first()
        )
        if latest is None or latest[0] != build_id:
            return False
        record = (
            db.query(BuildRecord).filter(BuildRecord.build_id == build_id).order_by(BuildRecord.id.desc()).first()
        )
        error = f"{ERROR_PREFIX}: {reason}"
        tail = pipeline._app_log_tail(build)
        if tail:
            build.logs = (build.logs or "").rstrip() + tail
        if record is not None:
            record.status = "failed"   # finished가 먼저 와서 기록이 닫혔어도 결말은 실패로
            record.error = error
            pipeline._mark_failed(db, build, record, error)
            pipeline._attach_diagnosis(db, build, record, diagnose.rollout_failure)
        else:
            build.status, build.error = "failed", error
            db.commit()
        logger.info("build %s: failed after deploy — %s", build_id, reason)
        return True
    finally:
        db.close()
