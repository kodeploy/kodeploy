"""v2 배포 경로 — users.pipeline == "v2"인 앱의 서버 빌드를 Go 빌더에 맡긴다.

core가 하는 일: 제출 조건 검사 → 네임스페이스·Secret·PVC 준비 → 서명한 kind=build 요청 전송.
그 뒤(Job·로그·커밋·Argo 대기)는 빌더가 하고, 결과는 콜백(app/internal)으로 받아 builds를 갱신한다.
계약: docs/go-builder-plan.md 3절, docs/builder-e2e.md "4부 할 일".

v2가 아직 받지 않는 것 (빌더가 400으로 거부하거나, 한 앱이 v1·v2로 갈라지는 것):
  서버 없는 앱, 정적 사이트, nixpacks 자동 빌드, private repo, 초기 DB 복원.
"""

import asyncio
import logging
import os.path
import uuid
from datetime import datetime, timezone

from sqlalchemy import func

from app import config
from app.apps import service as apps_service
from app.apps.model import App
from app.auth.model import User
from app.builder import client as builder
from app.deploy import crud
from app.deploy.build.github import _detect_build, _repo_is_public
from app.deploy.model import Build, BuildRecord
from app.deploy.routing.hostnames import _slot_hostnames
from app.deploy.stack import env as env_module
from app.deploy.stack.resources import _apply_storage, _apply_volume, _ensure_tenant_ns, ensure_dep_secrets
from app.shared.db import SessionLocal

logger = logging.getLogger(__name__)

NOT_YET = "새 경로(v2)는 아직"


def is_v2(app: App) -> bool:
    return app.pipeline == "v2"


def is_v2_build(build: Build) -> bool:
    return build.last_event_seq is not None


# 제출 조건 검사 — 통과하면 빌드에 쓸 Dockerfile 경로(repo 기준)를 돌려준다. 아니면 ValueError(→ 400).
# detect(웹 기본값)면 여기서 바로 감지한다: Dockerfile이면 그 경로로 진행, 없으면(nixpacks 대상) 거절.
async def check_submit(
    user: User,
    *,
    runtime: str,
    repo_url: str,
    branch: str,
    build_mode: str,
    dockerfile_path: str,
    use_static: bool,
    init_dump_token: str | None,
) -> str:
    if not config.BUILDER_HMAC_SECRET:
        raise ValueError("새 경로(v2) 빌더 연결이 설정되지 않았습니다")
    if runtime == "none":
        raise ValueError(f"{NOT_YET} 서버 없는 앱을 지원하지 않습니다")
    if use_static:
        raise ValueError(f"{NOT_YET} 정적 사이트를 지원하지 않습니다")
    if init_dump_token:
        raise ValueError(f"{NOT_YET} 초기 DB 복원을 지원하지 않습니다")
    if build_mode == "auto":
        raise ValueError(f"{NOT_YET} Dockerfile 빌드만 지원합니다")

    public = await asyncio.to_thread(_repo_is_public, repo_url)
    if public is None:
        raise ValueError("GitHub에서 저장소를 확인하지 못했습니다. 잠시 후 다시 시도하세요")
    if not public:
        raise ValueError(f"{NOT_YET} public 저장소만 지원합니다")

    if build_mode == "detect":
        probe = Build(repo_url=repo_url, branch=branch, project_path="", runtime=runtime, user_id=user.id)
        mode, path = await asyncio.to_thread(_detect_build, probe)
        if mode != "dockerfile":
            raise ValueError(f"{NOT_YET} Dockerfile 빌드만 지원합니다 (저장소에서 Dockerfile을 찾지 못했습니다)")
        return path
    return dockerfile_path or "Dockerfile"


# 빌더 요청 본문 (계약 3-1). values에는 core 소유 칸만 — image·runtime·port는 빌더 소유라 넣으면 400.
def build_payload(build: Build, owner: User, hostnames: list[str]) -> dict:
    subdir, filename = os.path.split(build.dockerfile_path or "Dockerfile")
    image_repo = build.image.rsplit(":", 1)[0]
    spec = {
        "repo": build.repo_url,
        "ref": build.branch,
        "mode": "dockerfile",
        "dockerfile_dir": subdir,
        "dockerfile_name": filename,
        "image_repo": image_repo,
        "image_tag": build.build_id,
    }
    if config.BUILD_REGISTRY_CACHE_ENABLED:
        spec["cache_ref"] = f"{image_repo}:buildcache"
    return {
        "build_id": build.build_id,
        "actor": owner.id.hex[:8],
        "namespace": build.tenant_id,
        "slot": "server",
        "kind": "build",
        "values": {
            "name": build.app_name,
            "userId": owner.id.hex,
            "db": build.db_type or "none",
            "redis": bool(build.use_redis),
            "volume": {"mountPath": build.volume_mount_path or ""},
            "hostnames": hostnames,
            "static": {"enabled": False, "hostnames": []},
        },
        "unit": {"runtime": build.runtime, "port": build.port},
        "build": spec,
    }


