"""CI 토큰 — GitHub ID 토큰 교환(발급), Bearer 확인, CI 경로 재배포의 범위와 기록.

교환 검사 순서 (계약 문서의 CI 절과 같다. 하나라도 걸리면 거절)
  1~4  서명(RS256, GitHub JWKS)·iss·aud·exp/iat/nbf — oidc.verify. 여기까지는 DB를 건드리지 않는다
  5    jti 1회용 — 이미 교환에 쓴 jti면 401
  6    앱이 있고(404) GitHub Actions 배포 허용이 켜져 있음(403)
  7    event_name이 CI_ALLOWED_EVENTS 안 (403)
  8    라이브 슬롯 중 저장소와 브랜치가 모두 토큰과 같은 슬롯이 하나 이상 (403). 라이브 배포가 없으면 409
  9    sha가 40자 hex (401)
  10   앱에 워크플로 파일을 지정했으면 job_workflow_ref가 그 저장소의 그 파일 (403 workflow_mismatch)
  11   그 저장소의 ID를 알고 있으면 repository_id가 같음 (403 repo_id_mismatch). 모르면 이번 교환의 값을 적어 둔다
토큰 원문은 응답으로만 나간다. DB에는 sha256만 두고, 로그에는 앱 id와 거절 코드만 남긴다.
"""

import hashlib
import json
import logging
import re
import secrets
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import config
from app.apps import service as apps_service
from app.apps.model import App
from app.auth import github_app
from app.ci import oidc
from app.ci.model import CiBuild, CiToken
from app.deploy.build import redeploy
from app.deploy.model import Build
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

    if app.ci_workflow and not workflow_matches(claims.get("job_workflow_ref"), repository, app.ci_workflow):
        raise _reject(app_id, 403, f"이 앱은 {app.ci_workflow} 워크플로에서만 배포할 수 있어요", "workflow_mismatch")

    repo_name = repository.lower()
    claim_id = _repo_id_claim(claims.get("repository_id"))
    known = dict(app.ci_repo_ids or {})
    if repo_name in known:
        if claim_id != known[repo_name]:
            raise _reject(
                app_id, 403, "저장소 ID가 허용을 켤 때와 달라요. 같은 이름으로 다시 만든 저장소면 허용을 껐다 켜 주세요",
                "repo_id_mismatch",
            )
    elif claim_id is not None:
        known[repo_name] = claim_id
        app.ci_repo_ids = known        # 허용을 켤 때 못 받은 저장소 — 첫 교환의 값을 적어 둔다 (토큰 행과 같이 커밋)

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


# --- 워크플로 파일과 저장소 ID (검사 10, 11) ------------------------------------------

# job_workflow_ref = "<owner>/<repo>/<경로>@<ref>". 저장소는 대소문자를 가리지 않고, 파일 경로는 가린다(GitHub가 가린다).
# ref는 보지 않는다 — 브랜치는 검사 8이 이미 봤고, 재사용 워크플로를 태그로 부르는 경우도 같은 파일로 친다.
def workflow_matches(job_workflow_ref: object, repository: str, path: str) -> bool:
    if not isinstance(job_workflow_ref, str):
        return False
    left, sep, _ref = job_workflow_ref.partition("@")
    parts = left.split("/")
    if not sep or len(parts) < 3:
        return False
    return "/".join(parts[:2]).lower() == repository.lower() and "/".join(parts[2:]) == path


# repository_id 클레임은 숫자 문자열이다. 형식이 다르면 없는 것으로 본다.
def _repo_id_claim(value: object) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if isinstance(value, str) and value.isdigit() and len(value) <= 20:
        return value
    return None


_GITHUB_URL_RE = re.compile(r"^https://github\.com/([a-z0-9_.-]+/[a-z0-9_.-]+)$")


# 슬롯의 저장소 주소 → "owner/repo"(소문자). GitHub 저장소가 아니면 None.
def repo_name_of(url: str) -> str | None:
    m = _GITHUB_URL_RE.match(repo_key(url))
    return m.group(1) if m else None


# GitHub 저장소 ID. 못 받으면 None (없는 저장소, 비공개인데 토큰 없음, 네트워크, rate limit).
def github_repo_id(name: str, token: str | None = None) -> str | None:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "kodeploy"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"https://api.github.com/repos/{name}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            rid = json.load(resp).get("id")
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    return str(rid) if isinstance(rid, int) and not isinstance(rid, bool) else None


