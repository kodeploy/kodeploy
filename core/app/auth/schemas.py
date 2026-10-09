"""auth 도메인 응답 DTO."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    github_id: int
    login: str
    email: str | None = None
    avatar_url: str | None = None
    app_name: str | None = None                          # 첫 배포 후에만 값 — 클라이언트가 DeployForm name 입력란 분기에 사용
    site_enabled: bool = False                           # 정적 사이트 슬롯 선언 — DeployForm 정적 토글 prefill에 사용
    tier: str = "basic"                                  # 앱 개수 등급 — 새 앱 만들기 버튼 분기에 사용
    max_apps: int | None = 1                             # 이 등급이 만들 수 있는 앱 수 (None=무제한)
    app_count: int = 0                                   # 지금 내가 가진 앱 수
    role: str = "user"                                   # "user" | "admin" | "root" — TopBar 관리자 링크 노출 분기에 사용
    github_connected: bool = False                       # private repo 연결(App 설치) 여부 — DeployForm 연결 버튼 분기
    created_at: datetime
