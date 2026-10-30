"""유저 의존성 Secret(mysql/postgres/redis 자격증명, R2) + 로컬 볼륨 PVC. 워크로드는 차트가 그린다."""

import secrets

from app.deploy.stack.manifests._renderer import render_all


def mysql(
    tenant_id: str,
    user_id: str,
    database: str = "app",
    username: str = "app",
) -> list[dict]:
    return render_all(
        "dependencies/mysql.yaml.j2",
        tenant_id=tenant_id,
        user_id=user_id,
        database=database,
        username=username,
        root_password=secrets.token_urlsafe(24),
        password=secrets.token_urlsafe(24),
    )


def postgres(
    tenant_id: str,
    user_id: str,
    database: str = "app",
    username: str = "app",
) -> list[dict]:
    return render_all(
        "dependencies/postgres.yaml.j2",
        tenant_id=tenant_id,
        user_id=user_id,
        database=database,
        username=username,
        password=secrets.token_urlsafe(24),
    )


def redis(tenant_id: str, user_id: str) -> list[dict]:
    return render_all(
        "dependencies/redis.yaml.j2",
        tenant_id=tenant_id,
        user_id=user_id,
        password=secrets.token_urlsafe(24),
    )


# R2 스토리지 Secret.
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


# 로컬 영속 볼륨 PVC — 영속저장소 "local" 모드. 차트의 Deployment가 volume.mountPath에 마운트한다.
# storage_class/size는 호출 측이 Build의 volume_* 필드로 넘긴다.
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
