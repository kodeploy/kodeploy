"""v2 배포 경로 — users.pipeline == "v2"인 앱의 서버 빌드를 Go 빌더에 맡긴다.

core가 하는 일: 제출 조건 검사 → 네임스페이스·Secret·PVC 준비 → 서명한 kind=build 요청 전송.
그 뒤(Job·로그·커밋·Argo 대기)는 빌더가 하고, 결과는 콜백(app/internal)으로 받아 builds를 갱신한다.
계약: docs/go-builder-plan.md 3절, docs/builder-e2e.md "4부 할 일".

v2가 아직 받지 않는 것 (빌더가 400으로 거부하거나, 한 앱이 v1·v2로 갈라지는 것):
  초기 DB 복원, 서버를 쓰던 앱에서 서버를 빼는 것 (정적 사이트만 남기기).
"""

import asyncio
import base64
import json
import logging
import os.path
import re
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import func

from app import config
from app.apps import service as apps_service
from app.apps.model import App
from app.auth import github_app
from app.auth.model import User
from app.builder import client as builder
from app.deploy import crud
from app.deploy.build.github import _detect_build, _repo_is_public
from app.deploy.model import Build, BuildRecord
from app.deploy.routing.hostnames import _slot_hostnames
from app.deploy.stack import env as env_module
from app.deploy.stack import manifests
from app.deploy.stack.resources import _apply_storage, _apply_volume, _ensure_tenant_ns, ensure_dep_secrets
from app.shared.db import SessionLocal

logger = logging.getLogger(__name__)

NOT_YET = "새 경로(v2)는 아직"


def is_v2(app: App) -> bool:
    return app.pipeline == "v2"


def is_v2_build(build: Build) -> bool:
    return build.last_event_seq is not None


# 제출 조건 검사 — 통과하면 (빌드 방식, 경로)를 돌려준다. 아니면 ValueError(→ 400).
#   ("dockerfile", Dockerfile 경로) 또는 ("auto", 프로젝트 경로) — 경로는 repo 기준이다. 서버가 없는 제출(정적만)은
#   서버 빌드가 없으니 방식·경로를 정하지 않고 기본값을 돌려준다.
# detect(웹 기본값)면 여기서 바로 감지한다: Dockerfile이 있으면 dockerfile, 없으면 nixpacks 자동 빌드(auto)로 간다.
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
    project_path: str = "",                # 자동 빌드의 프로젝트 서브디렉토리 (비면 nixpacks가 자동 탐색)
    static_repo_url: str = "",             # 정적 사이트를 다른 저장소에서 받을 때 그 저장소 (비면 repo_url)
    app: App | None = None,                # 이미 있는 앱이면 그 앱 (저장소 접근은 앱 주인의 GitHub 연결로 한다)
    installation_id: int | None = None,    # 저장소를 받을 GitHub 연결 (비공개 저장소 확인용)
) -> tuple[str, str]:
    if not config.BUILDER_HMAC_SECRET:
        raise ValueError("새 경로(v2) 빌더 연결이 설정되지 않았습니다")
    if init_dump_token:
        raise ValueError(f"{NOT_YET} 초기 DB 복원을 지원하지 않습니다")

    # 빌드할 저장소를 모두 확인한다: 서버가 있으면 repo_url, 정적 사이트가 있으면 그 저장소 (기본은 repo_url)
    repos: list[str] = []
    if runtime != "none":
        repos.append(repo_url)
    if use_static:
        repos.append(static_repo_url.strip() or repo_url)
    for url in dict.fromkeys(repos):
        await _ensure_repo_access(url, installation_id)

    if runtime == "none":
        return "dockerfile", dockerfile_path or "Dockerfile"
    if build_mode == "detect":
        probe = Build(
            repo_url=repo_url, branch=branch, project_path=project_path, runtime=runtime, user_id=user.id,
            app_id=app.id if app is not None else None,
        )
        return await asyncio.to_thread(_detect_build, probe)   # ("dockerfile", Dockerfile 경로) | ("auto", 프로젝트 경로)
    if build_mode == "auto":
        return "auto", project_path
    return "dockerfile", dockerfile_path or "Dockerfile"


# 저장소를 받을 수 있는지 본다: public이면 통과, 아니면 GitHub 연결이 접근할 수 있어야 한다. 못 하면 ValueError.
async def _ensure_repo_access(repo_url: str, installation_id: int | None) -> None:
    public = await asyncio.to_thread(_repo_is_public, repo_url)
    if public is None:
        raise ValueError("GitHub에서 저장소를 확인하지 못했습니다. 잠시 후 다시 시도하세요")
    if not public:
        # public이 아니면 비공개이거나 없는 저장소다 — 연결된 GitHub App(앱 주인의 것)이 접근할 수 있으면 비공개로 받는다.
        # 빌드 Job이 그 연결의 토큰으로 clone한다 (run_v2_build가 빌드별 Secret을 만든다).
        if not await asyncio.to_thread(_installation_has_repo, installation_id, repo_url):
            raise ValueError(
                "저장소를 찾을 수 없거나 접근할 수 없어요. 비공개 저장소라면 GitHub 연결에서 이 저장소를 추가해 주세요"
            )


