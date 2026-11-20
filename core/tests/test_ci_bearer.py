"""CI 토큰(Bearer) — 받는 곳은 재배포와 상태 조회 두 곳뿐이고, CI 경로 재배포는 토큰의 저장소·브랜치·커밋으로만 간다.

test_redeploy의 env(SQLite 파일 DB + 진짜 start_deploy, 바깥 호출만 교체)를 그대로 쓰고, 토큰은 교환 API로 받는다.
"""

import logging
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps import router as apps_router
from app.apps.model import App, AppMember
from app.auth.deps import get_current_user_optional
from app.auth.model import User
from app.ci import router as ci_router
from app.ci.model import CiBuild, CiToken
from app.deploy import router as deploy_router
from app.deploy.model import Build, BuildRecord, DeployRequestKey
from app.shared import errors
from app.shared.db import get_db
from tests import ci_helpers as ci
from tests.test_redeploy import (  # noqa: F401 — env는 fixture
    API_REPO, APP_ID, OWNER, WEB_REPO, add_server, add_static, env, make_app,
)

OTHER_APP = uuid.UUID("99999999-2222-4333-8444-555555555555")
TOKEN_SHA = ci.SHA          # ci_helpers의 기본 sha (test_redeploy의 SHA와 같은 값)
SHA2 = "f" * 40


@pytest.fixture
def h(env, monkeypatch):
    engine = env.Session.kw["bind"]
    for table in (CiToken.__table__, CiBuild.__table__, User.__table__, AppMember.__table__, BuildRecord.__table__):
        table.create(engine)
    with env.Session() as s:
        s.add(User(id=OWNER, github_id=1, login="owner"))
        s.commit()
    jwks = ci.install_jwks(monkeypatch)

    access = {}

    def access_for(db, user, app_id):
        role = access.get(user.id)
        app = db.get(App, app_id)
        return (app, role, False) if app is not None and role else None

    monkeypatch.setattr(deploy_router.sharing, "access_for", access_for)

    state = SimpleNamespace(cookie_user=None)
    api = FastAPI()
    errors.install(api)
    api.include_router(deploy_router.router)
    api.include_router(deploy_router.router, prefix="/apps/{app_id}")
    api.include_router(apps_router.router)
    api.include_router(ci_router.router)

    def db():
        s = env.Session()
        try:
            yield s
        finally:
            s.close()

    api.dependency_overrides[get_db] = db
    api.dependency_overrides[get_current_user_optional] = lambda: state.cookie_user
    client = TestClient(api)

    def exchange(**claims):
        r = client.post(f"/apps/{APP_ID}/ci/token", json={"id_token": ci.id_token(**claims)})
        assert r.status_code == 201, r.text
        return r.json()["token"]

    def bearer(token):
        return {"Authorization": f"Bearer {token}"}

    def redeploy(token, body=None, key=None, path=None):
        headers = bearer(token)
        if key:
            headers["Idempotency-Key"] = key
        return client.post(path or f"/apps/{APP_ID}/deploy/redeploy", json=body or {}, headers=headers)

    def as_owner():
        access[OWNER] = "owner"
        state.cookie_user = User(id=OWNER, login="owner")

    return SimpleNamespace(env=env, client=client, jwks=jwks, access=access, state=state, exchange=exchange,
                           bearer=bearer, redeploy=redeploy, as_owner=as_owner)


def seed(h, *, enabled=True, static=None):
    """서버 acme/api@main. static=(repo, branch)면 정적 슬롯도."""
    with h.env.Session() as s:
        app = make_app(s, site_enabled=static is not None)
        app.ci_deploy_enabled = enabled
        s.commit()
        add_server(s, "a0000001", 0)
        if static is not None:
            add_static(s, "c0000001", 0, repo_url=static[0], branch=static[1])


def refs(r):
    return {b["slot"]: b["ref"] for b in r.json()["builds"]}


def set_toggle(h, enabled):
    h.as_owner()
    r = h.client.put(f"/apps/{APP_ID}/ci-deploy", json={"enabled": enabled})
    h.state.cookie_user = None
    assert r.status_code == 200, r.text
    return r


# --- 받는 곳 두 곳 --------------------------------------------------------------

def test_redeploy_with_a_ci_token(h):
    seed(h)
    r = h.redeploy(h.exchange())
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["app_id"] == str(APP_ID) and body["requested_sha"] == TOKEN_SHA and body["replayed"] is False
    assert refs(r) == {"server": TOKEN_SHA}
    with h.env.Session() as s:
        build = s.get(Build, body["builds"][0]["build_id"])
        assert build.user_id == OWNER and build.requested_sha == TOKEN_SHA    # 빌드 사용자는 앱 주인


