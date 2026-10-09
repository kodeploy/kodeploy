from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, WebSocket
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

import asyncio
import json
import re
import uuid
from datetime import datetime, timezone

from app.apps import service as apps_service
from app.apps import sharing
from app.apps.model import App
from app.auth.deps import get_current_user
from app.auth.model import User
from app.auth import github_app, service as auth_service
from app.deploy import crud, status
from app.builder import client as builder
from app.deploy.build import github, pipeline, v2, validation
from app.deploy.build.naming import repo_key
from app.deploy.console import dbquery, logs, metrics, snapshots, terminal
from app.deploy.routing import hostnames
from app.deploy.stack import env, resources
from app.deploy.model import Build, SavedQuery
from app import config
from app.deploy.schemas import (
    DbQueryRequest,
    DeployBuildRef,
    DeployRequest,
    DeployResponse,
    DomainRequest,
    EnvVarsRequest,
    EnvVarsResponse,
    SavedQueryCreate,
    SavedQueryOut,
    SavedQueryUpdate,
    StatusResponse,
)
from app.shared.db import get_db

router = APIRouter(prefix="/deploy", tags=["deploy"])


# 요청이 가리키는 앱과 그 앱에서 요구하는 단계.
# /apps/{app_id}/deploy/... 로 들어오면 그 앱 — 접근 권한이 없으면 404(존재 여부를 알리지 않는다), 있어도 단계가
# 모자라면 403. 옛 경로 /deploy/... 는 app_id가 없어서 내(주인) 첫 앱이다 (없으면 None).
# 이 라우터는 두 경로에 모두 붙는다 (main.py).
#
# 단계는 owner > editor > viewer (apps/sharing.py). 기본 의존성(current_app*)은 owner를 요구해서, 아래 두 개
# (viewer_app*, editor_app*)를 명시한 라우트만 멤버에게 열린다 — 새 라우트는 기본이 "주인만"이다.
def _app_at(app_id: uuid.UUID | None, db: Session, user: User, need: str) -> App | None:
    if app_id is None:
        return apps_service.get_user_app(db, user.id)
    found = sharing.access(db, user.id, app_id)
    if found is None:
        raise HTTPException(status_code=404, detail="app not found")
    app, role = found
    if sharing.RANK[role] < sharing.RANK[need]:
        raise HTTPException(status_code=403, detail="이 앱에서 할 수 없는 동작입니다 — 권한이 부족해요")
    return app


