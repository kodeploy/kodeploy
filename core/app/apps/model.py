"""apps 도메인 ORM — 앱 개체. 앱 하나 = K8s ns 하나, 소유자는 유저 한 명.

앱 속성(이름·도메인·슬롯·파이프라인)이 users에 붙어 있던 것을 독립 테이블로 뺀 것이다 (1유저:1앱 해제).
core는 이 테이블만 읽고 쓴다. users의 같은 칸들은 옛 값으로 남아 있고(정리 마이그레이션 전까지) 아무도 읽지 않는다.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# 등급 — 유저가 만들 수 있는 앱 수. 코드 상수가 아니라 테이블이라 개수를 DB/관리자 API로 조절한다.
class Tier(Base):
    __tablename__ = "tiers"

    name: Mapped[str] = mapped_column(String(20), primary_key=True)
    # None이면 무제한 (master)
    max_apps: Mapped[int | None] = mapped_column(Integer, nullable=True)


class App(Base):
    __tablename__ = "apps"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # 소유자 — 삭제·공유·도메인은 소유자만. FK는 걸지 않는다 (builds.user_id와 같은 방침).
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    # 서브도메인이라 전역 유일 ({app}.kodeploy.com). users.app_name에서 옮겨 왔다.
    name: Mapped[str] = mapped_column(String(50), unique=True)
    # K8s ns 이름. 기존 앱은 tenant-<유저 hex8>, 새 앱은 app-<id hex8>이라 규칙으로 계산할 수 없어 칸으로 저장한다.
    namespace: Mapped[str] = mapped_column(String(63), unique=True)
    # 서버+정적 슬롯 선언 (desired state) — users.site_enabled와 같은 뜻.
    site_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # 커스텀 도메인 (CF for SaaS custom hostname). None이면 미설정. status는 "pending" | "active".
    custom_domain: Mapped[str | None] = mapped_column(String(253), nullable=True, unique=True)
    custom_domain_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 플랫폼 기능 밖의 추가 hostname (콤마 구분) — 운영자가 DB에서 직접 등록.
    extra_hostnames: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 빌드·배포 경로: "v1"(core가 직접) | "v2"(Go 빌더 → kodeploy-apps 커밋 → Argo).
    # 운영자가 DB에서 앱 하나씩 켠다 (UPDATE apps SET pipeline='v2' WHERE name=...). 앱을 삭제하면 같이 사라진다.
    pipeline: Mapped[str] = mapped_column(String(2), default="v1", server_default="v1")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


# 공유 — 앱 주인 말고 이 앱을 쓰는 유저. role은 "viewer"(보기) | "editor"(배포·환경변수까지).
# 주인은 여기 넣지 않고 apps.owner_id로만 표현한다. FK는 걸지 않는다 (다른 테이블과 같은 방침).
class AppMember(Base):
    __tablename__ = "app_members"

    app_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, index=True)
    role: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


# 받는 사람이 수락하기 전의 초대. 이메일(GitHub 로그인에서 받은 primary 이메일) 또는 GitHub 아이디로 가리킨다 —
# 둘 중 하나만 채우고, 상대가 아직 가입 전이어도 그 이메일·아이디로 로그인하면 보인다. 수락·거절·취소하면 행이 지워진다.
class AppInvite(Base):
    __tablename__ = "app_invites"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    app_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    role: Mapped[str] = mapped_column(String(10))
    invited_by: Mapped[uuid.UUID] = mapped_column(Uuid)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)          # 소문자
    github_login: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)   # 소문자
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
