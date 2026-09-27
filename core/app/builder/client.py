"""빌더 HTTP 클라이언트 — POST /internal/deploys, DELETE /internal/deploys/{build_id}.

응답 (builder/internal/api/server.go):
  202 {build_id}            받음
  400 {error}               검증 실패 — 이유를 그대로 유저에게
  401                       서명 실패 — BUILDER_HMAC_SECRET 불일치
  409 {error, build_id}     같은 build_id 또는 같은 namespace+slot이 진행 중 → 그 build_id를 DELETE 후 다시
  429 + Retry-After         동시 빌드 상한 → 기다렸다 다시
  5xx / 연결 실패           잠깐 뒤 다시
"""

import asyncio
import json
import logging
from typing import Awaitable, Callable

import httpx

from app import config
from app.builder import sign

logger = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(10.0)
MAX_ATTEMPTS = 6                 # 409·429·5xx 재시도를 합친 상한
BUSY_WAIT_CAP = 60.0             # 429 Retry-After 한 번에 기다리는 상한 (초)
CONFLICT_WAIT = 2.0              # 409 → DELETE 뒤 옛 요청이 정리되기를 기다리는 시간
ERROR_BACKOFF = (1.0, 2.0, 4.0, 8.0, 8.0)


class BuilderError(Exception):
    """빌더가 받지 않았다. message는 화면에 보여도 되는 이유."""


def _secret() -> bytes:
    if not config.BUILDER_HMAC_SECRET:
        raise BuilderError("빌더 연결이 설정되지 않았습니다 (BUILDER_HMAC_SECRET)")
    return config.BUILDER_HMAC_SECRET.encode()


def _transport() -> httpx.AsyncBaseTransport | None:
    return None  # 테스트가 MockTransport로 갈아끼운다


async def _request(method: str, path: str, body: bytes = b"") -> httpx.Response:
    headers = sign.headers(_secret(), method, path, body)
    if body:
        headers["Content-Type"] = "application/json"
    async with httpx.AsyncClient(base_url=config.BUILDER_URL, timeout=TIMEOUT, transport=_transport()) as c:
        return await c.request(method, path, content=body, headers=headers)


def _error_text(r: httpx.Response) -> str:
    try:
        return str(r.json().get("error") or r.text)
    except ValueError:
        return r.text


async def cancel(build_id: str) -> bool:
    """진행 중인 요청을 취소한다. 빌더가 받았으면(202) True, 이미 없으면(404) False."""
    r = await _request("DELETE", f"/internal/deploys/{build_id}")
    if r.status_code == 202:
        return True
    if r.status_code == 404:
        return False
    raise BuilderError(f"빌더 취소 실패 ({r.status_code}): {_error_text(r)}")


async def submit(payload: dict, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
    """배포 요청을 보낸다. 202를 받으면 돌아오고, 받지 못하면 BuilderError.

    409면 응답의 진행 중 build_id를 취소하고 다시 보낸다 (재배포가 옛 빌드를 대체 — v1 _cancel_stale_builds와 같은 역할).
    429면 Retry-After(상한 BUSY_WAIT_CAP)만큼, 5xx·연결 실패면 짧게 기다렸다 다시. 전부 합쳐 MAX_ATTEMPTS번.
    """
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    last = "응답 없음"
    for attempt in range(MAX_ATTEMPTS):
        try:
            r = await _request("POST", "/internal/deploys", body)
        except httpx.HTTPError as e:
            last = f"빌더에 연결하지 못했습니다: {e.__class__.__name__}"
            await sleep(ERROR_BACKOFF[min(attempt, len(ERROR_BACKOFF) - 1)])
            continue
        code = r.status_code
        if code == 202:
            return
        if code == 400:
            raise BuilderError(_error_text(r))
        if code == 401:
            raise BuilderError("빌더가 서명을 거부했습니다 (BUILDER_HMAC_SECRET 확인)")
        if code == 409:
            other = (r.json() or {}).get("build_id")
            if other == payload.get("build_id"):
                return  # 앞선 시도가 이미 받아졌다 (응답만 못 받음)
            last = f"진행 중인 요청과 충돌했습니다 ({other})"
            if other:
                logger.info("builder 409: cancelling %s to submit %s", other, payload.get("build_id"))
                await cancel(other)
            await sleep(CONFLICT_WAIT)
            continue
        if code == 429:
            try:
                wait = float(r.headers.get("Retry-After", "15"))
            except ValueError:
                wait = 15.0
            last = "빌더가 바빠서 받지 못했습니다 (동시 빌드 상한)"
            await sleep(min(max(wait, 1.0), BUSY_WAIT_CAP))
            continue
        last = f"빌더 오류 ({code}): {_error_text(r)}"
        if code < 500:
            raise BuilderError(last)
        await sleep(ERROR_BACKOFF[min(attempt, len(ERROR_BACKOFF) - 1)])
    raise BuilderError(last)
