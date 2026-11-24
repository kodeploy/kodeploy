"""CLI 토큰(Bearer kdcli_) — 앱 주인이 웹에서 발급하고, 받는 곳은 재배포·상태 조회·최근 커밋 조회 세 곳뿐이다.
재배포는 발급한 사람이 웹에서 "배포하기"를 누른 것과 같은 규칙으로 돈다 (CI 토큰의 커밋 강제·칸 고정은 없다).

test_redeploy의 env(SQLite 파일 DB + 진짜 start_deploy, 바깥 호출만 교체)를 그대로 쓰고, 시계는 cli 서비스의 _now만 바꾼다.
"""

import hashlib
import logging
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config
from app.admin.model import AdminAction
from app.apps import router as apps_router
from app.apps.model import App, AppMember
from app.auth.deps import get_current_user_optional
from app.auth.model import User
from app.ci import router as ci_router
from app.ci.model import CiBuild, CiToken
from app.cli import router as cli_router
from app.cli import service as cli_service
from app.cli.model import CliToken
from app.deploy import router as deploy_router
from app.deploy.model import Build, BuildRecord, DeployRequestKey
from app.exec import router as exec_router
from app.shared import errors
from app.shared.db import get_db
from tests.test_redeploy import (  # noqa: F401 — env는 fixture
    API_REPO, APP_ID, OWNER, SERVER_FIELDS, STATIC_FIELDS, WEB_REPO, add_server, add_static, env, make_app,
)

ORIGIN = "https://kodeploy.test"
OTHER_APP = uuid.UUID("99999999-2222-4333-8444-555555555555")
EDITOR = uuid.UUID("e0000000-0000-4000-8000-000000000001")
VIEWER = uuid.UUID("e0000000-0000-4000-8000-000000000002")
STRANGER = uuid.UUID("e0000000-0000-4000-8000-000000000003")
ROOT = uuid.UUID("e0000000-0000-4000-8000-000000000004")
SHA2 = "f" * 40
LIVE_SERVER = "a1" * 20
LIVE_STATIC = "c3" * 20
T = datetime(2026, 11, 25, 3, 0, 0)          # 발급 시각 (naive UTC)


@pytest.fixture
def h(env, monkeypatch):
    engine = env.Session.kw["bind"]
    for table in (CliToken, CiToken, CiBuild, User, AppMember, BuildRecord, AdminAction):
        table.__table__.create(engine)
    with env.Session() as s:
        s.add_all([
            User(id=OWNER, github_id=1, login="owner"),
            User(id=EDITOR, github_id=2, login="editor"),
            User(id=VIEWER, github_id=3, login="viewer"),
            User(id=STRANGER, github_id=4, login="stranger"),
            User(id=ROOT, github_id=5, login="root", role="root"),
        ])
        s.commit()

    clock = SimpleNamespace(now=T)
    monkeypatch.setattr(cli_service, "_now", lambda: clock.now)
    monkeypatch.setattr(config, "ALLOWED_ORIGINS", [ORIGIN])

    def recent(repo_url, branch, per_page=10, installation_id=None):
        return [{"sha": c * 40, "message": f"commit {c}", "date": "2026-11-20T00:00:00Z", "url": None} for c in "abcd"]

    monkeypatch.setattr(deploy_router.github, "fetch_recent_commits", recent)
    monkeypatch.setattr(deploy_router.github, "fetch_commit", lambda repo_url, sha, installation_id=None: None)

    state = SimpleNamespace(cookie_user=None, tickets=[])

    async def issue_ticket(ns, **kw):
        state.tickets.append((ns, kw))
        return {"ticket": "TK", "expires_at": "x"}

    monkeypatch.setattr(exec_router.exec_client, "issue_ticket", issue_ticket)
    api = FastAPI()
    errors.install(api)
    api.include_router(deploy_router.router)
    api.include_router(deploy_router.router, prefix="/apps/{app_id}")
    api.include_router(apps_router.router)
    api.include_router(ci_router.router)
    api.include_router(cli_router.router)
    api.include_router(exec_router.router)

    def db():
        s = env.Session()
        try:
            yield s
        finally:
            s.close()

    api.dependency_overrides[get_db] = db
    api.dependency_overrides[get_current_user_optional] = lambda: state.cookie_user
    client = TestClient(api)

    def login(user_id):
        if user_id is None:
            state.cookie_user = None
            return
        with env.Session() as s:
            state.cookie_user = s.get(User, user_id)

    def issue(user_id=OWNER, app_id=APP_ID):
        login(user_id)
        r = client.post(f"/apps/{app_id}/cli-token", headers={"Origin": ORIGIN})
        login(None)
        assert r.status_code == 201, r.text
        return r.json()["token"]

    def bearer(token):
        return {"Authorization": f"Bearer {token}"}

    def redeploy(token, body=None, key=None, path=None):
        headers = bearer(token)
        if key:
            headers["Idempotency-Key"] = key
        return client.post(path or f"/apps/{APP_ID}/deploy/redeploy", json=body or {}, headers=headers)

    def status(token, build_id="a0000001", app_id=APP_ID):
        return client.get(f"/apps/{app_id}/deploy/{build_id}", headers=bearer(token))

    def row():
        with env.Session() as s:
            rows = s.query(CliToken).order_by(CliToken.id.desc()).all()
            for r in rows:
                s.expunge(r)
            return rows[0] if rows else None

    return SimpleNamespace(env=env, client=client, clock=clock, state=state, login=login, issue=issue, bearer=bearer,
                           redeploy=redeploy, status=status, row=row)


