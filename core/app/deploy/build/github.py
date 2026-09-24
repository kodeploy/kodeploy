"""GitHub API 연동 — raw fetch · 빌드 방식 감지 · 최근 커밋 캐시."""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from app.auth import github_app
from app.auth.model import User
from app.deploy.model import Build
from app.shared.db import SessionLocal

_GITHUB_REPO_PATTERN = re.compile(r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$")


# Public GitHub repo에서 파일 텍스트를 raw URL로 fetch. 실패하면 None (빌드는 계속).
# Private repo · GitHub 외 SCM은 미지원 — 그 경우엔 dockerfile_content NULL로 두고
# UI에서 "표시 불가"로 처리.
def _fetch_github_raw(repo_url: str, branch: str, path: str) -> str | None:
    m = _GITHUB_REPO_PATTERN.match(repo_url)
    if not m:
        return None
    user, repo = m.group(1), m.group(2)
    raw_url = f"https://raw.githubusercontent.com/{user}/{repo}/{branch}/{path}"
    try:
        with urllib.request.urlopen(raw_url, timeout=10) as resp:
            if resp.status == 200:
                return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, ValueError):
        pass
    return None


# 빌드 user의 installation id 조회 (private repo tree/clone 토큰 발급용). 없으면 None.
def _installation_id_for(build: Build) -> "int | None":
    if build.user_id is None:
        return None
    db = SessionLocal()
    try:
        owner = db.query(User).filter_by(id=build.user_id).first()
        return owner.github_installation_id if owner else None
    finally:
        db.close()


# nixpacks가 프로젝트 루트로 인식하는 마커 파일 — auto 모드의 앱 디렉토리 자동 탐색용.
# 런타임별로 나눠, 감지 시 유저가 선택한 런타임의 마커를 우선한다 — 풀스택 repo에서
# frontend의 package.json이 백엔드 감지를 가로채지 않게 (역방향도 동일).
_RUNTIME_MARKERS = {
    "python": frozenset({"requirements.txt", "pyproject.toml", "Pipfile", "setup.py"}),
    "java": frozenset({"pom.xml", "build.gradle", "build.gradle.kts"}),
    "php": frozenset({"composer.json"}),
    "javascript": frozenset({"package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"}),
}
_NIXPACKS_MARKERS = frozenset().union(*_RUNTIME_MARKERS.values())

# 아직 지원하지 않는 런타임의 마커 — 런타임 추정에서 "지원 안 함"을 알려주는 데만 쓴다.
# 지원을 추가하면 _RUNTIME_MARKERS로 옮긴다.
_UNSUPPORTED_MARKERS = {
    "go": frozenset({"go.mod"}),
    "ruby": frozenset({"Gemfile"}),
    "rust": frozenset({"Cargo.toml"}),
    "elixir": frozenset({"mix.exs"}),
}

# 런타임 추정 순서 — 깊이가 같으면 앞쪽이 이긴다. package.json은 다른 언어 repo에도 프론트·도구용으로
# 흔해서 맨 뒤다 (Laravel = composer.json + package.json → php, Rails = Gemfile + package.json → ruby).
_DETECT_ORDER = ("java", "python", "php", "go", "ruby", "rust", "elixir", "javascript")


# build_mode="detect" 해소 — GitHub tree API로 빌드 방식 + 경로를 한 번에 감지 (깊이 3까지).
#   1) Dockerfile 있으면        → ("dockerfile", 그 경로)
#   2) 없고 nixpacks 마커 있으면 → ("auto", 그 디렉토리)  ← 모노레포 서브디렉토리 자동
#   3) 둘 다 없으면             → ("auto", project_path 그대로) — nixpacks가 root에서 시도
# project_path 하위 우선, 선택 런타임 마커 우선, root에 가까운 것 우선.
# private은 installation 토큰. fallback 아님(존재 기반).
def _detect_build(build: Build) -> "tuple[str, str]":
    fallback = ("auto", build.project_path or "")
    tree = _fetch_tree(build.repo_url, build.branch, _installation_id_for(build))
    if tree is None:
        return fallback
    chosen = _choose_build_target(tree, build.project_path, build.runtime)
    return chosen if chosen else fallback


