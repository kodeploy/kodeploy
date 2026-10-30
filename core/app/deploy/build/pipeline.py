"""배포 제출 오케스트레이션 — 제출 검증·이력 행·빌더 제출(v2) spawn, 롤백, 실패 진단 기록, 크래시 판정."""

import asyncio
import json
import logging
import threading
import uuid
from datetime import datetime, timezone

from kubernetes.client.exceptions import ApiException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import config
from app.auth import github_app
from app.apps import service as apps_service
from app.apps.model import App
from app.auth.model import User
from app.deploy import crud
from app.deploy.console import logs as runtime_logs
from app.deploy.stack import r2
from app.deploy.build import diagnose, v2
from app.deploy.build.naming import _normalize_repo_url, _resolve_app, repo_key
from app.deploy.build.source import validate_branch, validate_repo_path, validate_repo_url
from app.deploy.build.validation import (
    _validate_static_env,
    _validate_static_fields,
    _validate_volume_fields,
    reserved_env_keys,
)
from app.deploy.model import Build, BuildRecord
from app.shared import k8s
from app.shared.db import SessionLocal

logger = logging.getLogger(__name__)


# 백그라운드 오케스트레이션을 전용 daemon 스레드의 자체 이벤트 루프에서 실행.
# run_v2_build 등은 async지만 내부에서 동기 K8s/DB 클라이언트 + _ensure_tenant_ns의
# time.sleep(최대 60초)을 쓴다. uvicorn 워커 1개 = 메인 루프 1개라, 메인 루프에 올리면
# 빌드 도는 동안 /healthz 포함 전 유저 요청이 멈춘다. 스레드마다 asyncio.run으로 독립
# 루프를 띄워 블로킹을 그 스레드에 격리한다.
# 작업은 I/O 바운드라 작업당 스레드 1개로 충분. create_task와 달리 GC로 사라질 위험도 없음.
def spawn_background(coro_func, *args) -> None:
    threading.Thread(
        target=lambda: asyncio.run(coro_func(*args)),
        daemon=True,
    ).start()


# 실패 진단을 build row(유저 대면 진단문)와 record(계측)에 각각 붙인다 (best-effort).
# 호출 시점엔 status/error가 이미 커밋돼 있으므로, 진단이 실패하든 API가 죽어 있든 배포
# 결과는 그대로 남는다 — r2/domains 정리 실패를 삼키는 것과 같은 철학. 동기 호출인 이유:
# 진단은 이미 전용 스레드에서 돌고(spawn_background), 블로킹이 그 스레드에 격리된다. to_thread로 떼면 ORM 객체를 다른 스레드에서
# 만지게 되어(commit 후 attribute expire → 재조회) Session 스레드 안전성이 깨진다.
#
# 두 곳에 나눠 쓰는 이유: build.ai_analysis는 유저가 읽는 전문이라 delete_app에 함께 지워지고,
# record의 llm_* 는 앱이 지워진 뒤에도 남아야 하는 운영 계측이다 (build_records는 append-only).
# diagnose는 LLM 실패로 예외를 안 던지므로(결말도 세야 하니까) 정상 경로에서 outcome이 온다.
# 오늘(UTC) 쓴 진단 토큰 합계. build_records가 호출 계측의 진실원이라 별도 카운터를
# 두지 않는다 — 카운터를 따로 두면 재시작에 날아가거나 기록과 어긋난다.
# started_at 인덱스를 그대로 타므로 빌드당 1회 호출은 부담이 되지 않는다.
def _today_llm_tokens(db: Session) -> int:
    # DB의 datetime은 naive UTC로 저장된다 — 비교도 naive UTC로.
    since = (
        datetime.now(timezone.utc)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .replace(tzinfo=None)
    )
    total = (
        db.query(
            func.coalesce(func.sum(BuildRecord.llm_prompt_tokens), 0)
            + func.coalesce(func.sum(BuildRecord.llm_completion_tokens), 0)
        )
        .filter(BuildRecord.started_at >= since)
        .scalar()
    )
    return int(total or 0)


