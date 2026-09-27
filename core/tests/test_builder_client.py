"""빌더 서명·클라이언트 — Go(builder/internal/sign)와 같은 서명, 409·429·5xx 재시도 규칙."""

import asyncio
import json

import httpx
import pytest

from app import config
from app.builder import client, sign

SECRET = b"shared-secret"
NOW = 1_790_000_000
BODY = b'{"build_id":"3f9a2c1d"}'


# --- 서명 ---

def test_known_vector_matches_go():
    # builder/internal/sign/sign_test.go TestKnownVector와 같은 입력·기대값
    got = sign.compute(b"k", "1700000000", "POST", "/internal/deploys", b"{}")
    assert got == "b667dc8d0d3b6aceef1df8ccc81d840550dad21280fd97e22d130af861f895a4"


def _good():
    return sign.compute(SECRET, str(NOW), "POST", "/internal/deploys", BODY)


@pytest.mark.parametrize("ts,sig,method,path,body,secret,at,want", [
    (str(NOW), None, "POST", "/internal/deploys", BODY, SECRET, NOW, True),
    (str(NOW), None, "POST", "/internal/deploys", BODY, SECRET, NOW + 60, True),
    (str(NOW), "upper", "POST", "/internal/deploys", BODY, SECRET, NOW, True),
    (str(NOW), None, "POST", "/internal/deploys", BODY, SECRET, NOW + 61, False),
    (str(NOW), None, "POST", "/internal/deploys", BODY, SECRET, NOW - 61, False),
    ("yesterday", None, "POST", "/internal/deploys", BODY, SECRET, NOW, False),
    (" " + str(NOW), None, "POST", "/internal/deploys", BODY, SECRET, NOW, False),   # Go ParseInt는 공백 거부
    (str(NOW), "", "POST", "/internal/deploys", BODY, SECRET, NOW, False),
    ("", None, "POST", "/internal/deploys", BODY, SECRET, NOW, False),
    (str(NOW), None, "POST", "/internal/deploys", b'{"build_id":"00000000"}', SECRET, NOW, False),
    (str(NOW), None, "DELETE", "/internal/deploys", BODY, SECRET, NOW, False),
    (str(NOW), None, "POST", "/internal/deploys/x", BODY, SECRET, NOW, False),
    (str(NOW), None, "POST", "/internal/deploys", BODY, b"other", NOW, False),
    (str(NOW), "notHex", "POST", "/internal/deploys", BODY, SECRET, NOW, False),
    (str(NOW), "spaced", "POST", "/internal/deploys", BODY, SECRET, NOW, False),     # fromhex는 공백을 받는다
    (str(NOW), "truncated", "POST", "/internal/deploys", BODY, SECRET, NOW, False),
    ("0" + str(NOW), None, "POST", "/internal/deploys", BODY, SECRET, NOW, False),   # 헤더 문자열이 다르면 다른 서명
])
def test_verify_cases_match_go(ts, sig, method, path, body, secret, at, want):
    good = _good()
    sig = {None: good, "upper": good.upper(), "notHex": "zz" + good[2:],
           "spaced": good[:2] + " " + good[2:], "truncated": good[:32]}.get(sig, sig)
    assert sign.verify(secret, ts, sig, method, path, body, now=at) is want


def test_headers_roundtrip():
    h = sign.headers(SECRET, "DELETE", "/internal/deploys/3f9a2c1d", b"", now=NOW)
    assert h[sign.HEADER_TIMESTAMP] == str(NOW)
    assert sign.verify(SECRET, h[sign.HEADER_TIMESTAMP], h[sign.HEADER_SIGNATURE],
                       "DELETE", "/internal/deploys/3f9a2c1d", b"", now=NOW + 5)


# --- 클라이언트 ---

