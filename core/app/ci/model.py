"""ci 도메인 ORM — GitHub ID 토큰을 교환해 발급한 CI 토큰."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# CI 토큰 한 장. 원문(kdci_…)은 교환 응답으로만 나가고 여기에는 sha256 hex만 남는다.
# jti는 교환에 쓴 ID 토큰의 jti — 유일 제약이 "ID 토큰 한 장은 한 번만 교환"을 지킨다.
# 나머지 칸은 그 ID 토큰의 클레임이다. 이 토큰으로 할 수 있는 일(어느 앱, 어느 저장소·브랜치의 어느 커밋)을 정한다.
# 시각은 naive UTC (DB 칸이 DATETIME이라 sessions.expires_at과 같은 방식으로 비교한다).
class CiToken(Base):
    __tablename__ = "ci_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    jti: Mapped[str] = mapped_column(String(128), unique=True)
    app_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    repository: Mapped[str] = mapped_column(String(200))          # owner/repo
    ref: Mapped[str] = mapped_column(String(255))                 # refs/heads/<branch>
    sha: Mapped[str] = mapped_column(String(40))                  # 40자 소문자 hex
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    run_attempt: Mapped[str | None] = mapped_column(String(10), nullable=True)
    actor: Mapped[str | None] = mapped_column(String(100), nullable=True)
    workflow: Mapped[str | None] = mapped_column(String(255), nullable=True)
    event_name: Mapped[str] = mapped_column(String(30))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
