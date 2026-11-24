"""CLI 토큰 API — 앱 주인이 웹에서 발급·폐기한다.

POST   /apps/{app_id}/cli-token  발급 (원문은 이 응답으로 한 번만). 그 앱의 이전 토큰은 바로 끝난다
DELETE /apps/{app_id}/cli-token  폐기
GET    /apps/{app_id}/cli-token  지금 살아 있는 토큰의 발급자·시각 (원문 없음) — 앱 설정 화면이 띄울 때 읽는다
모두 쿠키 세션의 앱 주인만 (root는 남의 앱도 주인으로 들어오고, 바꾸는 요청이면 관리자 동작으로 기록한다).
발급·폐기는 터미널 티켓(exec/router.py)과 같은 Origin 검사를 한다 — 다른 사이트에서 토큰을 받아 가거나 폐기하지 못하게.
CLI 토큰(Bearer)으로는 부를 수 없다: get_current_user는 쿠키만 본다.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app import config
from app.admin import audit
from app.apps import sharing
from app.apps.model import App
from app.auth.deps import get_current_user
from app.auth.model import User
from app.cli import service
from app.cli.schemas import CliTokenInfo, CliTokenIssued, CliTokenState
from app.shared.db import get_db

router = APIRouter(prefix="/apps/{app_id}/cli-token", tags=["cli"])


def _owner_app(app_id: uuid.UUID, request: Request, db: Session, user: User) -> App:
    if request.method != "GET" and request.headers.get("origin") not in config.ALLOWED_ORIGINS:
        raise HTTPException(status_code=403, detail="origin not allowed")
    found = sharing.access_for(db, user, app_id)
    if found is None:
        raise HTTPException(status_code=404, detail="app not found")
    app, role, by_admin = found
    if role != "owner":
        raise HTTPException(status_code=403, detail="CLI 토큰은 앱 주인만 발급할 수 있어요")
    if by_admin and request.method != "GET":
        audit.record(db, user, audit.action_of(request), app=app)
    return app


@router.get("", response_model=CliTokenState)
def token_state(
    app_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CliTokenState:
    app = _owner_app(app_id, request, db, user)
    info = service.summary(db, app.id)
    return CliTokenState(current=CliTokenInfo(**info) if info else None, idle_seconds=config.CLI_TOKEN_IDLE_SECONDS,
                         max_seconds=config.CLI_TOKEN_MAX_SECONDS)


@router.post("", response_model=CliTokenIssued, status_code=201)
def issue_token(
    app_id: uuid.UUID,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CliTokenIssued:
    app = _owner_app(app_id, request, db, user)
    issued = service.issue(db, app, user)
    response.headers["Cache-Control"] = "no-store"
    info = service.summary(db, app.id)
    return CliTokenIssued(token=issued.token, idle_seconds=config.CLI_TOKEN_IDLE_SECONDS, **info)


@router.delete("")
def revoke_token(
    app_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    app = _owner_app(app_id, request, db, user)
    ended = service.revoke(db, app, user)
    return {"status": "revoked", "revoked": ended}