# GitHub tree API(recursive)로 repo 파일 목록 조회. private은 installation 토큰. 실패하면 None.
def _fetch_tree(repo_url: str, branch: str, installation_id: "int | None") -> "list[dict] | None":
    m = _GITHUB_REPO_PATTERN.match(repo_url.rstrip("/"))
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "kodeploy"}
    token = github_app.get_clone_token(installation_id)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    api_url = (
        f"https://api.github.com/repos/{owner}/{repo}/git/trees/"
        f"{urllib.parse.quote(branch)}?recursive=1"
    )
    try:
        req = urllib.request.Request(api_url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
    return data.get("tree", [])


# tree(파싱된 GitHub tree API 응답)에서 빌드 방식+경로 선택 — 순수 함수.
# Dockerfile > 선택 런타임의 마커 > 그 외 마커, 각각 root 가까운 것 우선. 없으면 None.
def _choose_build_target(
    tree: "list[dict]", project_path: "str | None", runtime: str,
) -> "tuple[str, str] | None":
    prefix = (project_path or "").strip("/")

    def in_scope(p: str) -> bool:
        return not prefix or p == prefix or p.startswith(f"{prefix}/")

    dockerfiles, markers = [], []
    for item in tree:
        if item.get("type") != "blob":
            continue
        path = item.get("path", "")
        if path.count("/") > 3:                        # 깊이 3 초과 제외
            continue
        name = path.rsplit("/", 1)[-1]
        if name == "Dockerfile" and in_scope(path):
            dockerfiles.append(path)
        elif name in _NIXPACKS_MARKERS and in_scope(path):
            markers.append(path)

    if dockerfiles:                                    # Dockerfile 우선 (root 가까운 것)
        dockerfiles.sort(key=lambda p: p.count("/"))
        return ("dockerfile", dockerfiles[0])
    if markers:                                        # nixpacks 마커 디렉토리
        preferred = _RUNTIME_MARKERS.get(runtime, frozenset())
        markers.sort(
            key=lambda p: (p.rsplit("/", 1)[-1] not in preferred, p.count("/"))
        )
        best = markers[0]
        return ("auto", best.rsplit("/", 1)[0] if "/" in best else "")
    return None


# 배포 폼 2단계 런타임 미리 채우기 — repo 마커 파일로 런타임을 추정한다.
# 폼이 저장소·브랜치·경로를 바꿀 때마다 부르므로 tree를 잠깐 캐시한다(비인증 한도 IP당 60req/h 보호).
# 빌드 시점 감지(_detect_build)는 최신 tree가 필요해서 이 캐시를 쓰지 않는다.
_TREE_CACHE: dict[tuple, tuple[float, list]] = {}
_TREE_TTL_SECONDS = 60


def detect_runtime(
    repo_url: str, branch: str, project_path: str, installation_id: "int | None",
) -> dict:
    key = (repo_url.rstrip("/").lower(), branch, installation_id is not None)
    hit = _TREE_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < _TREE_TTL_SECONDS:
        tree = hit[1]
    else:
        tree = _fetch_tree(repo_url, branch, installation_id)
        if tree is None:
            return {"checked": False, "runtime": None, "marker": None, "unsupported": None}
        _TREE_CACHE[key] = (time.monotonic(), tree)
    return {"checked": True, **_choose_runtime(tree, project_path)}


# tree에서 런타임 추정 — 순수 함수. project_path 안, 깊이 3까지.
# root에 가까운 마커가 이기고, 깊이가 같으면 _DETECT_ORDER 앞쪽이 이긴다.
# 지원하는 런타임이면 runtime, 지원 안 하는 런타임이면 unsupported, 마커가 없으면 둘 다 None.
def _choose_runtime(tree: "list[dict]", project_path: "str | None") -> dict:
    prefix = (project_path or "").strip("/")
    markers = {**_RUNTIME_MARKERS, **_UNSUPPORTED_MARKERS}
    found = []                                         # (깊이, 순서, 런타임, 경로)
    for item in tree:
        if item.get("type") != "blob":
            continue
        path = item.get("path", "")
        if path.count("/") > 3:
            continue
        if prefix and not (path == prefix or path.startswith(f"{prefix}/")):
            continue
        name = path.rsplit("/", 1)[-1]
        for rank, rt in enumerate(_DETECT_ORDER):
            if name in markers[rt]:
                found.append((path.count("/"), rank, rt, path))
    if not found:
        return {"runtime": None, "marker": None, "unsupported": None}
    _, _, rt, path = min(found)
    if rt in _RUNTIME_MARKERS:
        return {"runtime": rt, "marker": path, "unsupported": None}
    return {"runtime": None, "marker": path, "unsupported": rt}


# GitHub 커밋 캐시 — unauthenticated 한도(IP당 60req/h, core egress IP 공유) 보호.
# (owner, repo, branch) → {"etag", "data", "at"}.
# - TTL 안: 네트워크 생략 (탭/유저 수와 무관하게 repo당 분당 최대 1회)
# - TTL 후: If-None-Match 조건부 요청 — 304는 GitHub이 rate limit에서 차감 안 함
# - 403(한도 초과) 등 실패: stale 캐시라도 반환 — UI가 갑자기 비지 않게
_COMMITS_CACHE: dict[tuple, dict] = {}
_COMMITS_TTL_SECONDS = 60


# 최근 커밋 조회 — public repo 한정 (unauthenticated GitHub API).
# private repo 지원은 App installation token 도입 시 분기 추가.
# 실패는 캐시 fallback → 빈 리스트로 swallow — UI에서 "없음" 표시되면 충분.
def fetch_recent_commits(
    repo_url: str, branch: str, per_page: int = 10,
) -> list[dict]:
    m = _GITHUB_REPO_PATTERN.match(repo_url.rstrip("/"))
    if not m:
        return []
    owner, repo = m.group(1), m.group(2)

    key = (owner, repo, branch)
    now = time.time()
    cached = _COMMITS_CACHE.get(key)
    if cached and now - cached["at"] < _COMMITS_TTL_SECONDS:
        return cached["data"]

    api_url = (
        f"https://api.github.com/repos/{owner}/{repo}/commits"
        f"?sha={branch}&per_page={per_page}"
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "kodeploy",
    }
    if cached and cached.get("etag"):
        headers["If-None-Match"] = cached["etag"]
    try:
        req = urllib.request.Request(api_url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
            etag = resp.headers.get("ETag")
    except urllib.error.HTTPError as e:
        if e.code == 304 and cached:          # 변경 없음 — 한도 미차감, 캐시 연장
            cached["at"] = now
            return cached["data"]
        return cached["data"] if cached else []  # 403(한도) 등 — stale 캐시 fallback
    except (urllib.error.URLError, TimeoutError, ValueError):
        return cached["data"] if cached else []
    out = []
    for c in data:
        try:
            full_msg = c["commit"]["message"]
            title, _, body = full_msg.partition("\n")
            out.append({
                "sha": c["sha"][:7],
                "message": title.strip(),
                "body": body.strip(),  # 빈 문자열 가능 — UI에서 "(본문 없음)" 처리
                "author": c["commit"]["author"].get("name", "unknown"),
                "date": c["commit"]["author"].get("date"),
                "url": c["html_url"],
            })
        except (KeyError, TypeError):
            continue
    _COMMITS_CACHE[key] = {"etag": etag, "data": out, "at": now}
    return out