# 이 GitHub 연결(installation)이 접근할 수 있는 저장소인가 — 비공개 저장소를 받아도 되는지 본다.
def _installation_has_repo(installation_id: int | None, repo_url: str) -> bool:
    if not installation_id:
        return False
    m = re.search(r"github\.com[/:]([^/]+)/([^/]+?)(?:\.git)?/?$", repo_url.strip())
    if not m:
        return False
    slug = f"{m.group(1)}/{m.group(2)}".lower()
    return any((r.get("full_name") or "").lower() == slug for r in github_app.list_installation_repos(installation_id))


# 정적 사이트 빌드에 쓸 Dockerfile 본문 — repo엔 없고 플랫폼이 만든다 (v1과 같은 템플릿).
# 빌드 입력(빌드 커맨드·출력 폴더·빌드 변수)만의 함수라 같은 행이면 늘 같은 글자다 (화면의 Dockerfile 탭에도 이걸 둔다).
def static_dockerfile_text(build: Build) -> str:
    return manifests.static_dockerfile(
        build.build_cmd or "",
        build.output_dir or "",
        build_env=json.loads(build.build_env) if build.build_env else None,
    )


# 정적 사이트 슬롯 빌드의 values — core 칸 중 이 슬롯의 것만 보낸다. 서버 칸(db·redis·volume)은 보내지 않는다:
# 이 행은 서버 설정을 모르니(db none·볼륨 없음) 보내면 이미 배포된 서버 설정을 지워 버린다.
def _static_values(build: Build, owner: User, server_hosts: list[str], static_hosts: list[str]) -> dict:
    return {
        "name": build.app_name.removesuffix("-static"),   # 정적 행의 이름은 {앱}-static, 차트의 values.name은 앱 이름이다
        "userId": owner.id.hex,
        "hostnames": server_hosts,
        "static": {"enabled": True, "hostnames": static_hosts},
    }


def build_payload(
    build: Build,
    owner: User,
    hostnames: list[str],
    git_auth_secret: str = "",
    static_enabled: bool = False,                 # 서버 빌드가 함께 싣는 정적 슬롯 선언 (앱의 site_enabled)
    static_hosts: list[str] | None = None,
) -> dict:
    image_repo = build.image.rsplit(":", 1)[0]
    spec = {
        "repo": build.repo_url,
        "ref": build.branch,
        "image_repo": image_repo,
        "image_tag": build.build_id,
    }
    is_static = build.runtime == "static"
    if is_static:
        # 정적 사이트: core가 만든 Dockerfile을 base64로 보낸다 (project_path가 있으면 그 폴더가 빌드 context)
        spec["mode"] = "static"
        spec["dockerfile_b64"] = base64.b64encode(static_dockerfile_text(build).encode("utf-8")).decode("ascii")
        if build.project_path:
            spec["project_path"] = build.project_path
    elif build.build_mode == "auto":
        # 자동 빌드(nixpacks): Dockerfile 칸은 쓰지 않고, 프로젝트 경로만 (비면 빌더가 자동 탐색한다)
        spec["mode"] = "auto"
        if build.project_path:
            spec["project_path"] = build.project_path
    else:
        subdir, filename = os.path.split(build.dockerfile_path or "Dockerfile")
        spec.update({"mode": "dockerfile", "dockerfile_dir": subdir, "dockerfile_name": filename})
    if config.BUILD_REGISTRY_CACHE_ENABLED:
        spec["cache_ref"] = f"{image_repo}:buildcache"
    if git_auth_secret:
        spec["git_auth_secret"] = git_auth_secret       # 비공개 저장소: clone 컨테이너에만 토큰이 간다
    if is_static:
        return {
            "build_id": build.build_id,
            "actor": owner.id.hex[:8],
            "namespace": build.tenant_id,
            "slot": "static",
            "kind": "build",
            "values": _static_values(build, owner, hostnames, static_hosts or []),
            "build": spec,
        }
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
            "static": {"enabled": static_enabled, "hostnames": static_hosts or []},
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
        # 정적 사이트는 서버가 쓰는 환경변수·DB·저장소·볼륨이 없다 (그건 서버 슬롯 빌드의 몫이다).
        is_static = build.runtime == "static"
        _ensure_tenant_ns(build)
        if not is_static:
            if initial_env:
                env_module.set_env(build.tenant_id, build.app_name, initial_env)
            ensure_dep_secrets(build)
            _apply_storage(build)
            _apply_volume(build)
        else:
            build.dockerfile_content = static_dockerfile_text(build)   # 빌드 전에 보존 (화면 Dockerfile 탭 · 진단)
            db.commit()

        db.refresh(build)
        if build.status == "cancelled":  # 준비 중에 재배포로 대체됨
            close_record(record, "cancelled")
            db.commit()
            return
        server_hosts, static_hosts = _slot_hostnames(app_row)
        # 비공개 저장소: 앱 주인의 GitHub 연결로 토큰을 발급해 빌드별 Secret에 담는다 ("" = public clone).
        # 빌드가 끝나면(콜백의 finished·failed·cancelled) 지운다.
        from app.deploy.build import pipeline

        git_auth = await asyncio.to_thread(pipeline._provision_git_auth, build)
        build.status = "building"         # 이벤트가 먼저 와도 뒤로 돌리지 않게 전송 전에 바꾼다
        db.commit()
        await builder.submit(
            build_payload(build, owner, server_hosts, git_auth, static_enabled=app_row.site_enabled, static_hosts=static_hosts)
        )
        logger.info("build %s submitted to builder", build_id)
    except builder.BuilderError as e:
        db.rollback()
        _drop_git_auth(build_id)
        _fail(db, build, record, f"빌더가 받지 않았습니다: {e}")
    except Exception as e:
        db.rollback()
        _drop_git_auth(build_id)
        if build is not None:
            _fail(db, build, record, f"오케스트레이션 에러: {e}")
    finally:
        db.close()


