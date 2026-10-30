"""정적 사이트 Dockerfile 본문 (templates/static_dockerfile.j2 렌더). 빌드 Job은 빌더가 만든다."""

from app.deploy.stack.manifests._renderer import render_text


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