def seed(h, *, static=None, members=True):
    """서버 acme/api@main(떠 있는 커밋 LIVE_SERVER). static=(repo, branch)면 정적 슬롯도. GitHub Actions 배포 허용은 꺼 둔다."""
    with h.env.Session() as s:
        make_app(s, site_enabled=static is not None)
        if members:
            s.add_all([AppMember(app_id=APP_ID, user_id=EDITOR, role="editor"),
                       AppMember(app_id=APP_ID, user_id=VIEWER, role="viewer")])
            s.commit()
        add_server(s, "a0000001", 0, source_sha=LIVE_SERVER)
        if static is not None:
            add_static(s, "c0000001", 0, repo_url=static[0], branch=static[1], source_sha=LIVE_STATIC)


def refs(r):
    return {b["slot"]: b["ref"] for b in r.json()["builds"]}


# --- 발급 ---------------------------------------------------------------------

def test_owner_issues_a_token_shown_only_once(h, caplog):
    seed(h)
    caplog.set_level(logging.DEBUG)
    h.login(OWNER)
    r = h.client.post(f"/apps/{APP_ID}/cli-token", headers={"Origin": ORIGIN})
    assert r.status_code == 201, r.text
    assert r.headers["cache-control"] == "no-store"
    raw = r.json()["token"]
    assert raw.startswith("kdcli_") and len(raw) == len("kdcli_") + 43           # 32바이트 → urlsafe base64 43자
    body = r.json()
    assert body["issued_by_login"] == "owner" and body["idle_seconds"] == 900
    assert body["expires_at"] == (T + timedelta(minutes=15)).isoformat()
    assert body["max_expires_at"] == (T + timedelta(hours=8)).isoformat()

    # 서버에는 sha256만 — 어느 칸에도, 어느 로그에도 원문이 없다
    row = h.row()
    assert row.token_hash == hashlib.sha256(raw.encode()).hexdigest()
    assert all(raw not in str(getattr(row, c.name)) for c in CliToken.__table__.columns)
    assert raw not in caplog.text
    # 다시 읽어도 원문은 나오지 않는다
    state = h.client.get(f"/apps/{APP_ID}/cli-token")
    assert state.status_code == 200 and raw not in state.text
    assert state.json()["current"]["issued_by_login"] == "owner"
    assert "token" not in state.json()["current"]


