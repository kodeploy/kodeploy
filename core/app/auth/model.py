"""auth 도메인 ORM — User + UserSession (HttpSession 스타일 DB 세션)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# GitHub 식별자(github_id)를 진실원으로 둠. login은 사용자가 GitHub에서 바꿀 수 있으니 캐시.
class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    github_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    login: Mapped[str] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # GitHub App을 자기 repo에 설치하면 OAuth 콜백에 실려오는 installation id (BigInteger).
    # 빌드 시점에 이 installation의 access token(1h, repo-scoped)을 발급해 private repo를 clone한다.
    # None이면 App 미설치 → public repo만. 토큰이 이 installation에 스코프되므로 남의 private repo는 못 받음.
    github_installation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 등급: "user"(기본) | "admin"(관리자 페이지) | "root"(소유자 — 등급 변경 가능).
    # root는 코드에 하드코딩 안 함 — 운영자가 DB에서 직접 지정(재시작에도 안 덮어씀).
    # admin은 root가 /admin에서 부여.
    role: Mapped[str] = mapped_column(String(10), default="user")
    # 앱 개수 등급 (tiers.name). role(권한)과 별개다 — 만들 수 있는 앱 수만 정한다.
    tier: Mapped[str] = mapped_column(String(20), default="basic", server_default="basic")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

    # private repo 연결 여부 (UI 분기용) — App 설치로 installation_id가 잡혔으면 True.
    # 컬럼 아닌 property — installation_id가 진실원, UserOut이 from_attributes로 읽어 노출.
    @property
    def github_connected(self) -> bool:
        return self.github_installation_id is not None


# 세션 ID 자체가 secrets.token_urlsafe(48) (64자) — cookie에 그대로 담기고 DB lookup으로 검증.
# JWT 안 씀 — DB가 진실원이라 revoke가 즉시 반영됨.
class UserSession(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)

    __table_args__ = (Index("ix_sessions_user_expires", "user_id", "expires_at"),)