# 빌드별 git-auth Secret 정리 — 못 지워도 빌드를 막지 않는다 (토큰은 1시간 뒤 어차피 만료된다).
def _drop_git_auth(build_id: str) -> None:
    try:
        from app.deploy.build import pipeline

        pipeline._cleanup_git_auth(build_id)
    except Exception as e:
        logger.warning("build %s: git-auth cleanup failed — %s", build_id, e)


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


# 이 앱에 배포된 서버가 있는가 — 성공한 v2 서버 배포가 하나라도 있으면 있다.
# v2는 서버를 내리는 길이 아직 없어서, 서버가 있는 앱을 정적 사이트만 남기게 둘 수 없다.
def has_server_deployed(db, app: App) -> bool:
    return (
        db.query(Build.build_id)
        .filter(
            Build.app_id == app.id, Build.runtime != "static", Build.kind == "build",
            Build.status == "running", Build.last_event_seq.isnot(None),
        )
        .first()
        is not None
    )


# ---- 설정 변경 (환경변수·도메인) -------------------------------------------------------------------
# v2 앱의 Deployment·HTTPRoute는 Argo가 git values로 그린다. core가 직접 고치면 Argo가 되돌리므로,
# 값만 바꾸는 요청(kind=config)을 빌더에 보내 values를 커밋하게 한다 — Argo가 적용한다.
#   환경변수: Secret({app}-env)은 core가 쓰고(Argo가 안 건드린다), envRevision을 올려 Pod을 다시 띄운다.
#   도메인: hostnames 목록(슬롯 규칙으로 계산)을 values에 싣는다.
# 서버 배포가 도는 중에는 보내지 않는다 — 같은 ns의 요청이 겹치면 빌더가 먼저 온 쪽을 취소하게 되어 배포가 죽는다.

_ACTIVE = ("queued", "building", "built", "deploying")


# 환경변수는 정적 사이트와 상관없어 서버 배포만 기다린다. 도메인은 정적 슬롯의 호스트도 바꾸니 정적 배포까지 기다린다
# (돌던 정적 빌드가 나중에 옛 호스트 목록을 커밋해 새 도메인을 되돌리지 않게).
def ensure_idle(db, app: App, *, include_static: bool = False) -> None:
    conds = [Build.app_id == app.id, Build.status.in_(_ACTIVE)]
    if not include_static:
        conds.append(Build.runtime != "static")
    busy = db.query(Build).filter(*conds).first()
    if busy is not None:
        raise ValueError("배포가 진행 중이에요. 끝난 뒤에 다시 시도해 주세요")


def config_payload(build_id: str, actor: User, namespace: str, values: dict) -> dict:
    return {
        "build_id": build_id,
        "actor": actor.id.hex[:8],
        "namespace": namespace,
        "slot": "server",
        "kind": "config",
        "values": values,
    }


# 환경변수를 바꿨다 — Secret은 이미 썼고, envRevision을 올려 Pod을 다시 띄우게 한다.
# 시각(초)을 쓴다: 늘 커지고, 직전 값과 같을 일이 없어서 카운터를 따로 저장하지 않아도 된다.
# 결과(committed → deployed 또는 failed)는 콜백이 이 환경변수 변경 행(event)에 반영한다.
async def submit_env_revision(event: Build, actor: User) -> None:
    await builder.submit(
        config_payload(event.build_id, actor, event.tenant_id, {"envRevision": int(time.time())})
    )


# 도메인이 바뀌었다 — 슬롯 규칙으로 계산한 두 슬롯의 hostnames를 values에 싣는다 (새 목록이 route를 통째로 대체한다).
# 정적 사이트가 켜져 있으면 커스텀 도메인은 정적 슬롯으로 간다. 정적 슬롯의 켬/끔(enabled)은 건드리지 않는다.
# 이 요청은 이력 행이 없다(도메인 변경은 이력에 안 남는다) — 콜백은 모르는 build_id라 무시된다.
async def apply_hostnames(app: App, actor: User) -> None:
    server_hosts, static_hosts = _slot_hostnames(app)
    await builder.submit(
        config_payload(
            uuid.uuid4().hex[:8], actor, app.namespace,
            {"hostnames": server_hosts, "static": {"hostnames": static_hosts}},
        )
    )