@pytest.mark.parametrize("who,code", [(EDITOR, 403), (VIEWER, 403), (STRANGER, 404), (None, 401)])
def test_only_the_owner_can_issue_or_revoke(h, who, code):
    seed(h)
    h.login(who)
    assert h.client.post(f"/apps/{APP_ID}/cli-token", headers={"Origin": ORIGIN}).status_code == code
    assert h.client.delete(f"/apps/{APP_ID}/cli-token", headers={"Origin": ORIGIN}).status_code == code
    assert h.client.get(f"/apps/{APP_ID}/cli-token").status_code == code
    assert h.row() is None


@pytest.mark.parametrize("origin", [None, "https://evil.example"])
def test_issue_and_revoke_need_an_allowed_origin(h, origin):
    seed(h)
    h.login(OWNER)
    headers = {"Origin": origin} if origin else {}
    assert h.client.post(f"/apps/{APP_ID}/cli-token", headers=headers).status_code == 403
    assert h.client.delete(f"/apps/{APP_ID}/cli-token", headers=headers).status_code == 403
    assert h.row() is None


def test_a_cli_token_cannot_issue_or_revoke_tokens(h):
    seed(h)
    token = h.issue()
    headers = {**h.bearer(token), "Origin": ORIGIN}
    assert h.client.post(f"/apps/{APP_ID}/cli-token", headers=headers).status_code == 401
    assert h.client.delete(f"/apps/{APP_ID}/cli-token", headers=headers).status_code == 401
    assert h.status(token).status_code == 200                               # 그대로 살아 있다


def test_root_can_issue_for_any_app_and_it_is_recorded_as_cli(h):
    seed(h)
    token = h.issue(ROOT)
    r = h.redeploy(token)
    assert r.status_code == 202, r.text
    with h.env.Session() as s:
        build = s.get(Build, r.json()["builds"][0]["build_id"])
        assert build.user_id == ROOT                                         # 발급한 사람의 행동
        actions = [(a.actor_login, a.action) for a in s.query(AdminAction).order_by(AdminAction.id)]
    assert actions == [("root", "POST /apps/{app_id}/cli-token"), ("root", "POST /deploy/redeploy (cli)")]
    # 상태 조회(GET)는 기록하지 않는다 — 쿠키 경로와 같다
    assert h.status(token).status_code == 200
    with h.env.Session() as s:
        assert s.query(AdminAction).count() == 2


def test_issuer_who_is_no_longer_owner_cannot_use_the_token(h):
    seed(h)
    token = h.issue(ROOT)
    with h.env.Session() as s:
        s.get(User, ROOT).role = "user"
        s.commit()
    r = h.status(token)
    assert r.status_code == 401 and r.json()["code"] == "cli_token_invalid"


# --- 받는 곳 세 곳 ---------------------------------------------------------------

def outcome(h, r):
    """재배포 응답과 만든 빌드 행 — 빌드 id·시각을 빼고 비교할 값."""
    body = r.json()
    with h.env.Session() as s:
        rows = [s.get(Build, b["build_id"]) for b in body["builds"]]
        built = [
            {**{n: getattr(b, n) for n in (STATIC_FIELDS if b.runtime == "static" else SERVER_FIELDS)},
             "requested_sha": b.requested_sha, "user_id": b.user_id, "status": b.status}
            for b in rows
        ]
    return r.status_code, body["requested_sha"], body["replayed"], refs(r), built


def undo(h, r):
    ids = [b["build_id"] for b in r.json()["builds"]]
    with h.env.Session() as s:
        s.query(Build).filter(Build.build_id.in_(ids)).delete(synchronize_session=False)
        s.query(DeployRequestKey).delete()
        s.commit()


@pytest.mark.parametrize("static", [None, (API_REPO, "gh-pages"), (WEB_REPO, "develop")])
@pytest.mark.parametrize("body", [{}, {"commit_sha": SHA2}])
def test_cli_redeploy_is_the_same_as_the_web_deploy_button(h, static, body):
    seed(h, static=static)
    token = h.issue()

    h.login(OWNER)
    web = h.client.post(f"/apps/{APP_ID}/deploy/redeploy", json=body)
    assert web.status_code == 202, web.text
    expected = outcome(h, web)
    undo(h, web)
    h.login(None)

    cli = h.redeploy(token, body)
    assert cli.status_code == 202, cli.text
    assert outcome(h, cli) == expected


