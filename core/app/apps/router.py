"""앱 API — 내 앱 목록과 새 앱 만들기, 공유(멤버·초대).

앱 안의 동작(배포·로그·환경변수 …)은 /apps/{app_id}/deploy/... 로 들어온다 (deploy 라우터, main.py).
공유 관리는 앱 주인만 한다. 초대를 받은 쪽은 /invites 에서 수락·거절한다.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.apps import service, sharing
from app.apps.model import App
from app.apps.schemas import AppCreate, AppOut, InviteCreate, InviteOut, RoleUpdate
from app.auth.deps import get_current_user
from app.auth.model import User
from app.deploy.build import naming
from app.shared.db import get_db

router = APIRouter(prefix="/apps", tags=["apps"])
invites_router = APIRouter(prefix="/invites", tags=["apps"])


def _out(app: App, role: str, owner_login: str | None) -> AppOut:
    return AppOut(
        id=app.id, name=app.name, site_enabled=app.site_enabled, custom_domain=app.custom_domain,
        created_at=app.created_at, role=role, owner_login=owner_login, pipeline=app.pipeline,
    )


# 내 앱 + 공유받은 앱 (내 앱이 먼저)
@router.get("", response_model=list[AppOut])
def list_apps(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return [_out(a, role, owner) for a, role, owner in sharing.list_accessible(db, user.id)]


# 빈 앱을 만든다 (이름과 ns만 잡는다). 배포는 만들어진 앱의 /apps/{id}/deploy 로 한다.
@router.post("", response_model=AppOut)
def create_app(
    req: AppCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if service.at_app_limit(db, user):
        raise HTTPException(status_code=400, detail=f"만들 수 있는 앱은 최대 {service.app_limit(db, user)}개입니다")
    try:
        return _out(naming.create_named_app(req.name, req.repo_url, user, db), "owner", None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- 공유 관리 (앱 주인) -------------------------------------------------------

# 주인 앱만. 내 앱이 아니면 404, 멤버가 부르면 403 (앱이 있다는 건 그 멤버도 안다).
def _owned_app(app_id: uuid.UUID, db: Session, user: User) -> App:
    found = sharing.access(db, user.id, app_id)
    if found is None:
        raise HTTPException(status_code=404, detail="app not found")
    if found[1] != "owner":
        raise HTTPException(status_code=403, detail="앱 주인만 할 수 있습니다")
    return found[0]


def _share_error(e: sharing.ShareError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


@router.get("/{app_id}/members")
def members(app_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    return sharing.members_of(db, _owned_app(app_id, db, user))


@router.post("/{app_id}/invites")
def invite(
    app_id: uuid.UUID,
    req: InviteCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    app = _owned_app(app_id, db, user)
    try:
        inv = sharing.invite(db, app, user, req.target, req.role)
    except sharing.ShareError as e:
        raise _share_error(e)
    return {"id": str(inv.id), "email": inv.email, "github_login": inv.github_login, "role": inv.role}


@router.delete("/{app_id}/invites/{invite_id}")
def cancel_invite(
    app_id: uuid.UUID,
    invite_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    app = _owned_app(app_id, db, user)
    try:
        sharing.cancel(db, app, invite_id)
    except sharing.ShareError as e:
        raise _share_error(e)
    return {"status": "cancelled"}


@router.patch("/{app_id}/members/{user_id}")
def change_role(
    app_id: uuid.UUID,
    user_id: uuid.UUID,
    req: RoleUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    app = _owned_app(app_id, db, user)
    try:
        sharing.set_role(db, app, user_id, req.role)
    except sharing.ShareError as e:
        raise _share_error(e)
    return {"status": "updated"}


# 주인이 멤버를 내보내거나, 멤버가 스스로 나간다.
@router.delete("/{app_id}/members/{user_id}")
def remove_member(
    app_id: uuid.UUID,
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    found = sharing.access(db, user.id, app_id)
    if found is None:
        raise HTTPException(status_code=404, detail="app not found")
    app, role = found
    if role != "owner" and user_id != user.id:
        raise HTTPException(status_code=403, detail="앱 주인만 다른 멤버를 내보낼 수 있습니다")
    try:
        sharing.remove_member(db, app, user_id)
    except sharing.ShareError as e:
        raise _share_error(e)
    return {"status": "removed"}


# --- 받은 초대 (초대받은 쪽) ----------------------------------------------------

@invites_router.get("", response_model=list[InviteOut])
def my_invites(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return [
        InviteOut(id=inv.id, app_name=app.name, owner_login=owner, role=inv.role, created_at=inv.created_at)
        for inv, app, owner in sharing.pending_for(db, user)
    ]


@invites_router.post("/{invite_id}/accept", response_model=AppOut)
def accept_invite(invite_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        app = sharing.accept(db, invite_id, user)
    except sharing.ShareError as e:
        raise HTTPException(status_code=404, detail=str(e))
    _, role = sharing.access(db, user.id, app.id)
    owner = db.get(User, app.owner_id)
    return _out(app, role, owner.login if owner else None)


@invites_router.post("/{invite_id}/decline")
def decline_invite(invite_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    try:
        sharing.decline(db, invite_id, user)
    except sharing.ShareError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "declined"}