def _new_record(db, build: Build) -> BuildRecord:
    seq = (
        db.query(func.count(BuildRecord.id)).filter(BuildRecord.user_id == build.user_id).scalar() or 0
    ) + 1
    record = BuildRecord(
        build_id=build.build_id,
        user_id=build.user_id,
        app_id=build.app_id,
        seq=seq,
        app_name=build.app_name,
        runtime=build.runtime,
        build_mode=build.build_mode,
        started_at=datetime.now(timezone.utc),
    )
    db.add(record)
    db.commit()
    return record


# 기록 마감 — 어떤 결말이든 finished_at·total_seconds·status를 채운다 (콜백도 같이 쓴다).
# started_at은 DB에서 다시 읽으면 naive(UTC)라 tz를 붙여 뺀다.
def close_record(record: BuildRecord, status: str, error: str | None = None) -> None:
    now = datetime.now(timezone.utc)
    record.status = status
    record.error = error
    record.finished_at = now
    record.total_seconds = (now - record.started_at.replace(tzinfo=timezone.utc)).total_seconds()


def _fail(db, build: Build | None, record: BuildRecord | None, error: str) -> None:
    if build is None:
        return
    db.refresh(build)
    if build.status == "cancelled":   # 그사이 재배포로 대체됐다 — 실패로 덮지 않는다
        if record is not None:
            close_record(record, "cancelled")
    else:
        build.status = "failed"
        build.error = error
        if record is not None:
            close_record(record, "failed", error)
    db.commit()


# spawn_background로 도는 v2 제출: 준비(ns·Secret·PVC) → 빌더 전송. 이후 상태는 콜백이 바꾼다.
async def run_v2_build(build_id: str, initial_env: dict[str, str] | None = None) -> None:
    db = SessionLocal()
    record = None
    build = None
    try:
        build = crud.get_build(db, build_id)
        if not build or build.status == "cancelled":
            return
        record = _new_record(db, build)
        owner = db.query(User).filter_by(id=build.user_id).first()
        app_row = apps_service.get_app(db, build.app_id) if build.app_id else None
        if not owner or not app_row:
            return

        # 빌더에 보내기 전에 Pod이 참조할 것들을 만든다 — 없으면 DB가 Secret을 못 찾아 바로 실패한다.
        _ensure_tenant_ns(build)
        if initial_env:
            env_module.set_env(build.tenant_id, build.app_name, initial_env)
        ensure_dep_secrets(build)
        _apply_storage(build)
        _apply_volume(build)

        db.refresh(build)
        if build.status == "cancelled":  # 준비 중에 재배포로 대체됨
            close_record(record, "cancelled")
            db.commit()
            return
        server_hosts, _ = _slot_hostnames(app_row)
        build.status = "building"         # 이벤트가 먼저 와도 뒤로 돌리지 않게 전송 전에 바꾼다
        db.commit()
        await builder.submit(build_payload(build, owner, server_hosts))
        logger.info("build %s submitted to builder", build_id)
    except builder.BuilderError as e:
        db.rollback()
        _fail(db, build, record, f"빌더가 받지 않았습니다: {e}")
    except Exception as e:
        db.rollback()
        if build is not None:
            _fail(db, build, record, f"오케스트레이션 에러: {e}")
    finally:
        db.close()


# 재배포가 옛 v2 빌드를 대체할 때 빌더에도 취소를 보낸다 (best-effort — 새 제출의 409 처리가 한 번 더 막는다).
async def cancel_remote(build_id: str) -> None:
    try:
        await builder.cancel(build_id)
    except Exception as e:
        logger.warning("build %s: builder cancel failed — %s", build_id, e)


# ---- 앱 삭제 ----------------------------------------------------------------------------------------
# 앱 삭제는 요청 하나가 끝까지 기다리는 흐름이다: 빌더가 values 폴더를 지우고 Application이 사라지면 deleted를
# 보낸다. 그 뒤에야 core가 네임스페이스를 지운다 — Application이 남은 채로 지우면 selfHeal이 다시 만든다.
# 기다리는 쪽(request_delete)과 이벤트를 받는 쪽(on_delete_event)이 같은 이벤트 루프라 Future로 이어 준다.
# core가 재시작돼 기다림이 끊기면 유저가 다시 누르면 된다 (빌더의 delete는 파일이 없어도 성공이다).

DELETE_TIMEOUT = 90.0   # 초. Cloudflare 요청 상한(100초) 안쪽. 넘기면 유저가 다시 누른다

_deleting: dict[str, asyncio.Future] = {}


def delete_payload(user: User, app: App, delete_id: str) -> dict:
    return {
        "build_id": delete_id,
        "actor": user.id.hex[:8],
        "namespace": app.namespace,
        "kind": "delete",
    }


