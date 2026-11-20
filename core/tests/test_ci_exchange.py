"""CI 토큰 교환 — POST /apps/{app_id}/ci/token. 검사 1~9와 각각의 거절, 발급한 토큰은 해시만 남는지.

SQLite 파일 DB에 apps·builds·ci_tokens를 만들고 진짜 라우터를 부른다. GitHub JWKS만 테스트 키로 바꾼다 (ci_helpers).
"""

import hashlib
import logging
import time
import uuid
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.apps.model import App
from app.ci import router as ci_router
from app.ci.model import CiToken
from app.deploy.model import Build, DeployRequestKey
from app.shared import errors
from app.shared.db import get_db
from tests import ci_helpers as ci
from tests.test_redeploy import APP_ID, WEB_REPO, add_server, add_static, make_app

URL = f"/apps/{APP_ID}/ci/token"


@pytest.fixture
def env(tmp_path, monkeypatch):
    eng = sa.create_engine(f"sqlite:///{tmp_path / 'core.sqlite'}")
    for table in (Build.__table__, App.__table__, DeployRequestKey.__table__, CiToken.__table__):
        table.create(eng)
    Session = sessionmaker(bind=eng)
    jwks = ci.install_jwks(monkeypatch)

    api = FastAPI()
    errors.install(api)
    api.include_router(ci_router.router)

    def db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    api.dependency_overrides[get_db] = db
    return SimpleNamespace(Session=Session, jwks=jwks, api=api, client=TestClient(api))


def seed(env, *, enabled=True, server=True, static_branch=None, static_repo=None, server_repo=None):
    with env.Session() as s:
        app = make_app(s, site_enabled=static_branch is not None)
        app.ci_deploy_enabled = enabled
        s.commit()
        if server:
            add_server(s, "a0000001", 0, **({"repo_url": server_repo} if server_repo else {}))
        if static_branch is not None:
            add_static(s, "c0000001", 0, branch=static_branch, **({"repo_url": static_repo} if static_repo else {}))


def post(env, token):
    return env.client.post(URL, json={"id_token": token})


def tokens(env):
    with env.Session() as s:
        return s.query(CiToken).all()


def assert_rejected(r, status, code):
    assert r.status_code == status, r.text
    body = r.json()
    assert body["code"] == code and isinstance(body["detail"], str)
    assert set(body) == {"detail", "code"}


# --- 발급 ---------------------------------------------------------------------

def test_exchange_issues_a_token_and_keeps_only_its_hash(env):
    seed(env)
    tok = ci.id_token(run_id="555", run_attempt="2", actor="octocat", workflow="deploy")
    r = post(env, tok)
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == {"token", "expires_in", "app_id", "repository", "ref", "sha"}
    assert body["token"].startswith("kdci_") and len(body["token"]) > 40
    assert body["expires_in"] == 900
    assert (body["app_id"], body["repository"], body["ref"], body["sha"]) == (str(APP_ID), "acme/api", "refs/heads/main", ci.SHA)

    [row] = tokens(env)
    assert row.token_hash == hashlib.sha256(body["token"].encode()).hexdigest()
    stored = " ".join(str(getattr(row, c.name)) for c in CiToken.__table__.columns)
    assert body["token"] not in stored and tok not in stored        # 원문은 DB 어디에도 없다
    assert (row.run_id, row.run_attempt, row.actor, row.workflow, row.event_name) == ("555", "2", "octocat", "deploy", "push")
    assert 890 <= (row.expires_at - row.created_at).total_seconds() <= 900


def test_upper_case_sha_is_stored_lower_case(env):
    seed(env)
    r = post(env, ci.id_token(sha=ci.SHA.upper()))
    assert r.status_code == 201 and r.json()["sha"] == ci.SHA


def test_workflow_dispatch_is_allowed(env):
    seed(env)
    assert post(env, ci.id_token(event_name="workflow_dispatch")).status_code == 201


def test_repo_comparison_ignores_case_scheme_and_dot_git(env):
    seed(env, server_repo="http://github.com/Acme/API/")
    assert post(env, ci.id_token()).status_code == 201


def test_a_static_slot_on_its_own_branch_also_matches(env):
    seed(env, static_branch="gh-pages")
    r = post(env, ci.id_token(ref="refs/heads/gh-pages"))
    assert r.status_code == 201 and r.json()["ref"] == "refs/heads/gh-pages"