def test_legacy_query_path_also_takes_the_token(h):
    seed(h)
    r = h.redeploy(h.exchange(), path=f"/deploy/redeploy?app_id={APP_ID}")
    assert r.status_code == 202, r.text


def test_status_with_a_ci_token_shows_the_actions_run(h):
    seed(h)
    token = h.exchange(actor="octocat", run_id="4242", run_attempt="3", workflow="deploy")
    build_id = h.redeploy(token).json()["builds"][0]["build_id"]
    r = h.client.get(f"/apps/{APP_ID}/deploy/{build_id}", headers=h.bearer(token))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued"
    assert r.json()["ci"] == {"actor": "octocat", "repository": "acme/api", "ref": "refs/heads/main", "sha": TOKEN_SHA,
                              "run_id": "4242", "run_attempt": "3", "workflow": "deploy", "event_name": "push"}
    # 웹으로 시작한 옛 빌드는 ci가 없다
    assert h.client.get(f"/apps/{APP_ID}/deploy/a0000001", headers=h.bearer(token)).json()["ci"] is None


def test_status_of_another_apps_build_is_404(h):
    seed(h)
    with h.env.Session() as s:
        s.add(App(id=OTHER_APP, owner_id=OWNER, name="other", namespace="app-99999999", pipeline="v2"))
        s.commit()
        add_server(s, "b0000001", 1, app_id=OTHER_APP, namespace="app-99999999")
    token = h.exchange()
    r = h.client.get(f"/apps/{APP_ID}/deploy/b0000001", headers=h.bearer(token))
    assert r.status_code == 404


def test_legacy_status_path_without_an_app_is_400_for_a_token(h):
    seed(h)
    r = h.client.get("/deploy/a0000001", headers=h.bearer(h.exchange()))
    assert r.status_code == 400 and r.json()["code"] == "app_id_required"


@pytest.mark.parametrize("method,path", [
    ("get", "/apps/{app}/deploy"),                       # 빌드 목록
    ("get", "/apps/{app}/deploy/env"),
    ("put", "/apps/{app}/deploy/env"),
    ("delete", "/apps/{app}/deploy/app"),
    ("post", "/apps/{app}/deploy"),                      # 설정 변경(배포 폼)
    ("post", "/apps/{app}/deploy/a0000001/rollback"),
    ("get", "/apps/{app}/deploy/redeploy/commits"),
    ("get", "/apps/{app}/deploy/app/logs"),
    ("put", "/apps/{app}/ci-deploy"),
    ("get", "/apps/{app}"),
    ("get", "/apps"),
])
def test_other_routes_reject_a_token_only_request(h, method, path):
    seed(h)
    token = h.exchange()
    kw = {"json": {}} if method in ("post", "put") else {}
    r = getattr(h.client, method)(path.replace("{app}", str(APP_ID)), headers=h.bearer(token), **kw)
    assert r.status_code == 401, (path, r.status_code, r.text)


@pytest.mark.parametrize("route", ["redeploy", "status"])
def test_a_token_for_another_app_is_403(h, route):
    seed(h)
    with h.env.Session() as s:
        s.add(App(id=OTHER_APP, owner_id=OWNER, name="other", namespace="app-99999999", pipeline="v2",
                  ci_deploy_enabled=True))
        s.commit()
    token = h.exchange()
    if route == "redeploy":
        r = h.redeploy(token, path=f"/apps/{OTHER_APP}/deploy/redeploy")
    else:
        r = h.client.get(f"/apps/{OTHER_APP}/deploy/a0000001", headers=h.bearer(token))
    assert r.status_code == 403 and r.json()["code"] == "ci_token_app_mismatch"


def test_an_expired_token_is_401(h):
    seed(h)
    token = h.exchange()
    with h.env.Session() as s:
        s.query(CiToken).update({CiToken.expires_at: datetime.utcnow() - timedelta(seconds=1)})
        s.commit()
    r = h.redeploy(token)
    assert r.status_code == 401 and r.json()["code"] == "ci_token_expired"
    r = h.client.get(f"/apps/{APP_ID}/deploy/a0000001", headers=h.bearer(token))
    assert r.status_code == 401 and r.json()["code"] == "ci_token_expired"


