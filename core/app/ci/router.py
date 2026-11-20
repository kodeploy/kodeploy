"""CI API — GitHub Actions ID 토큰을 CI 토큰으로 바꾼다.

POST /apps/{app_id}/ci/token 은 인증 없이 열려 있다 (본문의 ID 토큰이 인증). 검사 순서는 service.exchange.
JWKS 조회가 동기 HTTP라 일반 def로 둬서 스레드풀에서 돈다 (이벤트 루프를 막지 않게).
발급한 CI 토큰은 재배포(POST /apps/{app_id}/deploy/redeploy)와 상태 조회(GET /apps/{app_id}/deploy/{build_id})에서만 받는다.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import config
from app.ci import service
from app.ci.schemas import TokenExchangeRequest, TokenExchangeResponse
from app.shared.db import get_db
from app.shared.errors import ApiError

router = APIRouter(prefix="/apps/{app_id}/ci", tags=["ci"])


@router.post("/token", response_model=TokenExchangeResponse, status_code=201)
def exchange_token(
    app_id: uuid.UUID,
    req: TokenExchangeRequest,
    db: Session = Depends(get_db),
) -> TokenExchangeResponse:
    try:
        issued = service.exchange(db, app_id, req.id_token)
    except service.CiAuthError as e:
        raise ApiError(e.status, e.detail, e.code)
    row = issued.row
    return TokenExchangeResponse(
        token=issued.token,
        expires_in=config.CI_TOKEN_TTL_SECONDS,
        app_id=row.app_id,
        repository=row.repository,
        ref=row.ref,
        sha=row.sha,
    )