@pytest.fixture
def builder(monkeypatch):
    """응답 목록을 차례로 돌려주는 가짜 빌더. 받은 요청(메서드, 경로, 서명 검증 결과)을 기록한다."""
    monkeypatch.setattr(config, "BUILDER_HMAC_SECRET", "shared-secret")
    monkeypatch.setattr(config, "BUILDER_URL", "http://builder:8080")
    state = {"responses": [], "seen": [], "slept": []}

    def handler(req: httpx.Request) -> httpx.Response:
        body = req.read()
        ok = sign.verify(SECRET, req.headers.get(sign.HEADER_TIMESTAMP), req.headers.get(sign.HEADER_SIGNATURE),
                         req.method, req.url.raw_path.decode(), body)
        state["seen"].append((req.method, req.url.path, ok, body))
        nxt = state["responses"].pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    monkeypatch.setattr(client, "_transport", lambda: httpx.MockTransport(handler))

    async def fake_sleep(s):
        state["slept"].append(s)

    state["sleep"] = fake_sleep
    return state


PAYLOAD = {"build_id": "3f9a2c1d", "kind": "build", "namespace": "tenant-d6d8b759"}


def run_submit(b):
    return asyncio.run(client.submit(PAYLOAD, sleep=b["sleep"]))


def test_submit_accepted_and_signed(builder):
    builder["responses"] = [httpx.Response(202, json={"build_id": "3f9a2c1d"})]
    run_submit(builder)
    method, path, ok, body = builder["seen"][0]
    assert (method, path, ok) == ("POST", "/internal/deploys", True)
    assert json.loads(body) == PAYLOAD


def test_submit_cancels_conflicting_build_then_resends(builder):
    builder["responses"] = [
        httpx.Response(409, json={"error": "another request", "build_id": "aaaa1111"}),
        httpx.Response(202),                      # DELETE /internal/deploys/aaaa1111
        httpx.Response(202, json={"build_id": "3f9a2c1d"}),
    ]
    run_submit(builder)
    assert [(m, p, ok) for m, p, ok, _ in builder["seen"]] == [
        ("POST", "/internal/deploys", True),
        ("DELETE", "/internal/deploys/aaaa1111", True),
        ("POST", "/internal/deploys", True),
    ]
    assert builder["slept"] == [client.CONFLICT_WAIT]


def test_submit_409_on_own_id_means_accepted(builder):
    builder["responses"] = [httpx.Response(409, json={"build_id": "3f9a2c1d"})]
    run_submit(builder)
    assert len(builder["seen"]) == 1


def test_submit_waits_retry_after_with_cap(builder):
    builder["responses"] = [
        httpx.Response(429, headers={"Retry-After": "15"}),
        httpx.Response(429, headers={"Retry-After": "600"}),
        httpx.Response(202),
    ]
    run_submit(builder)
    assert builder["slept"] == [15.0, client.BUSY_WAIT_CAP]


def test_submit_400_is_final_with_reason(builder):
    builder["responses"] = [httpx.Response(400, json={"error": "build.ref must not start with - or /"})]
    with pytest.raises(client.BuilderError, match="build.ref must not start"):
        run_submit(builder)
    assert len(builder["seen"]) == 1


def test_submit_gives_up_after_max_attempts(builder):
    builder["responses"] = [httpx.ConnectError("refused")] + [httpx.Response(503)] * (client.MAX_ATTEMPTS - 1)
    with pytest.raises(client.BuilderError, match="503"):
        run_submit(builder)
    assert len(builder["seen"]) == client.MAX_ATTEMPTS


def test_submit_without_secret_is_refused(monkeypatch):
    monkeypatch.setattr(config, "BUILDER_HMAC_SECRET", "")
    with pytest.raises(client.BuilderError, match="BUILDER_HMAC_SECRET"):
        asyncio.run(client.submit(PAYLOAD))


def test_cancel(builder):
    builder["responses"] = [httpx.Response(202), httpx.Response(404)]
    assert asyncio.run(client.cancel("3f9a2c1d")) is True
    assert asyncio.run(client.cancel("3f9a2c1d")) is False
    assert all(ok and body == b"" for _, _, ok, body in builder["seen"])
