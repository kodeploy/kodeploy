"""유저 의존성(mysql/postgres/redis) 매니페스트 빌더."""

import secrets

from app.deploy.stack.manifests._renderer import render_all
from app.deploy.runtimes import get_resources


def mysql(
    tenant_id: str,
    user_id: str,
    database: str = "app",
    username: str = "app",
) -> list[dict]:
    res = get_resources("mysql")
    return render_all(
        "dependencies/mysql.yaml.j2",
        tenant_id=tenant_id,
        user_id=user_id,
        database=database,
        username=username,
        root_password=secrets.token_urlsafe(24),
        password=secrets.token_urlsafe(24),
        **res,
    )


def postgres(
    tenant_id: str,
    user_id: str,
    database: str = "app",
    username: str = "app",
) -> list[dict]:
    res = get_resources("postgres")
    return render_all(
        "dependencies/postgres.yaml.j2",
        tenant_id=tenant_id,
        user_id=user_id,
        database=database,
        username=username,
        password=secrets.token_urlsafe(24),
        **res,
    )


def redis(tenant_id: str, user_id: str) -> list[dict]:
    res = get_resources("redis")
    return render_all(
        "dependencies/redis.yaml.j2",
        tenant_id=tenant_id,
        user_id=user_id,
        password=secrets.token_urlsafe(24),
        **res,
    )


# R2 스토리지 Secret — mysql/redis와 달리 K8s 워크로드가 아니라 Secret 1개뿐.
# 자격증명(env)과 token_id는 호출 측이 r2.provision()으로 받아 넘긴다 (CF API 산출물).
def storage(
    tenant_id: str, user_id: str, env: dict[str, str], token_id: str
) -> list[dict]:
    return render_all(
        "dependencies/r2-secret.yaml.j2",
        tenant_id=tenant_id,
        user_id=user_id,
        env=env,
        token_id=token_id,
    )


# 로컬 영속 볼륨 PVC — 영속저장소 "local" 모드. mysql처럼 워크로드가 아니라 PVC 1개뿐.
# Deployment(python/java/php/javascript/go 템플릿)가 volume_mount_path에 마운트한다. storage_class/size는
# 런타임 무관 — 호출 측(service)이 Build의 volume_* 필드로 넘긴다 (mysql res 주입과 같은 위치).
def volume(
    tenant_id: str,
    user_id: str,
    app_name: str,
    storage_class: str = "local-path",
    size: str = "5Gi",
) -> list[dict]:
    return render_all(
        "dependencies/pvc.yaml.j2",
        tenant_id=tenant_id,
        user_id=user_id,
        app_name=app_name,
        storage_class=storage_class,
        size=size,
    )