# 일일 토큰 예산을 넘었으면 호출을 건너뛰고 그 사실을 기록한다 (True면 스킵).
# ★ 예산은 "이번 호출을 하기 전"의 누적으로 판정한다. 호출 후 토큰을 알 수 있으므로
#   마지막 한 건은 예산을 넘길 수 있다 — 정확한 상한이 아니라 폭주 차단이 목적이라
#   그 오차(호출 1건)는 의도적으로 받아들인다. 정확히 막으려면 호출 전에 입력 토큰을
#   세야 하는데, 그건 토크나이저를 제공자별로 들여오는 비용이 이득보다 크다.
def _llm_budget_exceeded(db: Session, record: BuildRecord) -> bool:
    budget = config.LLM_DAILY_TOKEN_BUDGET
    if budget <= 0:
        return False
    used = _today_llm_tokens(db)
    if used < budget:
        return False
    # 왜 안 불렀는지가 남아야 한다. outcome이 NULL이면 "성공한 빌드라 안 부름"과
    # 구분이 안 되고, 대시보드에서 진단이 조용히 사라진 것처럼 보인다.
    # llm_model도 같이 남긴다 — 어느 단가 구간에서 상한에 닿았는지가 정책 조정의 근거다.
    record.llm_model = config.LLM_MODEL
    record.llm_outcome = "budget_exceeded"
    db.commit()
    logger.warning(
        "AI 진단 건너뜀 — 일일 토큰 예산 초과 (%s/%s), build %s",
        f"{used:,}", f"{budget:,}", record.build_id,
    )
    return True


# 실패를 커밋하면서 진단이 뒤따를지를 같은 커밋에 싣는다 (ai_status="pending").
# ★ 같은 커밋이어야 하는 이유: 프론트는 status가 failed가 되는 순간 빌드 폴링을 멈춘다.
#   failed만 먼저 보이고 pending이 한 틱 늦으면 그 사이 폴링이 끝나 진단이 영영 안 뜬다
#   (rollout 실패는 failed 커밋과 진단 사이에 cache export 회수 대기까지 끼어 틈이 길다).
# 진단을 안 부를 경우(기능 OFF·예산 초과)엔 ai_status를 NULL로 둬 프론트가 기다리지 않게 한다.
# 예산 판정은 status를 만지기 전에 한다 — _llm_budget_exceeded가 record를 커밋하는데,
# 그때 failed가 pending 없이 먼저 실려 나가면 위 틈이 다시 생긴다.
def _mark_failed(db: Session, build: Build, record: BuildRecord, error: str) -> None:
    wanted = diagnose.is_configured() and not _llm_budget_exceeded(db, record)
    build.status = "failed"
    build.error = error
    if wanted:
        build.ai_status = "pending"
    db.commit()


def _attach_diagnosis(
    db: Session, build: Build, record: BuildRecord, diagnose_fn
) -> None:
    # 부를지는 _mark_failed가 이미 정했다 — 여기서 예산을 다시 보면 판정이 둘로 갈린다.
    if build.ai_status != "pending":
        return
    try:
        result = diagnose_fn(build)
    except Exception as e:
        # diagnose가 흡수 못 한 것(K8s 조회 등 재료 수집 단계 예외)만 여기로 온다.
        db.rollback()
        logger.warning("build %s: AI 진단 실패 — %s", build.build_id, e)
        _finish_diagnosis(db, build)
        return
    try:
        if result.payload:                       # 실패 결말이면 진단문 없음 — 카드도 안 그린다
            build.ai_analysis = result.payload
        build.ai_status = "done"
        record.llm_model = result.model
        record.llm_outcome = result.outcome
        record.llm_latency_ms = result.latency_ms
        record.llm_prompt_tokens = result.prompt_tokens
        record.llm_completion_tokens = result.completion_tokens
        record.llm_cause_category = result.cause_category
        record.llm_inconsistent = result.inconsistent
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("build %s: AI 진단 기록 실패 — %s", build.build_id, e)
        _finish_diagnosis(db, build)


# 어떤 경로로 끝나든 pending은 반드시 닫는다 — 남아 있으면 화면이 "분석 중"에서 못 벗어난다.
# (프로세스가 진단 도중 죽는 경우는 여기로도 못 온다 — 프론트가 시간 상한으로 따로 끊는다.)
def _finish_diagnosis(db: Session, build: Build) -> None:
    try:
        build.ai_status = "done"
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("build %s: AI 진단 상태 마감 실패 — %s", build.build_id, e)