# 빌더에 삭제를 맡기고 deleted를 기다린다. 못 하면 ValueError(→ 400), 화면에 보여도 되는 이유.
async def request_delete(user: User, app: App) -> None:
    delete_id = uuid.uuid4().hex[:8]
    fut = asyncio.get_running_loop().create_future()
    _deleting[delete_id] = fut
    try:
        await builder.submit(delete_payload(user, app, delete_id))   # 진행 중인 빌드가 있으면 409 → 취소 후 다시
        await asyncio.wait_for(fut, DELETE_TIMEOUT)
    except builder.BuilderError as e:
        raise ValueError(f"빌더가 삭제를 받지 않았습니다: {e}")
    except asyncio.TimeoutError:
        raise ValueError("삭제가 아직 끝나지 않았습니다. 잠시 후 다시 시도하세요")
    finally:
        _deleting.pop(delete_id, None)


# 삭제 요청의 콜백이면 기다리는 쪽에 알리고 True. 아니면 False (일반 빌드 이벤트).
def on_delete_event(build_id: str, ev: dict) -> bool:
    fut = _deleting.get(build_id)
    if fut is None:
        return False
    kind = ev.get("type")
    if fut.done() or kind not in ("deleted", "failed", "cancelled"):
        return True
    if kind == "deleted":
        fut.set_result(None)
    elif kind == "failed":
        stage, reason = ev.get("stage") or "", ev.get("reason") or ""
        fut.set_exception(ValueError(f"삭제하지 못했습니다 ({stage}: {reason})" if stage else f"삭제하지 못했습니다 ({reason})"))
    else:
        fut.set_exception(ValueError("삭제 요청이 취소되었습니다. 다시 시도하세요"))
    return True


# ---- 롤백 ----------------------------------------------------------------------------------------
# 이력의 한 배포로 되돌린다. 이미지를 다시 빌드하지 않고, 그 배포가 올라갔던 digest(repo:tag@sha256:…)를
# 빌더에 set-image로 보내 git values의 이미지 칸만 바꾼다 — Argo가 그 이미지로 롤아웃한다.
# 포트·런타임은 그 배포 것으로 돌아가고, DB·Redis·볼륨·호스트 같은 core 소유 설정은 지금 값을 그대로 둔다.
# v1 앱은 대상이 아니다.

# 되돌릴 수 있는 배포 — 서버 슬롯의 v2 빌드가 성공했고(running) digest가 남아 있는 것.
def is_rollbackable(build: Build) -> bool:
    return (
        (build.kind or "build") == "build"
        and build.runtime != "static"
        and build.status == "running"
        and is_v2_build(build)
        and "@sha256:" in (build.image or "")
    )


# 롤백할 수 있는지 검사한다. 못 하면 ValueError(화면에 보여도 되는 이유).
def check_rollback_target(app: App, target: Build | None) -> None:
    if not is_v2(app):
        raise ValueError("새 경로(v2) 앱만 되돌릴 수 있습니다")
    if target is None or target.app_id != app.id:
        raise LookupError("배포를 찾을 수 없습니다")
    if not is_rollbackable(target):
        raise ValueError("이 배포로는 되돌릴 수 없습니다 — 성공한 새 경로(v2) 서버 배포만 되돌릴 수 있어요")


# 롤백 요청 본문 (계약 3-1 kind=set-image). values는 보내지 않는다 — 지금 설정이 그대로 남는다.
def rollback_payload(build: Build, actor: User) -> dict:
    return {
        "build_id": build.build_id,
        "actor": actor.id.hex[:8],
        "namespace": build.tenant_id,
        "slot": "server",
        "kind": "set-image",
        "unit": {"runtime": build.runtime, "port": build.port},
        "image": build.image,
    }


# spawn_background로 도는 롤백 제출. 이후 상태(committed → deployed 또는 failed)는 콜백이 바꾼다.
async def run_v2_rollback(build_id: str) -> None:
    db = SessionLocal()
    record = None
    build = None
    try:
        build = crud.get_build(db, build_id)
        if not build or build.status == "cancelled":
            return
        record = _new_record(db, build)
        actor = db.query(User).filter_by(id=build.user_id).first()
        if not actor:
            return
        db.refresh(build)
        if build.status == "cancelled":   # 제출 전에 다른 배포가 대체했다
            close_record(record, "cancelled")
            db.commit()
            return
        build.status = "deploying"        # 이벤트가 먼저 와도 뒤로 돌리지 않게 전송 전에 바꾼다
        db.commit()
        await builder.submit(rollback_payload(build, actor))
        logger.info("rollback %s (of %s) submitted to builder", build_id, build.rollback_of)
    except builder.BuilderError as e:
        db.rollback()
        _fail(db, build, record, f"빌더가 받지 않았습니다: {e}")
    except Exception as e:
        db.rollback()
        if build is not None:
            _fail(db, build, record, f"오케스트레이션 에러: {e}")
    finally:
        db.close()
