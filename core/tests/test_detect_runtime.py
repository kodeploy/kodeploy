"""배포 폼 런타임 추정 규칙 고정 — _choose_runtime 순수 함수.

root에 가까운 마커가 이기고, 깊이가 같으면 _DETECT_ORDER 앞쪽이 이긴다.
package.json은 다른 언어 repo에도 흔해서 가장 약하다. 지원 안 하는 런타임은 unsupported로 알린다.
"""

from app.deploy import runtimes
from app.deploy.build.github import (
    _DETECT_ORDER,
    _RUNTIME_MARKERS,
    _UNSUPPORTED_MARKERS,
    _choose_runtime,
)


def _tree(*paths):
    return [{"type": "blob", "path": p} for p in paths]


def _rt(tree, path=""):
    r = _choose_runtime(tree, path)
    return r["runtime"], r["marker"], r["unsupported"]


# --- 지원 런타임 ---

def test_java_pom():
    assert _rt(_tree("pom.xml", "src/main/java/App.java")) == ("java", "pom.xml", None)


def test_java_gradle_kts():
    assert _rt(_tree("build.gradle.kts")) == ("java", "build.gradle.kts", None)


def test_python_pyproject():
    assert _rt(_tree("pyproject.toml")) == ("python", "pyproject.toml", None)


def test_js_package_json():
    assert _rt(_tree("package.json")) == ("javascript", "package.json", None)


def test_laravel_is_php_not_js():
    assert _rt(_tree("composer.json", "package.json"))[0] == "php"


def test_spring_with_frontend_dir_is_java():
    assert _rt(_tree("pom.xml", "frontend/package.json"))[0] == "java"


def test_closer_marker_wins_over_order():
    # root가 JS 앱이면 하위 backend/보다 root를 따른다. 백엔드를 원하면 프로젝트 경로를 준다.
    tree = _tree("package.json", "backend/requirements.txt")
    assert _rt(tree)[0] == "javascript"
    assert _rt(tree, "backend") == ("python", "backend/requirements.txt", None)


# --- 지원 안 하는 런타임 ---

def test_go_is_unsupported():
    assert _rt(_tree("go.mod", "main.go")) == (None, "go.mod", "go")


def test_go_root_beats_nested_package_json():
    assert _rt(_tree("go.mod", "web/package.json")) == (None, "go.mod", "go")


def test_rails_is_ruby_not_js():
    assert _rt(_tree("Gemfile", "package.json")) == (None, "Gemfile", "ruby")


def test_supported_root_beats_nested_unsupported():
    assert _rt(_tree("pom.xml", "tools/go.mod"))[0] == "java"


# --- 경계 ---

def test_nothing_found():
    assert _rt(_tree("Dockerfile", "README.md")) == (None, None, None)


def test_depth_over_three_excluded():
    assert _rt(_tree("a/b/c/d/pom.xml")) == (None, None, None)


def test_non_blob_ignored():
    assert _rt([{"type": "tree", "path": "pom.xml"}]) == (None, None, None)


def test_project_path_scopes_search():
    tree = _tree("pom.xml", "api/requirements.txt")
    assert _rt(tree, "/api/")[0] == "python"


# --- 표 정합 ---

def test_detect_order_covers_every_marker_table():
    assert set(_DETECT_ORDER) == set(_RUNTIME_MARKERS) | set(_UNSUPPORTED_MARKERS)
    assert not set(_RUNTIME_MARKERS) & set(_UNSUPPORTED_MARKERS)


def test_all_selectable_runtimes_detectable():
    for rt in runtimes.SELECTABLE_RUNTIMES:
        if rt == "static":                # 정적 슬롯은 서버 런타임 추정 대상 아님
            continue
        assert rt in _RUNTIME_MARKERS, f"{rt} 마커 없음 - 감지 불가"