_ACTIVE_STATUSES = {"queued", "building", "built", "deploying"}


# slot="server"|"static" — 그 슬롯의 활성 빌드만 취소.
# 두 슬롯이 한 제출에서 동시에 빌드되므로 유저 전체 취소면 서로 죽인다.
def _cancel_stale_builds(db: Session, app_id: uuid.UUID, slot: str) -> None:
    q = db.query(Build).filter(
        Build.app_id == app_id,
        Build.status.in_(_ACTIVE_STATUSES),
    )
    if slot == "static":
        q = q.filter(Build.runtime == "static")
    else:
        q = q.filter(Build.runtime != "static")
    stale = q.all()
    if not stale:
        return
    for build in stale:
        build.status = "cancelled"
        if v2.is_v2_build(build):   # v1 시절 행은 빌더가 모른다 — 표시만 닫는다
            spawn_background(v2.cancel_remote, build.build_id)  # Job은 빌더 것 — 빌더가 지우고 cancelled를 보낸다
    db.commit()


# 빌드가 속한 앱. app_id가 비어 있는 옛 행은 소유 유저의 앱으로 찾는다.
def _app_of(db: Session, build: Build) -> App | None:
    if build.app_id is not None:
        return apps_service.get_app(db, build.app_id)
    return apps_service.get_user_app(db, build.user_id) if build.user_id else None


# --- private repo clone 인증 (GitHub App installation token) -------------------
# 빌드별 Secret에 토큰을 담아 잡이 마운트, 빌드 후 삭제. 토큰은 그 유저 installation에 스코프돼
# 남의 private repo는 GitHub이 거부 — 격리 자동. See app/auth/github_app.py.

def _git_auth_secret_name(build_id: str) -> str:
    return f"git-auth-{build_id}"


# build의 user가 App 설치(installation_id)했고 App이 설정돼 있으면 installation token을 발급해
# kodeploy-build ns의 Secret에 담고 그 이름을 반환. 토큰 없으면(미설치/미설정/실패) "" → public clone.
def _provision_git_auth(build: Build) -> str:
    if build.user_id is None or not github_app.is_configured():
        return ""
    db = SessionLocal()
    try:
        # 저장소 접근은 앱 주인의 GitHub 연결로 한다 — 편집 권한으로 초대받은 사람이 배포해도 주인의 비공개 저장소를 받는다
        app_row = _app_of(db, build)
        if app_row is not None:
            installation_id = apps_service.repo_installation_id(db, app_row)
        else:
            owner = db.query(User).filter_by(id=build.user_id).first()
            installation_id = owner.github_installation_id if owner else None
    finally:
        db.close()
    token = github_app.get_clone_token(installation_id)
    if not token:
        return ""
    name = _git_auth_secret_name(build.build_id)
    body = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": name,
            "namespace": config.BUILD_NAMESPACE,
            "labels": {"app": "kodeploy-build", "build-id": build.build_id},
        },
        "type": "Opaque",
        "stringData": {"GIT_AUTH_TOKEN": token},
    }
    core = k8s.core_v1()
    try:
        core.create_namespaced_secret(namespace=config.BUILD_NAMESPACE, body=body)
    except ApiException as e:
        if e.status != 409:  # 재시도 등으로 이미 있으면 최신 토큰으로 교체
            raise
        core.replace_namespaced_secret(
            name=name, namespace=config.BUILD_NAMESPACE, body=body
        )
    return name


def _cleanup_git_auth(build_id: str) -> None:
    try:
        k8s.core_v1().delete_namespaced_secret(
            name=_git_auth_secret_name(build_id), namespace=config.BUILD_NAMESPACE
        )
    except ApiException as e:
        if e.status != 404:
            raise


