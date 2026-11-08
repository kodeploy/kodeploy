"""앱 이름·repo URL 규칙 고정 — service.py 분할(리팩토링) 전 안전망.

이름 규칙은 K8s metadata.name + 서브도메인 둘 다를 만족해야 하는 보안/충돌 경계라
동작이 1글자라도 바뀌면 라우팅·예약어 보호가 깨진다. 현행 동작을 그대로 고정한다.
"""

import pytest

from app.deploy.build import naming, pipeline


# --- _normalize_repo_url — BuildKit이 요구하는 .git 접미사 보장 ---

def test_normalize_repo_url_appends_git():
    assert naming._normalize_repo_url("https://github.com/u/r") == "https://github.com/u/r.git"


def test_normalize_repo_url_strips_space_and_trailing_slash():
    assert naming._normalize_repo_url(" https://github.com/u/r/ ") == "https://github.com/u/r.git"


def test_normalize_repo_url_idempotent_on_git_suffix():
    assert naming._normalize_repo_url("https://github.com/u/r.git") == "https://github.com/u/r.git"


# --- _validate_name_format — DNS-1123 + 예약 규칙 ---

@pytest.mark.parametrize("name", ["myapp", "a", "a-1b", "abc-def-123"])
def test_valid_names_pass(name):
    naming._validate_name_format(name)  # 예외 없으면 통과


@pytest.mark.parametrize("name", [
    "app-x",       # 자동 생성 prefix 예약
    "foo-api",     # {app}-api 서버 호스트 충돌 방지
    "api",         # RESERVED_NAMES
    "origin",      # RESERVED_NAMES — CF for SaaS fallback origin
    "myhome",      # RESERVED_NAMES — 클러스터 밖 VM이 쓰는 이름
    "myhome-ssh",  # RESERVED_NAMES — 같은 VM의 SSH 터널
    "a" * 41,      # 길이 40 초과
    "1abc",        # 숫자 시작
    "Abc",         # 대문자
    "a_b",         # 언더스코어
    "a-",          # 하이픈 끝
    "-a",          # 하이픈 시작
])
def test_invalid_names_raise(name):
    with pytest.raises(ValueError):
        naming._validate_name_format(name)


# --- _extract_from_repo — repo URL 마지막 segment → 이름 후보 ---

def test_extract_basic_and_git_suffix():
    assert naming._extract_from_repo("https://github.com/u/MyRepo.git") == "myrepo"


def test_extract_sanitizes_invalid_chars_to_hyphen():
    assert naming._extract_from_repo("https://github.com/u/My_Repo.Name") == "my-repo-name"


def test_extract_reserved_name_returns_none():
    assert naming._extract_from_repo("https://github.com/u/api") is None


def test_extract_invalid_format_returns_none():
    assert naming._extract_from_repo("https://github.com/u/123abc") is None


def test_extract_truncates_to_40():
    assert naming._extract_from_repo("https://github.com/u/" + "a" * 50) == "a" * 40


def test_extract_empty_after_sanitize_returns_none():
    assert naming._extract_from_repo("https://github.com/u/___") is None


# --- _extract_between — 빌드 로그 마커 추출 (nixpacks Dockerfile 보존 경로) ---

def test_extract_between_found():
    text = "noise\n===A===\nFROM x\n===B===\ntail"
    assert pipeline._extract_between(text, "===A===", "===B===") == "FROM x"


def test_extract_between_missing_markers():
    assert pipeline._extract_between("no markers", "===A===", "===B===") is None
    assert pipeline._extract_between("===A=== only start", "===A===", "===B===") is None
