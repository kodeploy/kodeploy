"""매니페스트 렌더 불변식 고정 — Job 이름 규칙·토큰 주입 경계·생성 Dockerfile.

템플릿은 grep으로 안 잡히는 변경 표면이라, 렌더 결과(dict)의 핵심 불변식을 박는다:
- Job 이름이 service._build_job_name과 일치 (로그 조회·취소가 이 이름으로 Job을 찾음)
- GIT_AUTH_TOKEN은 init 컨테이너에만, git_auth_secret 없으면 어디에도 없음 (토큰 비노출 경계)
- static Dockerfile의 base64 왕복과 ENV 이스케이프
"""

import base64
import uuid

from app.deploy.stack import manifests
from app.deploy.build import pipeline
from app.deploy.stack.manifests.build import _dockerfile_env_value

UID = uuid.UUID("deadbeef-0000-0000-0000-000000000000").hex
IMAGE = "ghcr.io/op/deadbeef/foo:ab12cd34"
REPO = "https://github.com/u/r.git"


def _init_env_names(job, idx=0):
    return [e["name"] for e in job["spec"]["template"]["spec"]["initContainers"][idx]["env"]]


def _main(job):
    return job["spec"]["template"]["spec"]["containers"][0]


# --- buildkit_job (유저 Dockerfile 모드) ---

def test_buildkit_job_name_matches_service_convention():
    job = manifests.buildkit_job(
        build_id="ab12cd34", user_id=UID, image=IMAGE, repo_url=REPO, branch="main",
    )
    assert job["metadata"]["name"] == pipeline._build_job_name("ab12cd34", UID)
    assert job["metadata"]["labels"]["build-id"] == "ab12cd34"
    assert job["metadata"]["labels"]["build-mode"] == "dockerfile"


def test_buildkit_job_spec_invariants():
    job = manifests.buildkit_job(
        build_id="ab12cd34", user_id=UID, image=IMAGE, repo_url=REPO, branch="main",
    )
    assert job["spec"]["ttlSecondsAfterFinished"] == 1200
    assert job["spec"]["backoffLimit"] == 0
    assert job["spec"]["template"]["spec"]["restartPolicy"] == "Never"
    assert job["spec"]["template"]["spec"]["initContainers"][0]["name"] == "clone"
    assert _main(job)["name"] == "buildkit"
    assert f"--output=type=image,name={IMAGE},push=true" in _main(job)["args"]


def test_buildkit_job_subdir_and_filename():
    job = manifests.buildkit_job(
        build_id="ab12cd34", user_id=UID, image=IMAGE, repo_url=REPO, branch="main",
        dockerfile_subdir="backend", dockerfile_filename="Dockerfile.multi",
    )
    args = _main(job)["args"]
    assert "--local=context=/workspace/src/backend" in args
    assert "--opt=filename=Dockerfile.multi" in args


def test_git_auth_token_absent_for_public_clone():
    job = manifests.buildkit_job(
        build_id="ab12cd34", user_id=UID, image=IMAGE, repo_url=REPO, branch="main",
        git_auth_secret="",
    )
    assert "GIT_AUTH_TOKEN" not in _init_env_names(job)
    # main(유저 RUN 실행 환경)엔 env 키 자체가 없거나 토큰이 없어야 함
    main_env = [e["name"] for e in _main(job).get("env", [])]
    assert "GIT_AUTH_TOKEN" not in main_env


def test_git_auth_token_only_in_init_container():
    job = manifests.buildkit_job(
        build_id="ab12cd34", user_id=UID, image=IMAGE, repo_url=REPO, branch="main",
        git_auth_secret="git-auth-ab12cd34",
    )
    init_env = job["spec"]["template"]["spec"]["initContainers"][0]["env"]
    token = next(e for e in init_env if e["name"] == "GIT_AUTH_TOKEN")
    assert token["valueFrom"]["secretKeyRef"]["name"] == "git-auth-ab12cd34"
    main_env = [e["name"] for e in _main(job).get("env", [])]
    assert "GIT_AUTH_TOKEN" not in main_env


# --- nixpacks_buildkit_job (auto 모드) ---

def test_nixpacks_job_init_and_project_path():
    job = manifests.nixpacks_buildkit_job(
        build_id="ab12cd34", user_id=UID, image=IMAGE, repo_url=REPO, branch="main",
        project_path="backend",
    )
    assert job["metadata"]["labels"]["build-mode"] == "auto"
    init = job["spec"]["template"]["spec"]["initContainers"][0]
    assert init["name"] == "nixpacks"
    env = {e["name"]: e.get("value") for e in init["env"]}
    assert env["PROJECT_PATH"] == "backend"
    assert env["NIXPACKS_VERSION"].startswith("v")


# --- static_buildkit_job + static_dockerfile (정적 모드) ---

def test_static_job_dockerfile_b64_roundtrip():
    text = manifests.static_dockerfile("npm ci && npm run build", "dist")
    job = manifests.static_buildkit_job(
        build_id="ab12cd34", user_id=UID, image=IMAGE, repo_url=REPO, branch="main",
        dockerfile_text=text, project_path="web",
    )
    assert job["metadata"]["labels"]["build-mode"] == "static"
    env = {e["name"]: e.get("value") for e in job["spec"]["template"]["spec"]["initContainers"][0]["env"]}
    assert base64.b64decode(env["DOCKERFILE_B64"]).decode("utf-8") == text
    assert "--local=context=/workspace/src/web" in _main(job)["args"]


def test_static_dockerfile_with_build_cmd():
    text = manifests.static_dockerfile("npm run build", "build")
    assert "FROM node:22-alpine AS build" in text
    assert "RUN npm run build" in text
    assert "COPY --from=build /app/build/ /usr/share/nginx/html/" in text


def test_static_dockerfile_without_build_cmd_serves_repo_as_is():
    # 현행 동작 고정: 빌드 없으면 repo 통째 서빙 (.git 포함 — 03 문서 부록 B N1).
    # N1 수정(.git 제거/deny) 시 이 단언을 의도 변경과 함께 갱신할 것.
    text = manifests.static_dockerfile("", "dist")
    assert "COPY . /usr/share/nginx/html/" in text
    assert "node:22-alpine" not in text


def test_static_dockerfile_env_injected_and_escaped():
    text = manifests.static_dockerfile(
        "npm run build", "dist", build_env={"VITE_A": "a$b"},
    )
    assert 'ENV VITE_A="a\\$b"' in text  # $ 이스케이프 — 빌드 변수 치환 오해 방지


# --- _dockerfile_env_value — ENV 값 이스케이프 단위 ---

def test_dockerfile_env_value_escaping():
    assert _dockerfile_env_value("plain") == '"plain"'
    assert _dockerfile_env_value('q"x') == '"q\\"x"'
    assert _dockerfile_env_value("a$b") == '"a\\$b"'
    assert _dockerfile_env_value("b\\s") == '"b\\\\s"'


def test_go_runtime_deployment_binds_port_env():
    from app.deploy.stack import manifests

    dep = manifests.deployment(
        runtime="go", app_name="demo", tenant_id="tenant-d6d8b759", user_id="d6d8b759" * 4,
        image="ghcr.io/x/y/demo:1", port=8080,
    )
    c = dep["spec"]["template"]["spec"]["containers"][0]
    assert dep["metadata"]["labels"]["runtime"] == "go"
    assert {"name": "PORT", "value": "8080"} in c["env"]
    assert c["resources"]["limits"]["memory"] == "256Mi"
