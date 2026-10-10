"""admin 도메인 ORM."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db import Base


# 관리자가 남의 앱·계정에 한 동작 한 건. 이름은 그때 값을 적어 둔다 — 앱·계정이 지워져도 기록은 읽힌다.
class AdminAction(Base):
    __tablename__ = "admin_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    actor_login: Mapped[str | None] = mapped_column(String(100), nullable=True)
    app_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    app_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_login: Mapped[str | None] = mapped_column(String(100), nullable=True)   # 앱 주인 또는 대상 계정
    action: Mapped[str] = mapped_column(String(200))                                # "PUT /deploy/env" 꼴
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
