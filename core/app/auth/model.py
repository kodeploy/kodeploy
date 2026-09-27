"""auth 도메인 ORM — User + UserSession (HttpSession 스타일 DB 세션)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, Text, Uuid
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
    # 1유저=1앱 — 첫 배포 시 결정되어 fix. 서브도메인이라 unique. None이면 아직 배포 안 함.
    app_name: Mapped[str | None] = mapped_column(
        String(50), nullable=True, unique=True
    )
    # 정적 사이트 슬롯 선언 (desired state) — 배포 제출 시 토글값으로 set.
    # true면 {app}.kodeploy.com=정적 / {app}-api.kodeploy.com=서버, 커스텀 도메인은 정적에.
    # false면 서버가 {app}·{app}-api 둘 다. 라우팅 규칙의 유일한 진실원 (K8s 상태 아님 —
    # 동시 빌드 중에도 결정적이어야 해서 DB 선언값 사용).
    site_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # 커스텀 도메인 (유저가 자기 도메인을 앱에 연결 — CF for SaaS custom hostname).
    # 1유저=1앱이라 도메인도 1개 → 별도 테이블 없이 User에 직접. None이면 미설정.
    custom_domain: Mapped[str | None] = mapped_column(
        String(253), nullable=True, unique=True  # 253 = DNS 호스트네임 최대 길이
    )
    # CF 검증 상태: "pending"(DCV/cert 발급 중) | "active". None이면 도메인 미설정.
    custom_domain_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 등급: "user"(기본) | "admin"(관리자 페이지) | "root"(소유자 — 등급 변경 가능).
    # root는 코드에 하드코딩 안 함 — 운영자가 DB에서 직접 지정(재시작에도 안 덮어씀).
    # admin은 root가 /admin에서 부여.
    role: Mapped[str] = mapped_column(String(10), default="user")
    # 플랫폼 기능 밖의 추가 hostname (콤마 구분) — 운영자가 DB에서 직접 등록 (role과 동일 방침).
    # 예: apex 도메인(Dailo의 dailoapp.com — 커스텀 도메인 기능은 서브도메인 전용이라 거부).
    # HTTPRoute hostnames reconcile 시 기본 서브도메인·커스텀 도메인과 함께 통째로 주입되므로
    # kubectl 수동 patch와 달리 다음 갱신에 사라지지 않는다.
    extra_hostnames: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 빌드·배포 경로: "v1"(core가 직접, 기본) | "v2"(Go 빌더 → kodeploy-apps 커밋 → Argo).
    # 운영자가 DB에서 앱 하나씩 켠다. v2는 Dockerfile 서버 빌드만 받는다 (build/v2.py).
    pipeline: Mapped[str] = mapped_column(String(2), default="v1", server_default="v1")
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
