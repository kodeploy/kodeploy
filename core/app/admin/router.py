"""admin 도메인 엔드포인트 — 관리자 페이지 (role: admin/root 전용).

GET /admin/overview          — 가입/빌드 통계
GET /admin/users             — 가입자 목록 (tenant·빌드 집계 포함)
GET /admin/builds            — 빌드 기록 목록 ("총 빌드" 카드 드릴다운)
GET /admin/nodes             — 노드별 CPU/메모리/디스크 사용량
GET /admin/nodes/{name}/pods — 그 노드 Pod별 사용량+limit (노드 카드 드릴다운)
GET /admin/users/{id}/tenant — 유저 테넌트 상세: 선택 스택 + Pod 상태 (유저 row 드릴다운)
PUT /admin/users/{id}/role   — 권한(role) 변경 (root 전용, user↔admin만)
GET /admin/tiers             — 앱 개수 등급 목록
PUT /admin/tiers/{name}      — 등급의 앱 수 조절 (root 전용, null=무제한)
PUT /admin/users/{id}/tier   — 유저의 앱 개수 등급 변경 (root 전용)
"""

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.admin import service
from app.auth.deps import get_admin_user, get_root_user
from app.auth.model import User
from app.shared.db import get_db

router = APIRouter(prefix="/admin", tags=["admin"])


class RoleRequest(BaseModel):
    role: str  # "user" | "admin" — service.ASSIGNABLE_ROLES가 검증


class TierLimitRequest(BaseModel):
    max_apps: int | None  # None=무제한


class UserTierRequest(BaseModel):
    tier: str


@router.get("/overview")
def overview(
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
) -> dict:
    return service.overview(db)


@router.get("/users")
def list_users(
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
) -> list[dict]:
    return service.list_users(db)


# 빌드 기록 목록 (최신순 100건) — "총 빌드" 카드 드릴다운.
@router.get("/builds")
def list_builds(
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
) -> list[dict]:
    return service.list_build_records(db)


# sync def — K8s proxy 호출(노드당 1회)이 블로킹이라 FastAPI threadpool에서 실행됨.
@router.get("/nodes")
def nodes(_: User = Depends(get_admin_user)) -> list[dict]:
    return service.node_stats()


# 노드 이름 형식 (DNS-1123) — proxy URL 경로에 들어가므로 형식 밖 입력은 사전 거절.
_NODE_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$")


# 그 노드에서 도는 Pod별 사용량 + limit — 노드 카드 드릴다운.
@router.get("/nodes/{node_name}/pods")
def node_pods(
    node_name: str,
    _: User = Depends(get_admin_user),
) -> list[dict]:
    if not _NODE_NAME_RE.match(node_name):
        raise HTTPException(status_code=400, detail="잘못된 노드 이름")
    try:
        return service.node_pod_stats(node_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# 유저 테넌트 상세 — 선택 스택(runtime/DB/Redis/스토리지) + 테넌트 ns Pod 상태.
# sync def — ns Pod 조회가 블로킹이라 threadpool 실행.
@router.get("/users/{user_id}/tenant")
def user_tenant(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
) -> dict:
    try:
        return service.user_tenant_detail(db, user_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/users/{user_id}/role")
def set_role(
    user_id: uuid.UUID,
    req: RoleRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(get_root_user),
) -> dict:
    try:
        return service.set_role(db, user_id, req.role, actor)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/tiers")
def list_tiers(
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
) -> list[dict]:
    return service.list_tiers(db)


@router.put("/tiers/{name}")
def set_tier_limit(
    name: str,
    req: TierLimitRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_root_user),
) -> dict:
    try:
        return service.set_tier_limit(db, name, req.max_apps)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/users/{user_id}/tier")
def set_user_tier(
    user_id: uuid.UUID,
    req: UserTierRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_root_user),
) -> dict:
    try:
        return service.set_user_tier(db, user_id, req.tier)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