# 배포 제출 — 원하는 스택(서버 슬롯 + 정적 슬롯)을 선언받아 슬롯별 빌더 제출(또는 서버 내리기)을 spawn.
# user 객체로 받음 — _resolve_app이 유저의 앱을 찾거나 첫 배포면 만들기 위해.
# 반환: 이 제출이 만든 Build row들 (슬롯당 최대 1개).
async def start_deploy(
    db: Session,
    user: User,
    repo_url: str,
    runtime: str,                          # "python" | "java" | "none"(서버 없음)
    name: str | None = None,
    branch: str = "main",
    port: int = 80,
    db_type: str = "none",
    use_redis: bool = False,
    storage: str = "none",                 # 영속저장소 — "none" | "local" | "object"
    volume_mount_path: str = "",           # local 전용 — PVC 마운트 경로
    volume_storage_class: str = "local-path",
    volume_size: str = "5Gi",
    build_mode: str = "dockerfile",
    dockerfile_path: str = "Dockerfile",
    project_path: str = "",
    env_vars: dict[str, str] | None = None,
    init_dump_token: str | None = None,
    use_static: bool = False,
    static_repo_url: str = "",
    static_branch: str = "",
    static_project_path: str = "",
    build_cmd: str = "",
    output_dir: str = "",
    static_env: dict[str, str] | None = None,
    app: App | None = None,                # 배포할 앱. None이면 유저의 앱(없으면 첫 배포로 새로 만든다)
    allowed_repos: set[str] | None = None,  # 주어지면 이 저장소(repo_key)로만 배포 — 편집 권한 멤버용
) -> list[Build]:
    has_server = runtime != "none"
    if not has_server and not use_static:
        raise ValueError("서버 런타임이나 정적 사이트 중 하나는 선택해야 합니다")
    # 영속저장소 셀렉터 → 내부 표현. object=R2(use_storage) / local=PVC(volume_mount_path 비어있지 않음).
    # 한 앱에 둘 다는 없음 — 단일 셀렉터라 상호배타 (DB 한 개 정책과 동일 철학).
    use_storage = storage == "object"
    use_volume = storage == "local"
    if not has_server and (db_type != "none" or use_redis or storage != "none"):
        raise ValueError("DB · Redis · 저장소는 서버 런타임과 함께만 쓸 수 있습니다")
    # object(R2)는 CF 설정이 갖춰졌을 때만 — 빌드 시작 전에 친절히 거절.
    if use_storage and not r2.is_configured():
        raise ValueError("오브젝트 스토리지(R2)가 서버에 설정되지 않았습니다")
    # local(PVC) 입력 검증 — 정규화한 값으로 교체 (mount_path 절대경로 / class·size 형식).
    if use_volume:
        volume_mount_path, volume_storage_class, volume_size = _validate_volume_fields(
            volume_mount_path, volume_storage_class, volume_size
        )
    if use_static:
        build_cmd, output_dir = _validate_static_fields(build_cmd, output_dir)
        static_env = _validate_static_env(static_env or {})

    # 유저 env가 켜진 dep의 자동 주입 키와 충돌하면 거절 (관리형 연결이 조용히 깨지는 것 방지).
    # 외부 서비스를 쓰려면 그 dep을 끄면 됨 — 그땐 시크릿이 없어 충돌 자체가 사라진다.
    if has_server and env_vars:
        collide = sorted(set(env_vars) & reserved_env_keys(db_type, use_redis, use_storage))
        if collide:
            raise ValueError(
                f"{', '.join(collide)} 는 선택한 의존성(DB·Redis·스토리지)이 자동 주입하는 "
                f"예약 키입니다. 환경변수에서 빼거나, 외부 서비스를 쓰려면 해당 의존성을 끄세요."
            )

    # 빌드 소스 검증 — Job YAML·git 인자로 들어가는 값 (source.py). 저장 전에 막아 이유를 화면에 보여준다.
    repo_url = _normalize_repo_url(repo_url)
    validate_repo_url(repo_url)
    validate_branch(branch)
    dockerfile_path = dockerfile_path.strip().removeprefix("./")
    if has_server:
        validate_repo_path(dockerfile_path, "Dockerfile 경로")
        validate_repo_path(project_path.strip("/"), "프로젝트 경로")
    if use_static:
        if static_repo_url.strip():
            validate_repo_url(_normalize_repo_url(static_repo_url))
        if static_branch.strip():
            validate_branch(static_branch.strip())
        validate_repo_path(static_project_path.strip("/"), "정적 사이트 경로")

    # 편집 권한 멤버는 앱이 이미 쓰는 저장소로만 재배포한다 (주인의 GitHub 연결이 주인의 다른 저장소까지 열려 있다)
    if allowed_repos is not None:
        used = [repo_url] + ([static_repo_url] if use_static and static_repo_url.strip() else [])
        for r in used:
            if repo_key(_normalize_repo_url(r)) not in allowed_repos:
                raise ValueError("편집 권한으로는 이 앱이 쓰던 저장소로만 배포할 수 있어요. 저장소를 바꾸려면 앱 주인이 해야 해요")

    # 빌더 제출 조건 — 안 맞으면 여기서 400 (아무것도 바꾸기 전). detect면 Dockerfile 경로를 확정한다.
    existing_app = app if app is not None else apps_service.get_user_app(db, user.id)
    build_mode, path = await v2.check_submit(
        user, runtime=runtime, repo_url=repo_url, branch=branch, build_mode=build_mode,
        dockerfile_path=dockerfile_path, use_static=use_static, init_dump_token=init_dump_token,
        project_path=project_path.strip("/"),
        static_repo_url=static_repo_url,
        app=existing_app,
        # 저장소는 앱 주인의 GitHub 연결로 받는다 (편집 권한 멤버가 배포해도 주인의 비공개 저장소를 받는다).
        # 첫 배포라 앱이 아직 없으면 배포하는 사람이 주인이 된다
        installation_id=(
            apps_service.repo_installation_id(db, existing_app) if existing_app is not None
            else user.github_installation_id
        ),
    )
    if build_mode == "auto":     # 경로는 프로젝트 서브디렉토리다 (감지로 정해졌을 수 있다)
        project_path = path
        validate_repo_path(project_path, "프로젝트 경로")
    else:
        dockerfile_path = path
        validate_repo_path(dockerfile_path, "Dockerfile 경로")
    app = existing_app if existing_app is not None else _resolve_app(name, repo_url, user, db)
    app_name = app.name
    image_owner = apps_service.namespace_hex(app.namespace)   # GHCR 경로 <hex8>/<app> — 빌더 검증이 ns의 hex8과 맞춘다

    # 슬롯 선언 저장 — 라우팅 규칙(_slot_hostnames)의 진실원.
    # 빌드 spawn 전에 확정해서 동시 빌드 둘 다 같은 desired state를 보게 한다.
    app.site_enabled = use_static
    db.commit()

    builds: list[Build] = []

    # --- 서버 슬롯 ---
    _cancel_stale_builds(db, app.id, slot="server")
    if has_server:
        build_id = uuid.uuid4().hex[:8]
        image = f"ghcr.io/{config.GHCR_USER}/{image_owner}/{app_name}:{build_id}"
        server_build = Build(
            build_id=build_id,
            repo_url=repo_url,
            branch=branch,
            image=image,
            app_name=app_name,
            port=port,
            runtime=runtime,
            user_id=user.id,
            app_id=app.id,
            namespace=app.namespace,
            db_type=db_type,
            use_redis=use_redis,
            use_storage=use_storage,
            volume_mount_path=volume_mount_path if use_volume else "",  # local 아니면 "" = 볼륨 비활성
            volume_storage_class=volume_storage_class,
            volume_size=volume_size,
            build_mode=build_mode,
            dockerfile_path=dockerfile_path,
            project_path=project_path.strip("/"),  # 앞뒤 슬래시 정리 — manifest에서 ${PROJECT_PATH:+/$PROJECT_PATH}로 결합
            last_event_seq=0,                      # v2 빌드 표시 겸 콜백 seq 시작점
        )
        builds.append(crud.create_build(db, server_build))
        spawn_background(v2.run_v2_build, build_id, env_vars or {}, init_dump_token)
    elif v2.has_server_deployed(db, app):
        # 서버 사용 안 함 — 서버 리소스는 Argo가 그리므로 빌더에 서버 슬롯을 내리게 한다 (PVC·버킷 보존).
        # 매 제출마다 보내서 직전 실패도 다음 제출에서 다시 시도된다
        spawn_background(v2.remove_server, app.namespace, user.id.hex[:8])

    # --- 정적 슬롯 ---
    _cancel_stale_builds(db, app.id, slot="static")
    if use_static:
        site_name = f"{app_name}-static"               # K8s 리소스 이름 (호스트는 {app} — 슬롯 규칙)
        s_repo = _normalize_repo_url(static_repo_url) if static_repo_url.strip() else repo_url
        build_id = uuid.uuid4().hex[:8]
        image = f"ghcr.io/{config.GHCR_USER}/{image_owner}/{site_name}:{build_id}"
        static_build = Build(
            build_id=build_id,
            repo_url=s_repo,
            branch=static_branch.strip() or branch,
            image=image,
            app_name=site_name,
            port=8080,                                 # nginx-unprivileged 고정
            runtime="static",
            user_id=user.id,
            app_id=app.id,
            namespace=app.namespace,
            db_type="none",
            use_redis=False,
            use_storage=False,
            build_mode="static",
            project_path=static_project_path.strip("/"),
            build_cmd=build_cmd,
            output_dir=output_dir,
            build_env=json.dumps(static_env) if static_env else None,
            last_event_seq=0,
        )
        builds.append(crud.create_build(db, static_build))
        spawn_background(v2.run_v2_build, build_id)
    # 정적을 끄는 배포는 서버 빌드가 static.enabled=false를 실어 보낸다 (별도 요청 없음)

    return builds


