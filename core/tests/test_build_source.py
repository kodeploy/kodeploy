"""빌드 소스 검증 — repo·브랜치·경로가 빌드 Job YAML/git 인자에 끼어들지 못하게.

Job은 값을 git 인자·환경변수로 그대로 쓴다. 따옴표·개행이 통과하면 Job에 필드(예: 다른 Secret 마운트)를
끼워 넣을 수 있어서, 제출(start_deploy)에서 막는다 (빌더도 요청 검증에서 한 번 더 막는다).
"""

import pytest

from app.deploy.build.source import validate_branch, validate_repo_path, validate_repo_url
from tests.test_start_deploy import builder_ok, fake_apps, make_user, run_deploy, spawned  # noqa: F401  (fixture)

pytestmark = pytest.mark.usefixtures("builder_ok")

INJECT_BRANCH = 'main"\n            - name: X\n              value: "y'


@pytest.mark.parametrize("url", [
    "https://github.com/u/repo.git",
    "https://github.com/my-org/my.repo_1",
])
def test_repo_url_ok(url):
    validate_repo_url(url)


@pytest.mark.parametrize("url", [
    "https://gitlab.com/u/repo.git",
    "http://github.com/u/repo.git",
    "https://github.com/u/repo.git\"",
    "https://github.com/-u/repo.git",
    "https://github.com/u/..",
    "https://github.com/u/repo/extra",
    "https://x@github.com/u/repo",
])
def test_repo_url_rejected(url):
    with pytest.raises(ValueError):
        validate_repo_url(url)


@pytest.mark.parametrize("ref", ["main", "feature/login", "release-1.2", "v1.0_rc"])
def test_branch_ok(ref):
    validate_branch(ref)


@pytest.mark.parametrize("ref", [
    "", INJECT_BRANCH, "-upload-pack=x", "/main", "a..b", "a//b", "main/", "main.", "main.lock", "ma in", "main;id",
])
def test_branch_rejected(ref):
    with pytest.raises(ValueError):
        validate_branch(ref)


@pytest.mark.parametrize("path", ["", "backend", "apps/web", "docker/Dockerfile.prod"])
def test_repo_path_ok(path):
    validate_repo_path(path, "경로")


@pytest.mark.parametrize("path", ["/etc", "../x", "a/../b", "a//b", "a/./b", 'a"b', "a\nb", "a b"])
def test_repo_path_rejected(path):
    with pytest.raises(ValueError):
        validate_repo_path(path, "경로")


# --- 제출 단계: 저장·spawn 전에 거절 ---

@pytest.mark.parametrize("kwargs", [
    {"branch": INJECT_BRANCH},
    {"repo_url": "https://example.com/u/repo"},
    {"dockerfile_path": "../Dockerfile"},
    {"project_path": 'api"'},
    {"use_static": True, "static_branch": "-x"},
    {"use_static": True, "static_repo_url": "https://gitlab.com/u/web"},
    {"use_static": True, "static_project_path": "../web"},
])
def test_start_deploy_rejects_bad_source(spawned, kwargs):  # noqa: F811
    with pytest.raises(ValueError):
        run_deploy(None, make_user(), **kwargs)
    assert spawned == []


def test_start_deploy_accepts_dot_slash_dockerfile(spawned):  # noqa: F811
    from unittest.mock import MagicMock
    builds = run_deploy(MagicMock(), make_user(), dockerfile_path="./docker/Dockerfile")
    assert builds[0].dockerfile_path == "docker/Dockerfile"