def test_a_static_slot_on_another_repo_matches_its_own_repo(env):
    seed(env, static_branch="main", static_repo=WEB_REPO)
    assert post(env, ci.id_token(repository="acme/web")).status_code == 201


# --- 1. 서명 -------------------------------------------------------------------

def test_signed_with_another_key_is_401(env):
    seed(env)
    assert_rejected(post(env, ci.id_token(key=ci.OTHER_KEY)), 401, "invalid_token")


@pytest.mark.parametrize("alg", ["none", "HS256"])
def test_swapped_algorithms_are_401(env, alg):
    seed(env)
    assert_rejected(post(env, ci.forged_token(alg)), 401, "invalid_token")


def test_garbage_is_401(env):
    seed(env)
    assert_rejected(post(env, "not-a-jwt"), 401, "invalid_token")


def test_unknown_kid_is_401(env):
    seed(env)
    assert_rejected(post(env, ci.id_token(kid="other")), 401, "invalid_token")


def test_nothing_touches_the_db_before_the_signature_is_checked(env):
    class NoDb:
        def __getattr__(self, name):
            raise AssertionError(f"DB used before the signature check: {name}")

    env.api.dependency_overrides[get_db] = lambda: NoDb()
    seed(env)
    for bad in (ci.id_token(key=ci.OTHER_KEY), ci.forged_token("none"), ci.id_token(aud="x"), "garbage"):
        assert post(env, bad).status_code == 401


def test_jwks_outage_is_503(env):
    seed(env)
    env.jwks.down = True
    assert_rejected(post(env, ci.id_token()), 503, "jwks_unavailable")


def test_rotated_key_is_fetched_once_after_the_cooldown(env):
    seed(env)
    assert post(env, ci.id_token()).status_code == 201
    assert env.jwks.fetches == 1
    env.jwks.keys = [ci.jwk_of(ci.KEY, ci.KID), ci.jwk_of(ci.OTHER_KEY, "kid-2")]

    # 방금 받았으면(30초 안) 모르는 kid라도 다시 받지 않는다 — 아무 kid나 넣어 GitHub 조회를 일으킬 수 없다
    assert_rejected(post(env, ci.id_token(key=ci.OTHER_KEY, kid="kid-2")), 401, "invalid_token")
    assert env.jwks.fetches == 1

    env.jwks.client._last_successful_fetch = time.monotonic() - 31
    assert post(env, ci.id_token(key=ci.OTHER_KEY, kid="kid-2")).status_code == 201
    assert env.jwks.fetches == 2
    assert post(env, ci.id_token(key=ci.OTHER_KEY, kid="kid-2")).status_code == 201
    assert env.jwks.fetches == 2                                    # 그다음부터는 캐시


# --- 2~4. iss · aud · 시각 --------------------------------------------------------

def test_other_issuer_is_401(env):
    seed(env)
    assert_rejected(post(env, ci.id_token(iss="https://evil.example.com")), 401, "invalid_issuer")


@pytest.mark.parametrize("aud", ["other", ["other", "x"], None])
def test_other_or_missing_audience_is_401(env, aud):
    seed(env)
    code = "invalid_token" if aud is None else "invalid_audience"
    assert_rejected(post(env, ci.id_token(aud=aud)), 401, code)


@pytest.mark.parametrize("over", [
    {"exp": -120, "iat": -600, "nbf": -600},     # 만료
    {"nbf": 120},                                 # 아직 유효하지 않음
    {"iat": 120},                                 # 미래에 발급
])
def test_time_claims_outside_the_leeway_are_401(env, over):
    seed(env)
    now = int(time.time())
    assert_rejected(post(env, ci.id_token(**{k: now + v for k, v in over.items()})), 401, "token_expired")


def test_time_claims_inside_the_60s_leeway_pass(env):
    seed(env)
    now = int(time.time())
    assert post(env, ci.id_token(exp=now - 30, nbf=now + 30, iat=now + 30)).status_code == 201


@pytest.mark.parametrize("claim", ["exp", "iat", "iss", "jti", "sub"])
def test_missing_required_claim_is_401(env, claim):
    seed(env)
    assert_rejected(post(env, ci.id_token(**{claim: None})), 401, "invalid_token")


# --- 5. jti -------------------------------------------------------------------