# 이력의 한 배포로 되돌린다 (빌더로 배포된 행만 — digest가 남아 있다). 새 배포 행을 만들어 이력에 남기고, 빌더에 set-image를 맡긴다.
# 진행 중이던 서버 배포는 대체된다 (재배포와 같은 규칙). 못 하면 ValueError, 대상이 없으면 LookupError.
async def start_rollback(db: Session, user: User, app: App, build_id: str) -> Build:
    target = crud.get_build(db, build_id, app_id=app.id)
    v2.check_rollback_target(app, target)

    _cancel_stale_builds(db, app.id, slot="server")
    new_id = uuid.uuid4().hex[:8]
    rollback = Build(
        build_id=new_id,
        repo_url=target.repo_url,
        branch=target.branch,
        image=target.image,                       # repo:tag@sha256:… — 그 배포가 올라갔던 그대로
        app_name=target.app_name,
        port=target.port,
        runtime=target.runtime,
        user_id=user.id,
        app_id=app.id,
        namespace=app.namespace,
        db_type=target.db_type,
        use_redis=target.use_redis,
        use_storage=target.use_storage,
        volume_mount_path=target.volume_mount_path,
        volume_storage_class=target.volume_storage_class,
        volume_size=target.volume_size,
        build_mode=target.build_mode,
        dockerfile_path=target.dockerfile_path,
        project_path=target.project_path,
        kind="build",
        status="deploying",
        rollback_of=target.build_id,
        last_event_seq=0,                         # v2 빌드 표시 겸 콜백 seq 시작점
    )
    crud.create_build(db, rollback)
    spawn_background(v2.run_v2_rollback, new_id)
    return rollback