def test_turning_the_toggle_off_invalidates_issued_tokens(h):
    seed(h)
    token = h.exchange()
    assert h.client.get(f"/apps/{APP_ID}/deploy/a0000001", headers=h.bearer(token)).status_code == 200
    set_toggle(h, False)
    r = h.redeploy(token)
    assert r.status_code == 401 and r.json()["code"] in ("ci_deploy_disabled", "ci_token_expired")
    with h.env.Session() as s:
        assert s.query(Build).count() == 1                       # 아무것도 시작하지 않았다
    # 다시 켜도 예전 토큰은 살아나지 않는다 — 새로 교환해야 한다
    set_toggle(h, True)
    r = h.redeploy(token)
    assert r.status_code == 401 and r.json()["code"] == "ci_token_expired"
    assert h.redeploy(h.exchange()).status_code == 202


def test_disabled_app_rejects_a_live_token(h):
    seed(h)
    token = h.exchange()
    with h.env.Session() as s:                                  # 토큰 만료 없이 허용만 꺼진 경우도 쓸 때 다시 본다
        s.get(App, APP_ID).ci_deploy_enabled = False
        s.commit()
    r = h.client.get(f"/apps/{APP_ID}/deploy/a0000001", headers=h.bearer(token))
    assert r.status_code == 401 and r.json()["code"] == "ci_deploy_disabled"


@pytest.mark.parametrize("value", ["kdci_unknown", "something-else", ""])
def test_unknown_tokens_are_401(h, value):
    seed(h)
    r = h.client.post(f"/apps/{APP_ID}/deploy/redeploy", json={}, headers={"Authorization": f"Bearer {value}"})
    assert r.status_code == 401 and r.json()["code"] == "ci_token_invalid"


def test_a_bad_bearer_is_not_rescued_by_a_valid_cookie(h):
    seed(h)
    h.as_owner()
    r = h.client.post(f"/apps/{APP_ID}/deploy/redeploy", json={}, headers={"Authorization": "Bearer kdci_nope"})
    assert r.status_code == 401
    assert h.client.post(f"/apps/{APP_ID}/deploy/redeploy", json={}).status_code == 202   # 쿠키만이면 그대로


# --- CI 경로 재배포 규칙 ----------------------------------------------------------

def test_commit_and_repo_default_to_the_token(h):
    seed(h)
    token = h.exchange()
    r = h.redeploy(token, {"commit_sha": TOKEN_SHA.upper(), "repo": "https://GitHub.com/acme/api"})
    assert r.status_code == 202 and refs(r) == {"server": TOKEN_SHA}


@pytest.mark.parametrize("body,status,code", [
    ({"commit_sha": SHA2}, 403, "commit_mismatch"),
    ({"repo": WEB_REPO}, 403, "repo_mismatch"),
    ({"commit_sha": "abc1234"}, 400, "invalid_commit_sha"),
    ({"env": {"A": "1"}}, 422, None),
])
def test_ci_request_cannot_choose_another_commit_or_repo(h, body, status, code):
    seed(h)
    r = h.redeploy(h.exchange(), body)
    assert r.status_code == status, r.text
    if code:
        assert r.json()["code"] == code
    with h.env.Session() as s:
        assert s.query(Build).count() == 1


def test_same_repo_static_slot_on_another_branch_is_not_pinned(h):
    seed(h, static=(API_REPO, "gh-pages"))
    r = h.redeploy(h.exchange(ref="refs/heads/main"))
    assert r.status_code == 202, r.text
    assert refs(r) == {"server": TOKEN_SHA, "static": "gh-pages"}


def test_web_session_still_pins_every_slot_on_the_same_repo(h):
    seed(h, static=(API_REPO, "gh-pages"))
    h.as_owner()
    r = h.client.post(f"/apps/{APP_ID}/deploy/redeploy", json={"commit_sha": SHA2})
    assert r.status_code == 202 and refs(r) == {"server": SHA2, "static": SHA2}     # 웹 경로 규칙은 그대로


def test_a_token_for_the_static_branch_pins_only_the_static_slot(h):
    seed(h, static=(API_REPO, "gh-pages"))
    r = h.redeploy(h.exchange(ref="refs/heads/gh-pages"))
    assert r.status_code == 202 and refs(r) == {"server": "main", "static": TOKEN_SHA}


def test_a_slot_on_another_repo_is_rebuilt_from_its_branch(h):
    seed(h, static=(WEB_REPO, "develop"))
    r = h.redeploy(h.exchange())
    assert r.status_code == 202, r.text
    assert refs(r) == {"server": TOKEN_SHA, "static": "develop"}
    with h.env.Session() as s:
        static = s.get(Build, r.json()["builds"][1]["build_id"])
        assert static.repo_url == WEB_REPO and static.requested_sha is None


