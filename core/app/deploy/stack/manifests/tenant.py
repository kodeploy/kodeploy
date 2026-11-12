"""테넌트 ns 프로비저닝 매니페스트 (Namespace + core RoleBinding + ghcr-auth Secret + kodeploy-exec HMAC Secret)."""

import base64
import secrets

from app.deploy.stack.manifests._renderer import render, render_all

# kodeploy-exec 내부 문 서명 키. 차트의 exec Deployment가 이 Secret을 /etc/kodeploy-exec/hmac로 마운트한다.
EXEC_HMAC_SECRET = "kodeploy-exec-hmac"
EXEC_HMAC_KEY = "hmac"


# 테넌트 ns 한 묶음 — Namespace, RoleBinding(core 권한), Secret(ghcr-auth) 3개 dict 반환 (이 순서로 만든다).
# 호출 측에서 list 순회하며 K8s API로 apply.
# dockerconfigjson_b64는 호출자가 운영 ghcr-auth Secret을 K8s API로 read해서 전달.
# (ResourceQuota는 제거됨 — Pod limit은 런타임 템플릿이 직접 박고, ns 상한은
#  컴포넌트 구성이 고정이라 구조적으로 결정됨. runtimes.py 주석 참고.)
def tenant(
    tenant_id: str,
    user_id: str,
    dockerconfigjson_b64: str,
) -> list[dict]:
    return render_all(
        "tenant.yaml.j2",
        tenant_id=tenant_id,
        user_id=user_id,
        dockerconfigjson_b64=dockerconfigjson_b64,
    )


# core RoleBinding만 — RoleBinding이 빠진 ns를 지우기 전에 다시 건다 (resources.rebind_core).
# 같은 템플릿에서 꺼내서 모양이 ns 프로비저닝 때와 어긋나지 않는다 (admission 정책이 이 모양 하나만 허용).
def core_binding(tenant_id: str) -> dict:
    docs = tenant(tenant_id=tenant_id, user_id="", dockerconfigjson_b64="")
    return next(d for d in docs if d["kind"] == "RoleBinding")


# kodeploy-exec HMAC Secret — 테넌트마다 무작위 32바이트. ns 프로비저닝 때 같이 만든다 (이미 있으면 409로 둔다).
def exec_hmac(tenant_id: str) -> dict:
    return render(
        "exec_hmac.yaml.j2",
        tenant_id=tenant_id,
        secret_name=EXEC_HMAC_SECRET,
        key_name=EXEC_HMAC_KEY,
        key_b64=base64.b64encode(secrets.token_bytes(32)).decode(),
    )
