"""CI 토큰 테스트 공용 — 테스트용 RSA 키로 만든 가짜 GitHub ID 토큰과, 그 공개키를 돌려주는 JWKS.

JWKS는 진짜 PyJWKClient를 쓰고 네트워크(fetch_data)만 바꿔 끼운다 — 키 캐시와 kid 재조회는 라이브러리 동작 그대로다.
"""

import base64
import hashlib
import hmac
import json
import time
import uuid
from types import SimpleNamespace

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError

from app import config
from app.ci import oidc

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KID = "kid-1"
ISS = "https://token.actions.githubusercontent.com"
AUD = "kodeploy"
SHA = "0123456789abcdef0123456789abcdef01234567"


def jwk_of(key, kid):
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid=kid, use="sig", alg="RS256")
    return jwk


def claims(**over):
    now = int(time.time())
    c = {
        "iss": ISS, "aud": AUD, "sub": "repo:acme/api:ref:refs/heads/main",
        "jti": str(uuid.uuid4()), "iat": now, "nbf": now, "exp": now + 300,
        "repository": "acme/api", "ref": "refs/heads/main", "sha": SHA, "event_name": "push",
        "run_id": "98765", "run_attempt": "1", "actor": "octocat", "workflow": "deploy",
    }
    c.update(over)
    return {k: v for k, v in c.items() if v is not None}


def id_token(key=KEY, kid=KID, **over):
    return jwt.encode(claims(**over), key, algorithm="RS256", headers={"kid": kid})


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


# 헤더의 alg를 바꿔치기한 토큰. HS256은 공개키 PEM을 HMAC 비밀로 쓰는 고전적인 혼동 공격이다.
def forged_token(alg: str, **over) -> str:
    header = _b64(json.dumps({"alg": alg, "typ": "JWT", "kid": KID}).encode())
    payload = _b64(json.dumps(claims(**over)).encode())
    signing_input = f"{header}.{payload}".encode()
    if alg == "none":
        return f"{header}.{payload}."
    pem = KEY.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    sig = hmac.new(pem, signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64(sig)}"


def install_jwks(monkeypatch, keys=None):
    """oidc의 PyJWKClient를 가짜 네트워크로 바꾼다. state.keys를 바꾸면 '키 교체'다."""
    state = SimpleNamespace(keys=keys or [jwk_of(KEY, KID)], fetches=0, down=False)
    client = PyJWKClient("https://jwks.test/.well-known/jwks", cache_jwk_set=True, lifespan=300)

    def fetch_data():
        state.fetches += 1
        if state.down:
            raise PyJWKClientConnectionError("Fail to fetch data from the url")
        data = {"keys": list(state.keys)}
        client.jwk_set_cache.put(data)
        client._last_successful_fetch = time.monotonic()
        return data

    monkeypatch.setattr(client, "fetch_data", fetch_data)
    monkeypatch.setattr(oidc, "_client", client)
    monkeypatch.setattr(config, "CI_OIDC_ISSUER", ISS)
    monkeypatch.setattr(config, "CI_OIDC_AUDIENCE", AUD)
    monkeypatch.setattr(config, "CI_ALLOWED_EVENTS", ["push", "workflow_dispatch"])
    state.client = client
    return state