# 의존성(DB·Redis)이 준비된 뒤에도 이만큼 더 죽으면 타임아웃을 기다리지 않고 실패로 본다.
# 첫 배포는 DB가 뜨기 전에 앱이 몇 번 죽는 게 정상이라, 그동안의 재시작은 세지 않는다.
CRASH_RESTART_LIMIT = 2
# 기다려도 스스로 풀리지 않는 컨테이너 대기 사유 — 보는 즉시 실패.
_FATAL_WAITING = {
    "ImagePullBackOff": "이미지를 받아오지 못했어요",
    "InvalidImageName": "이미지 이름이 올바르지 않아요",
    "CreateContainerConfigError": "컨테이너 설정에 오류가 있어요",
}
_DEP_SELECTOR = "component in (mysql,postgres,redis)"
APP_LOG_TAIL_LINES = 80


# 순수 판정 — 앱 Pod 목록 + 의존성 Pod 목록 → 실패 사유 또는 None. crash_base는 호출 사이에 이어 쓴다.
# limit·message는 배포 뒤 감시(v2 postwatch)가 바꿔 쓴다 — 이미 Ready였던 앱은 재시작 1회부터 비정상.
def _crash_reason(pods, dep_pods, crash_base: dict[str, int], limit: int = CRASH_RESTART_LIMIT,
                  message=None) -> str | None:
    deps_ready = all(_pod_ready(p) for p in dep_pods)
    for pod in pods:
        if pod.metadata.deletion_timestamp:
            continue
        for cs in pod.status.container_statuses or []:
            if cs.name != "app":
                continue
            waiting = cs.state.waiting.reason if cs.state and cs.state.waiting else None
            if waiting in _FATAL_WAITING:
                return f"{_FATAL_WAITING[waiting]} ({waiting})."
            if not deps_ready:
                continue
            restarts = cs.restart_count or 0
            base = crash_base.setdefault(pod.metadata.name, restarts)
            if restarts - base >= limit:
                return (message or _crash_message)(cs)
    return None