def test_cli_redeploy_does_not_use_the_ci_rules(h):
    # 같은 저장소의 다른 브랜치 정적 칸 — CI 토큰이면 라이브 커밋으로 고정되지만 웹 규칙은 커밋을 같이 건다
    seed(h, static=(API_REPO, "gh-pages"))
    r = h.redeploy(h.issue(), {"commit_sha": SHA2})
    assert r.status_code == 202 and refs(r) == {"server": SHA2, "static": SHA2}
    # 다른 저장소 칸은 브랜치 최신 (CI 경로는 라이브 커밋)
    with h.env.Session() as s:
        s.query(Build).delete()
        s.commit()
        add_server(s, "a0000001", 0, source_sha=LIVE_SERVER)
        add_static(s, "c0000001", 0, repo_url=WEB_REPO, branch="develop", source_sha=None)
    r = h.redeploy(h.issue())
    assert r.status_code == 202 and refs(r) == {"server": "main", "static": "develop"}    # 커밋 기록 없는 칸도 409가 아니다
    with h.env.Session() as s:
        assert s.query(CiBuild).count() == 0


def test_ci_deploy_switch_does_not_matter(h):
    seed(h)
    with h.env.Session() as s:
        assert s.get(App, APP_ID).ci_deploy_enabled is False
    token = h.issue()
    assert h.redeploy(token).status_code == 202
    # 켰다 꺼도 CLI 토큰은 그대로
    h.login(OWNER)
    assert h.client.put(f"/apps/{APP_ID}/ci-deploy", json={"enabled": True}).status_code == 200
    assert h.client.put(f"/apps/{APP_ID}/ci-deploy", json={"enabled": False}).status_code == 200
    h.login(None)
    assert h.status(token).status_code == 200


def test_same_key_replays(h):
    seed(h)
    token = h.issue()
    r1 = h.redeploy(token, key="cli-1")
    r2 = h.redeploy(token, key="cli-1")
    assert (r1.status_code, r2.status_code) == (202, 200)
    assert r2.json()["replayed"] is True and r2.json()["builds"] == r1.json()["builds"]


