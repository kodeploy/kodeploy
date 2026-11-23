"""CI 토큰 교환 검사 10(워크플로 파일)과 11(저장소 ID).

test_ci_exchange의 env(SQLite 파일 DB + 진짜 교환 라우터)를 그대로 쓴다.
"""

import pytest

from app.apps.model import App
from tests import ci_helpers as ci
from tests.test_ci_exchange import assert_rejected, env, post, seed, tokens  # noqa: F401 — env는 fixture
from tests.test_redeploy import APP_ID, WEB_REPO

WF = ".github/workflows/deploy.yml"
JWR = f"acme/api/{WF}@refs/heads/main"


def set_app(env, **fields):
    with env.Session() as s:
        app = s.get(App, APP_ID)
        for k, v in fields.items():
            setattr(app, k, v)
        s.commit()


def repo_ids(env):
    with env.Session() as s:
        return s.get(App, APP_ID).ci_repo_ids


# --- 10 워크플로 파일 ------------------------------------------------------------

def test_without_a_workflow_any_job_workflow_ref_passes(env):
    seed(env)
    assert post(env, ci.id_token(job_workflow_ref="acme/api/.github/workflows/other.yml@refs/heads/main")).status_code == 201
    assert post(env, ci.id_token()).status_code == 201          # 클레임이 없어도 (지금까지와 같다)


def test_the_pinned_workflow_passes(env):
    seed(env)
    set_app(env, ci_workflow=WF)
    r = post(env, ci.id_token(job_workflow_ref=JWR))
    assert r.status_code == 201, r.text


@pytest.mark.parametrize("jwr", [
    "acme/api/.github/workflows/other.yml@refs/heads/main",      # 같은 저장소의 다른 워크플로
    "acme/api/.github/workflows/Deploy.yml@refs/heads/main",     # 파일 경로는 대소문자를 가린다
    f"evil/actions/{WF}@refs/heads/main",                        # 다른 저장소의 같은 경로 (재사용 워크플로)
    f"acme/api/{WF}",                                            # @ref가 없는 값
    "acme@refs/heads/main",
    "",
])
def test_another_workflow_is_403_and_spends_nothing(env, jwr):
    seed(env)
    set_app(env, ci_workflow=WF)
    assert_rejected(post(env, ci.id_token(job_workflow_ref=jwr)), 403, "workflow_mismatch")
    assert tokens(env) == []


def test_a_missing_job_workflow_ref_is_403_when_pinned(env):
    seed(env)
    set_app(env, ci_workflow=WF)
    assert_rejected(post(env, ci.id_token()), 403, "workflow_mismatch")


def test_the_repository_part_ignores_case(env):
    seed(env)
    set_app(env, ci_workflow=WF)
    r = post(env, ci.id_token(repository="Acme/API", job_workflow_ref=f"ACME/api/{WF}@refs/heads/main"))
    assert r.status_code == 201, r.text


def test_the_workflow_must_be_in_the_token_repository_for_a_static_slot_repo(env):
    # 정적 슬롯이 다른 저장소(acme/web)여도 그 저장소의 같은 경로여야 한다
    seed(env, static_branch="main", static_repo=WEB_REPO)
    set_app(env, ci_workflow=WF)
    ok = post(env, ci.id_token(repository="acme/web", job_workflow_ref=f"acme/web/{WF}@refs/heads/main"))
    assert ok.status_code == 201, ok.text
    bad = post(env, ci.id_token(repository="acme/web", job_workflow_ref=JWR))
    assert_rejected(bad, 403, "workflow_mismatch")


# --- 11 저장소 ID --------------------------------------------------------------

def test_a_known_repository_id_must_match(env):
    seed(env)
    set_app(env, ci_repo_ids={"acme/api": "101"})
    assert post(env, ci.id_token(repository_id="101")).status_code == 201
    assert_rejected(post(env, ci.id_token(repository_id="202")), 403, "repo_id_mismatch")
    assert_rejected(post(env, ci.id_token()), 403, "repo_id_mismatch")          # 클레임이 없으면 다르다고 본다
    assert len(tokens(env)) == 1


def test_the_repository_name_is_compared_case_insensitively(env):
    seed(env)
    set_app(env, ci_repo_ids={"acme/api": "101"})
    assert post(env, ci.id_token(repository="ACME/Api", repository_id="101")).status_code == 201


def test_an_unknown_repository_id_is_recorded_on_the_first_exchange(env):
    seed(env)
    assert post(env, ci.id_token(repository_id="101")).status_code == 201
    assert repo_ids(env) == {"acme/api": "101"}
    # 같은 이름으로 다시 만든 저장소(다른 ID)는 그 뒤로 거절
    assert_rejected(post(env, ci.id_token(repository_id="303")), 403, "repo_id_mismatch")
    assert repo_ids(env) == {"acme/api": "101"}


def test_recording_adds_to_ids_already_known_for_other_repositories(env):
    seed(env, static_branch="main", static_repo=WEB_REPO)
    set_app(env, ci_repo_ids={"acme/api": "101"})
    assert post(env, ci.id_token(repository="acme/web", repository_id="202")).status_code == 201
    assert repo_ids(env) == {"acme/api": "101", "acme/web": "202"}


def test_a_rejected_exchange_records_nothing(env):
    seed(env)
    set_app(env, ci_workflow=WF)
    assert_rejected(post(env, ci.id_token(repository_id="101")), 403, "workflow_mismatch")
    assert repo_ids(env) is None


@pytest.mark.parametrize("bad", ["abc", "-1", "1" * 21, True])
def test_a_malformed_repository_id_claim_is_not_recorded(env, bad):
    seed(env)
    assert post(env, ci.id_token(repository_id=bad)).status_code == 201
    assert repo_ids(env) is None


def test_a_numeric_repository_id_claim_is_read_as_a_string(env):
    seed(env)
    set_app(env, ci_repo_ids={"acme/api": "101"})
    assert post(env, ci.id_token(repository_id=101)).status_code == 201