def test_branch_changed_after_the_exchange_is_403(h):
    seed(h)
    token = h.exchange()
    with h.env.Session() as s:                                   # 그 사이 설정 변경으로 서버 브랜치가 바뀌었다
        add_server(s, "a0000002", 5, branch="release")
    r = h.redeploy(token)
    assert r.status_code == 403 and r.json()["code"] == "branch_mismatch"


def test_ci_builds_are_recorded_and_listed(h):
    seed(h, static=(API_REPO, "gh-pages"))
    token = h.exchange(actor="octocat", run_id="777", run_attempt="1", workflow="deploy")
    ids = [b["build_id"] for b in h.redeploy(token).json()["builds"]]
    with h.env.Session() as s:
        rows = {r.build_id: r for r in s.query(CiBuild).all()}
        assert set(rows) == set(ids)
        assert all((r.actor, r.run_id, r.repository, r.ref, r.sha) == ("octocat", "777", "acme/api", "refs/heads/main", TOKEN_SHA)
                   for r in rows.values())
    h.as_owner()
    listed = {b["build_id"]: b["ci"] for b in h.client.get(f"/apps/{APP_ID}/deploy").json()}
    assert all(listed[i]["run_id"] == "777" for i in ids)
    assert listed["a0000001"] is None and listed["c0000001"] is None


def test_same_key_replays_without_new_builds_or_records(h):
    seed(h)
    token = h.exchange()
    r1 = h.redeploy(token, key="gha-1-1")
    r2 = h.redeploy(token, key="gha-1-1")
    assert (r1.status_code, r2.status_code) == (202, 200)
    assert r2.json()["replayed"] is True and r2.json()["builds"] == r1.json()["builds"]
    # 토큰을 다시 교환해 같은 키로 보내도 그대로 (CLI의 401 재교환 뒤 재전송)
    r3 = h.redeploy(h.exchange(), key="gha-1-1")
    assert r3.status_code == 200 and r3.json()["builds"] == r1.json()["builds"]
    with h.env.Session() as s:
        assert s.query(CiBuild).count() == 1 and s.query(Build).count() == 2


def test_key_in_progress_has_a_machine_code(h):
    seed(h)
    with h.env.Session() as s:
        s.add(DeployRequestKey(app_id=APP_ID, key="gha-2-1", build_ids=None))
        s.commit()
    r = h.redeploy(h.exchange(), key="gha-2-1")
    assert r.status_code == 409 and r.json()["code"] == "key_in_progress"


def test_web_session_errors_carry_codes_too(h):
    with h.env.Session() as s:
        make_app(s)
    h.as_owner()
    r = h.client.post(f"/apps/{APP_ID}/deploy/redeploy", json={})
    assert r.status_code == 409 and r.json()["code"] == "no_live_deploy"
    assert "배포 폼" in r.json()["detail"]


# --- 토글 API ------------------------------------------------------------------

@pytest.mark.parametrize("role,code", [("owner", 200), ("editor", 403), ("viewer", 403), (None, 404)])
def test_only_the_owner_flips_the_toggle(h, role, code):
    seed(h, enabled=False)
    user = User(id=OWNER if role == "owner" else uuid.uuid4(), login="u")
    if role:
        h.access[user.id] = role
    h.state.cookie_user = user
    r = h.client.put(f"/apps/{APP_ID}/ci-deploy", json={"enabled": True})
    assert r.status_code == code, r.text
    with h.env.Session() as s:
        assert s.get(App, APP_ID).ci_deploy_enabled is (code == 200)


def test_app_response_carries_the_toggle(h):
    seed(h, enabled=False)
    h.as_owner()
    assert h.client.get(f"/apps/{APP_ID}").json()["ci_deploy_enabled"] is False
    h.client.put(f"/apps/{APP_ID}/ci-deploy", json={"enabled": True})
    assert h.client.get(f"/apps/{APP_ID}").json()["ci_deploy_enabled"] is True


# --- 토큰 원문이 남지 않는다 -------------------------------------------------------

def test_tokens_never_show_up_in_responses_or_logs(h, caplog):
    seed(h)
    caplog.set_level(logging.DEBUG)
    token = h.exchange()
    texts = [
        h.redeploy(token).text,
        h.redeploy(token, {"commit_sha": SHA2}).text,
        h.client.get(f"/apps/{APP_ID}/deploy/a0000001", headers=h.bearer(token)).text,
        h.client.get(f"/apps/{OTHER_APP}/deploy/a0000001", headers=h.bearer(token)).text,
    ]
    set_toggle(h, False)
    texts.append(h.redeploy(token).text)
    for t in texts:
        assert token not in t
    assert token not in caplog.text
