"""core ↔ 빌더 HMAC 서명 — builder/internal/sign과 같은 규칙 (양방향).

    signature = hex(HMAC-SHA256(secret, timestamp + "\\n" + METHOD + "\\n" + path + "\\n" + body))

path는 쿼리를 뺀 경로 원문, timestamp는 헤더에 실린 문자열 그대로(unix 초), body는 보낸 바이트 그대로.
검증은 시각 차이 60초 이내 + 상수 시간 비교.
"""

import hashlib
import hmac
import re
import time

HEADER_TIMESTAMP = "X-Kodeploy-Timestamp"
HEADER_SIGNATURE = "X-Kodeploy-Signature"
MAX_SKEW_SECONDS = 60
# Go strconv.ParseInt·hex.DecodeString이 받는 형식만 (파이썬 int()·fromhex()는 공백·_도 받는다)
_TS_RE = re.compile(r"[+-]?[0-9]+")
_HEX_RE = re.compile(r"(?:[0-9a-fA-F]{2})*")


def compute(secret: bytes, ts: str, method: str, path: str, body: bytes) -> str:
    msg = b"\n".join([ts.encode(), method.encode(), path.encode(), body])
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


# 보낼 요청의 서명 헤더
def headers(secret: bytes, method: str, path: str, body: bytes, now: float | None = None) -> dict[str, str]:
    ts = str(int(now if now is not None else time.time()))
    return {HEADER_TIMESTAMP: ts, HEADER_SIGNATURE: compute(secret, ts, method, path, body)}


# 받은 요청 검증. 통과하면 True. 헤더 누락·시각 초과·hex 아님·불일치는 전부 False.
def verify(secret: bytes, ts: str | None, sig: str | None, method: str, path: str, body: bytes,
           now: float | None = None) -> bool:
    if not ts or not sig or not secret:
        return False
    if not _TS_RE.fullmatch(ts) or not _HEX_RE.fullmatch(sig):
        return False
    sec, got = int(ts), bytes.fromhex(sig)
    if abs((now if now is not None else time.time()) - sec) > MAX_SKEW_SECONDS:
        return False
    want = hmac.new(secret, b"\n".join([ts.encode(), method.encode(), path.encode(), body]), hashlib.sha256).digest()
    return hmac.compare_digest(want, got)
