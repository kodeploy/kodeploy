"""빌드→배포 파이프라인 오케스트레이션 — 제출·빌드 Job·배포·teardown·기록."""

import asyncio
import json
import logging
import os.path
import re
import threading
import time
import uuid
from datetime import datetime, timezone

from kubernetes.client.exceptions import ApiException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import config
from app.auth import github_app
from app.auth.model import User
from app.deploy import crud
from app.deploy.console import logs as runtime_logs, snapshots
from app.deploy.stack import env as env_module, manifests, r2
from app.deploy.build import diagnose
from app.deploy.build.github import _detect_build, _fetch_github_raw
from app.deploy.build.naming import _normalize_repo_url, _resolve_app_name
from app.deploy.build.validation import (
    _validate_static_env,
    _validate_static_fields,
    _validate_volume_fields,
    reserved_env_keys,
)
from app.deploy.model import Build, BuildRecord
from app.deploy.routing.hostnames import _reconcile_route_hostnames, _slot_hostnames
from app.deploy.stack.resources import (
    _apply_db,
    _apply_deployment,
    _apply_redis,
    _apply_storage,
    _apply_volume,
    _ensure_tenant_ns,
    _teardown_one_db,
    _teardown_redis,
    _teardown_storage,
)
from app.shared import k8s
from app.shared.db import SessionLocal

logger = logging.getLogger(__name__)

# ★ 이 마커 정규식 3개(_PUSH_DONE_RE / _EXPORT_IMAGE_RE / _IMPORT_HIT_RE)는 벤치·측정 스크립트의
#   kodeploy-bench/markers.py와 "동일 문자열"이어야 한다. 여기(운영)가 실제 트리거를 쏘는 진실원이라,
#   바꾸면 markers.py도 반드시 같이 바꿀 것. (markers.py가 이 파일을 역으로 가리킨다.)
# early-trigger — buildkit이 이미지 매니페스트 push를 끝낸 마커. 이게 찍히면 이미지가
# 레지스트리에 있다 = 배포 가능. 캐시를 켜면 매니페스트 push가 이미지+캐시로 2번 찍히는데,
# 로그에 먼저 나타나는 건 이미지 쪽(cache export는 그 뒤에 끝남)이라 첫 감지가 곧 이미지다.
_PUSH_DONE_RE = re.compile(r"pushing manifest .*\bdone\b")
# 이미지 레이어 export 시작 마커 — 이 뒤에 처음 오는 push 완료가 곧 "이미지" 매니페스트 push다.
# 캐시를 켜면 매니페스트 push가 이미지+캐시로 2번 찍히므로, 이 앵커 없이 첫 매치만 잡으면
# (v0.27+ 병렬 export의 인터리브 로그에서) 캐시 push를 오탐할 여지가 있다. 벤치 parse.py와 동일 방어.
_EXPORT_IMAGE_RE = re.compile(r"exporting to image")
# 레지스트리 캐시 import 히트 여부 — 이게 없으면 cold(clean) 빌드.
_IMPORT_HIT_RE = re.compile(r"importing cache manifest")


# 백그라운드 오케스트레이션을 전용 daemon 스레드의 자체 이벤트 루프에서 실행.
# _run_build 등은 async지만 내부에서 동기 K8s/DB 클라이언트 + _ensure_tenant_ns의
# time.sleep(최대 60초)을 쓴다. uvicorn 워커 1개 = 메인 루프 1개라, 메인 루프에 올리면
# 빌드 도는 동안 /healthz 포함 전 유저 요청이 멈춘다. 스레드마다 asyncio.run으로 독립
# 루프를 띄워 블로킹을 그 스레드에 격리 — 코루틴 로직(빌드→배포→대기)은 그대로 보존.
# 빌드는 I/O 바운드라 빌드당 스레드 1개로 충분. create_task와 달리 GC로 사라질 위험도 없음.
def spawn_background(coro_func, *args) -> None:
    threading.Thread(
        target=lambda: asyncio.run(coro_func(*args)),
        daemon=True,
    ).start()


# env_change row의 status를 Pod 결과에 따라 갱신.
# - 빌드 row는 영구 기록(안 건드림). 이 함수는 특정 env_change row(build_id로 식별)에만 적용.
# - ready 되면 "running", 5분 타임아웃이면 "failed"로.
async def watch_env_change_rollout(
    user_id: uuid.UUID, app_name: str, tenant_id: str, event_build_id: str,
) -> None:
    deadline = time.time() + ROLLOUT_TIMEOUT_SECONDS
    apps = k8s.apps_v1()
    while time.time() < deadline:
        try:
            dep = apps.read_namespaced_deployment_status(
                name=app_name, namespace=tenant_id,
            )
        except ApiException:
            await asyncio.sleep(5)
            continue
        ready = dep.status.ready_replicas or 0
        desired = dep.spec.replicas or 1
        observed = dep.status.observed_generation or 0
        current = dep.metadata.generation or 0
        if ready >= desired and observed >= current:
            _update_event_status(user_id, event_build_id, "running")
            return
        await asyncio.sleep(5)
    _update_event_status(
        user_id, event_build_id, "failed", error="Pod 시작 실패 (타임아웃)",
    )


