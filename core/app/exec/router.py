"""웹용 exec 엔드포인트 (exec 게이트웨이 지시서 3-6).

POST /apps/{app_id}/exec-tickets {"target": "app|db|redis"} → {"ticket", "url", "protocol", "expires_in"}
- 쿠키 인증 + 기존 터미널 WebSocket과 같은 Origin 검사, 같은 권한 규칙("터미널은 앱 주인만", root는 남의 앱도).
- core는 그 테넌트 exec Pod의 내부 문에 발급을 요청하고, 받은 티켓과 공개 문 url을 돌려준다.
- 일반 발급은 core 로그(client.log_event, 티켓 sha256)로만 남긴다. 관리자가 남의 앱에 여는 경우만 admin_actions에도 남긴다.
GET /exec/features → 웹이 새 터미널 경로를 쓸지 (EXEC_AGENT_TERMINAL).
"""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import config
from app.admin import audit
from app.apps import sharing
from app.auth.deps import get_current_user
from app.auth.model import User
from app.exec import client as exec_client
from app.shared.db import get_db

router = APIRouter(tags=["exec"])

_OP = {"app": "shell", "db": "console", "redis": "console"}


class ExecTicketRequest(BaseModel):
    target: Literal["app", "db", "redis"]
    cols: int = Field(default=80, ge=1, le=1000)
    rows: int = Field(default=24, ge=1, le=1000)


@router.get("/exec/features")
def exec_features(user: User = Depends(get_current_user)) -> dict:
    return {"terminal": config.EXEC_AGENT_TERMINAL}


@router.post("/apps/{app_id}/exec-tickets")
async def create_exec_ticket(
    app_id: uuid.UUID,
    req: ExecTicketRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    if not config.EXEC_AGENT_TERMINAL:
        raise HTTPException(status_code=404, detail="exec terminal is disabled")
    # 기존 터미널 WebSocket(_ws_origin_allowed)과 같은 Origin 검사 — 티켓을 다른 사이트에서 받아 가지 못하게
    if request.headers.get("origin") not in config.ALLOWED_ORIGINS:
        raise HTTPException(status_code=403, detail="origin not allowed")
    found = sharing.access_for(db, user, app_id)
    if found is None:
        raise HTTPException(status_code=404, detail="app not found")
    app, role, by_admin = found
    if role != "owner":                                          # 터미널은 앱 주인만 (root는 owner로 들어온다)
        raise HTTPException(status_code=403, detail="터미널은 앱 주인만 열 수 있어요")
    if by_admin:
        audit.record(db, user, audit.action_of(request), app=app)

    try:
        res = await exec_client.issue_ticket(
            app.namespace, user_id=str(user.id), app_id=str(app.id), app_name=app.name,
            target=req.target, op=_OP[req.target], cols=req.cols, rows=req.rows,
        )
    except exec_client.ExecError as e:
        raise HTTPException(status_code=e.http_status, detail=e.message)
    return {
        "ticket": res["ticket"],
        "url": f"{config.EXEC_PUBLIC_URL}/exec/{app.namespace}/v1/sessions",
        "protocol": exec_client.PROTOCOL,
        "expires_in": 30,
    }
