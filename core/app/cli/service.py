"""CLI 토큰 — 앱 주인이 웹에서 발급하고, 로컬 kodeploy CLI가 Authorization: Bearer로 싣는다.

- 원문은 kdcli_ + 32바이트 난수(urlsafe base64). 발급 응답으로 한 번만 나가고 DB에는 sha256만 둔다. 로그에도 원문은 없다.
- 수명: 마지막으로 쓴 뒤 15분(쓸 때마다 15분으로 늘린다). 발급 뒤 8시간이 지나면 쓰고 있어도 만료.
  늘리는 쓰기는 남은 시간이 10분 아래일 때만 한다 — 빌드 상태 조회가 몇 초마다 와서 요청마다 쓰지 않으려고.
  처음 쓸 때만은 예외로 한 번 쓴다(마지막 사용 시각이 "아직 없음"으로 남지 않게).
- 앱당 살아 있는 토큰은 하나. 새로 발급하면 이전 토큰은 바로 끝나고, 폐기도 같은 방식이다. 끝난 행은 지우지 않는다.
- 받는 라우트는 재배포·상태 조회·최근 커밋 조회뿐이다 (deploy/router.py). 재배포는 발급한 사람이 웹에서 "배포하기"를 누른 것과
  같은 규칙이다 — CI 토큰의 규칙(커밋 강제, 다른 칸 고정)은 쓰지 않는다.
- "GitHub Actions 배포 허용"(apps.ci_deploy_enabled)과는 따로다. CI 토큰(kdci_, app/ci)은 건드리지 않는다.
- 쓸 때마다 발급한 사람이 아직 그 앱의 주인인지 다시 본다 (root가 남의 앱에 발급한 경우도 같은 판정).
"""

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app import config
from app.apps import sharing
from app.apps.model import App
from app.auth.model import User
from app.cli.model import CliToken

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "kdcli_"

EXPIRED_DETAIL = "CLI 토큰이 만료됐어요. 웹에서 다시 발급해 주세요"