def test_status_and_commits_take_the_token(h):
    seed(h)
    token = h.issue()
    r = h.status(token)
    assert r.status_code == 200 and r.json()["build_id"] == "a0000001" and r.json()["ci"] is None
    r = h.client.get(f"/apps/{APP_ID}/deploy/redeploy/commits", headers=h.bearer(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["branch"] == "main" and len(body["commits"]) == 3 and body["current_sha"] == LIVE_SERVER
    assert body["current"]["sha"] == LIVE_SERVER


def test_legacy_paths(h):
    seed(h)
    token = h.issue()
    assert h.redeploy(token, path=f"/deploy/redeploy?app_id={APP_ID}").status_code == 202
    r = h.client.get("/deploy/a0000001", headers=h.bearer(token))
    assert r.status_code == 400 and r.json()["code"] == "app_id_required"


@pytest.mark.parametrize("method,path", [
    ("get", "/apps/{app}/deploy"),                       # 빌드 목록
    ("get", "/apps/{app}/deploy/env"),
    ("put", "/apps/{app}/deploy/env"),
    ("delete", "/apps/{app}/deploy/app"),
    ("post", "/apps/{app}/deploy"),                      # 설정 변경(배포 폼)
    ("post", "/apps/{app}/deploy/a0000001/rollback"),
    ("get", "/apps/{app}/deploy/app/logs"),
    ("post", "/apps/{app}/deploy/app/db/query"),
    ("put", "/apps/{app}/ci-deploy"),
    ("get", "/apps/{app}"),
    ("get", "/apps"),
    ("get", "/apps/{app}/cli-token"),
])
def test_other_routes_reject_a_cli_token(h, method, path):
    seed(h)
    token = h.issue()
    kw = {"json": {}} if method in ("post", "put") else {}
    headers = {**h.bearer(token), "Origin": ORIGIN}
    r = getattr(h.client, method)(path.replace("{app}", str(APP_ID)), headers=headers, **kw)
    assert r.status_code == 401, (path, r.status_code, r.text)


@pytest.mark.parametrize("route", ["redeploy", "status", "commits"])
def test_a_token_for_another_app_is_403(h, route):
    seed(h)
    with h.env.Session() as s:
        s.add(App(id=OTHER_APP, owner_id=OWNER, name="other", namespace="app-99999999", pipeline="v2"))
        s.commit()
        add_server(s, "b0000001", 1, app_id=OTHER_APP, namespace="app-99999999")
    token = h.issue()
    if route == "redeploy":
        r = h.redeploy(token, path=f"/apps/{OTHER_APP}/deploy/redeploy")
    elif route == "status":
        r = h.status(token, "b0000001", app_id=OTHER_APP)
    else:
        r = h.client.get(f"/apps/{OTHER_APP}/deploy/redeploy/commits", headers=h.bearer(token))
    assert r.status_code == 403 and r.json()["code"] == "cli_token_app_mismatch"
    with h.env.Session() as s:
        assert s.query(Build).count() == 2


def test_status_of_another_apps_build_is_404(h):
    seed(h)
    with h.env.Session() as s:
        s.add(App(id=OTHER_APP, owner_id=OWNER, name="other", namespace="app-99999999", pipeline="v2"))
        s.commit()
        add_server(s, "b0000001", 1, app_id=OTHER_APP, namespace="app-99999999")
    assert h.status(h.issue(), "b0000001").status_code == 404


@pytest.mark.parametrize("value", ["kdcli_unknown", "kdcli_"])
def test_unknown_cli_tokens_are_401(h, value):
    seed(h)
    r = h.redeploy(value)
    assert r.status_code == 401 and r.json()["code"] == "cli_token_invalid"


def test_a_bad_cli_token_is_not_rescued_by_a_valid_cookie(h):
    seed(h)
    h.login(OWNER)
    r = h.client.post(f"/apps/{APP_ID}/deploy/redeploy", json={}, headers={"Authorization": "Bearer kdcli_nope"})
    assert r.status_code == 401
    r = h.client.get(f"/apps/{APP_ID}/deploy/redeploy/commits", headers={"Authorization": "Bearer kdcli_nope"})
    assert r.status_code == 401
    assert h.client.post(f"/apps/{APP_ID}/deploy/redeploy", json={}).status_code == 202    # 쿠키만이면 그대로


# --- 수명 --------------------------------------------------------------------

def test_unused_for_15_minutes_expires(h):
    seed(h)
    token = h.issue()
    h.clock.now = T + timedelta(minutes=14, seconds=59)
    assert h.status(token).status_code == 200
    h.clock.now = T + timedelta(minutes=14, seconds=59) + timedelta(minutes=15)
    r = h.status(token)
    assert r.status_code == 401 and r.json()["code"] == "cli_token_expired"


def test_never_used_for_15_minutes_expires(h):
    seed(h)
    token = h.issue()
    h.clock.now = T + timedelta(minutes=15)
    r = h.redeploy(token)
    assert r.status_code == 401 and r.json()["code"] == "cli_token_expired"
    with h.env.Session() as s:
        assert s.query(Build).count() == 1


def test_using_it_extends_to_15_minutes_from_now(h):
    seed(h)
    token = h.issue()
    h.clock.now = T + timedelta(minutes=10)
    assert h.status(token).status_code == 200
    assert h.row().expires_at == T + timedelta(minutes=25) and h.row().last_used_at == T + timedelta(minutes=10)
    h.clock.now = T + timedelta(minutes=24)                  # 발급 기준 15분은 지났지만 마지막 사용 기준으로는 살아 있다
    assert h.status(token).status_code == 200
    assert h.row().expires_at == T + timedelta(minutes=39)


def test_writes_only_when_less_than_10_minutes_are_left(h):
    seed(h)
    token = h.issue()
    h.clock.now = T + timedelta(minutes=1)                   # 처음 쓸 때는 마지막 사용 시각을 남긴다
    assert h.status(token).status_code == 200
    first = h.row()
    assert (first.last_used_at, first.expires_at) == (T + timedelta(minutes=1), T + timedelta(minutes=16))
    for minute in (2, 3, 4, 5, 6):                            # 남은 시간 10분 이상 — 쓰지 않는다
        h.clock.now = T + timedelta(minutes=minute)
        assert h.status(token).status_code == 200
        r = h.row()
        assert (r.last_used_at, r.expires_at) == (first.last_used_at, first.expires_at), minute
    h.clock.now = T + timedelta(minutes=6, seconds=1)        # 남은 시간 9분 59초 — 늘린다
    assert h.status(token).status_code == 200
    assert h.row().expires_at == T + timedelta(minutes=21, seconds=1)


def test_eight_hours_after_issue_it_expires_even_if_used(h):
    seed(h)
    token = h.issue()
    minute = 0
    while minute < 8 * 60:                                   # 8시간 동안 9분마다 쓴다
        h.clock.now = T + timedelta(minutes=minute)
        assert h.status(token).status_code == 200, minute
        assert h.row().expires_at <= T + timedelta(hours=8)
        minute += 9
    h.clock.now = T + timedelta(hours=8)
    r = h.status(token)
    assert r.status_code == 401 and r.json()["code"] == "cli_token_expired"


# --- 폐기·재발급 -----------------------------------------------------------------

def test_revoke(h):
    seed(h)
    token = h.issue()
    assert h.status(token).status_code == 200
    h.login(OWNER)
    r = h.client.delete(f"/apps/{APP_ID}/cli-token", headers={"Origin": ORIGIN})
    assert r.status_code == 200 and r.json() == {"status": "revoked", "revoked": 1}
    assert h.client.get(f"/apps/{APP_ID}/cli-token").json()["current"] is None
    h.login(None)
    for r in (h.status(token), h.redeploy(token),
              h.client.get(f"/apps/{APP_ID}/deploy/redeploy/commits", headers=h.bearer(token))):
        assert r.status_code == 401 and r.json()["code"] == "cli_token_revoked"
    assert h.row().revoked_at == T                            # 지우지 않고 기록으로 남는다


def test_reissue_ends_the_old_token_right_away(h):
    seed(h)
    old = h.issue()
    assert h.status(old).status_code == 200
    h.clock.now = T + timedelta(minutes=1)
    new = h.issue()
    assert new != old
    r = h.status(old)
    assert r.status_code == 401 and r.json()["code"] == "cli_token_revoked"
    assert h.status(new).status_code == 200
    with h.env.Session() as s:
        live = s.query(CliToken).filter(CliToken.revoked_at.is_(None)).all()
        assert len(live) == 1 and s.query(CliToken).count() == 2


def test_one_live_token_per_app_not_per_user(h):
    seed(h)
    with h.env.Session() as s:
        s.add(App(id=OTHER_APP, owner_id=OWNER, name="other", namespace="app-99999999", pipeline="v2"))
        s.commit()
        add_server(s, "b0000001", 1, app_id=OTHER_APP, namespace="app-99999999")
    a = h.issue()
    b = h.issue(app_id=OTHER_APP)
    assert h.status(a).status_code == 200                     # 다른 앱 토큰을 발급해도 그대로
    assert h.status(b, "b0000001", app_id=OTHER_APP).status_code == 200


# --- 셸·DB 접속 티켓 (POST /apps/{id}/exec-tickets) ----------------------------------

TICKETS = f"/apps/{APP_ID}/exec-tickets"


@pytest.mark.parametrize("target,op", [("app", "shell"), ("db", "console"), ("redis", "console")])
def test_exec_ticket_with_a_cli_token(h, target, op):
    seed(h)
    token = h.issue()
    r = h.client.post(TICKETS, json={"target": target, "cols": 100, "rows": 30}, headers=h.bearer(token))
    assert r.status_code == 200, r.text                         # CLI는 Origin을 보내지 않는다
    assert r.json() == {"ticket": "TK", "url": f"{config.EXEC_PUBLIC_URL}/exec/app-11111111/v1/sessions",
                        "protocol": "kodeploy.exec.v1", "expires_in": 30}
    ns, kw = h.state.tickets[0]
    assert ns == "app-11111111" and kw["op"] == op and kw["target"] == target
    assert kw["user_id"] == str(OWNER) and kw["source"] == "cli" and (kw["cols"], kw["rows"]) == (100, 30)


def test_exec_ticket_cookie_path_is_unchanged(h):
    seed(h)
    h.login(OWNER)
    assert h.client.post(TICKETS, json={"target": "app"}).status_code == 403                    # Origin 없음
    r = h.client.post(TICKETS, json={"target": "app"}, headers={"Origin": ORIGIN})
    assert r.status_code == 200 and h.state.tickets[0][1]["source"] == "web"
    h.login(EDITOR)
    assert h.client.post(TICKETS, json={"target": "app"}, headers={"Origin": ORIGIN}).status_code == 403


def test_exec_ticket_cli_token_rules(h):
    seed(h)
    with h.env.Session() as s:
        s.add(App(id=OTHER_APP, owner_id=OWNER, name="other", namespace="app-99999999", pipeline="v2"))
        s.commit()
    token = h.issue()
    bad_target = h.client.post(TICKETS, json={"target": "shell"}, headers=h.bearer(token))
    assert bad_target.status_code == 422                                          # 웹과 같은 세 대상만
    other = h.client.post(f"/apps/{OTHER_APP}/exec-tickets", json={"target": "app"}, headers=h.bearer(token))
    assert other.status_code == 403 and other.json()["code"] == "cli_token_app_mismatch"
    foreign = h.client.post(TICKETS, json={"target": "app"}, headers={**h.bearer(token), "Origin": "https://evil.example"})
    assert foreign.status_code == 403                                             # Origin이 붙어 오면 허용 목록만
    assert h.state.tickets == []
    # 폐기한 토큰은 거절, 쿠키가 있어도 살리지 않는다
    h.login(OWNER)
    assert h.client.delete(f"/apps/{APP_ID}/cli-token", headers={"Origin": ORIGIN}).status_code == 200
    r = h.client.post(TICKETS, json={"target": "app"}, headers={**h.bearer(token), "Origin": ORIGIN})
    assert r.status_code == 401 and r.json()["code"] == "cli_token_revoked"
    assert h.state.tickets == []


def test_exec_ticket_does_not_take_a_ci_token(h):
    seed(h)
    with h.env.Session() as s:
        s.get(App, APP_ID).ci_deploy_enabled = True
        s.add(CiToken(token_hash=hashlib.sha256(b"kdci_x").hexdigest(), jti="j", app_id=APP_ID, repository="acme/api",
                      ref="refs/heads/main", sha="0" * 40, event_name="push", expires_at=T + timedelta(hours=1)))
        s.commit()
    r = h.client.post(TICKETS, json={"target": "app"}, headers={"Authorization": "Bearer kdci_x"})
    assert r.status_code == 401 and h.state.tickets == []                         # 쿠키만 보는 지금 그대로


def test_exec_ticket_by_root_with_a_cli_token_is_recorded(h):
    seed(h)
    token = h.issue(ROOT)
    assert h.client.post(TICKETS, json={"target": "db"}, headers=h.bearer(token)).status_code == 200
    assert h.state.tickets[0][1]["user_id"] == str(ROOT)
    with h.env.Session() as s:
        actions = [a.action for a in s.query(AdminAction).order_by(AdminAction.id)]
    assert actions == ["POST /apps/{app_id}/cli-token", "POST /apps/{app_id}/exec-tickets (cli)"]
