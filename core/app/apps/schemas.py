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
    # 유저와 앱의 관계. 지금은 소유자뿐이고, 공유가 생기면 "viewer" | "editor"가 더해진다.
    role: str = "owner"


class AppCreate(BaseModel):
    name: str | None = None      # 비우면 repo 이름으로, 그것도 없으면 app-<hex8>로 짓는다
    repo_url: str = ""           # 이름을 자동으로 지을 때만 쓴다
