"""재배포 — 지금 라이브인 설정 그대로 다시 배포한다. 커밋 하나를 지정할 수 있다 (POST /deploy/redeploy).

설정은 요청으로 받지 않고 이 앱의 라이브 빌드에서 다시 만들어, 웹 배포 폼(POST /deploy)과 같은 start_deploy에 넘긴다.
환경변수와 초기 DB 덤프는 보내지 않는다 — env_vars가 비면 run_v2_build가 Secret을 건드리지 않는다.
계약(CLI·CI가 보는 문서): docs/deploy-api-contract.md

라이브 판정 (슬롯별)
- 서버: kind=build·서버 행 중 status=running인 가장 최근 행. 그 행이 롤백(rollback_of)이면
  저장소·브랜치·런타임·포트·빌드 방식·경로는 원본 배포(롤백 사슬의 끝)에서 가져온다 — 롤백 행은 원본 값을 복사해 둔 것이라 같다.
  DB·Redis·저장소 설정은 롤백이 아닌 가장 최근 running 서버 행에서 가져온다. 롤백(set-image)은 values를 보내지 않아
  core 소유 칸이 롤백 전 배포 그대로 남기 때문이다 — 원본 배포의 값을 쓰면 DB를 내리거나 R2를 회수할 수 있다.
- 서버를 내린 앱: 정적만 남긴 제출(runtime=none)은 서버 행을 만들지 않고 옛 running 행도 그대로 둔다. 그래서 가장 최근 서버 행
  뒤에 정적 행이 둘 이상 있거나(한 제출의 정적 짝은 하나다) 가장 최근 서버 행이 롤백인데 뒤에 정적 행이 있으면, 그 사이에
  정적만 남긴 제출이 있었던 것이라 서버를 빼고 다시 배포한다. 정적 행이 하나뿐이면 같은 제출의 짝인지 구분할 수 없어 서버가
  있는 쪽으로 본다 (잘못 빼면 서버가 내려가고, 잘못 넣으면 내렸던 서버가 다시 올라온다 — 덜 위험한 쪽).
- 정적: 앱의 site_enabled(선언값)가 켜져 있을 때만. 그때 status=running인 가장 최근 정적 행.
- 선언된 슬롯에 running 행이 없으면 그대로 다시 만들 설정이 없어 409다 (첫 배포는 배포 폼으로).
"""

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.apps.model import App
from app.auth.model import User
from app.deploy import crud
from app.deploy.build import pipeline
from app.deploy.build.naming import _normalize_repo_url, repo_key
from app.deploy.model import Build, DeployRequestKey

logger = logging.getLogger(__name__)

COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
KEY_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")

# 같은 키가 처리 중이면(동시 요청) 이만큼 기다리며 다시 읽는다. 첫 요청은 GitHub 확인까지 보통 1~3초 걸린다.
REPLAY_WAIT_SECONDS = 0.5
REPLAY_WAIT_TRIES = 10


