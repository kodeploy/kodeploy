"""웹·CLI용 exec 엔드포인트.

POST /apps/{app_id}/exec-tickets {"target": "app|db|redis"} → {"ticket", "url", "protocol", "expires_in"}
- 웹: 쿠키 인증 + CORS 허용 목록과 같은 Origin 검사, 권한 규칙은 "터미널은 앱 주인만"(root는 남의 앱도).
- CLI: Authorization: Bearer kdcli_… (앱 주인이 웹에서 발급한 CLI 토큰, app/cli). 토큰이 인증이라 쿠키는 보지 않는다.
  CLI는 Origin을 보내지 않는다 — Origin이 붙어 오면 공개 문(게이트웨이)처럼 허용 목록만 받는다. 대상은 웹과 같은 세 가지뿐.
- core는 그 테넌트 exec Pod의 내부 문에 발급을 요청하고, 받은 티켓과 공개 문 url을 돌려준다.
- 일반 발급은 core 로그(client.log_event, 티켓 sha256, source=web|cli)로만 남긴다. 관리자가 남의 앱에 여는 경우만 admin_actions에도 남긴다.
"""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import config
from app.admin import audit
from app.apps import sharing
from app.auth.deps import get_current_user, get_current_user_optional
from app.auth.model import User
from app.ci.service import bearer_token
from app.cli import service as cli_service
from app.exec import client as exec_client
from app.shared.db import get_db
from app.shared.errors import ApiError

router = APIRouter(tags=["exec"])

_OP = {"app": "shell", "db": "console", "redis": "console"}


class ExecTicketRequest(BaseModel):
    target: Literal["app", "db", "redis"]
    cols: int = Field(default=80, ge=1, le=1000)
    rows: int = Field(default=24, ge=1, le=1000)


@router.post("/apps/{app_id}/exec-tickets")
async def create_exec_ticket(
    app_id: uuid.UUID,
    req: ExecTicketRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
) -> dict:
    raw = bearer_token(request)
    origin = request.headers.get("origin")
    if cli_service.is_cli_token(raw):
        # CLI 토큰 — 발급한 사람이 아직 이 앱의 주인인지까지 authenticate가 본다. 티켓은 발급한 사람 이름으로 나간다
        if origin is not None and origin not in config.ALLOWED_ORIGINS:
            raise HTTPException(status_code=403, detail="origin not allowed")
        try:
            p = cli_service.authenticate(db, raw, app_id)
        except cli_service.CliAuthError as e:
            raise ApiError(e.status, e.detail, e.code)
        app, user, source = p.app, p.user, "cli"
        if p.by_admin:
            audit.record(db, user, audit.action_of(request), app=app, source="cli")
    else:
        user = get_current_user(user)
        # CORS 허용 목록과 같은 Origin 검사 — 티켓을 다른 사이트에서 받아 가지 못하게
        if origin not in config.ALLOWED_ORIGINS:
            raise HTTPException(status_code=403, detail="origin not allowed")
        found = sharing.access_for(db, user, app_id)
        if found is None:
            raise HTTPException(status_code=404, detail="app not found")
        app, role, by_admin = found
        if role != "owner":                                          # 터미널은 앱 주인만 (root는 owner로 들어온다)
            raise HTTPException(status_code=403, detail="터미널은 앱 주인만 열 수 있어요")
        if by_admin:
            audit.record(db, user, audit.action_of(request), app=app)
        source = "web"

    try:
        res = await exec_client.issue_ticket(
            app.namespace, user_id=str(user.id), app_id=str(app.id), app_name=app.name,
            target=req.target, op=_OP[req.target], cols=req.cols, rows=req.rows, source=source,
        )
    except exec_client.ExecError as e:
        raise HTTPException(status_code=e.http_status, detail=e.message)
    return {
        "ticket": res["ticket"],
        "url": f"{config.EXEC_PUBLIC_URL}/exec/{app.namespace}/v1/sessions",
        "protocol": exec_client.PROTOCOL,
        "expires_in": 30,
    }
