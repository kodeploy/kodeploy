"""사용자 앱 실행용 K8s 매니페스트 (templates/runtimes/{runtime}.yaml.j2 렌더)."""

from app import config
from app.deploy.stack.manifests._renderer import render, render_all
from app.deploy.runtimes import get_resources


# 런타임 → template 매핑. SELECTABLE_RUNTIMES와 sync 유지.
_RUNTIME_TEMPLATES = {
    "python": "runtimes/python.yaml.j2",
    "java": "runtimes/java.yaml.j2",
    "php": "runtimes/php.yaml.j2",
    "javascript": "runtimes/javascript.yaml.j2",
    "go": "runtimes/go.yaml.j2",
    "static": "runtimes/static.yaml.j2",
}


# 사용자 앱 Deployment (런타임별 템플릿 + RUNTIME_RESOURCES 자동 주입)
# volume_mount_path 있으면(영속저장소 "local") 런타임 템플릿이 pvc_name PVC를 그 경로에 마운트.
# 빈 값이면 마운트 블록이 렌더 안 됨 (static은 템플릿에 블록 자체가 없어 무시).
def deployment(
    runtime: str,
    app_name: str,
    tenant_id: str,
    user_id: str,
    image: str,
    port: int,
    replicas: int = 1,
    volume_mount_path: str = "",
    pvc_name: str = "",
) -> dict:
    template_name = _RUNTIME_TEMPLATES[runtime]          # 미지원 runtime → KeyError (스키마가 막아주니까)
    res = get_resources(runtime)
    return render(
        template_name,
        app_name=app_name,
        tenant_id=tenant_id,
        user_id=user_id,
        image=image,
        port=port,
        replicas=replicas,
        ghcr_auth_secret=config.GHCR_AUTH_SECRET_NAME,
        volume_mount_path=volume_mount_path,             # "" 이면 템플릿이 마운트 블록 skip
        pvc_name=pvc_name,
        **res,                                           # req_cpu/lim_cpu/req_mem/lim_mem
    )


# 앱 앞단 ClusterIP Service (런타임과 무관 — 동일 template)
def service(app_name: str, tenant_id: str, user_id: str, port: int) -> dict:
    return render(
        "service.yaml.j2",
        app_name=app_name,
        tenant_id=tenant_id,
        user_id=user_id,
        port=port,
    )


# 앱 라우팅 (HTTPS + HTTP→HTTPS redirect).
# hostnames는 호출자가 슬롯 규칙(service._route_hostnames)으로 계산해 주입 —
# 리소스 이름(app_name)과 hostname이 더 이상 1:1이 아님 (정적 슬롯이 {app}을 가져갈 수 있음).
# origin_verify_secret 있으면 https route에 X-Origin-Verify 헤더매칭 주입 (origin-lock Layer B′).
def httproute(
    app_name: str, tenant_id: str, user_id: str, port: int, hostnames: list[str],
) -> list[dict]:
    return render_all(
        "httproute.yaml.j2",
        app_name=app_name,
        tenant_id=tenant_id,
        user_id=user_id,
        port=port,
        hostnames=hostnames,
        origin_verify_secret=config.ORIGIN_VERIFY_SECRET,
    )


# Calico namespace-scoped NP (per-tenant).
# ingress: 같은 ns + Envoy Gateway 허용
# egress: 같은 ns 허용 (나머지는 GlobalNetworkPolicy가 처리)
def networkpolicy(tenant_id: str) -> dict:
    return render(
        "networkpolicy.yaml.j2",
        tenant_id=tenant_id,
    )