class RedeployError(Exception):
    """HTTP 상태, 화면에 보여도 되는 이유, 기계용 코드 (코드 목록은 계약 문서 3-4)."""

    def __init__(self, status: int, detail: str, code: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.code = code


@dataclass
class LiveSlots:
    server: Build | None = None       # 서버 슬롯의 라이브 행 (롤백이면 그 롤백 행)
    server_code: Build | None = None  # 저장소·브랜치·런타임·포트·빌드 방식·경로를 가져올 행 (롤백이면 원본)
    server_deps: Build | None = None  # DB·Redis·저장소 설정을 가져올 행
    static: Build | None = None       # 정적 슬롯의 라이브 행


# CI 토큰이 배포할 수 있는 범위 (app/ci). 그 저장소·브랜치의 그 커밋만.
@dataclass
class CiScope:
    repo_url: str       # https://github.com/<owner/repo>.git
    branch: str         # ref의 refs/heads/ 뒤
    sha: str            # 40자 소문자 hex


@dataclass
class RedeployResult:
    builds: list[Build] = field(default_factory=list)
    replayed: bool = False
    requested_sha: str | None = None


def _rollback_origin(build: Build, by_id: dict[str, Build]) -> Build:
    seen: set[str] = set()
    cur = build
    while cur.rollback_of and cur.rollback_of in by_id and cur.build_id not in seen:
        seen.add(cur.build_id)
        cur = by_id[cur.rollback_of]
    return cur


# 가장 최근 서버 행 뒤에 "정적만 남긴 제출"이 있었는지 (모듈 설명 참고). created_at은 초 단위라 같은 제출의 짝은 같은 시각일 수 있다.
def _server_removed(latest_server: Build, statics: list[Build]) -> bool:
    after = [s for s in statics if s.created_at >= latest_server.created_at]
    return len(after) >= (1 if latest_server.rollback_of else 2)


def live_slots(db: Session, app: App) -> LiveSlots:
    rows = [b for b in crud.list_builds(db, app_id=app.id) if (b.kind or "build") == "build"]   # 최신순
    by_id = {b.build_id: b for b in rows}
    servers = [b for b in rows if b.runtime != "static"]
    statics = [b for b in rows if b.runtime == "static"]

    live = LiveSlots()
    if servers and not _server_removed(servers[0], statics):
        live.server = next((b for b in servers if b.status == "running"), None)
        if live.server is None:
            raise RedeployError(409, "서버가 한 번도 성공적으로 배포되지 않아 지금 설정 그대로 배포할 수 없어요. 설정 변경(배포 폼)에서 배포해 주세요", "no_successful_slot")
        live.server_code = _rollback_origin(live.server, by_id)
        live.server_deps = next(
            (b for b in servers if b.status == "running" and not b.rollback_of), live.server_code,
        )
    if app.site_enabled:
        live.static = next((b for b in statics if b.status == "running"), None)
        if live.static is None:
            raise RedeployError(409, "정적 사이트가 한 번도 성공적으로 배포되지 않아 지금 설정 그대로 배포할 수 없어요. 설정 변경(배포 폼)에서 배포해 주세요", "no_successful_slot")
    if live.server is None and live.static is None:
        raise RedeployError(409, "성공한 배포가 없어요. 첫 배포는 설정 변경(배포 폼)에서 해 주세요", "no_live_deploy")
    return live


# 슬롯별 (저장소, 브랜치) — 커밋을 지정하면 그 커밋이 걸리는 설정이다 (서버는 롤백이면 원본 배포의 값).
def slot_sources(live: LiveSlots) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    if live.server is not None:
        out["server"] = (live.server_code.repo_url, live.server_code.branch)
    if live.static is not None:
        out["static"] = (live.static.repo_url, live.static.branch)
    return out


# 배포하기에서 커밋을 고를 기준 — 커밋을 지정하면 걸리는 슬롯의 행(서버, 없으면 정적. repo를 안 줬을 때의 기준 저장소)과
# 그 슬롯에 지금 떠 있는 커밋. 롤백 행은 원본의 source_sha를 복사해 두지만, 그 전에 만든 롤백 행은 비어 있어 원본에서 읽는다.
def commit_base(db: Session, app: App) -> tuple[Build, str | None]:
    live = live_slots(db, app)
    if live.server is not None:
        return live.server_code, live.server.source_sha or live.server_code.source_sha
    return live.static, live.static.source_sha


# 라이브 행들 → start_deploy 인자. 환경변수·초기 덤프는 보내지 않는다.
def redeploy_args(app: App, live: LiveSlots) -> dict:
    args: dict = {"app": app, "env_vars": None, "init_dump_token": None, "use_static": live.static is not None}
    if live.server is not None:
        code, deps = live.server_code, live.server_deps
        args.update(
            repo_url=code.repo_url,
            branch=code.branch,
            runtime=code.runtime,
            port=code.port,
            build_mode=code.build_mode,
            dockerfile_path=code.dockerfile_path or "Dockerfile",
            project_path=code.project_path or "",
            db_type=deps.db_type or "none",
            use_redis=bool(deps.use_redis),
            storage=deps.storage_mode,
            volume_mount_path=deps.volume_mount_path or "",
            volume_storage_class=deps.volume_storage_class or "local-path",
            volume_size=deps.volume_size or "5Gi",
        )
    else:
        # 정적 단독 — start_deploy는 repo_url을 정적 저장소의 기본값으로 쓴다
        args.update(repo_url=live.static.repo_url, branch=live.static.branch, runtime="none")
    if live.static is not None:
        s = live.static
        args.update(
            static_repo_url=s.repo_url,
            static_branch=s.branch,
            static_project_path=s.project_path or "",
            build_cmd=s.build_cmd or "",
            output_dir=s.output_dir or "",
            static_env=s.static_env,
        )
    return args


def check_commit_sha(commit_sha: str | None) -> str | None:
    if commit_sha is None:
        return None
    sha = commit_sha.lower()
    if not COMMIT_SHA_RE.match(sha):
        raise RedeployError(400, "commit_sha는 40자 커밋 해시여야 해요 (짧은 해시는 받지 않아요)", "invalid_commit_sha")
    return sha


def check_key(key: str | None) -> None:
    if key is not None and not KEY_RE.match(key):
        raise RedeployError(400, "Idempotency-Key는 1~100자의 영문·숫자·._- 만 쓸 수 있어요", "invalid_idempotency_key")


async def _start(
    db: Session, user: User, app: App, commit_sha: str | None, repo: str | None, allowed_repos: set[str] | None,
    ci_branch: str | None = None,
) -> RedeployResult:
    live = live_slots(db, app)
    args = redeploy_args(app, live)

    slot_repos = {
        "server": live.server_code.repo_url if live.server is not None else None,
        "static": live.static.repo_url if live.static is not None else None,
    }
    used = {repo_key(u) for u in slot_repos.values() if u}
    if repo is not None and repo.strip():
        base = repo_key(_normalize_repo_url(repo))
        if base not in used:
            if ci_branch is not None:
                raise RedeployError(403, "저장소가 이 앱이 배포하는 저장소와 달라요", "repo_mismatch")
            raise RedeployError(400, "이 앱의 어느 슬롯도 그 저장소를 쓰지 않아요", "repo_not_used")
    else:
        base = repo_key(slot_repos["server"] or slot_repos["static"])
    # 커밋은 기준 저장소를 쓰는 슬롯에만 — 다른 저장소의 슬롯은 그 브랜치 최신으로 같이 다시 빌드된다
    # (start_deploy는 선언된 슬롯을 모두 빌드하고, 빠진 슬롯은 내린다)
    # CI 경로(ci_branch)는 브랜치까지 같아야 고정한다 — 같은 저장소라도 다른 브랜치를 쓰는 슬롯(예: 정적 gh-pages)은
    # 그 브랜치 최신으로 다시 빌드된다. 웹 세션 경로는 지금처럼 저장소만 본다.
    branches = {slot: branch for slot, (_, branch) in slot_sources(live).items()}
    pins = {
        slot: commit_sha
        if commit_sha and url and repo_key(url) == base and (ci_branch is None or branches.get(slot) == ci_branch)
        else None
        for slot, url in slot_repos.items()
    }
    if ci_branch is not None and not any(pins.values()):
        raise RedeployError(403, "브랜치가 이 앱의 배포 브랜치와 달라요", "branch_mismatch")

    # 편집 권한 멤버는 이 앱이 쓰던 저장소로만 (start_deploy도 같은 검사를 400으로 하지만, 여기선 권한 문제라 403)
    if allowed_repos is not None and not used <= allowed_repos:
        raise RedeployError(403, "편집 권한으로는 이 앱이 쓰던 저장소로만 배포할 수 있어요", "repo_not_allowed")

    try:
        builds = await pipeline.start_deploy(
            db, user, **args, allowed_repos=allowed_repos,
            requested_sha=pins["server"], static_requested_sha=pins["static"],
        )
    except ValueError as e:
        raise RedeployError(400, str(e), "deploy_rejected")
    return RedeployResult(builds=builds, replayed=False, requested_sha=commit_sha)


# (app_id, key) 행을 먼저 넣어 키를 선점한다. 이미 있으면 None.
def _claim(db: Session, app_id: uuid.UUID, key: str) -> int | None:
    row = DeployRequestKey(app_id=app_id, key=key, build_ids=None)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    return row.id


# 빌드를 시작하지 못했다 — 선점을 풀어 같은 키로 다시 시도할 수 있게 한다.
def _release(db: Session, claim_id: int) -> None:
    try:
        db.rollback()
        db.query(DeployRequestKey).filter(DeployRequestKey.id == claim_id).delete()
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("redeploy: releasing idempotency claim %s failed — %s", claim_id, e)


# 같은 키로 이미 처리한 요청 — 그때 만든 빌드들을 지금 상태 그대로 돌려준다 (실패·취소여도 다시 빌드하지 않는다).
async def _replay(db: Session, app_id: uuid.UUID, key: str) -> RedeployResult:
    for attempt in range(REPLAY_WAIT_TRIES + 1):
        db.rollback()   # 새 트랜잭션에서 읽는다 (MySQL REPEATABLE READ는 같은 트랜잭션에서 옛 값을 보여 준다)
        row = db.query(DeployRequestKey).filter_by(app_id=app_id, key=key).first()
        if row is None:
            raise RedeployError(409, "같은 Idempotency-Key로 처리하던 요청이 실패했어요. 같은 키로 다시 보내 주세요", "key_released")
        if row.build_ids:
            builds = [b for b in (crud.get_build(db, bid, app_id=app_id) for bid in row.build_ids) if b is not None]
            sha = next((b.requested_sha for b in builds if b.requested_sha), None)
            return RedeployResult(builds=builds, replayed=True, requested_sha=sha)
        if attempt < REPLAY_WAIT_TRIES:
            await asyncio.sleep(REPLAY_WAIT_SECONDS)
    raise RedeployError(409, "같은 Idempotency-Key의 요청을 아직 처리하고 있어요. 잠시 뒤 같은 키로 다시 보내 주세요", "key_in_progress")


async def start_redeploy(
    db: Session,
    user: User,
    app: App,
    *,
    commit_sha: str | None = None,
    repo: str | None = None,
    key: str | None = None,
    allowed_repos: set[str] | None = None,
    ci_branch: str | None = None,
) -> RedeployResult:
    commit_sha = check_commit_sha(commit_sha)
    check_key(key)
    if key is None:
        return await _start(db, user, app, commit_sha, repo, allowed_repos, ci_branch)

    claim_id = _claim(db, app.id, key)
    if claim_id is None:
        return await _replay(db, app.id, key)
    try:
        result = await _start(db, user, app, commit_sha, repo, allowed_repos, ci_branch)
    except BaseException:
        _release(db, claim_id)
        raise
    db.query(DeployRequestKey).filter(DeployRequestKey.id == claim_id).update(
        {"build_ids": [b.build_id for b in result.builds]}
    )
    db.commit()
    return result


# CI 토큰으로 재배포 — 커밋과 저장소는 토큰의 값이다. 요청에 다른 값을 넣으면 403.
# 빌드 사용자는 앱 주인이다(호출하는 쪽이 넘긴다). 토큰의 저장소·브랜치가 모두 같은 슬롯에만 커밋을 고정하고,
# 나머지 슬롯(같은 저장소의 다른 브랜치, 다른 저장소)은 그 슬롯 브랜치의 최신으로 같이 다시 빌드된다.
async def start_ci_redeploy(
    db: Session,
    owner: User,
    app: App,
    scope: CiScope,
    *,
    commit_sha: str | None = None,
    repo: str | None = None,
    key: str | None = None,
) -> RedeployResult:
    commit_sha = check_commit_sha(commit_sha)
    if commit_sha is not None and commit_sha != scope.sha:
        raise RedeployError(403, "CI 토큰으로는 그 워크플로가 돈 커밋만 배포할 수 있어요", "commit_mismatch")
    if repo is not None and repo.strip() and repo_key(_normalize_repo_url(repo)) != repo_key(scope.repo_url):
        raise RedeployError(403, "CI 토큰으로는 그 워크플로의 저장소만 배포할 수 있어요", "repo_mismatch")
    return await start_redeploy(
        db, owner, app, commit_sha=scope.sha, repo=scope.repo_url, key=key, allowed_repos=None, ci_branch=scope.branch,
    )


def slot_of(build: Build) -> str:
    return "static" if build.runtime == "static" else "server"
