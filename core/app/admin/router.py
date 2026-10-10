"""admin 도메인 엔드포인트 — 관리자 페이지 (role: admin/root 전용).

GET /admin/overview          — 가입/빌드 통계
GET /admin/users             — 가입자 목록 (tenant·빌드 집계 포함)
GET /admin/builds            — 빌드 기록 목록 ("총 빌드" 카드 드릴다운)
GET /admin/nodes             — 노드별 CPU/메모리/디스크 사용량
GET /admin/nodes/{name}/pods — 그 노드 Pod별 사용량+limit (노드 카드 드릴다운)
GET /admin/apps              — 전체 앱 목록 (주인·멤버 수·마지막 배포)
GET /admin/apps/{id}         — 앱 상세: 선택 스택 + Pod 상태 (앱 row 드릴다운)
PUT /admin/users/{id}/role   — 권한(role) 변경 (root 전용, user↔admin만)
DELETE /admin/users/{id}     — 계정 강제 탈퇴 (root 전용 — 회원 탈퇴와 같은 정리, 기록을 남긴다)
GET /admin/tiers             — 앱 개수 등급 목록
PUT /admin/tiers/{name}      — 등급의 앱 수 조절 (root 전용, null=무제한)
PUT /admin/users/{id}/tier   — 유저의 앱 개수 등급 변경 (root 전용)
GET /admin/actions           — 관리자가 남의 앱·계정에 한 동작 기록 (최신 200건)

남의 앱 안의 동작(삭제·터미널·재배포 …)은 앱 화면을 그대로 쓴다 — root는 모든 앱에서 주인과 같고 admin은 보기만
한다 (apps/sharing.py access_for). 그 동작의 기록은 앱 접근 판정이 남긴다 (admin/audit.py).
"""

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.admin import audit, service
from app.auth import account
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


@router.get("/apps")
def list_apps(
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
) -> list[dict]:
    return service.list_apps(db)


# 앱 상세 — 선택 스택(runtime/DB/Redis/스토리지) + 앱 ns Pod 상태.
# sync def — ns Pod 조회가 블로킹이라 threadpool 실행.
@router.get("/apps/{app_id}")
def app_detail(
    app_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
) -> dict:
    try:
        return service.app_detail(db, app_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/actions")
def list_actions(
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
) -> list[dict]:
    return service.list_actions(db)


# 계정 강제 탈퇴 — 회원 탈퇴(DELETE /auth/me)와 같은 정리(앱 전부 삭제 → 계정 행 정리). 지우기 전에 기록한다.
@router.delete("/users/{user_id}")
async def delete_user(
    user_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(get_root_user),
) -> dict:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="유저를 찾을 수 없습니다")
    if target.id == actor.id:
        raise HTTPException(status_code=400, detail="자기 계정은 여기서 지울 수 없습니다")
    audit.record(db, actor, audit.action_of(request), target=target)
    try:
        await account.delete_account(db, target)
    except ValueError as e:                     # root 계정, 앱 삭제 실패(빌더가 못 지움 등)
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "deleted"}


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
