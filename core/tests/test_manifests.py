"""생성 Dockerfile 고정 — 정적 사이트 Dockerfile 본문과 ENV 이스케이프.

빌드 Job 매니페스트는 빌더가 만든다 (builder/internal/job, 골든 파일로 고정).
"""

from app.deploy.stack import manifests
from app.deploy.stack.manifests.build import _dockerfile_env_value


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