def lookup_repo_ids(db: Session, app: App) -> dict[str, str]:
    """허용을 켤 때 라이브 슬롯 저장소들의 ID를 받는다. 공개 저장소는 토큰 없이, 못 받으면 앱 주인의 GitHub App 설치 토큰으로
    한 번 더. 그래도 못 받은 저장소는 빠지고, 그 저장소의 첫 교환이 repository_id 클레임으로 채운다."""
    try:
        live = redeploy.live_slots(db, app)
    except redeploy.RedeployError:
        return {}
    out: dict[str, str] = {}
    token: str | None = None
    token_tried = False
    for url, _branch in redeploy.slot_sources(live).values():
        name = repo_name_of(url)
        if name is None or name in out:
            continue
        rid = github_repo_id(name)
        if rid is None:
            if not token_tried:
                token_tried = True
                try:
                    token = github_app.get_clone_token(apps_service.repo_installation_id(db, app))
                except Exception as e:   # 조회는 보조 — 실패해도 허용은 켠다
                    logger.warning("ci repo id lookup: installation token failed — %s", type(e).__name__)
            if token:
                rid = github_repo_id(name, token)
        if rid is not None:
            out[name] = rid
    logger.info("ci repo ids app=%s found=%d", app.id, len(out))
    return out


# --- Bearer -------------------------------------------------------------------

def bearer_token(request: Request) -> str | None:
    """Authorization: Bearer <값>이면 그 값. 다른 방식이거나 없으면 None (쿠키 경로)."""
    header = request.headers.get("authorization")
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip()


def authenticate(db: Session, raw: str, app_id: uuid.UUID | None) -> tuple[CiToken, App]:
    """요청마다 확인: 토큰이 있고, 만료 안 됐고, 경로의 앱이 토큰의 앱이고, 그 앱의 허용이 아직 켜져 있다."""
    if not raw.startswith(TOKEN_PREFIX):
        raise CiAuthError(401, "CI 토큰이 아니에요", "ci_token_invalid")
    row = db.query(CiToken).filter(CiToken.token_hash == hash_token(raw)).first()
    if row is None:
        raise CiAuthError(401, "CI 토큰이 없거나 폐기됐어요", "ci_token_invalid")
    if row.expires_at <= _now():
        raise CiAuthError(401, "CI 토큰이 만료됐어요. 새 ID 토큰으로 다시 교환해 주세요", "ci_token_expired")
    if app_id is None:
        raise CiAuthError(400, "app_id가 필요해요 (/apps/{app_id}/deploy/...)", "app_id_required")
    if row.app_id != app_id:
        raise CiAuthError(403, "이 CI 토큰은 다른 앱용이에요", "ci_token_app_mismatch")
    app = db.get(App, app_id)
    if app is None:
        raise CiAuthError(401, "CI 토큰이 없거나 폐기됐어요", "ci_token_invalid")
    if not app.ci_deploy_enabled:
        raise CiAuthError(401, "이 앱은 GitHub Actions 배포가 꺼져 있어요", "ci_deploy_disabled")
    return row, app


# 허용을 끄면 아직 살아 있는 토큰도 바로 끝낸다 — 다시 켰을 때 예전 토큰이 되살아나지 않게.
def expire_tokens(db: Session, app_id: uuid.UUID) -> None:
    now = _now()
    db.query(CiToken).filter(CiToken.app_id == app_id, CiToken.expires_at > now).update(
        {CiToken.expires_at: now}, synchronize_session=False,
    )


# --- CI 경로의 재배포 ------------------------------------------------------------

def scope_of(token: CiToken) -> redeploy.CiScope:
    """토큰이 배포할 수 있는 범위 — 그 저장소·브랜치의 그 커밋."""
    return redeploy.CiScope(repo_url=repo_url_of(token.repository), branch=branch_of(token.ref) or "", sha=token.sha)


def record_builds(db: Session, token: CiToken, builds: list[Build]) -> None:
    for b in builds:
        db.merge(CiBuild(
            build_id=b.build_id, app_id=token.app_id, token_id=token.id,
            repository=token.repository, ref=token.ref, sha=token.sha,
            run_id=token.run_id, run_attempt=token.run_attempt, actor=token.actor,
            workflow=token.workflow, event_name=token.event_name,
        ))
    db.commit()


def runs_for(db: Session, build_ids: list[str]) -> dict[str, CiBuild]:
    if not build_ids:
        return {}
    rows = db.query(CiBuild).filter(CiBuild.build_id.in_(build_ids)).all()
    return {r.build_id: r for r in rows}
