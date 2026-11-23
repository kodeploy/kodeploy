"""PUT /apps/{id}/ci-deploy — 켤 때 저장소 ID를 받아 두는지, 허용할 워크플로 파일을 저장하는지.

test_ci_bearer의 h(진짜 apps·ci·deploy 라우터)를 그대로 쓴다. GitHub 조회는 conftest가 막아 두고 여기서 바꿔 끼운다.
"""

import pytest

from app.apps import router as apps_router
from app.apps.model import App
from app.ci import service as ci_service
from tests import ci_helpers as ci
from tests.test_ci_bearer import h, seed  # noqa: F401 — h는 fixture
from tests.test_redeploy import API_REPO, APP_ID, WEB_REPO, env  # noqa: F401 — env는 fixture

WF = ".github/workflows/deploy.yml"


def put(h, body):
    h.as_owner()
    r = h.client.put(f"/apps/{APP_ID}/ci-deploy", json=body)
    h.state.cookie_user = None
    return r


def app_row(h):
    with h.env.Session() as s:
        return s.get(App, APP_ID)


@pytest.fixture
def lookups(monkeypatch):
    calls = []
    ids = {"acme/api": "101", "acme/web": "202"}

    def fake(name, token=None):
        calls.append((name, token))
        return ids.get(name)

    monkeypatch.setattr(ci_service, "github_repo_id", fake)
    return calls


# --- 저장소 ID -----------------------------------------------------------------

def test_turning_it_on_stores_the_ids_of_the_live_repositories(h, lookups):
    seed(h, enabled=False, static=(WEB_REPO, "main"))
    r = put(h, {"enabled": True})
    assert r.status_code == 200, r.text
    assert app_row(h).ci_repo_ids == {"acme/api": "101", "acme/web": "202"}
    assert sorted(n for n, _ in lookups) == ["acme/api", "acme/web"]


def test_the_same_repository_is_looked_up_once(h, lookups):
    seed(h, enabled=False, static=(API_REPO, "gh-pages"))
    put(h, {"enabled": True})
    assert lookups == [("acme/api", None)]
    assert app_row(h).ci_repo_ids == {"acme/api": "101"}


def test_staying_on_does_not_look_up_again(h, lookups):
    seed(h, enabled=True)
    put(h, {"enabled": True})
    assert lookups == []
    assert app_row(h).ci_repo_ids is None


def test_a_failed_lookup_leaves_the_id_for_the_first_exchange(h, monkeypatch):
    seed(h, enabled=False)
    monkeypatch.setattr(ci_service, "github_repo_id", lambda name, token=None: None)
    put(h, {"enabled": True})
    assert app_row(h).ci_repo_ids is None
    assert h.client.post(f"/apps/{APP_ID}/ci/token", json={"id_token": ci.id_token(repository_id="555")}).status_code == 201
    assert app_row(h).ci_repo_ids == {"acme/api": "555"}


def test_turning_it_back_on_takes_fresh_ids(h, lookups, monkeypatch):
    seed(h, enabled=False)
    put(h, {"enabled": True})
    put(h, {"enabled": False})
    monkeypatch.setattr(ci_service, "github_repo_id", lambda name, token=None: "909")
    put(h, {"enabled": True})
    assert app_row(h).ci_repo_ids == {"acme/api": "909"}


def test_the_installation_token_is_tried_once_when_the_anonymous_lookup_fails(h, monkeypatch):
    seed(h, enabled=False, static=(WEB_REPO, "main"))
    calls = []

    def fake(name, token=None):
        calls.append((name, token))
        return "303" if token else None

    monkeypatch.setattr(ci_service, "github_repo_id", fake)
    got = []
    monkeypatch.setattr(ci_service.github_app, "get_clone_token", lambda inst: got.append(inst) or "ghs_test")
    put(h, {"enabled": True})
    assert len(got) == 1
    assert app_row(h).ci_repo_ids == {"acme/api": "303", "acme/web": "303"}


# --- 워크플로 파일 --------------------------------------------------------------

def test_the_workflow_is_saved_and_returned(h):
    seed(h, enabled=True)
    r = put(h, {"enabled": True, "workflow": " " + WF + " "})
    assert r.status_code == 200, r.text
    assert r.json() == {"ci_deploy_enabled": True, "ci_workflow": WF}
    assert app_row(h).ci_workflow == WF
    assert apps_router._out(app_row(h), "owner", None).ci_workflow == WF    # 앱 응답(AppOut)에도 실린다


def test_leaving_the_workflow_out_keeps_it(h):
    seed(h, enabled=True)
    put(h, {"enabled": True, "workflow": WF})
    put(h, {"enabled": False})
    put(h, {"enabled": True})
    assert app_row(h).ci_workflow == WF


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_null_or_empty_clears_it(h, empty):
    seed(h, enabled=True)
    put(h, {"enabled": True, "workflow": WF})
    r = put(h, {"enabled": True, "workflow": empty})
    assert r.status_code == 200 and r.json()["ci_workflow"] is None
    assert app_row(h).ci_workflow is None


@pytest.mark.parametrize("bad", [
    "deploy.yml",
    ".github/workflows/deploy.txt",
    ".github/workflows/sub/deploy.yml",          # GitHub는 하위 폴더를 읽지 않는다
    ".github/workflows/../deploy.yml",
    "/.github/workflows/deploy.yml",
    ".github/workflows/de ploy.yml",
    ".github/workflows/" + "a" * 250 + ".yml",
])
def test_a_bad_workflow_path_is_422_and_changes_nothing(h, bad):
    seed(h, enabled=True)
    put(h, {"enabled": True, "workflow": WF})
    assert put(h, {"enabled": True, "workflow": bad}).status_code == 422
    assert app_row(h).ci_workflow == WF


def test_yaml_extension_is_accepted(h):
    seed(h, enabled=True)
    assert put(h, {"enabled": True, "workflow": ".github/workflows/ship-it.yaml"}).status_code == 200


def test_the_pinned_workflow_applies_to_the_next_exchange(h):
    seed(h, enabled=True)
    put(h, {"enabled": True, "workflow": WF})
    url = f"/apps/{APP_ID}/ci/token"
    bad = h.client.post(url, json={"id_token": ci.id_token(job_workflow_ref="acme/api/.github/workflows/x.yml@refs/heads/main")})
    assert bad.status_code == 403 and bad.json()["code"] == "workflow_mismatch"
    ok = h.client.post(url, json={"id_token": ci.id_token(job_workflow_ref=f"acme/api/{WF}@refs/heads/main")})
    assert ok.status_code == 201, ok.text