def test_reusing_a_jti_is_401(env):
    seed(env)
    tok = ci.id_token()
    assert post(env, tok).status_code == 201
    assert_rejected(post(env, tok), 401, "token_reused")
    jti = ci.claims()["jti"]
    assert post(env, ci.id_token(jti=jti)).status_code == 201       # 다른 jti는 따로
    assert_rejected(post(env, ci.id_token(jti=jti)), 401, "token_reused")
    assert len(tokens(env)) == 2


def test_a_rejected_exchange_does_not_spend_the_jti(env):
    seed(env, enabled=False)
    tok = ci.id_token()
    assert_rejected(post(env, tok), 403, "ci_deploy_disabled")
    with env.Session() as s:
        s.get(App, APP_ID).ci_deploy_enabled = True
        s.commit()
    assert post(env, tok).status_code == 201


# --- 6~9. 앱 · 이벤트 · 저장소/브랜치 · sha ----------------------------------------

def test_toggle_off_is_403(env):
    seed(env, enabled=False)
    assert_rejected(post(env, ci.id_token()), 403, "ci_deploy_disabled")
    assert tokens(env) == []


def test_missing_app_is_404(env):
    seed(env)
    r = env.client.post(f"/apps/{uuid.uuid4()}/ci/token", json={"id_token": ci.id_token()})
    assert_rejected(r, 404, "app_not_found")


@pytest.mark.parametrize("event", ["pull_request", "pull_request_target", "schedule"])
def test_other_events_are_403(env, event):
    seed(env)
    assert_rejected(post(env, ci.id_token(event_name=event)), 403, "event_not_allowed")


def test_other_repository_is_403(env):
    seed(env)
    assert_rejected(post(env, ci.id_token(repository="acme/other")), 403, "repo_mismatch")


@pytest.mark.parametrize("ref", ["refs/heads/dev", "refs/tags/v1.0.0", "refs/pull/1/merge", "main"])
def test_other_branch_or_non_branch_ref_is_403(env, ref):
    seed(env)
    assert_rejected(post(env, ci.id_token(ref=ref)), 403, "branch_mismatch")


def test_branch_of_a_static_slot_on_another_repo_is_not_enough(env):
    # 서버는 acme/api@main, 정적은 acme/web@gh-pages — acme/api의 gh-pages는 어느 슬롯과도 맞지 않는다
    seed(env, static_branch="gh-pages", static_repo=WEB_REPO)
    assert_rejected(post(env, ci.id_token(ref="refs/heads/gh-pages")), 403, "branch_mismatch")


@pytest.mark.parametrize("sha", ["abc1234", "z" * 40, "", None])
def test_short_or_bad_sha_is_401(env, sha):
    seed(env)
    assert_rejected(post(env, ci.id_token(sha=sha)), 401, "invalid_sha")


def test_no_live_deploy_is_409(env):
    seed(env, server=False)
    assert_rejected(post(env, ci.id_token()), 409, "no_live_deploy")


def test_declared_slot_without_a_success_is_409(env):
    seed(env, static_branch="main")
    with env.Session() as s:
        s.query(Build).filter(Build.runtime == "static").update({Build.status: "failed"})
        s.commit()
    assert_rejected(post(env, ci.id_token()), 409, "no_successful_slot")


# --- 본문·로그에 토큰이 남지 않는다 -----------------------------------------------

def test_rejections_and_logs_do_not_echo_the_token(env, caplog):
    seed(env)
    caplog.set_level(logging.DEBUG)
    cases = [ci.id_token(key=ci.OTHER_KEY), ci.id_token(iss="x"), ci.id_token(ref="refs/heads/dev"),
             ci.id_token(repository="acme/other"), ci.id_token(event_name="pull_request")]
    for tok in cases:
        r = post(env, tok)
        assert r.status_code in (401, 403)
        assert tok not in r.text and tok.split(".")[2] not in r.text
        assert "octocat" not in r.text
    ok = post(env, ci.id_token())
    issued = ok.json()["token"]
    for tok in cases:
        assert tok.split(".")[2] not in caplog.text
    assert issued not in caplog.text


def test_body_shape_errors_are_422(env):
    seed(env)
    assert env.client.post(URL, json={}).status_code == 422
    assert env.client.post(URL, json={"id_token": ""}).status_code == 422
    assert env.client.post(URL, json={"id_token": "x" * 20000}).status_code == 422
    assert env.client.post(URL, json={"id_token": ci.id_token(), "extra": 1}).status_code == 422
