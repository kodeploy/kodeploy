"""GitHub 최근 커밋 조회 — 40자 sha, 앱 주인의 연결 토큰, installation별 캐시, 커밋 하나 조회."""

import io
import json
import urllib.error

import pytest

from app.deploy.build import github

REPO = "https://github.com/acme/api.git"


def _gh_commit(sha, msg="feat: one\n\nbody"):
    return {"sha": sha, "html_url": f"https://github.com/acme/api/commit/{sha}",
            "commit": {"message": msg, "author": {"name": "kim", "date": "2026-11-18T06:20:00Z"}}}


class _Resp(io.BytesIO):
    def __init__(self, payload, etag=None):
        super().__init__(json.dumps(payload).encode())
        self.headers = {"ETag": etag} if etag else {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def net(monkeypatch):
    seen = []
    replies = []

    def urlopen(req, timeout=None):
        seen.append(req)
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(github.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(github.github_app, "get_clone_token", lambda inst: f"tok-{inst}" if inst else None)
    monkeypatch.setattr(github, "_COMMITS_CACHE", {})
    monkeypatch.setattr(github, "_COMMIT_CACHE", {})
    return seen, replies


def test_recent_commits_keep_the_full_sha_and_use_the_owner_token(net):
    seen, replies = net
    replies.append(_Resp([_gh_commit("a" * 40), _gh_commit("b" * 40, "fix")], etag='"e1"'))
    out = github.fetch_recent_commits(REPO, "release/1", installation_id=7)
    assert [c["sha"] for c in out] == ["a" * 40, "b" * 40]
    assert out[0]["message"] == "feat: one" and out[0]["body"] == "body" and out[0]["date"] == "2026-11-18T06:20:00Z"
    req = seen[0]
    assert req.full_url == "https://api.github.com/repos/acme/api/commits?sha=release%2F1&per_page=10"
    assert req.get_header("Authorization") == "Bearer tok-7"


def test_cache_is_separate_per_installation(net):
    seen, replies = net
    replies += [_Resp([_gh_commit("a" * 40)]), _Resp([])]
    assert len(github.fetch_recent_commits(REPO, "main", installation_id=7)) == 1
    assert github.fetch_recent_commits(REPO, "main") == []        # 토큰 없는 요청은 토큰으로 읽은 목록을 받지 않는다
    assert seen[1].get_header("Authorization") is None
    assert len(github.fetch_recent_commits(REPO, "main", installation_id=7)) == 1   # TTL 안 — 네트워크 없음
    assert len(seen) == 2


def test_revalidates_with_the_etag_after_the_ttl(net, monkeypatch):
    seen, replies = net
    clock = [1000.0]
    monkeypatch.setattr(github.time, "time", lambda: clock[0])
    replies += [_Resp([_gh_commit("a" * 40)], etag='"e1"'),
                urllib.error.HTTPError("u", 304, "not modified", {}, None)]
    github.fetch_recent_commits(REPO, "main")
    clock[0] += github._COMMITS_TTL_SECONDS + 1
    assert [c["sha"] for c in github.fetch_recent_commits(REPO, "main")] == ["a" * 40]
    assert seen[1].get_header("If-none-match") == '"e1"'


def test_one_commit_is_cached_and_failures_are_none(net):
    seen, replies = net
    replies += [_Resp(_gh_commit("c" * 40, "old")), urllib.error.HTTPError("u", 404, "nf", {}, None)]
    assert github.fetch_commit(REPO, "c" * 40, installation_id=7)["message"] == "old"
    assert github.fetch_commit(REPO, "c" * 40, installation_id=7)["sha"] == "c" * 40    # 캐시
    assert seen[0].full_url == f"https://api.github.com/repos/acme/api/commits/{'c' * 40}"
    assert github.fetch_commit(REPO, "d" * 40) is None
    assert github.fetch_commit("https://gitlab.com/a/b", "d" * 40) is None
    assert len(seen) == 2
