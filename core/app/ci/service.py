"""CI 토큰 — GitHub ID 토큰 교환(발급)과 Bearer 확인.

교환 검사 순서 (계약 문서의 CI 절과 같다. 하나라도 걸리면 거절)
  1~4  서명(RS256, GitHub JWKS)·iss·aud·exp/iat/nbf — oidc.verify. 여기까지는 DB를 건드리지 않는다
  5    jti 1회용 — 이미 교환에 쓴 jti면 401
  6    앱이 있고(404) GitHub Actions 배포 허용이 켜져 있음(403)
  7    event_name이 CI_ALLOWED_EVENTS 안 (403)
  8    라이브 슬롯 중 저장소와 브랜치가 모두 토큰과 같은 슬롯이 하나 이상 (403). 라이브 배포가 없으면 409
  9    sha가 40자 hex (401)
토큰 원문은 응답으로만 나간다. DB에는 sha256만 두고, 로그에는 앱 id와 거절 코드만 남긴다.
"""

import hashlib
import logging
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import config
from app.apps.model import App
from app.ci import oidc
from app.ci.model import CiToken
from app.deploy.build import redeploy
from app.deploy.build.naming import _normalize_repo_url, repo_key

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "kdci_"
SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
JTI_MAX = 128


class CiAuthError(Exception):
    def __init__(self, status: int, detail: str, code: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ID 토큰의 repository(owner/repo) → 빌드 행의 repo_url과 비교할 주소
def repo_url_of(repository: str) -> str:
    return f"https://github.com/{repository}.git"


def branch_of(ref: str) -> str | None:
    return ref.removeprefix("refs/heads/") if ref.startswith("refs/heads/") else None


# 라이브 슬롯 중 저장소와 브랜치가 모두 (repository, ref)와 같은 슬롯들. 저장소는 같은데 브랜치만 다른 슬롯이 있었는지도 같이 준다.
def matching_slots(live: redeploy.LiveSlots, repository: str, ref: str) -> tuple[list[str], bool]:
    want_repo = repo_key(_normalize_repo_url(repo_url_of(repository)))
    want_branch = branch_of(ref)
    same_repo = False
    matched = []
    for slot, (url, branch) in redeploy.slot_sources(live).items():
        if repo_key(_normalize_repo_url(url)) != want_repo:
            continue
        same_repo = True
        if want_branch is not None and branch == want_branch:
            matched.append(slot)
    return matched, same_repo


def _claim(claims: dict, name: str, limit: int, required: bool = True) -> str | None:
    value = claims.get(name)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value or len(value) > limit:
        raise CiAuthError(401, f"ID 토큰의 {name} 클레임이 없거나 형식이 맞지 않아요", "invalid_token")
    return value


def _reject(app_id: uuid.UUID, status: int, detail: str, code: str) -> CiAuthError:
    logger.info("ci exchange rejected app=%s code=%s", app_id, code)
    return CiAuthError(status, detail, code)


@dataclass
class Issued:
    token: str          # 원문 — 응답으로만 내보낸다
    row: CiToken


def exchange(db: Session, app_id: uuid.UUID, id_token: str) -> Issued:
    try:
        claims = oidc.verify(id_token)
    except oidc.OidcError as e:
        raise _reject(app_id, e.status, e.detail, e.code)

    jti = _claim(claims, "jti", JTI_MAX)
    if db.query(CiToken.id).filter(CiToken.jti == jti).first() is not None:
        raise _reject(app_id, 401, "이미 교환에 쓴 ID 토큰이에요. 새 ID 토큰을 받아 주세요", "token_reused")

    app = db.get(App, app_id)
    if app is None:
        raise _reject(app_id, 404, "app not found", "app_not_found")
    if not app.ci_deploy_enabled:
        raise _reject(app_id, 403, "이 앱은 GitHub Actions 배포가 꺼져 있어요. 앱 설정에서 켜 주세요", "ci_deploy_disabled")

    event = _claim(claims, "event_name", 30)
    if event not in config.CI_ALLOWED_EVENTS:
        allowed = ", ".join(config.CI_ALLOWED_EVENTS)
        raise _reject(app_id, 403, f"{event} 이벤트로는 배포할 수 없어요 ({allowed}만)", "event_not_allowed")

    repository = _claim(claims, "repository", 200)
    ref = _claim(claims, "ref", 255)
    try:
        live = redeploy.live_slots(db, app)
    except redeploy.RedeployError as e:
        raise _reject(app_id, e.status, e.detail, e.code)
    matched, same_repo = matching_slots(live, repository, ref)
    if not matched:
        if same_repo:
            raise _reject(app_id, 403, "브랜치가 이 앱의 배포 브랜치와 달라요", "branch_mismatch")
        raise _reject(app_id, 403, "저장소가 이 앱이 배포하는 저장소와 달라요", "repo_mismatch")

    sha = claims.get("sha")
    if not isinstance(sha, str) or not SHA_RE.match(sha):
        raise _reject(app_id, 401, "ID 토큰의 sha가 40자 커밋 해시가 아니에요", "invalid_sha")

    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    now = _now()
    row = CiToken(
        token_hash=hash_token(raw),
        jti=jti,
        app_id=app.id,
        repository=repository,
        ref=ref,
        sha=sha.lower(),
        run_id=_claim(claims, "run_id", 32, required=False),
        run_attempt=_claim(claims, "run_attempt", 10, required=False),
        actor=_claim(claims, "actor", 100, required=False),
        workflow=(claims.get("workflow") or "")[:255] if isinstance(claims.get("workflow"), str) else None,
        event_name=event,
        expires_at=now + timedelta(seconds=config.CI_TOKEN_TTL_SECONDS),
        created_at=now,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # 같은 jti가 동시에 두 번 왔다 — 먼저 들어간 쪽만 발급
        db.rollback()
        raise _reject(app_id, 401, "이미 교환에 쓴 ID 토큰이에요. 새 ID 토큰을 받아 주세요", "token_reused")
    logger.info("ci token issued app=%s token_id=%s", app.id, row.id)
    return Issued(token=raw, row=row)
