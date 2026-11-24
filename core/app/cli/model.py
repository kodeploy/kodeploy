"""cli 도메인 ORM — 앱 주인이 웹에서 발급한 CLI 토큰."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# CLI 토큰 한 장. 원문(kdcli_…)은 발급 응답으로만 나가고 여기에는 sha256 hex만 남는다.
# expires_at은 "마지막으로 쓴 뒤 15분"이라 쓸 때 늘어난다. 발급 뒤 8시간 상한은 created_at에서 계산한다.
# 폐기·재발급으로 끝난 토큰은 지우지 않고 revoked_at을 적는다 — 누가 언제 발급하고 언제까지 썼는지 남기려고.
# 시각은 naive UTC (ci_tokens와 같은 방식).
class CliToken(Base):
    __tablename__ = "cli_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    app_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    issued_by: Mapped[uuid.UUID] = mapped_column(Uuid)                      # 발급한 사람 — 이 토큰으로 한 일은 이 사람의 행동
    issued_by_login: Mapped[str | None] = mapped_column(String(100), nullable=True)   # 그때의 GitHub 아이디
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