class CliAuthError(Exception):
    def __init__(self, status: int, detail: str, code: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def is_cli_token(raw: str | None) -> bool:
    return raw is not None and raw.startswith(TOKEN_PREFIX)


# 발급 뒤 8시간 — 쓰고 있어도 이때 끝난다
def hard_expiry(row: CliToken) -> datetime:
    return row.created_at + timedelta(seconds=config.CLI_TOKEN_MAX_SECONDS)


def _live_filter(q, app_id: uuid.UUID, now: datetime):
    return q.filter(CliToken.app_id == app_id, CliToken.revoked_at.is_(None), CliToken.expires_at > now)


def live_token(db: Session, app_id: uuid.UUID) -> CliToken | None:
    now = _now()
    row = _live_filter(db.query(CliToken), app_id, now).order_by(CliToken.id.desc()).first()
    if row is None or hard_expiry(row) <= now:
        return None
    return row


# 아직 살아 있는 토큰을 끝낸다 (재발급·폐기). 몇 개를 끝냈는지 돌려준다. 커밋은 부르는 쪽이 한다.
def _end_live(db: Session, app_id: uuid.UUID, now: datetime) -> int:
    return _live_filter(db.query(CliToken), app_id, now).update(
        {CliToken.revoked_at: now, CliToken.expires_at: now}, synchronize_session=False,
    )


@dataclass
class Issued:
    token: str          # 원문 — 응답으로만 내보낸다
    row: CliToken


def issue(db: Session, app: App, user: User) -> Issued:
    """새 토큰을 발급하고 그 앱의 이전 토큰은 바로 끝낸다. 앱 행을 잠가 같은 앱의 발급을 줄 세운다 —
    두 번 겹쳐 눌러도 살아 있는 토큰은 하나다 (MySQL SELECT … FOR UPDATE)."""
    db.query(App.id).filter(App.id == app.id).with_for_update().one()
    now = _now()
    ended = _end_live(db, app.id, now)
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = CliToken(
        token_hash=hash_token(raw),
        app_id=app.id,
        issued_by=user.id,
        issued_by_login=user.login,
        created_at=now,
        expires_at=now + timedelta(seconds=min(config.CLI_TOKEN_IDLE_SECONDS, config.CLI_TOKEN_MAX_SECONDS)),
    )
    db.add(row)
    db.commit()
    logger.info("cli token issued app=%s token_id=%s by=%s ended=%d", app.id, row.id, user.id, ended)
    return Issued(token=raw, row=row)


def revoke(db: Session, app: App, user: User) -> int:
    now = _now()
    ended = _end_live(db, app.id, now)
    db.commit()
    logger.info("cli token revoked app=%s by=%s ended=%d", app.id, user.id, ended)
    return ended


@dataclass
class CliPrincipal:
    token: CliToken
    app: App
    user: User          # 발급한 사람 — 이 토큰으로 한 일은 이 사람의 행동이다
    by_admin: bool      # 발급한 사람이 관리자 권한(root)으로 남의 앱에 들어온 경우


def authenticate(db: Session, raw: str, app_id: uuid.UUID | None) -> CliPrincipal:
    """요청마다 확인: 토큰이 있고, 폐기·만료가 아니고, 경로의 앱이 토큰의 앱이고, 발급한 사람이 아직 그 앱의 주인이다.
    통과하면 마지막 사용 시각과 만료를 늘린다 (남은 시간이 10분 아래일 때만 DB에 쓴다)."""
    if not is_cli_token(raw):
        raise CliAuthError(401, "CLI 토큰이 아니에요", "cli_token_invalid")
    row = db.query(CliToken).filter(CliToken.token_hash == hash_token(raw)).first()
    if row is None:
        raise CliAuthError(401, "CLI 토큰이 없어요. 웹에서 발급해 주세요", "cli_token_invalid")
    now = _now()
    if row.revoked_at is not None:
        raise CliAuthError(401, "폐기됐거나 새로 발급해서 끝난 CLI 토큰이에요. 웹에서 다시 발급해 주세요", "cli_token_revoked")
    if row.expires_at <= now or hard_expiry(row) <= now:
        raise CliAuthError(401, EXPIRED_DETAIL, "cli_token_expired")
    if app_id is None:
        raise CliAuthError(400, "app_id가 필요해요 (/apps/{app_id}/deploy/...)", "app_id_required")
    if row.app_id != app_id:
        raise CliAuthError(403, "이 CLI 토큰은 다른 앱용이에요", "cli_token_app_mismatch")
    user = db.get(User, row.issued_by)
    found = sharing.access_for(db, user, app_id) if user is not None else None
    if found is None or found[1] != "owner":
        raise CliAuthError(401, "토큰을 발급한 사람이 이제 이 앱의 주인이 아니에요", "cli_token_invalid")
    app, _role, by_admin = found
    _touch(db, row, now)
    return CliPrincipal(token=row, app=app, user=user, by_admin=by_admin)


def _touch(db: Session, row: CliToken, now: datetime) -> None:
    left = (row.expires_at - now).total_seconds()
    if row.last_used_at is not None and left >= config.CLI_TOKEN_EXTEND_BELOW_SECONDS:
        return
    values: dict = {CliToken.last_used_at: now}
    new_exp = min(now + timedelta(seconds=config.CLI_TOKEN_IDLE_SECONDS), hard_expiry(row))
    if new_exp > row.expires_at:
        values[CliToken.expires_at] = new_exp
    # 그 사이 폐기됐으면 되살리지 않는다
    db.query(CliToken).filter(CliToken.id == row.id, CliToken.revoked_at.is_(None)).update(
        values, synchronize_session=False,
    )
    db.commit()


# 앱 설정 화면이 보이는 지금 토큰 (원문 없음). 살아 있는 토큰이 없으면 None.
def summary(db: Session, app_id: uuid.UUID) -> dict | None:
    row = live_token(db, app_id)
    if row is None:
        return None
    return {
        "issued_at": row.created_at,
        "issued_by_login": row.issued_by_login,
        "last_used_at": row.last_used_at,
        "expires_at": min(row.expires_at, hard_expiry(row)),
        "max_expires_at": hard_expiry(row),
    }
