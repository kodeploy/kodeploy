"""GitHub Actions ID 토큰(OIDC) 검증 — 서명·발급자·audience·시각까지. DB는 건드리지 않는다.

교환 API(POST /apps/{app_id}/ci/token)는 인증 없이 열려 있어서, 여기서 서명이 맞다고 나온 토큰만 DB 검사(jti·앱·브랜치)로 간다.
키는 GitHub JWKS에서 받는다. PyJWKClient가 키 묶음을 5분 캐시하고, 캐시에 없는 kid가 오면 한 번 새로 받는다 — 새로 받는 건
30초에 한 번까지라(cooldown_duration) 아무 kid나 넣은 요청이 GitHub 조회를 계속 일으키지 못한다.
알고리즘은 RS256만 받는다 (none·HS256 같은 헤더 바꿔치기는 jwt.decode의 algorithms가 거절한다).
오류 메시지에는 어느 검사에서 걸렸는지만 적는다. 토큰 원문·클레임·서명값은 응답과 로그에 남기지 않는다.
"""

import logging
import threading

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError

from app import config

logger = logging.getLogger(__name__)

LEEWAY_SECONDS = 60
REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "jti", "sub"]


class OidcError(Exception):
    def __init__(self, status: int, detail: str, code: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.code = code


_client: PyJWKClient | None = None
_lock = threading.Lock()


def _jwks() -> PyJWKClient:
    global _client
    with _lock:
        if _client is None:
            _client = PyJWKClient(config.CI_OIDC_JWKS_URL, cache_jwk_set=True, lifespan=300, timeout=10)
        return _client


def verify(id_token: str) -> dict:
    """서명과 표준 클레임을 검사해 클레임을 돌려준다. 실패하면 OidcError(401, 키를 못 받으면 503)."""
    try:
        key = _jwks().get_signing_key_from_jwt(id_token)
    except PyJWKClientConnectionError as e:
        logger.warning("ci oidc: fetching the JWKS failed — %s", type(e).__name__)
        raise OidcError(503, "GitHub 서명 키를 받아 오지 못했어요. 잠시 뒤 다시 시도해 주세요", "jwks_unavailable")
    except (PyJWKClientError, jwt.PyJWTError):
        raise OidcError(401, "ID 토큰 서명을 확인할 수 없어요", "invalid_token")

    try:
        return jwt.decode(
            id_token,
            key.key,
            algorithms=["RS256"],
            audience=config.CI_OIDC_AUDIENCE,
            issuer=config.CI_OIDC_ISSUER,
            leeway=LEEWAY_SECONDS,
            options={"require": REQUIRED_CLAIMS},
        )
    except jwt.InvalidIssuerError:
        raise OidcError(401, "ID 토큰 발급자(iss)가 GitHub Actions가 아니에요", "invalid_issuer")
    except jwt.InvalidAudienceError:
        raise OidcError(401, f"ID 토큰 audience가 {config.CI_OIDC_AUDIENCE}가 아니에요", "invalid_audience")
    except (jwt.ExpiredSignatureError, jwt.ImmatureSignatureError, jwt.InvalidIssuedAtError):
        raise OidcError(401, "ID 토큰이 만료됐거나 아직 유효하지 않아요 (exp·iat·nbf)", "token_expired")
    except jwt.MissingRequiredClaimError as e:
        raise OidcError(401, f"ID 토큰에 {e.claim} 클레임이 없어요", "invalid_token")
    except jwt.PyJWTError:
        raise OidcError(401, "ID 토큰 서명을 확인할 수 없어요", "invalid_token")
