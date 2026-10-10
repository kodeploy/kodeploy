"""관리자 동작 기록 — 관리자 권한으로 남의 앱·계정에 손댄 것만 남긴다 (보기만 한 것은 남기지 않는다).

앱 접근 판정(deploy 라우터의 _app_at, 앱 라우터의 _owned_app, 터미널 WebSocket)이 "관리자 권한으로 들어왔다"고
알려 주면 그 자리에서 요청 하나를 적는다. 동작은 라우트 경로로 적는다 — 화면이 아는 경로는 우리말로 바꿔 보여 준다.
"""

import uuid

from sqlalchemy.orm import Session
from starlette.requests import HTTPConnection

from app.admin.model import AdminAction
from app.apps.model import App
from app.auth.model import User

_APP_PREFIX = "/apps/{app_id}"


# 요청의 동작 이름 — "메서드 라우트경로". 앱 안 동작(/apps/{app_id}/deploy/...)은 앞부분을 떼 /deploy/... 로 적는다
# (같은 라우터가 옛 경로에도 붙어 있어 이름이 하나로 모인다). WebSocket은 메서드 자리에 WS.
def action_of(conn: HTTPConnection) -> str:
    route = conn.scope.get("route")
    path = getattr(route, "path", None) or conn.url.path
    if path.startswith(_APP_PREFIX + "/deploy"):
        path = path[len(_APP_PREFIX):]
    method = "WS" if conn.scope.get("type") == "websocket" else conn.scope.get("method", "")
    return f"{method} {path}"


def record(db: Session, actor: User, action: str, *, app: App | None = None, target: User | None = None) -> None:
    owner_id: uuid.UUID | None = app.owner_id if app is not None else None
    if target is None and owner_id is not None:
        target = db.get(User, owner_id)
    db.add(AdminAction(
        actor_id=actor.id,
        actor_login=actor.login,
        app_id=app.id if app is not None else None,
        app_name=app.name if app is not None else None,
        target_login=target.login if target is not None else None,
        action=action[:200],
    ))
    db.commit()


def recent(db: Session, limit: int = 200) -> list[AdminAction]:
    return db.query(AdminAction).order_by(AdminAction.id.desc()).limit(limit).all()
