"""ci 도메인 입출력 스키마."""

import uuid

from pydantic import BaseModel, ConfigDict, Field


class TokenExchangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # GitHub ID 토큰 (JWT). 보통 1~2KB라 넉넉히 16KB까지만 받는다.
    id_token: str = Field(min_length=1, max_length=16384)


class TokenExchangeResponse(BaseModel):
    token: str                  # kdci_… — 이 응답으로만 나간다
    expires_in: int             # 초
    app_id: uuid.UUID
    repository: str             # owner/repo
    ref: str                    # refs/heads/<branch>
    sha: str                    # 40자 소문자 hex
