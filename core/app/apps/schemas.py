"""apps 도메인 입출력 스키마."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AppOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    site_enabled: bool = False
    custom_domain: str | None = None
    created_at: datetime
    # 유저와 앱의 관계: "owner" | "editor" | "viewer"
    role: str = "owner"
    owner_login: str | None = None   # 공유받은 앱이면 주인의 GitHub 아이디
    pipeline: str = "v1"             # "v1" | "v2" — 화면이 v2에만 있는 기능(롤백)을 보일지 정한다
    admin: bool = False              # 관리자 권한으로 보는 남의 앱이면 True — 화면이 "관리자로 보는 중" 띠를 띄운다


class InviteCreate(BaseModel):
    target: str          # 이메일 또는 GitHub 아이디 — "@"가 들어 있으면 이메일
    role: str = "viewer"


class RoleUpdate(BaseModel):
    role: str


class InviteOut(BaseModel):
    id: uuid.UUID
    app_name: str
    owner_login: str | None = None
    role: str
    created_at: datetime


class AppCreate(BaseModel):
    name: str | None = None      # 비우면 repo 이름으로, 그것도 없으면 app-<hex8>로 짓는다
    repo_url: str = ""           # 이름을 자동으로 지을 때만 쓴다