def _pod_ready(pod) -> bool:
    return any(
        c.type == "Ready" and c.status == "True" for c in (pod.status.conditions or [])
    )


def _crash_message(cs) -> str:
    return f"{_crash_head(cs)} 재시작이 반복돼 배포를 멈췄어요. 로그 끝의 앱 로그를 확인하세요."


# 마지막 종료 사유로 한 문장 (배포 중·배포 뒤 판정이 같이 쓴다)
def _crash_head(cs) -> str:
    term = cs.last_state.terminated if cs.last_state else None
    if term and term.reason == "OOMKilled":
        head = "메모리가 부족해 앱이 강제 종료됐어요 (OOMKilled)."
    elif term and term.exit_code in (137, 143):
        head = (
            f"앱이 제시간에 응답하지 않아 플랫폼이 재시작했어요 (종료 코드 {term.exit_code}). "
            "포트 설정을 확인하세요."
        )
    elif term:
        head = f"앱이 시작 중에 종료됐어요 (종료 코드 {term.exit_code})."
    else:
        head = "앱이 계속 재시작되고 있어요."
    return head


# 실패한 앱의 런타임 로그 꼬리 — 빌드 로그 끝에 붙일 블록. 없으면 "".
def _app_log_tail(build: Build) -> str:
    try:
        logs = runtime_logs.fetch_app_logs(build.tenant_id, build.app_name)
    except Exception:
        return ""
    lines = logs.get("previous") or logs.get("current") or []
    if not lines:
        return ""
    label = "마지막으로 종료된 인스턴스" if logs.get("previous") else "현재 인스턴스"
    tail = "\n".join(lines[-APP_LOG_TAIL_LINES:])
    return f"\n\n=== 앱 로그 ({label}) ===\n{tail}\n"


# build_id들의 "사용자 체감" 소요시간 조회 (BuildRecord append-only 기록에서). build_id→{total_seconds} dict.
# ★ UI "소요"로 노출하는 값 = deploy_ready_at − started_at (rollout 완료 = 사용자가 앱을 본 시점).
#   저장 컬럼 total_seconds(= finished_at − started_at)는 early-trigger 경로에서 뒤따르는 백그라운드
#   cache export 회수(reap)까지 포함하는데, 그건 사용자가 실제로 안 기다린 시간이라 체감 지표론 부풀린다.
#   그래서 노출값은 deploy_ready 기준으로 계산하고, deploy_ready_at이 없으면(구 레코드/실패/미완/
#   env_change) 저장된 total_seconds로 폴백한다. (저장 컬럼 자체는 운영 분석용으로 그대로 둔다.)
# 단계별(nixpacks/buildkit)은 내부 도구명이라 사용자에게 안 보냄 — BuildRecord엔 그대로 남아 운영 분석용.
# env_change row 등 BuildRecord가 없는 빌드는 키 자체가 없음 → 호출부에서 빈 dict로 fallback.
def get_build_timings(db: Session, build_ids: list[str]) -> dict[str, dict]:
    if not build_ids:
        return {}
    rows = (
        db.query(
            BuildRecord.build_id,
            BuildRecord.started_at,
            BuildRecord.deploy_ready_at,
            BuildRecord.total_seconds,
        )
        .filter(BuildRecord.build_id.in_(build_ids))
        .all()
    )
    out: dict[str, dict] = {}
    for build_id, started, ready, total in rows:
        # 사용자 체감 = 빌드 시작 → rollout 완료. 둘 다 있을 때만 계산, 아니면 저장값 폴백.
        secs = (ready - started).total_seconds() if (started and ready) else total
        out[build_id] = {"total_seconds": secs}
    return out


# 텍스트에서 marker 사이 추출 — nixpacks init container 로그 파싱용
def _extract_between(text: str, start: str, end: str) -> str | None:
    s = text.find(start)
    if s < 0:
        return None
    s += len(start)
    e = text.find(end, s)
    if e < 0:
        return None
    return text[s:e].strip("\n")
