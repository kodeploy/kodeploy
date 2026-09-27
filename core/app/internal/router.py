"""POST /internal/builds/{build_id}/events — Go 빌더 콜백 (계약 3-2, 서명 3-3).

공개 HTTPRoute가 api.kodeploy.com/ 전체를 core로 보내므로 게이트웨이를 거친 요청을 여기서 걸러낸다:
Envoy가 붙이는 X-Forwarded-For·X-Envoy-External-Address, 공개 route 매칭 조건인 X-Origin-Verify 중
하나라도 있으면 404. 빌더는 Service(http://kodeploy-core)로 바로 부르므로 이 헤더가 없다.
"""

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request

from app import config
from app.builder import sign
from app.internal import events

router = APIRouter(prefix="/internal", tags=["internal"], include_in_schema=False)

_GATEWAY_HEADERS = ("x-forwarded-for", "x-envoy-external-address", "x-origin-verify")
MAX_BODY = 16 << 20   # 로그 이벤트는 최대 200줄 × 줄당 64KiB


@router.post("/builds/{build_id}/events")
async def build_event(build_id: str, request: Request) -> dict:
    if any(h in request.headers for h in _GATEWAY_HEADERS) or not config.BUILDER_HMAC_SECRET:
        raise HTTPException(status_code=404)
    body = await request.body()
    if len(body) > MAX_BODY:
        raise HTTPException(status_code=413)
    path = request.scope.get("raw_path", b"").decode("latin-1") or request.url.path   # 쿼리 없는 경로 원문
    if not sign.verify(
        config.BUILDER_HMAC_SECRET.encode(),
        request.headers.get(sign.HEADER_TIMESTAMP),
        request.headers.get(sign.HEADER_SIGNATURE),
        request.method, path, body,
    ):
        raise HTTPException(status_code=401)
    try:
        ev = json.loads(body)
        int(ev["seq"])
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status_code=400, detail="invalid event")
    # 동기 DB 호출이라 메인 루프를 막지 않게 스레드에서
    result = await asyncio.to_thread(events.apply, build_id, ev)
    return {"result": result}
