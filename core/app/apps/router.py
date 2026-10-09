"""GET/POST /apps — 내 앱 목록과 새 앱 만들기.

앱 안의 동작(배포·로그·환경변수 …)은 /apps/{app_id}/deploy/... 로 들어온다 (deploy 라우터, main.py).
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.apps import service
from app.apps.schemas import AppCreate, AppOut
from app.auth.deps import get_current_user
from app.auth.model import User
from app.deploy.build import naming
from app.shared.db import get_db

router = APIRouter(prefix="/apps", tags=["apps"])


@router.get("", response_model=list[AppOut])
def list_apps(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return service.list_user_apps(db, user.id)


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
        return naming.create_named_app(req.name, req.repo_url, user, db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
