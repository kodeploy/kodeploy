"""빌드 단계용 K8s 매니페스트 (templates/build*.yaml.j2 렌더)."""

import base64

from app import config
from app.deploy.build.source import validate_branch, validate_repo_path, validate_repo_url
from app.deploy.stack.manifests._renderer import render, render_text


# 레지스트리 레이어 캐시 ref — 이미지와 같은 repo의 :buildcache 태그.
# 이미지(=...:{build_id})와 같은 repo에 두면, 방금 이미지 push가 올린 blob을 cache export가
# "이미 있네"로 스킵해 export를 싸게 만든다(존재확인은 repo 단위로 작동).
def _cache_ref(image: str) -> str:
    return image.rsplit(":", 1)[0] + ":buildcache"


# 렌더 직전 마지막 방어선 — 템플릿은 autoescape 없이 값을 YAML·셸에 그대로 넣는다.
# start_deploy가 먼저 막지만, 감지 결과(detect)·옛 빌드 행도 여기를 지나므로 한 번 더 본다.
def _check_source(repo_url: str, branch: str, *paths: tuple[str, str]) -> None:
    validate_repo_url(repo_url)
    validate_branch(branch)
    for path, label in paths:
        validate_repo_path(path, label)


# 3개 빌더가 공유하는 캐시/deadline 컨텍스트. 캐시 플래그는 BUILD_REGISTRY_CACHE_ENABLED 게이트.
def _build_ctx(image: str) -> dict:
    return {
        "registry_cache": config.BUILD_REGISTRY_CACHE_ENABLED,
        "cache_ref": _cache_ref(image),
        "active_deadline_seconds": config.BUILD_ACTIVE_DEADLINE_SECONDS,
    }


# rootless BuildKit 일회성 Job (git → GHCR push) — dockerfile 모드 전용
# dockerfile_subdir: repo root 기준 Dockerfile이 있는 디렉토리 (없으면 root). BuildKit context로 사용됨.
# dockerfile_filename: Dockerfile 파일 이름 (default "Dockerfile", "Dockerfile.multi" 등 가능).
def buildkit_job(
    build_id: str,
    user_id: str,
    image: str,
    repo_url: str,
    branch: str,
    dockerfile_subdir: str = "",
    dockerfile_filename: str = "Dockerfile",
    git_auth_secret: str = "",            # private repo: GIT_AUTH_TOKEN 담은 빌드별 Secret 이름 (없으면 public clone)
) -> dict:
    _check_source(repo_url, branch, (dockerfile_subdir, "Dockerfile 경로"), (dockerfile_filename, "Dockerfile 이름"))
    if not dockerfile_filename:
        raise ValueError("Dockerfile 이름이 비어 있습니다")
    return render(
        "buildkit_job.yaml.j2",
        build_id=build_id,
        user_id=user_id,
        image=image,
        repo_url=repo_url,
        branch=branch,
        dockerfile_subdir=dockerfile_subdir,
        dockerfile_filename=dockerfile_filename,
        buildkit_image=config.BUILDKIT_IMAGE,
        ghcr_auth_secret=config.GHCR_AUTH_SECRET_NAME,
        git_auth_secret=git_auth_secret,
        **_build_ctx(image),
    )


# 자동 빌드 모드 Job — init container(nixpacks) + main(BuildKit), emptyDir 공유.
# project_path: repo root 기준 서브디렉토리 (빈 값=root). 사용자가 명시 — 자동 탐색 없음 (Heroku/Railway 등과 동일 컨벤션).
# build_args: nixpacks가 만든 Dockerfile에 박힌 ARG들 (백엔드가 plan.json에서 파싱해 전달).
def nixpacks_buildkit_job(
    build_id: str,
    user_id: str,
    image: str,
    repo_url: str,
    branch: str,
    project_path: str = "",
    build_args: dict[str, str] | None = None,
    git_auth_secret: str = "",            # private repo: GIT_AUTH_TOKEN 담은 빌드별 Secret 이름
) -> dict:
    _check_source(repo_url, branch, (project_path, "프로젝트 경로"))
    return render(
        "nixpacks_buildkit_job.yaml.j2",
        build_id=build_id,
        user_id=user_id,
        image=image,
        repo_url=repo_url,
        branch=branch,
        project_path=project_path,
        build_args=build_args or {},
        buildkit_image=config.BUILDKIT_IMAGE,
        ghcr_auth_secret=config.GHCR_AUTH_SECRET_NAME,
        git_auth_secret=git_auth_secret,
        **_build_ctx(image),
    )


# Dockerfile ENV 값 이스케이프 — 쌍따옴표로 감싸고 \ " $ 처리.
# $는 Dockerfile 변수 치환을 막기 위해 \$로 (유저 값의 $가 빌드 변수로 오해되지 않게).
# Dockerfile 전체가 base64로 Job에 들어가므로 전송 레이어 이스케이프는 불필요 — 이게 전부다.
def _dockerfile_env_value(v: str) -> str:
    escaped = v.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
    return f'"{escaped}"'


# static 런타임용 Dockerfile 본문 렌더 (repo에 Dockerfile 없음 — 플랫폼이 생성).
# build_cmd 있으면 node 빌드 스테이지 → output_dir만 nginx로, 없으면 repo 통째 서빙.
# build_env는 빌드 스테이지 ENV로 주입 (VITE_* 등 — 번들에 박히는 공개 값. 최종
# nginx 스테이지엔 안 남음). service.py가 이 텍스트를 dockerfile_content로 보존.
def static_dockerfile(
    build_cmd: str = "",
    output_dir: str = "dist",
    build_env: dict[str, str] | None = None,
) -> str:
    return render_text(
        "static_dockerfile.j2",
        build_cmd=build_cmd,
        output_dir=output_dir or "dist",
        build_env={k: _dockerfile_env_value(v) for k, v in (build_env or {}).items()},
    )


# static 런타임 빌드 Job — init(clone + Dockerfile 작성) + main(BuildKit), emptyDir 공유.
# Dockerfile은 base64로 주입 — 유저 입력(build_cmd)이 YAML/셸 이스케이프를 깨지 못하게.
def static_buildkit_job(
    build_id: str,
    user_id: str,
    image: str,
    repo_url: str,
    branch: str,
    dockerfile_text: str,
    project_path: str = "",
    git_auth_secret: str = "",            # private repo: GIT_AUTH_TOKEN 담은 빌드별 Secret 이름
) -> dict:
    _check_source(repo_url, branch, (project_path, "정적 사이트 경로"))
    return render(
        "static_buildkit_job.yaml.j2",
        build_id=build_id,
        user_id=user_id,
        image=image,
        repo_url=repo_url,
        branch=branch,
        project_path=project_path,
        dockerfile_b64=base64.b64encode(dockerfile_text.encode("utf-8")).decode("ascii"),
        buildkit_image=config.BUILDKIT_IMAGE,
        ghcr_auth_secret=config.GHCR_AUTH_SECRET_NAME,
        git_auth_secret=git_auth_secret,
        **_build_ctx(image),
    )
