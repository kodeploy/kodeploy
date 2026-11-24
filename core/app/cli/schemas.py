"""cli 도메인 입출력 스키마."""

from datetime import datetime

from pydantic import BaseModel


# 지금 살아 있는 CLI 토큰 (원문 없음)
class CliTokenInfo(BaseModel):
    issued_at: datetime
    issued_by_login: str | None = None
    last_used_at: datetime | None = None     # 정확도는 5분 안팎 (남은 시간이 10분 아래일 때만 적는다)
    expires_at: datetime                     # 지금 그대로 안 쓰면 이때 끝난다 (쓰면 늘어난다)
    max_expires_at: datetime                 # 발급 뒤 8시간 — 쓰고 있어도 이때 끝난다


class CliTokenIssued(CliTokenInfo):
    token: str                               # kdcli_… — 이 응답으로만 나간다
    idle_seconds: int                        # 마지막으로 쓴 뒤 이만큼 지나면 만료


# GET /apps/{id}/cli-token — 살아 있는 토큰이 없으면 current가 null
class CliTokenState(BaseModel):
    current: CliTokenInfo | None = None
    idle_seconds: int
    max_seconds: int