def current_app_or_none(
    app_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> App | None:
    return _app_at(app_id, db, user, "owner")


def editor_app_or_none(
    app_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> App | None:
    return _app_at(app_id, db, user, "editor")


def viewer_app_or_none(
    app_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> App | None:
    return _app_at(app_id, db, user, "viewer")


def _required(app: App | None) -> App:
    if app is None:
        raise HTTPException(status_code=400, detail="배포된 앱이 없습니다")
    return app


def current_app(app: App | None = Depends(current_app_or_none)) -> App:
    return _required(app)


def viewer_app(app: App | None = Depends(viewer_app_or_none)) -> App:
    return _required(app)


# Build ORM 객체 → StatusResponse 응답 DTO 변환.
# timing: get_build_timings()가 준 {total/nixpacks/buildkit_seconds} (없으면 빈 dict).
def _to_status(build: Build, timing: dict | None = None) -> StatusResponse:
    timing = timing or {}
    return StatusResponse(
        build_id=build.build_id,
        status=build.status,
        repo_url=build.repo_url,
        branch=build.branch,
        app_name=build.app_name,
        runtime=build.runtime,
        build_mode=build.build_mode,
        port=build.port,
        db_type=build.db_type or "none",
        use_redis=build.use_redis or False,
        use_storage=build.use_storage or False,
        # 영속저장소 모드 파생 — object(R2) 우선, 아니면 local(PVC) 있으면 local, 둘 다 없으면 none.
        storage=(
            "object" if build.use_storage
            else ("local" if build.volume_mount_path else "none")
        ),
        volume_mount_path=build.volume_mount_path or "",
        volume_storage_class=build.volume_storage_class or "local-path",
        volume_size=build.volume_size or "5Gi",
        kind=build.kind or "build",
        dockerfile_path=build.dockerfile_path or "Dockerfile",
        project_path=build.project_path or "",
        build_cmd=build.build_cmd or "",
        output_dir=build.output_dir or "",
        static_env=json.loads(build.build_env) if build.build_env else {},
        dockerfile_content=build.dockerfile_content,
        error=build.error,
        env_change_summary=build.env_change_summary,
        rollback_of=build.rollback_of,
        rollbackable=v2.is_rollbackable(build),
        ai_analysis=build.ai_analysis,
        ai_status=build.ai_status,
        logs=build.logs,
        total_seconds=timing.get("total_seconds"),
        created_at=build.created_at,
        updated_at=build.updated_at,
    )


# 저장소를 조회할 때 쓸 GitHub 연결. 내 앱이면 내 연결이고, 편집 권한으로 초대받은 앱이면 그 앱이 쓰는 저장소에
# 한해 주인의 연결을 쓴다 (내 연결로는 주인의 비공개 저장소가 안 보이고, 주인의 연결을 아무 저장소에나 쓰게 둘 수는 없다).
def _github_installation(db: Session, user: User, app: App | None, repo_url: str) -> int | None:
    if app is None or app.owner_id == user.id:
        return user.github_installation_id
    if repo_key(repo_url) in apps_service.app_repo_keys(db, app):
        return apps_service.repo_installation_id(db, app)
    return None


# 배포 제출 — 스택 선언(서버+정적 슬롯)을 받아 슬롯별 빌드를 백그라운드로 시작.
@router.post("", response_model=DeployResponse)
async def create_deploy(
    req: DeployRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    app: App | None = Depends(editor_app_or_none),
) -> DeployResponse:
    try:
        builds = await pipeline.start_deploy(
            db,
            user=user,
            app=app,
            repo_url=str(req.repo_url),
            runtime=req.runtime,
            name=req.name,
            branch=req.branch,
            port=req.port,
            db_type=req.db_type,
            use_redis=req.use_redis,
            storage=req.storage,
            volume_mount_path=req.volume_mount_path,
            volume_storage_class=req.volume_storage_class,
            volume_size=req.volume_size,
            build_mode=req.build_mode,
            dockerfile_path=req.dockerfile_path,
            project_path=req.project_path,
            env_vars=req.env,
            init_dump_token=req.init_dump_token,
            use_static=req.use_static,
            static_repo_url=req.static_repo_url,
            static_branch=req.static_branch,
            static_project_path=req.static_project_path,
            build_cmd=req.build_cmd,
            output_dir=req.output_dir,
            static_env=req.static_env,
            # 편집 권한 멤버는 이 앱이 쓰던 저장소로만 (주인은 제한 없음)
            allowed_repos=(
                apps_service.app_repo_keys(db, app) if app is not None and app.owner_id != user.id else None
            ),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    deployed_app = app if app is not None else apps_service.get_user_app(db, user.id)
    return DeployResponse(
        app_id=deployed_app.id if deployed_app else None,
        app_name=deployed_app.name if deployed_app else "",
        builds=[
            DeployBuildRef(build_id=b.build_id, runtime=b.runtime, status=b.status)
            for b in builds
        ],
    )


# 사용자 앱의 환경변수 조회. 첫 배포 전이거나 한 번도 설정 안 했으면 빈 dict.
# /{build_id} GET 핸들러보다 위에 등록해야 "env"가 build_id로 잡히지 않음.
@router.get("/env", response_model=EnvVarsResponse)
def env_get(app: App | None = Depends(editor_app_or_none)) -> EnvVarsResponse:
    if app is None:
        return EnvVarsResponse(env={})
    return EnvVarsResponse(env=env.get_env(app.namespace, app.name))


# 환경변수 전체 replace. 저장 직후 rolling update 트리거로 새 값 즉시 반영.
# 빌드 status는 영구 기록이라 안 건드림. 새 Pod 상태는 헤더의 앱 상태(/deploy/app/status)로 표시.
@router.put("/env", response_model=EnvVarsResponse)
async def env_put(
    req: EnvVarsRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    app: App | None = Depends(editor_app_or_none),
) -> EnvVarsResponse:
    if app is None:
        raise HTTPException(status_code=400, detail="첫 배포 완료 후 환경변수 설정 가능")
    tenant_id = app.namespace
    is_v2 = v2.is_v2(app)
    if is_v2:   # v2는 Pod 재시작을 Argo가 한다 — 서버 배포가 도는 중이면 요청이 겹쳐 배포가 죽으니 먼저 막는다
        try:
            v2.ensure_idle(db, app)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
    # 변경 전 현재 env (Secret) 조회 — set_env 전에 받아둬야 diff 계산 가능
    old_env = env.get_env(tenant_id, app.name)
    try:
        env.set_env(tenant_id, app.name, req.env, restart=not is_v2)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 어떤 키가 바뀌었는지만 기록 (값은 보안상 저장 X). 형식: "KEY (추가), KEY2 (수정), KEY3 (삭제)".
    new_env = req.env
    added = sorted(k for k in new_env if k not in old_env)
    modified = sorted(
        k for k in new_env if k in old_env and new_env[k] != old_env[k]
    )
    removed = sorted(k for k in old_env if k not in new_env)
    entries = (
        [f"{k} (추가)" for k in added]
        + [f"{k} (수정)" for k in modified]
        + [f"{k} (삭제)" for k in removed]
    )
    if not entries:
        # 같은 값 재저장 — 히스토리에 noise 안 남김
        return EnvVarsResponse(env=req.env)

    # 환경변수 변경 이벤트를 히스토리에 기록 — kind="env_change"라 #N 번호 안 매김.
    # 직전 build에서 repo/branch/runtime/image 컨텍스트 복사 (none이면 빈 값).
    latest = (
        db.query(Build).filter_by(app_id=app.id)
        .order_by(Build.created_at.desc()).first()
    )
    event = Build(
        build_id=uuid.uuid4().hex[:8],
        repo_url=latest.repo_url if latest else "",
        branch=latest.branch if latest else "",
        image=latest.image if latest else "",
        app_name=app.name,
        port=latest.port if latest else 80,
        runtime=latest.runtime if latest else "",
        user_id=user.id,
        app_id=app.id,
        namespace=app.namespace,
        db_type=latest.db_type if latest else "none",
        use_redis=latest.use_redis if latest else False,
        use_storage=latest.use_storage if latest else False,
        volume_mount_path=latest.volume_mount_path if latest else "",
        volume_storage_class=latest.volume_storage_class if latest else "local-path",
        volume_size=latest.volume_size if latest else "5Gi",
        kind="env_change",
        status="applied",
        env_change_summary=", ".join(entries),  # "KEY (추가), KEY2 (수정), KEY3 (삭제)"
        last_event_seq=0 if is_v2 else None,   # v2 표시 겸 빌더 콜백 seq 시작점
    )
    db.add(event)
    db.commit()

    if is_v2:
        # Secret은 이미 썼다. envRevision을 올리는 커밋을 빌더에 맡기면 Argo가 Pod을 다시 띄우고,
        # 결과(deployed 또는 failed)는 콜백이 이 행에 반영한다.
        try:
            await v2.submit_env_revision(event, user)
        except builder.BuilderError as e:
            event.status, event.error = "failed", f"빌더가 받지 않았습니다: {e}"
            db.commit()
            raise HTTPException(
                status_code=502,
                detail=f"환경변수는 저장했지만 적용 요청이 실패했어요 ({e}). 다음 배포 때 반영돼요",
            )
        return EnvVarsResponse(env=req.env)

    # Pod이 새 env로 부팅했는지 백그라운드 폴링 → 이 event row의 status를 running/failed로 갱신.
    # 동기 K8s 클라이언트를 쓰므로 메인 루프 대신 전용 스레드에서 (pipeline.spawn_background).
    pipeline.spawn_background(
        pipeline.watch_env_change_rollout,
        user.id, app.name, tenant_id, event.build_id,
    )
    return EnvVarsResponse(env=req.env)


# 현재 user 앱의 Pod 상태 — 빌드와 독립. 프론트가 폴링.
# 응답: {"status": "running" | "pending" | "crashing" | "missing"}
@router.get("/app/status")
def app_status(app: App | None = Depends(viewer_app_or_none)) -> dict:
    return status.get_app_status(app)


# 런타임 로그 스냅샷 — 현재 + 이전 인스턴스 로그 JSON. 프론트 30초 폴링.
@router.get("/app/logs")
def app_logs(app: App = Depends(viewer_app)):
    return logs.fetch_app_logs(app.namespace, app.name)


@router.get("/app/metrics")
def app_metrics(
    range: str = "1h",
    app: App = Depends(viewer_app),
):
    return metrics.fetch_app_metrics(app.namespace, app.name, range)


# WebSocket은 CORS·SameSite 보호 밖이라(브라우저가 자동 차단 안 함) Origin을 서버가
# 직접 검증해야 한다. 허용 목록(CORS와 동일)에 없으면 핸드셰이크 거절 — CSWSH(교차사이트
# WebSocket 하이재킹) 방지. 브라우저는 WS에 Origin을 항상 붙이고 JS가 위조 못 하므로
# Origin 없음(비-브라우저)도 거절 — 이 WS는 web 프론트 전용.
def _ws_origin_allowed(ws: WebSocket) -> bool:
    return ws.headers.get("origin") in config.ALLOWED_ORIGINS


# WebSocket이 가리키는 앱 — /apps/{app_id}/deploy/... 면 그 앱(내 것만), 옛 경로면 유저의 첫 앱.
def _ws_app(db: Session, ws: WebSocket, user_id: uuid.UUID) -> App | None:
    raw = ws.path_params.get("app_id")
    if raw is None:
        return apps_service.get_user_app(db, user_id)
    try:
        return apps_service.get_owned_app(db, user_id, uuid.UUID(raw))
    except ValueError:
        return None


# Pod exec WebSocket — xterm.js 프론트와 양방향. cookie로 인증.
@router.websocket("/app/terminal")
async def app_terminal(ws: WebSocket):
    if not _ws_origin_allowed(ws):
        await ws.close(code=4403, reason="origin not allowed")
        return
    sid = ws.cookies.get("kd_session")
    if not sid:
        await ws.close(code=4001, reason="인증 필요")
        return
    from app.shared.db import SessionLocal
    db = SessionLocal()
    try:
        sess = auth_service.get_active_session(db, sid)
        if not sess:
            await ws.close(code=4001, reason="세션 만료")
            return
        app = _ws_app(db, ws, sess.user_id)
        if not app:
            await ws.close(code=4002, reason="앱 없음")
            return
        tenant_id = app.namespace
        app_name = app.name
    finally:
        db.close()
    await terminal.handle_terminal(ws, tenant_id, app_name)


# DB Pod exec WebSocket — mysql/psql CLI. cookie 인증.
@router.websocket("/app/db-terminal")
async def app_db_terminal(ws: WebSocket):
    if not _ws_origin_allowed(ws):
        await ws.close(code=4403, reason="origin not allowed")
        return
    sid = ws.cookies.get("kd_session")
    if not sid:
        await ws.close(code=4001, reason="인증 필요")
        return
    from app.shared.db import SessionLocal
    db = SessionLocal()
    try:
        sess = auth_service.get_active_session(db, sid)
        if not sess:
            await ws.close(code=4001, reason="세션 만료")
            return
        app = _ws_app(db, ws, sess.user_id)
        if not app:
            await ws.close(code=4002, reason="앱 없음")
            return
        tenant_id = app.namespace
    finally:
        db.close()
    await terminal.handle_db_terminal(ws, tenant_id)


# Redis Pod exec WebSocket — redis-cli. cookie 인증. db-terminal과 동일 격리/검증.
@router.websocket("/app/redis-terminal")
async def app_redis_terminal(ws: WebSocket):
    if not _ws_origin_allowed(ws):
        await ws.close(code=4403, reason="origin not allowed")
        return
    sid = ws.cookies.get("kd_session")
    if not sid:
        await ws.close(code=4001, reason="인증 필요")
        return
    from app.shared.db import SessionLocal
    db = SessionLocal()
    try:
        sess = auth_service.get_active_session(db, sid)
        if not sess:
            await ws.close(code=4001, reason="세션 만료")
            return
        app = _ws_app(db, ws, sess.user_id)
        if not app:
            await ws.close(code=4002, reason="앱 없음")
            return
        tenant_id = app.namespace
    finally:
        db.close()
    await terminal.handle_redis_terminal(ws, tenant_id)


# DB 콘솔 — 단발 SQL 실행 후 구조화된 결과(columns/rows) 반환. 표 UI가 렌더.
# raw 터미널(/app/db-terminal)과 같은 exec 인프라를 쓰되 결과를 JSON으로 파싱해 돌려줌.
# /{build_id} 핸들러보다 위에 등록해야 "app"이 build_id로 잡히지 않음.
@router.post("/app/db/query")
async def db_query(
    req: DbQueryRequest,
    app: App = Depends(current_app),
) -> dict:
    try:
        return await dbquery.run_query(app.namespace, req.sql, offset=req.offset)
    except dbquery.QueryError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── 저장된 쿼리 (DB 콘솔) ────────────────────────────────────────────────────
# 저장 위치는 **플랫폼(관리) DB**다 — 유저 앱 DB에 관리 테이블을 만들지 않는다.
# /{build_id}보다 위에 등록해야 "app"이 build_id로 잡히지 않음 (아래 storage와 동일).

# 한 스코프가 들 수 있는 쿼리 수 상한. 무한정 쌓이면 목록 응답과 관리 DB가 같이 커진다.
MAX_SAVED_QUERIES = 100


# DB 콘솔의 스코프 — (앱, DB 종류). 클라이언트 입력을 전혀 받지 않고 세션 유저의 앱과 최신
# 서버 빌드에서만 뽑는다. 저장된 쿼리의 모든 핸들러가 첫 줄에서 이걸 부르고, 그 값이
# 그대로 WHERE에 들어가므로 "남의 앱/DB 칸"이 애초에 표현 불가능하다.
def _db_scope(db: Session, app: App) -> str:
    build = crud.get_server_build(db, app.id)
    db_type = (build.db_type if build else None) or "none"
    if db_type == "none":
        raise HTTPException(
            status_code=400,
            detail="DB가 활성화돼 있지 않습니다 — DB(MySQL/PostgreSQL)를 추가한 앱에서만 쓸 수 있습니다.",
        )
    return db_type


def _to_saved_query(row: SavedQuery) -> SavedQueryOut:
    return SavedQueryOut(
        id=row.id,
        name=row.name,
        sql=row.sql_text,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# 이름·SQL 정규화 + 검증. SQL 길이 상한은 실행 경로(dbquery)와 같은 값을 쓴다 —
# 저장은 되는데 실행은 못 하는 쿼리가 생기지 않게.
def _clean_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="쿼리 이름을 입력해주세요")
    if len(name) > 100:
        raise HTTPException(status_code=400, detail="쿼리 이름이 너무 깁니다 (최대 100자)")
    return name


def _clean_sql(sql: str) -> str:
    sql = (sql or "").strip()
    if not sql:
        raise HTTPException(status_code=400, detail="SQL을 입력해주세요")
    if len(sql) > dbquery.MAX_SQL_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"SQL이 너무 깁니다 (최대 {dbquery.MAX_SQL_LENGTH}자)",
        )
    return sql


@router.get("/app/db/queries")
def saved_queries_list(
    app: App = Depends(current_app),
    db: Session = Depends(get_db),
) -> list[SavedQueryOut]:
    db_type = _db_scope(db, app)
    rows = crud.list_saved_queries(db, app.id, db_type)
    return [_to_saved_query(r) for r in rows]


@router.post("/app/db/queries")
def saved_queries_create(
    req: SavedQueryCreate,
    user: User = Depends(get_current_user),
    app: App = Depends(current_app),
    db: Session = Depends(get_db),
) -> SavedQueryOut:
    db_type = _db_scope(db, app)
    name, sql = _clean_name(req.name), _clean_sql(req.sql)
    if crud.count_saved_queries(db, app.id, db_type) >= MAX_SAVED_QUERIES:
        raise HTTPException(
            status_code=400,
            detail=f"저장한 쿼리가 너무 많습니다 (최대 {MAX_SAVED_QUERIES}개) — 쓰지 않는 쿼리를 지워주세요",
        )
    row = crud.create_saved_query(
        db, user_id=user.id, app_id=app.id, app_name=app.name, db_type=db_type, name=name, sql=sql,
    )
    return _to_saved_query(row)


# 부분 수정 — 준 필드만 바뀐다. 스코프 밖 id는 404로 마스킹(존재 여부도 알려주지 않음).
@router.patch("/app/db/queries/{query_id}")
def saved_queries_update(
    query_id: int,
    req: SavedQueryUpdate,
    app: App = Depends(current_app),
    db: Session = Depends(get_db),
) -> SavedQueryOut:
    db_type = _db_scope(db, app)
    row = crud.get_saved_query(db, query_id, app.id, db_type)
    if not row:
        raise HTTPException(status_code=404, detail="저장된 쿼리를 찾을 수 없습니다")
    name = _clean_name(req.name) if req.name is not None else None
    sql = _clean_sql(req.sql) if req.sql is not None else None
    if name is None and sql is None:
        raise HTTPException(status_code=400, detail="바꿀 내용이 없습니다")
    return _to_saved_query(crud.update_saved_query(db, row, name=name, sql=sql))


@router.delete("/app/db/queries/{query_id}")
def saved_queries_delete(
    query_id: int,
    app: App = Depends(current_app),
    db: Session = Depends(get_db),
) -> dict:
    db_type = _db_scope(db, app)
    row = crud.get_saved_query(db, query_id, app.id, db_type)
    if not row:
        raise HTTPException(status_code=404, detail="저장된 쿼리를 찾을 수 없습니다")
    crud.delete_saved_query(db, row)
    return {"status": "deleted"}


# R2 오브젝트 목록 — 이미지 미리보기용 공개 URL 포함. ?token=으로 다음 페이지.
# /{build_id} GET보다 위에 등록해야 "app"이 build_id로 잡히지 않음.
@router.get("/app/storage/objects")
def storage_list(
    token: str | None = None,
    app: App = Depends(current_app),
) -> dict:
    try:
        return resources.list_storage_objects(app, token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# R2 오브젝트 1개의 본문 (텍스트 미리보기 전용 — 앞부분만, 상한 초과분은 truncated).
@router.get("/app/storage/object")
def storage_read(
    key: str,
    app: App = Depends(current_app),
) -> dict:
    try:
        return resources.read_storage_object(app, key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# R2 오브젝트 1개 삭제 (파괴적 — UI에서 확인 후 호출).
@router.delete("/app/storage/objects")
def storage_delete(
    key: str,
    app: App = Depends(current_app),
) -> dict:
    try:
        resources.delete_storage_object(app, key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "deleted"}


# 커스텀 도메인 조회 (+ CNAME 안내값). 호출마다 CF에서 검증/cert status 갱신.
# /{build_id} GET보다 위에 등록 (path param이 "domain"을 잡지 않게).
@router.get("/domain")
def get_domain(
    db: Session = Depends(get_db),
    app: App | None = Depends(viewer_app_or_none),
) -> dict:
    if app is None:   # 앱 전엔 연결된 도메인이 없다
        result = {"domain": None, "status": None, "ssl_status": None}
    else:
        result = hostnames.refresh_custom_domain_status(db, app)
    result["cname_target"] = config.CUSTOM_DOMAIN_CNAME_TARGET
    return result


# 커스텀 도메인 연결/변경 — CF custom hostname 생성 + 앱 HTTPRoute에 hostname 주입.
@router.put("/domain")
async def put_domain(
    req: DomainRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    app: App | None = Depends(current_app_or_none),
) -> dict:
    if app is None:
        raise HTTPException(status_code=400, detail="먼저 앱을 배포한 후 커스텀 도메인을 연결할 수 있습니다")
    is_v2 = v2.is_v2(app)
    try:
        if is_v2:
            v2.ensure_idle(db, app, include_static=True)   # 배포가 도는 중이면 빌더 요청이 겹치거나 옛 호스트를 되돌린다
        # CF·K8s 호출이 동기라 메인 루프를 막지 않게 스레드에서. v2는 route를 Argo가 그리므로 직접 안 고친다.
        result = await asyncio.to_thread(hostnames.set_custom_domain, db, app, req.domain, not is_v2)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if is_v2:
        await _apply_hostnames_or_502(app, user)
    result["cname_target"] = config.CUSTOM_DOMAIN_CNAME_TARGET
    return result


# 커스텀 도메인 해제 — CF custom hostname 삭제 + route에서 hostname 제거.
@router.delete("/domain")
async def delete_domain(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    app: App | None = Depends(current_app_or_none),
) -> dict:
    if app is None:
        return {"status": "cleared"}
    is_v2 = v2.is_v2(app)
    if is_v2:
        try:
            v2.ensure_idle(db, app, include_static=True)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
    await asyncio.to_thread(hostnames.clear_custom_domain, db, app, not is_v2)
    if is_v2:
        await _apply_hostnames_or_502(app, user)
    return {"status": "cleared"}


# v2: 바뀐 hostnames를 빌더로 보낸다. 도메인 자체(CF·DB)는 이미 바뀐 뒤라, 요청이 실패하면 그 사실을 알려 다시 시도하게 한다.
async def _apply_hostnames_or_502(app: App, user: User) -> None:
    try:
        await v2.apply_hostnames(app, user)
    except builder.BuilderError as e:
        raise HTTPException(
            status_code=502,
            detail=f"도메인은 저장했지만 주소 반영 요청이 실패했어요 ({e}). 같은 도메인을 다시 저장해 보세요",
        )


# 앱 완전 삭제 — K8s 리소스 + PVC + builds + 앱 행 삭제.
# v2 앱은 먼저 빌더가 values를 지우고 Application이 사라지길 기다린다 (그 전에 ns를 지우면 Argo가 되살린다).
# /{build_id} 핸들러보다 위에 등록해야 path param이 "app"을 잡지 않음.
@router.delete("/app")
async def delete_app(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    app: App | None = Depends(current_app_or_none),
):
    if app is None:
        raise HTTPException(status_code=400, detail="삭제할 앱이 없습니다")
    try:
        if v2.is_v2(app):
            await v2.request_delete(user, app)
        await asyncio.to_thread(status.delete_app, db, app)   # K8s·R2 호출이 동기라 메인 루프를 막지 않게
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "deleted"}


# 이력의 한 배포로 되돌린다 (v2 앱, 편집 권한 이상) — 이미지를 다시 빌드하지 않고 그 배포의 digest로 배포한다.
# 새 배포 행이 이력에 남고 진행은 그 build_id로 따라간다. /{build_id} 핸들러와 경로가 달라 순서 제약은 없다.
@router.post("/{build_id}/rollback", response_model=DeployBuildRef)
async def rollback_build(
    build_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    app: App | None = Depends(editor_app_or_none),
) -> DeployBuildRef:
    if app is None:
        raise HTTPException(status_code=404, detail="build not found")
    try:
        build = await pipeline.start_rollback(db, user, app, build_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return DeployBuildRef(build_id=build.build_id, runtime=build.runtime, status=build.status)


# 사용자의 최신 build의 repo+branch에서 GitHub 최근 커밋 N개 조회.
# /{build_id} GET 핸들러보다 위에 등록해야 "commits"가 build_id로 잡히지 않음.
# build가 없거나 repo 파싱 실패 시 빈 리스트. private repo는 unauthenticated 호출 실패라 빈 리스트.
@router.get("/commits")
def list_recent_commits(
    db: Session = Depends(get_db),
    app: App | None = Depends(viewer_app_or_none),
) -> list[dict]:
    if app is None:
        return []
    builds = status.list_builds(db, app_id=app.id)
    if not builds:
        return []
    latest = builds[0]
    return github.fetch_recent_commits(latest.repo_url, latest.branch)


# 연결된 GitHub App installation이 접근 가능한 repo 목록 — 배포 폼 private repo 선택 드롭다운용.
# 미연결(installation_id 없음)/미설정이면 빈 리스트. /{build_id} GET보다 위에 등록해야 "github"가 build_id로 안 잡힘.
@router.get("/github/repos")
def github_repos(user: User = Depends(get_current_user)) -> list[dict]:
    return github_app.list_installation_repos(user.github_installation_id)


# 특정 repo의 브랜치 목록 — 배포 폼 브랜치 드롭다운용. ?repo=<github url>.
# installation 토큰으로 private도 조회. /{build_id} GET보다 위에 등록해야 "github"가 build_id로 안 잡힘.
@router.get("/github/branches")
def github_branches(
    repo: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    app: App | None = Depends(editor_app_or_none),
) -> list[dict]:
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", repo.strip())
    if not m:
        return []
    return github_app.list_branches(_github_installation(db, user, app, repo), m.group(1), m.group(2))


# 저장소 런타임 추정 — 배포 폼 2단계 런타임 미리 채우기용. ?repo=<github url>&branch=&path=
# 마커 파일 기반. 지원 안 하는 런타임(ruby 등)이면 unsupported, 조회 실패면 checked=False.
# /{build_id} GET보다 위에 등록해야 "github"가 build_id로 안 잡힘.
@router.get("/github/detect")
def github_detect(
    repo: str,
    branch: str = "main",
    path: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    app: App | None = Depends(editor_app_or_none),
) -> dict:
    return github.detect_runtime(
        repo.strip(), branch.strip() or "main", path, _github_installation(db, user, app, repo),
    )


# DB 스냅샷 추출 — 현재 앱 MySQL을 mysqldump → .sql.gz 다운로드 스트림.
# /{build_id} GET 핸들러보다 위에 등록해야 "db"가 build_id로 잡히지 않음.
@router.get("/db/export")
async def db_export(app: App = Depends(current_app)):
    tenant_id = app.namespace
    try:
        await snapshots.ensure_db(tenant_id)             # 스트리밍 시작 전 검증
    except snapshots.SnapshotError as e:
        raise HTTPException(status_code=400, detail=str(e))
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"{app.name}-{ts}.sql.gz"
    return StreamingResponse(
        snapshots.export_stream(tenant_id),
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# 초기 데이터 stage — 첫 배포 시 함께 올릴 .sql(.gz)을 임시 보관하고 토큰 반환.
# 배포 요청(POST /deploy)의 init_dump_token에 이 값을 넣으면 mysql Ready 후 자동 복원.
@router.post("/db/stage-dump")
async def db_stage_dump(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
) -> dict:
    async def _chunks():
        while True:
            data = await file.read(256 * 1024)
            if not data:
                break
            yield data

    token = await snapshots.stage_dump(_chunks())
    return {"token": token}


# DB 스냅샷 복원 — 업로드한 .sql(.gz)을 현재 앱 MySQL에 적재. 파괴적(기존 데이터 덮어씀).
@router.post("/db/restore")
async def db_restore(
    file: UploadFile = File(...),
    app: App = Depends(current_app),
):
    tenant_id = app.namespace

    async def _chunks():
        while True:
            data = await file.read(256 * 1024)
            if not data:
                break
            yield data

    try:
        return await snapshots.restore(tenant_id, _chunks())
    except snapshots.SnapshotError as e:
        raise HTTPException(status_code=400, detail=str(e))


# dep별 자동 주입 env 키 맵 — 배포 폼이 환경변수 인라인 충돌 검증에 사용.
# 정적(테넌트 무관)이라 인증만 두고 캐시된 맵을 그대로 반환. /{build_id}보다 위에 등록.
@router.get("/reserved-keys")
def reserved_keys(user: User = Depends(get_current_user)) -> dict:
    return validation.reserved_env_keys_map()


# build_id 단건 상태 조회 — 본인 빌드만 (다른 user의 build_id는 404로 마스킹)
@router.get("/{build_id}", response_model=StatusResponse)
def get_status(
    build_id: str,
    db: Session = Depends(get_db),
    app: App | None = Depends(viewer_app_or_none),
) -> StatusResponse:
    build = status.get_state(db, build_id, app_id=app.id) if app else None
    if not build:
        raise HTTPException(status_code=404, detail="build not found")
    timings = pipeline.get_build_timings(db, [build.build_id])
    return _to_status(build, timings.get(build.build_id))


# 빌드 목록 — 본인 것만, 최신순
@router.get("", response_model=list[StatusResponse])
def list_builds(
    db: Session = Depends(get_db),
    app: App | None = Depends(viewer_app_or_none),
) -> list[StatusResponse]:
    builds = status.list_builds(db, app_id=app.id) if app else []
    timings = pipeline.get_build_timings(db, [b.build_id for b in builds])
    return [_to_status(b, timings.get(b.build_id)) for b in builds]
