"""빌드 소스(repo·브랜치·저장소 안 경로) 검증."""

import re

# 빌드 소스 검증 — repo·브랜치·경로는 빌드 Job YAML(Jinja, autoescape 없음)과 git clone 인자로
# 들어간다. 따옴표·개행이 섞이면 YAML에 필드를 끼워 넣을 수 있고(-로 시작하면 git 옵션), 이건 보안 경계다.
# 규칙은 Go 빌더(builder/internal/api/validate.go)와 같다. start_deploy에서 먼저 막아 화면에 이유를
# 보여주고, 매니페스트 렌더(manifests/build.py)에서 한 번 더 막는다 (감지 결과·옛 빌드 행도 거치게).
# manifests가 import하므로 app 모듈을 import하지 않는다 (validation.py는 manifests를 import해서 순환).
_REPO_URL_PATTERN = re.compile(r"^https://github\.com/([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100})$")
_REF_PATTERN = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")
_PATH_SEG_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,255}$")


def validate_repo_url(url: str) -> None:
    m = _REPO_URL_PATTERN.match(url or "")
    if not m or m.group(1).startswith("-"):
        raise ValueError("저장소 주소는 https://github.com/<계정>/<저장소> 형식이어야 합니다")
    name = m.group(2).removesuffix(".git")
    if name in ("", ".", ".."):
        raise ValueError("저장소 이름이 올바르지 않습니다")


def validate_branch(branch: str) -> None:
    b = branch or ""
    if not _REF_PATTERN.match(b):
        raise ValueError("브랜치 이름에는 영문·숫자·. _ / - 만 쓸 수 있습니다")
    if b.startswith(("-", "/")) or ".." in b or "//" in b or b.endswith(("/", ".", ".lock")):
        raise ValueError("브랜치 이름 형식이 올바르지 않습니다")


# repo 안의 상대 경로 (빈 값 = repo 루트). 절대경로·..·이상한 조각 거부.
def validate_repo_path(path: str, label: str) -> None:
    if not path:
        return
    if path.startswith("/") or not all(
        _PATH_SEG_PATTERN.match(seg) and seg not in (".", "..") for seg in path.split("/")
    ):
        raise ValueError(f"{label}는 저장소 안의 상대 경로여야 합니다 (영문·숫자·. _ - , .. 불가)")
