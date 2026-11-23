"""apps 도메인 입출력 스키마."""

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


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
    pipeline: str = "v2"             # 화면이 롤백을 보일지 정한다 (v1 앱은 이제 없다)
    admin: bool = False              # 관리자 권한으로 보는 남의 앱이면 True — 화면이 "관리자로 보는 중" 띠를 띄운다
    ci_deploy_enabled: bool = False  # GitHub Actions 배포 허용 (앱 설정, 주인만 바꾼다)
    ci_workflow: str | None = None   # 교환을 허용할 워크플로 파일. None이면 어느 워크플로든


# 워크플로 파일은 GitHub가 읽는 자리(.github/workflows 바로 아래)의 .yml·.yaml만 받는다. 하위 폴더는 GitHub가 읽지 않는다.
WORKFLOW_PATH_RE = re.compile(r"^\.github/workflows/[A-Za-z0-9._-]+\.ya?ml$")


class CiDeployUpdate(BaseModel):
    enabled: bool
    # 보내지 않으면 지금 값을 그대로 둔다(옛 웹은 enabled만 보낸다). 빈 문자열이나 null이면 지정을 지운다.
    workflow: str | None = None

    @field_validator("workflow")
    @classmethod
    def _workflow_path(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if len(v) > 255 or ".." in v or not WORKFLOW_PATH_RE.match(v):
            raise ValueError("워크플로 파일은 .github/workflows/<이름>.yml 형식이어야 해요")
        return v


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