def _update_event_status(
    user_id: uuid.UUID, build_id: str, status: str, error: str | None = None,
) -> None:
    db = SessionLocal()
    try:
        row = (
            db.query(Build)
            .filter_by(build_id=build_id, user_id=user_id)
            .first()
        )
        if row:
            row.status = status
            if error is not None:
                row.error = error
            db.commit()
    finally:
        db.close()


# 실패 진단을 build row(유저 대면 진단문)와 record(계측)에 각각 붙인다 (best-effort).
# 호출 시점엔 status/error가 이미 커밋돼 있으므로, 진단이 실패하든 API가 죽어 있든 배포
# 결과는 그대로 남는다 — r2/domains 정리 실패를 삼키는 것과 같은 철학. 동기 호출인 이유:
# _run_build는 이미 빌드 전용 스레드에서 돌고(spawn_background), _ensure_tenant_ns의
# time.sleep처럼 블로킹이 그 스레드에 격리된다. to_thread로 떼면 ORM 객체를 다른 스레드에서
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
def _cancel_stale_builds(db: Session, user_id: uuid.UUID, slot: str) -> None:
    q = db.query(Build).filter(
        Build.user_id == user_id,
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
        _cleanup_build_job(build.build_id, build.user_id_str)
    db.commit()


def _cleanup_build_job(build_id: str, user_id_str: str) -> None:
    job_name = _build_job_name(build_id, user_id_str)
    try:
        k8s.batch_v1().delete_namespaced_job(
            name=job_name,
            namespace=config.BUILD_NAMESPACE,
            propagation_policy="Background",
        )
    except ApiException as e:
        if e.status != 404:
            raise
    _cleanup_git_auth(build_id)


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


# 배포 제출 — 원하는 스택(서버 슬롯 + 정적 슬롯)을 선언받아 슬롯별 빌드/teardown을 spawn.
# user 객체로 받음 — _resolve_app_name이 user.app_name을 읽고/쓰기 위해.
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

    repo_url = _normalize_repo_url(repo_url)
    app_name = _resolve_app_name(name, repo_url, user, db)

    # 슬롯 선언 저장 — 라우팅 규칙(_slot_hostnames)의 진실원.
    # 빌드 spawn 전에 확정해서 동시 빌드 둘 다 같은 desired state를 보게 한다.
    user.site_enabled = use_static
    db.commit()

    builds: list[Build] = []

    # --- 서버 슬롯 ---
    _cancel_stale_builds(db, user.id, slot="server")
    if has_server:
        build_id = uuid.uuid4().hex[:8]
        image = f"ghcr.io/{config.GHCR_USER}/{user.id.hex[:8]}/{app_name}:{build_id}"
        server_build = Build(
            build_id=build_id,
            repo_url=repo_url,
            branch=branch,
            image=image,
            app_name=app_name,
            port=port,
            runtime=runtime,
            user_id=user.id,
            db_type=db_type,
            use_redis=use_redis,
            use_storage=use_storage,
            volume_mount_path=volume_mount_path if use_volume else "",  # local 아니면 "" = 볼륨 비활성
            volume_storage_class=volume_storage_class,
            volume_size=volume_size,
            build_mode=build_mode,
            dockerfile_path=dockerfile_path,
            project_path=project_path.strip("/"),  # 앞뒤 슬래시 정리 — manifest에서 ${PROJECT_PATH:+/$PROJECT_PATH}로 결합
        )
        builds.append(crud.create_build(db, server_build))
        spawn_background(_run_build, build_id, env_vars or {}, init_dump_token)
    else:
        # 서버 사용 안 함 — 기존 서버 리소스 + deps 정리 (PVC·버킷 보존). 매 제출마다
        # spawn이라 직전 실패도 다음 제출에서 재시도되는 self-healing.
        spawn_background(_teardown_server, user.id)

    # --- 정적 슬롯 ---
    _cancel_stale_builds(db, user.id, slot="static")
    if use_static:
        site_name = f"{app_name}-static"               # K8s 리소스 이름 (호스트는 {app} — 슬롯 규칙)
        s_repo = _normalize_repo_url(static_repo_url) if static_repo_url.strip() else repo_url
        build_id = uuid.uuid4().hex[:8]
        image = f"ghcr.io/{config.GHCR_USER}/{user.id.hex[:8]}/{site_name}:{build_id}"
        static_build = Build(
            build_id=build_id,
            repo_url=s_repo,
            branch=static_branch.strip() or branch,
            image=image,
            app_name=site_name,
            port=8080,                                 # nginx-unprivileged 고정
            runtime="static",
            user_id=user.id,
            db_type="none",
            use_redis=False,
            use_storage=False,
            build_mode="static",
            project_path=static_project_path.strip("/"),
            build_cmd=build_cmd,
            output_dir=output_dir,
            build_env=json.dumps(static_env) if static_env else None,
        )
        builds.append(crud.create_build(db, static_build))
        spawn_background(_run_build, build_id)
    else:
        spawn_background(_teardown_static, user.id)

    return builds


# 서버 슬롯 teardown — Deployment/Service/Route 쌍 + deps(mysql/postgres/redis/r2) 정리.
# PVC·버킷·Secret은 보존 (DB 토글 off와 동일 철학 — 다시 켜면 데이터 복원).
# best-effort: 실패는 삼킴 — 슬롯 off인 제출마다 다시 spawn되므로 다음 기회에 재시도.
async def _teardown_server(user_id: uuid.UUID) -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(id=user_id).first()
        if not user or not user.app_name:
            return
        app_name = user.app_name
        ns = f"tenant-{user_id.hex[:8]}"
        apps = k8s.apps_v1()
        core = k8s.core_v1()
        custom = k8s.custom()
        for call in (
            lambda: apps.delete_namespaced_deployment(name=app_name, namespace=ns),
            lambda: core.delete_namespaced_service(name=app_name, namespace=ns),
            lambda: custom.delete_namespaced_custom_object(
                group="gateway.networking.k8s.io", version="v1", namespace=ns,
                plural="httproutes", name=app_name),
            lambda: custom.delete_namespaced_custom_object(
                group="gateway.networking.k8s.io", version="v1", namespace=ns,
                plural="httproutes", name=f"{app_name}-redirect"),
        ):
            try:
                call()
            except ApiException as e:
                if e.status != 404:
                    raise
        _teardown_one_db(ns, "mysql")
        _teardown_one_db(ns, "postgres")
        _teardown_redis(ns)
        _teardown_storage(ns)
    except ApiException:
        pass
    finally:
        db.close()


# 정적 슬롯 teardown — 사이트 Deployment/Service/Route 쌍 삭제 + 서버 hostnames 원복.
async def _teardown_static(user_id: uuid.UUID) -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(id=user_id).first()
        if not user or not user.app_name:
            return
        site_name = f"{user.app_name}-static"
        ns = f"tenant-{user_id.hex[:8]}"
        apps = k8s.apps_v1()
        core = k8s.core_v1()
        custom = k8s.custom()
        for call in (
            lambda: apps.delete_namespaced_deployment(name=site_name, namespace=ns),
            lambda: core.delete_namespaced_service(name=site_name, namespace=ns),
            lambda: custom.delete_namespaced_custom_object(
                group="gateway.networking.k8s.io", version="v1", namespace=ns,
                plural="httproutes", name=site_name),
            lambda: custom.delete_namespaced_custom_object(
                group="gateway.networking.k8s.io", version="v1", namespace=ns,
                plural="httproutes", name=f"{site_name}-redirect"),
        ):
            try:
                call()
            except ApiException as e:
                if e.status != 404:
                    raise
        # {app}.kodeploy.com·커스텀 도메인이 서버로 복귀 (site_enabled=false 기준 재계산)
        _reconcile_route_hostnames(user)
    except ApiException:
        pass
    finally:
        db.close()


# BuildKit Job 이름 (template과 동일 규칙 — _wait_for_job/로그 조회 시 사용)
def _build_job_name(build_id: str, user_id_str: str) -> str:
    return f"build-{user_id_str[:8]}-{build_id}"


# 백그라운드 빌드 코루틴 (Job 생성 → 폴링 → 성공 시 배포 / 실패 시 로그 저장)
# event.set() 또는 task 완료 중 먼저 오는 것까지만 대기 (task는 취소하지 않음 — 호출부 소유).
async def _wait_first(event: asyncio.Event, task: asyncio.Task) -> None:
    waiter = asyncio.create_task(event.wait())
    try:
        await asyncio.wait({waiter, task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        if not waiter.done():
            waiter.cancel()


# import 캐시를 못 맞췄나(clean 빌드). 캐시 OFF면 판정 무의미 → None (컬럼 NULL).
def _is_cache_cold(logs: str | None) -> bool | None:
    if not config.BUILD_REGISTRY_CACHE_ENABLED:
        return None
    return not bool(_IMPORT_HIT_RE.search(logs or ""))


# Job 종료 후 최종 로그 + 단계별 소요시간 + cache_cold를 build·record에 반영 (commit은 호출부).
# early/기존 경로 양쪽이 공유 — 로그를 authoritative하게 덮고 auto 모드면 생성 Dockerfile도 추출.
def _finalize_build_artifacts(
    build: Build,
    record: BuildRecord,
    push_at: datetime | None = None,
    job_ended_at: datetime | None = None,
) -> None:
    phases = _get_build_phase_seconds(build.build_id)
    record.nixpacks_seconds = phases.get("nixpacks_seconds")
    record.buildkit_seconds = phases.get("buildkit_seconds")
    build.logs = _combined_job_logs(build.build_id, build.build_mode)
    if build.build_mode == "auto":
        init_logs = _get_init_container_logs(build.build_id, "nixpacks")
        if init_logs:
            extracted = _extract_between(
                init_logs,
                "===KODEPLOY_DOCKERFILE_START===",
                "===KODEPLOY_DOCKERFILE_END===",
            )
            if extracted:
                build.dockerfile_content = extracted
    record.cache_cold = _is_cache_cold(build.logs)
    # early-trigger 원본 시각만 저장 — 노출시간(= job_ended_at − push_done_at)은 파생값이라 뷰/쿼리에서 뺀다.
    if push_at is not None:
        record.push_done_at = push_at
    if job_ended_at is not None:
        record.job_ended_at = job_ended_at


async def _run_build(
    build_id: str,
    initial_env: dict[str, str] | None = None,
    init_dump_token: str | None = None,
) -> None:
    db = SessionLocal()
    try:
        build = crud.get_build(db, build_id)
        if not build:
            return
        if build.status == "cancelled":
            return

        def _check_cancelled() -> bool:
            db.refresh(build)
            return build.status == "cancelled"

        # 빌드 행위 영구 기록 (append-only — delete_app에도 안 지워짐).
        # seq = 이 유저의 N번째 빌드. 기록 테이블 카운트 기준이라 앱 삭제 후에도 이어진다.
        # started_at은 aware datetime 로컬 변수로 들고 있음 — commit 후 ORM 재로드되면
        # naive로 바뀌어 total_seconds 계산(aware-naive 빼기)이 깨지므로.
        started_at = datetime.now(timezone.utc)
        # 빌드 행위가 끝난 시각. AI 진단은 결과가 확정된 뒤 도는 사후 분석이라 빌드
        # 소요시간에 섞이면 안 되므로, 진단을 부르기 직전에 여기서 먼저 찍는다.
        # finally의 마감은 이미 찍혀 있으면 그대로 쓴다(최초 호출이 이긴다).
        # ORM 컬럼이 아니라 로컬 변수인 이유는 started_at과 같다 — 게다가 진단이 실패해
        # db.rollback()이 나도 이 값은 살아남아야 한다.
        finished_at: datetime | None = None

        def _stamp_finished() -> None:
            nonlocal finished_at
            if finished_at is None:
                finished_at = datetime.now(timezone.utc)

        seq = (
            db.query(func.count(BuildRecord.id))
            .filter(BuildRecord.user_id == build.user_id)
            .scalar()
            or 0
        ) + 1
        record = BuildRecord(
            build_id=build.build_id,
            user_id=build.user_id,
            seq=seq,
            app_name=build.app_name,
            runtime=build.runtime,
            build_mode=build.build_mode,
            started_at=started_at,
        )
        db.add(record)
        db.commit()

        try:
            # early-trigger: tail/job task 핸들 — 조기 return·예외 경로에서 finally가 정리하도록 선바인딩.
            stop_tail = None
            tail_task = None
            job_task = None

            build.status = "building"
            db.commit()

            # private repo면 이 빌드용 git 토큰 Secret 발급 (public이거나 App 미설정/미설치면 "" → 토큰 없이 clone).
            git_auth_secret = _provision_git_auth(build)

            # build_mode="detect" 해소 — Dockerfile 있으면 dockerfile / 없으면 auto(nixpacks).
            # GitHub tree API로 감지(깊이 3). private은 위에서 발급한 installation 토큰 캐시 재사용.
            if build.build_mode == "detect":
                mode, path = await asyncio.to_thread(_detect_build, build)
                build.build_mode = mode
                if mode == "dockerfile":
                    build.dockerfile_path = path
                else:                          # auto — 감지한 nixpacks 디렉토리를 project_path로
                    build.project_path = path
                db.commit()

            # 빌드 시작 전에 Dockerfile 텍스트를 DB에 보존 (UI 노출 + AI 분석용).
            # dockerfile 모드: GitHub raw fetch. auto 모드: 빌드 후 init container 로그에서 추출.
            if build.build_mode == "dockerfile":
                content = await asyncio.to_thread(
                    _fetch_github_raw, build.repo_url, build.branch, build.dockerfile_path
                )
                if content is not None:
                    build.dockerfile_content = content
                    db.commit()

            if build.runtime == "static":
                # 플랫폼 생성 Dockerfile — repo엔 없음. 빌드 전에 DB 보존 (UI 노출 + 재현성).
                dockerfile_text = manifests.static_dockerfile(
                    build.build_cmd or "",
                    build.output_dir or "",
                    build_env=json.loads(build.build_env) if build.build_env else None,
                )
                build.dockerfile_content = dockerfile_text
                db.commit()
                job = manifests.static_buildkit_job(
                    build_id=build.build_id,
                    user_id=build.user_id_str,
                    image=build.image,
                    repo_url=build.repo_url,
                    branch=build.branch,
                    dockerfile_text=dockerfile_text,
                    project_path=build.project_path,
                    git_auth_secret=git_auth_secret,
                )
            elif build.build_mode == "auto":
                job = manifests.nixpacks_buildkit_job(
                    build_id=build.build_id,
                    user_id=build.user_id_str,
                    image=build.image,
                    repo_url=build.repo_url,
                    branch=build.branch,
                    project_path=build.project_path,
                    git_auth_secret=git_auth_secret,
                )
            else:
                # dockerfile_path를 subdir + filename으로 분리 (BuildKit context를 subdir로 좁힘).
                dockerfile_subdir, dockerfile_filename = os.path.split(
                    build.dockerfile_path or "Dockerfile"
                )
                job = manifests.buildkit_job(
                    build_id=build.build_id,
                    user_id=build.user_id_str,
                    image=build.image,
                    repo_url=build.repo_url,
                    branch=build.branch,
                    dockerfile_subdir=dockerfile_subdir,
                    dockerfile_filename=dockerfile_filename,
                    git_auth_secret=git_auth_secret,
                )

            k8s.batch_v1().create_namespaced_job(
                namespace=config.BUILD_NAMESPACE, body=job
            )

            job_name = _build_job_name(build.build_id, build.user_id_str)

            # 빌드 도는 동안 1초마다 현재 로그를 build.logs에 흘려넣어 프론트가 실시간으로 본다.
            # job 폴링과 동시 task로 돌고, _wait_for_job이 끝나면 stop으로 종료시킨 뒤
            # 아래에서 최종 로그를 authoritative하게 덮는다.
            stop_tail = asyncio.Event()
            push_done = asyncio.Event()   # early-trigger: 이미지 push 완료 시 tailer가 set
            push_marker = {}              # tailer가 마커 감지 시각을 push_marker["at"]에 stamp
            tail_task = asyncio.create_task(
                _tail_build_logs(
                    build.build_id, build.build_mode, stop_tail, push_done, push_marker
                )
            )
            job_task = asyncio.create_task(_wait_for_job(job_name))

            # early-trigger: push 마커를 보면 cache export를 안 기다리고 곧장 배포로 넘어간다.
            # 이미지는 이미 레지스트리에 있어 배포는 유효 — Job(export)은 뒤에서 계속 돌고,
            # 배포 판정이 끝난 뒤 아래 reap에서 회수한다. 마커를 못 보면 기존 경로로 자동 폴백.
            # (플래그 OFF면 _wait_first를 건너뛰어 기존 동작과 완전히 동일.)
            triggered_early = False
            if config.EARLY_TRIGGER_ENABLED:
                await _wait_first(push_done, job_task)
                triggered_early = push_done.is_set()
            record.triggered_early = triggered_early  # A/B 축 — early로 빠졌나 폴백/플래그OFF인가

            if triggered_early:
                # 이미지 push 완료 = 빌드 성공 확정. 최종 로그/단계 기록은 reap 시점으로 미룬다
                # (그 사이 tailer가 export 로그까지 계속 흘려준다).
                success = True
            else:
                # 기존 경로: Job 완료까지 대기 → 최종 로그/단계 기록.
                try:
                    success, job_ended_at = await job_task
                finally:
                    stop_tail.set()
                    await tail_task
                # 단계별 소요시간 + 최종 로그(init+main) + auto Dockerfile 추출 + cache_cold + job_ended_at.
                # 빌드 실패가 init 단계여도 main/​init 로그를 함께 남긴다.
                _finalize_build_artifacts(
                    build, record, push_marker.get("at"), job_ended_at
                )
                db.commit()

                if not success:
                    _mark_failed(db, build, record, "빌드 실패")
                    # 로그가 최종본으로 확정된 뒤(위 _finalize_build_artifacts) 진단.
                    _stamp_finished()  # 진단 시간이 빌드 소요시간에 섞이지 않게 먼저 마감
                    _attach_diagnosis(db, build, record, diagnose.build_failure)
                    return

            if _check_cancelled():
                return

            build.status = "deploying"
            db.commit()
            _ensure_tenant_ns(build)

            # deps(환경변수/DB/Redis/스토리지/초기 덤프)는 서버 슬롯 선언 — static 빌드가
            # 건드리면 같은 ns의 서버 deps를 teardown해 버리므로 (db_type=none) 반드시 skip.
            if build.runtime != "static":
                # 사용자가 폼에서 보낸 환경변수 — ns 만든 직후, Deployment apply 전.
                # Deployment는 아직 없을 수 있어서 set_env 안의 annotation patch는 404 swallow.
                # Pod이 새로 만들어질 때 Secret 자연 mount.
                if initial_env:
                    try:
                        env_module.set_env(build.tenant_id, build.app_name, initial_env)
                    except ValueError as e:
                        build.error = f"환경변수 검증 실패: {e}"
                        db.commit()

                # DB 토글 — 선택된 db (mysql/postgres) 프로비저닝 + 다른 db 정리.
                # PVC/Secret은 보존 — 같은 db로 다시 토글 시 데이터 자연 복원.
                _apply_db(build, build.db_type or "none")
                _apply_redis(build)
                _apply_storage(build)
                _apply_volume(build)  # 영속저장소 local — PVC 생성(있을 때). Deployment apply 전에 (마운트 대상 보장)

                # 초기 데이터 자동 복원 — 폼에서 .sql(.gz) 첨부 + DB(mysql/postgres) 선택 시.
                # 앱이 채워진 DB 위에서 시작하도록 Deployment apply 전에 DB Ready 대기 후 복원.
                if init_dump_token:
                    if build.db_type in ("mysql", "postgres"):
                        if await snapshots.wait_db_ready(build.tenant_id, build.db_type):
                            try:
                                await snapshots.restore_staged(
                                    build.tenant_id, init_dump_token
                                )
                            except snapshots.SnapshotError as e:
                                build.error = f"초기 데이터 복원 실패: {e}"
                                db.commit()
                        else:
                            snapshots.discard_staged(init_dump_token)
                            build.error = "초기 데이터 복원 실패: DB 준비 타임아웃"
                            db.commit()
                    else:
                        # DB 없는데 토큰만 온 경우 — 임시 파일만 정리.
                        snapshots.discard_staged(init_dump_token)

            # 슬롯 규칙으로 이 빌드 route의 hostnames 계산 (User.site_enabled가 진실원).
            owner = db.query(User).filter_by(id=build.user_id).first()
            if not owner:
                return  # 빌드 도중 유저 삭제 — 배포 의미 없음
            if build.runtime == "static" and not owner.site_enabled:
                return  # 빌드 도중 정적 슬롯 해제 — teardown이 정리 중, apply하면 부활시킴
            server_hosts, site_hosts = _slot_hostnames(owner)
            hostnames = site_hosts if build.runtime == "static" else server_hosts

            record.deploy_started_at = datetime.now(timezone.utc)  # Deployment apply 직전
            _apply_deployment(build, hostnames)

            # 전체 route(서버 쌍 + 정적 쌍) hostnames reconcile — 슬롯 전환·커스텀 도메인·
            # 수동 drift가 이 시점에 DB 선언값으로 복원된다.
            _reconcile_route_hostnames(owner)

            problem = await _wait_for_rollout(build.app_name, build.tenant_id)
            if _check_cancelled():
                return
            app_log_tail = ""
            if problem is None:
                record.deploy_ready_at = datetime.now(timezone.utc)  # rollout 완료 = 사용자 대기 종료
                build.status = "running"
                db.commit()
            else:
                # 크래시한 인스턴스 로그는 지금 잡아 둔다 — 아래 reap이 cache export를 기다리는 사이
                # 재배포로 Pod이 사라질 수 있다. 붙이는 건 로그가 최종본으로 확정된 뒤(아래 failed 블록).
                app_log_tail = _app_log_tail(build)
                _mark_failed(db, build, record, problem)

            # early-trigger reap: 배포 판정이 끝났다. 뒤에서 돌던 Job(cache export)을 회수한다.
            # ★ 델타2 — Job이 실패해도 build.status를 덮지 않는다. push 마커를 봤으니 이미지는
            #   레지스트리에 있고 배포는 이미 유효하다. Job 실패 = cache export만 실패 → 다음 빌드가
            #   느려질 뿐, 이번 배포는 정상. best-effort 경고 + 지표로만 남긴다.
            #   (rollout 자체가 실패해 status가 "failed"인 경우는 export와 무관한 진짜 실패라 그대로 둔다.)
            if triggered_early:
                try:
                    # job_ended_at은 _wait_for_job이 종료를 감지한 순간 찍은 값 —
                    # await 반환(배포 후) 시각이 아니라 실제 Job 종료 시각이다.
                    export_ok, job_ended_at = await job_task
                finally:
                    stop_tail.set()
                    await tail_task
                # export 포함 최종 로그 + cache_cold + job_ended_at(노출시간은 파생: job_ended_at − push_done_at)
                _finalize_build_artifacts(
                    build, record, push_marker.get("at"), job_ended_at
                )
                if not export_ok:
                    record.cache_export_failed = True
                    logger.warning(
                        "build %s: cache export 실패/미완(Job failed) — 배포는 정상, "
                        "다음 빌드가 느려질 수 있음",
                        build_id,
                    )
                db.commit()

            # 이미지는 떴는데 Pod이 안 뜬 경우 — 이 플랫폼에서 가장 흔한 실패 유형이라
            # (포트 불일치·비-root 위반·부팅 크래시) 빌드 실패와 같은 비중으로 다룬다.
            # 재료가 빌드 로그가 아니라 런타임 로그라 진단 함수가 다르다.
            # reap 뒤에 두는 이유: cache export 회수를 API 호출만큼 지연시키지 않으려고.
            if build.status == "failed":
                _stamp_finished()  # 진단 시간이 빌드 소요시간에 섞이지 않게 먼저 마감
                # 진행 화면은 이 로그 하나만 보여 준다 — 앱 로그가 없으면 크래시 원인을 화면에서 못 본다.
                if app_log_tail:
                    build.logs = (build.logs or "").rstrip() + app_log_tail
                    db.commit()
                _attach_diagnosis(db, build, record, diagnose.rollout_failure)

        except Exception as e:
            db.rollback()
            db.refresh(build)
            if build.status != "cancelled":
                build.status = "failed"
                build.error = f"오케스트레이션 에러: {e}"
            # 실패를 표시한 뒤 진단까지 못 가고 예외로 빠진 경우 — "분석 중"을 닫는다.
            if build.ai_status == "pending":
                build.ai_status = "done"
            db.commit()
        finally:
            # early-trigger: 조기 return·예외로 tail/job task가 아직 살아있으면 정리.
            # stop_tail로 tailer를 깨우고 남은 task는 cancel — 이 스레드의 asyncio.run이
            # teardown에서 취소된 task를 수거한다(고아 task/미완 commit 방지). 정상 경로에선
            # 이미 await로 끝나 있어(.done()) 아무 일도 안 한다.
            if stop_tail is not None:
                stop_tail.set()
            for _t in (tail_task, job_task):
                if _t is not None and not _t.done():
                    _t.cancel()
            # 어떤 경로(성공/실패/취소/예외)로 끝나든 기록 마감.
            # 기록 실패가 빌드 흐름이나 세션 정리를 막지 않게 자체 예외는 삼킨다.
            try:
                _stamp_finished()  # 진단 경로에서 이미 찍혔으면 그 값을 쓴다
                record.finished_at = finished_at
                record.total_seconds = (finished_at - started_at).total_seconds()
                record.status = build.status
                record.error = build.error
                db.commit()
            except Exception:
                db.rollback()
            # private repo 토큰 Secret 정리 (어떤 경로로 끝나든 — 토큰 자체도 1h 만료라 이중 안전).
            try:
                _cleanup_git_auth(build_id)
            except ApiException:
                pass
    except Exception:
        pass
    finally:
        db.close()


# Job 완료까지 3초 간격 폴링. (성공/실패, 종료 감지 시각)을 반환한다.
# 기본 대기 한계 = Job 자신의 activeDeadlineSeconds + 여유. Job은 그 시각에 반드시 종료상태(성공/
# DeadlineExceeded 실패)에 이르므로, 그 전에 폴링을 포기하지 않아야 반환값이 Job의 실제 결말과 일치한다.
# early-trigger reap이 이 값으로 export 성공/실패를 판정하는데, 더 짧게 잡으면 "아직 export 중"을
# 실패로 오탐한다(대형 프로젝트의 긴 export). 빌드 시작 시점부터 잰다.
# ★ 두 번째 반환값(종료 시각)을 여기서 찍는 이유: early-trigger 경로는 push 마커로 배포를 먼저
#   시작하고 이 task는 배포 도중 종료를 감지해 끝난다. reap에서 await 반환 뒤에 now()를 찍으면
#   그 사이 배포(rollout) 시간이 통째로 섞여 노출시간(job_ended_at − push_done_at)이 부풀려진다.
#   감지 순간에 찍어야 실제 Job 종료 시각이다(폴링 간격 3초 내 오차).
async def _wait_for_job(
    job_name: str, timeout: float | None = None
) -> tuple[bool, datetime]:
    if timeout is None:
        timeout = config.BUILD_ACTIVE_DEADLINE_SECONDS + 30
    deadline = time.time() + timeout
    batch = k8s.batch_v1()
    while time.time() < deadline:
        job = batch.read_namespaced_job_status(
            name=job_name, namespace=config.BUILD_NAMESPACE
        )
        if job.status.succeeded:
            return True, datetime.now(timezone.utc)
        if job.status.failed:
            return False, datetime.now(timezone.utc)
        await asyncio.sleep(3)
    return False, datetime.now(timezone.utc)


ROLLOUT_TIMEOUT_SECONDS = 900
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


# rollout 대기. 준비되면 None, 실패면 화면에 보일 이유를 돌려준다.
# 준비 여부만 보면 크래시하는 앱도 타임아웃(15분)까지 "배포 중"으로 남는다 — Pod 상태로 조기 실패를 가른다.
async def _wait_for_rollout(app_name: str, namespace: str) -> str | None:
    deadline = time.time() + ROLLOUT_TIMEOUT_SECONDS
    apps = k8s.apps_v1()
    crash_base: dict[str, int] = {}  # Pod 이름 → 의존성이 준비됐을 때의 재시작 횟수
    while time.time() < deadline:
        try:
            dep = apps.read_namespaced_deployment_status(
                name=app_name, namespace=namespace
            )
        except ApiException:
            await asyncio.sleep(5)
            continue
        ready = dep.status.ready_replicas or 0
        desired = dep.spec.replicas or 1
        observed = dep.status.observed_generation or 0
        current = dep.metadata.generation or 0
        if ready >= desired and observed >= current:
            return None
        try:
            problem = _rollout_problem(dep, app_name, namespace, crash_base)
        except ApiException:
            problem = None
        if problem:
            return problem
        await asyncio.sleep(5)
    return "Pod 시작 실패 (타임아웃)"


# 이번 rollout의 앱 Pod만 본다 — 현재 템플릿 이미지를 쓰는 Pod. 빌드마다 이미지 태그가 바뀌므로
# 이전 빌드의 크래시 Pod을 Recreate 전환 중에 보고 오판하지 않는다. (ReplicaSet 조회는 core RBAC에 없어 쓰지 않는다.)
def _rollout_problem(dep, app_name: str, namespace: str, crash_base: dict[str, int]) -> str | None:
    containers = dep.spec.template.spec.containers or []
    image = next((c.image for c in containers if c.name == "app"), None)
    if not image:
        return None
    core = k8s.core_v1()
    pods = [
        p for p in core.list_namespaced_pod(namespace=namespace, label_selector=f"app={app_name}").items
        if any(c.name == "app" and c.image == image for c in (p.spec.containers or []))
    ]
    dep_pods = core.list_namespaced_pod(namespace=namespace, label_selector=_DEP_SELECTOR).items
    return _crash_reason(pods, dep_pods, crash_base)


# 순수 판정 — 앱 Pod 목록 + 의존성 Pod 목록 → 실패 사유 또는 None. crash_base는 호출 사이에 이어 쓴다.
def _crash_reason(pods, dep_pods, crash_base: dict[str, int]) -> str | None:
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
            if restarts - base >= CRASH_RESTART_LIMIT:
                return _crash_message(cs)
    return None


def _pod_ready(pod) -> bool:
    return any(
        c.type == "Ready" and c.status == "True" for c in (pod.status.conditions or [])
    )


def _crash_message(cs) -> str:
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
    return f"{head} 재시작이 반복돼 배포를 멈췄어요. 로그 끝의 앱 로그를 확인하세요."


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


# build-id 라벨로 BuildKit Pod 찾아 로그 조회 (main container 기본)
# quiet=True면 조회 실패를 ""로 (실시간 tailing 중 buildkit이 아직 init 단계라 PodInitializing
# 에러를 던지는 게 정상 — 그 에러 문자열을 로그에 끼워넣지 않게).
def _get_job_logs(build_id: str, quiet: bool = False) -> str:
    core = k8s.core_v1()
    pods = core.list_namespaced_pod(
        namespace=config.BUILD_NAMESPACE,
        label_selector=f"build-id={build_id}",
    )
    if not pods.items:
        return ""
    pod_name = pods.items[0].metadata.name
    try:
        return core.read_namespaced_pod_log(
            name=pod_name, namespace=config.BUILD_NAMESPACE
        )
    except ApiException as e:
        return "" if quiet else f"로그 조회 실패: {e}"


# 빌드 Pod의 init(clone/nixpacks) + main(buildkit) 로그를 헤더와 함께 합쳐 하나의 텍스트로.
# 실시간 tailing(_tail_build_logs)과 빌드 종료 후 최종 저장이 같은 형식을 쓰도록 통합 —
# 진행 중 보이던 로그가 완료 시점에 형식이 바뀌어 깜빡이지 않게.
# init 컨테이너 이름은 모드별로 다름: auto=nixpacks, dockerfile/static=clone.
# main(buildkit)은 init 단계엔 아직 안 떠서 quiet 조회 — 그 땐 init 로그만 나온다.
def _combined_job_logs(build_id: str, build_mode: str) -> str:
    if build_mode == "auto":
        init_name, init_label = "nixpacks", "nixpacks (init)"
    else:
        init_name, init_label = "clone", "clone (init)"
    init_logs = _get_init_container_logs(build_id, init_name)
    main_logs = _get_job_logs(build_id, quiet=True)
    parts = []
    if init_logs:
        parts.append(f"=== {init_label} ===\n{init_logs}")
    if main_logs:
        parts.append(f"=== buildkit (main) ===\n{main_logs}")
    return "\n\n".join(parts)


# 빌드 진행 중 1초마다 현재 Pod 로그를 읽어 build.logs를 통째 덮어쓴다 (프론트 실시간 표시용).
# _wait_for_job과 같은 이벤트 루프의 동시 task로 도는데, K8s 동기 호출은 루프를 막으므로
# to_thread로 떼어낸다 (안 그러면 1초 로그 조회가 job 폴링을 지연시킴).
# stop_event가 set되면(빌드 종료) 즉시 종료 — 이후 _run_build이 최종 로그를 authoritative하게 덮는다.
async def _tail_build_logs(
    build_id: str,
    build_mode: str,
    stop_event: asyncio.Event,
    push_done: asyncio.Event | None = None,
    push_marker: dict | None = None,   # 마커 첫 감지 시각을 push_marker["at"]에 stamp (계측용)
) -> None:
    db = SessionLocal()
    last = None
    try:
        while not stop_event.is_set():
            try:
                logs = await asyncio.to_thread(
                    _combined_job_logs, build_id, build_mode
                )
                if logs:
                    # early-trigger: "exporting to image" 이후 첫 push 완료(=이미지 매니페스트)를
                    # 보면 배포 트리거를 깨운다. 앵커 뒤부터 검색해 캐시 매니페스트 push를 안 잡는다.
                    if push_done is not None and not push_done.is_set():
                        exp = _EXPORT_IMAGE_RE.search(logs)
                        if exp and _PUSH_DONE_RE.search(logs, exp.end()):
                            if push_marker is not None:
                                push_marker["at"] = datetime.now(timezone.utc)
                            push_done.set()
                    if logs != last:
                        row = db.query(Build).filter_by(build_id=build_id).first()
                        if row and row.status != "cancelled":
                            row.logs = logs
                            db.commit()
                            last = logs
            except Exception:
                db.rollback()  # 폴링 한 틱 실패가 tailer를 죽이지 않게
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
    finally:
        db.close()


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


# 빌드 Pod의 컨테이너 종료 정보에서 단계별 소요시간 추출 (BuildRecord용).
# nixpacks=init container(auto 모드만) / buildkit=main container.
# 컨테이너가 아직 안 끝났거나(타임아웃) Pod이 없으면 해당 값 생략 — 기록은 best-effort.
def _get_build_phase_seconds(build_id: str) -> dict:
    core = k8s.core_v1()
    try:
        pods = core.list_namespaced_pod(
            namespace=config.BUILD_NAMESPACE,
            label_selector=f"build-id={build_id}",
        )
    except ApiException:
        return {}
    if not pods.items:
        return {}
    status = pods.items[0].status

    def terminated_seconds(statuses, name: str) -> float | None:
        for cs in statuses or []:
            if cs.name == name and cs.state and cs.state.terminated:
                t = cs.state.terminated
                if t.started_at and t.finished_at:
                    return (t.finished_at - t.started_at).total_seconds()
        return None

    out = {}
    nix = terminated_seconds(status.init_container_statuses, "nixpacks")
    if nix is not None:
        out["nixpacks_seconds"] = nix
    bk = terminated_seconds(status.container_statuses, "buildkit")
    if bk is not None:
        out["buildkit_seconds"] = bk
    return out


# 같은 Pod의 특정 init container 로그 조회 (auto 모드의 nixpacks container용)
def _get_init_container_logs(build_id: str, container_name: str) -> str:
    core = k8s.core_v1()
    pods = core.list_namespaced_pod(
        namespace=config.BUILD_NAMESPACE,
        label_selector=f"build-id={build_id}",
    )
    if not pods.items:
        return ""
    pod_name = pods.items[0].metadata.name
    try:
        return core.read_namespaced_pod_log(
            name=pod_name,
            namespace=config.BUILD_NAMESPACE,
            container=container_name,
        )
    except ApiException:
        return ""


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
