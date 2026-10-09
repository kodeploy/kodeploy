"""앱 이름 · repo URL 규칙 — 정규화·검증·해소."""

import re
import uuid

from sqlalchemy.orm import Session

from app.apps import service as apps_service
from app.apps.model import App
from app.auth.model import User

# repo URL 정규화 (BuildKit이 요구하는 .git 접미사 보장)
def _normalize_repo_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.endswith(".git"):
        url = url + ".git"
    return url


# 예약 서브도메인 (시스템 인프라용) — 유저 입력 거절
RESERVED_NAMES = {
    "api",       # kodeploy-core
    "ssh",       # cloudflared 터널
    "www",       # 미래 KoDeploy 랜딩
    "admin",     # 미래 관리 페이지
    "auth",      # 미래 인증
    "dashboard", # 미래 유저 대시보드
    "docs",      # 미래 문서 사이트
    "app",       # 자동 생성 prefix 충돌 회피
    "apps",
    "kodeploy",  # 브랜드명
    "origin",    # CF for SaaS fallback origin (origin.kodeploy.com) — 유저가 잡으면 커스텀 도메인 전부 깨짐
}

# DNS-1123 label rule (K8s metadata.name + 서브도메인 둘 다 만족)
_NAME_PATTERN = re.compile(r"^[a-z]([-a-z0-9]*[a-z0-9])?$")
_NAME_MAX_LENGTH = 40


def _validate_name_format(name: str) -> None:
    if name.startswith("app-"):
        raise ValueError("'app-' prefix는 자동 생성용으로 예약돼 있음")
    if name.endswith("-api"):
        # {app}-api.kodeploy.com이 서버 슬롯 파생 호스트라 — 남의 "foo" 앱의 서버 주소와
        # "foo-api"라는 새 앱 이름이 충돌하지 않게 suffix 자체를 금지.
        raise ValueError("'-api'로 끝나는 이름은 서버 주소용으로 예약돼 있음")
    if name in RESERVED_NAMES:
        raise ValueError(f"예약 이름: {name}")
    if len(name) > _NAME_MAX_LENGTH:
        raise ValueError(f"이름 너무 김 (최대 {_NAME_MAX_LENGTH}자)")
    if not _NAME_PATTERN.match(name):
        raise ValueError("DNS-1123 label 규칙 위배 (소문자 시작 + 영숫자/하이픈만)")


# repo URL 마지막 segment → 이름 후보. 형식 검증 통과 못하면 None.
def _extract_from_repo(repo_url: str) -> str | None:
    m = re.search(r"/([^/]+?)(?:\.git)?$", repo_url)
    if not m:
        return None
    candidate = re.sub(r"[^a-z0-9-]", "-", m.group(1).lower()).strip("-")
    candidate = candidate[:_NAME_MAX_LENGTH]
    if not candidate:
        return None
    try:
        _validate_name_format(candidate)
    except ValueError:
        return None
    return candidate


# 유저당 앱 하나(2단계까지): 이미 앱이 있으면 그 앱 그대로 (이름 변경 불가). 없으면 첫 배포로 보고
# 입력값 검증 + 다른 앱과 중복 검사 후 앱을 만든다.
def _resolve_app(
    name: str | None,
    repo_url: str,
    user: User,
    db: Session,
) -> App:
    # 두 번째 배포 이후 — 이미 고정된 앱 그대로
    existing_app = apps_service.get_user_app(db, user.id)
    if existing_app:
        return existing_app

    # 첫 배포 — 입력값 검증 또는 자동 생성
    return create_named_app(name, repo_url, user, db)


# 이름을 검증하고 새 앱을 만든다. 이름이 없으면 repo 이름(없으면 app-<hex8>)으로 자동 생성.
# 앱 수 상한은 호출하는 쪽이 본다 (첫 배포의 암묵적 생성은 앱이 0개일 때만 오므로 상한에 안 걸린다).
def create_named_app(
    name: str | None,
    repo_url: str,
    user: User,
    db: Session,
) -> App:
    if name:
        _validate_name_format(name)
        candidate = name
    else:
        candidate = _extract_from_repo(repo_url) or f"app-{uuid.uuid4().hex[:8]}"

    # 다른 앱이 이미 쓰고 있는지 확인 (DB unique 제약이 막아주지만 친절한 에러용)
    if db.query(App).filter(App.name == candidate).first():
        raise ValueError(f"이미 사용 중인 이름: {candidate}")

    return apps_service.create_app(db, user.id, candidate)
