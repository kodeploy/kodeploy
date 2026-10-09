"""빌더 콜백 이벤트 → builds / build_records 갱신 (계약 3-2).

- seq가 builds.last_event_seq 이하면 이미 처리한 것이라 무시한다 (빌더는 2xx를 받을 때까지 다시 보낸다).
  빈 번호는 허용한다 (빌더가 재시작하면 seq를 건너뛴다).
- 행을 잠그고(SELECT … FOR UPDATE) 처리해서, 재시도가 겹쳐도 같은 이벤트가 두 번 반영되지 않는다.
- 모르는 build_id·v1 빌드면 무시하고 성공으로 답한다 (4xx면 빌더가 영원히 다시 보낸다).
- core가 이미 취소한 빌드(재배포로 대체)는 seq만 올리고 내용은 반영하지 않는다.
"""

import logging
from datetime import datetime, timezone

from app.deploy.build import diagnose, pipeline, postwatch
from app.deploy.build import v2 as v2_module
from app.deploy.build.v2 import close_record
from app.deploy.model import Build, BuildRecord
from app.shared.db import SessionLocal

logger = logging.getLogger(__name__)

# 실패 단계별 진단 — 빌드 실패는 빌드 로그로, 배포 후 실패는 앱 상태·런타임 로그로.
# commit(레지스트리·git)·sync(Argo 적용)는 유저 코드가 원인이 아니라 진단하지 않는다.
_DIAGNOSIS = {"build": "build_failure", "health": "rollout_failure", "timeout": "rollout_failure"}
# 앱 로그(last_lines)를 빌드 로그 뒤에 붙이는 단계. build 단계의 last_lines는 이미 log 이벤트로 받은 줄이다.
_APP_LOG_STAGES = {"health", "timeout"}


def _at(ev: dict, key: str = "at") -> datetime | None:
    v = ev.get(key)
    if not v:
        return None
    try:
        return datetime.fromisoformat(v).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _append_logs(build: Build, lines: list[str]) -> None:
    if not lines:
        return
    text = "\n".join(str(l) for l in lines)
    build.logs = f"{build.logs}\n{text}" if build.logs else text


# 이벤트 하나를 반영한다. 반환값은 결과 이름 (로그·테스트용): 이벤트 type | "duplicate" | "ignored".
def apply(build_id: str, ev: dict) -> str:
    db = SessionLocal()
    try:
        build = db.query(Build).filter(Build.build_id == build_id).with_for_update().first()
        if build is None or build.last_event_seq is None:
            return "ignored"
        seq = int(ev["seq"])
        if seq <= build.last_event_seq:
            return "duplicate"
        build.last_event_seq = seq
        kind = str(ev.get("type"))
        record = (
            db.query(BuildRecord).filter(BuildRecord.build_id == build_id).order_by(BuildRecord.id.desc()).first()
        )

        diagnosis = None
        if build.status == "cancelled":
            if kind == "cancelled" and record is not None and record.finished_at is None:
                close_record(record, "cancelled")
        elif kind == "log":
            _append_logs(build, ev.get("lines") or [])
        elif kind == "committed":
            # 환경변수 변경 행은 "적용 중"(applied) 그대로 두고 deployed에서 성공으로 넘긴다 (env 상태 문구에 deploying이 없다)
            if (build.kind or "build") != "env_change":
                build.status = "deploying"
            if ev.get("image"):
                build.image = ev["image"]           # repo:tag@sha256:… (롤백 때 쓸 수 있게 digest까지)
            if record is not None:
                record.git_committed_at = _at(ev)
                record.push_done_at = _at(ev, "push_done_at") or record.push_done_at
                if ev.get("triggered_early") is not None:
                    record.triggered_early = bool(ev["triggered_early"])
        elif kind == "deployed":
            build.status = "running"
            if record is not None:
                record.argo_synced_at = _at(ev, "synced_at") or _at(ev)
                record.deploy_ready_at = _at(ev, "healthy_at") or _at(ev)
                record.status = "running"
        elif kind == "finished":
            # 배포 판정 뒤 Job(cache export) 회수 결과 — 배포 상태는 건드리지 않는다
            if record is not None:
                record.job_ended_at = _at(ev, "job_ended_at")
                record.cache_export_failed = bool(ev.get("export_failed"))
                close_record(record, build.status, build.error)
        elif kind == "failed":
            stage = str(ev.get("stage") or "")
            reason = str(ev.get("reason") or "")
            error = f"{stage}: {reason}" if stage else reason
            if stage in _APP_LOG_STAGES and ev.get("last_lines"):
                _append_logs(build, ["", *ev["last_lines"]])
            diagnosis = _DIAGNOSIS.get(stage)
            if diagnosis and record is not None:
                pipeline._mark_failed(db, build, record, error)   # 진단이 뒤따르면 ai_status=pending을 같이 싣는다
            else:
                build.status, build.error = "failed", error
            if record is not None:
                close_record(record, "failed", error)
        elif kind == "cancelled":
            build.status = "cancelled"
            if record is not None:
                close_record(record, "cancelled")
        # 자동 빌드(nixpacks)가 만든 Dockerfile은 init 컨테이너 로그에 표지와 함께 찍힌다 — 화면의 Dockerfile 탭과
        # 실패 진단이 쓰도록 빌드 행에 뽑아 둔다 (v1과 같다). 이미 뽑았으면 건너뛴다.
        if build.build_mode == "auto" and not build.dockerfile_content and build.logs:
            build.dockerfile_content = pipeline._extract_between(
                build.logs, "===KODEPLOY_DOCKERFILE_START===", "===KODEPLOY_DOCKERFILE_END==="
            )
        db.commit()

        # 비공개 저장소 빌드의 토큰 Secret은 빌드가 끝나면(성공·실패·취소) 지운다
        if kind in ("finished", "failed", "cancelled") and (build.kind or "build") == "build":
            v2_module._drop_git_auth(build_id)

        if diagnosis and build.ai_status == "pending":
            pipeline.spawn_background(_diagnose, build_id, diagnosis)
        if kind == "deployed" and build.status == "running" and (build.kind or "build") != "env_change":
            # 성공은 바로 표시하고, 뒤에서 60초 동안 시작 직후 크래시를 본다 (postwatch)
            pipeline.spawn_background(postwatch.watch_after_deploy, build_id)
        return kind
    finally:
        db.close()


# 실패 진단은 LLM 호출이라 수 초~수십 초 — 콜백 응답을 붙잡지 않게 백그라운드에서.
async def _diagnose(build_id: str, fn_name: str) -> None:
    db = SessionLocal()
    try:
        build = db.query(Build).filter(Build.build_id == build_id).first()
        record = (
            db.query(BuildRecord).filter(BuildRecord.build_id == build_id).order_by(BuildRecord.id.desc()).first()
        )
        if build is not None and record is not None:
            pipeline._attach_diagnosis(db, build, record, getattr(diagnose, fn_name))
    except Exception as e:
        logger.warning("build %s: v2 diagnosis failed — %s", build_id, e)
    finally:
        db.close()
