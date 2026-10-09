"""사용자 환경변수 — {app_name}-env Secret을 진실원으로.

K8s Secret이 envFrom으로 Pod에 주입됨. Secret만 수정하면 이미 떠 있는 Pod은
시작 시 읽은 옛 값을 그대로 들고 있으므로, replace 후 Deployment template의
annotations에 timestamp를 박아 rolling update를 트리거한다 (kubectl rollout
restart가 내부적으로 하는 patch와 동일 원리).
"""

import base64
import re
from datetime import datetime, timezone

from kubernetes.client.exceptions import ApiException

from app.shared import k8s

# envvar key 표준 형식 — 셸 호환을 위해 대문자/숫자/_만, 첫 글자는 영문 대문자 또는 _.
_KEY_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")

# 플랫폼이 Deployment env: 섹션에서 박는 키. envFrom보다 env가 우선이라 사용자가
# 같은 이름을 넣어도 어차피 무시되지만 "왜 안 먹지?" 혼란을 피하려고 명시 차단.
_RESERVED_KEYS = frozenset({
    "PYTHONUNBUFFERED",
    "JAVA_TOOL_OPTIONS",
})

MAX_KEYS = 50
MAX_VALUE_LENGTH = 4096


def _secret_name(app_name: str) -> str:
    return f"{app_name}-env"


# Secret replace 전 검증 — key 형식 / 개수 cap / value 길이.
def validate_env(env: dict[str, str]) -> None:
    if len(env) > MAX_KEYS:
        raise ValueError(f"환경변수는 최대 {MAX_KEYS}개")
    for key, value in env.items():
        if not _KEY_PATTERN.match(key):
            raise ValueError(f"잘못된 key 형식: {key} (대문자 시작, 영문/숫자/_)")
        if key in _RESERVED_KEYS:
            raise ValueError(f"예약된 key: {key}")
        if not isinstance(value, str):
            raise ValueError(f"value는 문자열이어야 함: {key}")
        if len(value) > MAX_VALUE_LENGTH:
            raise ValueError(f"value 너무 김 ({MAX_VALUE_LENGTH}자 초과): {key}")


# Secret 조회. 첫 배포 전이거나 한 번도 설정 안 했으면 빈 dict (404 정상).
# K8s가 data를 base64로 인코딩해 반환하므로 직접 디코딩.
def get_env(tenant_id: str, app_name: str) -> dict[str, str]:
    core = k8s.core_v1()
    try:
        secret = core.read_namespaced_secret(
            name=_secret_name(app_name), namespace=tenant_id
        )
    except ApiException as e:
        if e.status == 404:
            return {}
        raise

    if not secret.data:
        return {}
    return {k: base64.b64decode(v).decode("utf-8") for k, v in secret.data.items()}


# Secret 통째 replace + Pod 재생성 트리거.
# 부분 patch 안 함 — 클라이언트가 보낸 dict가 새 전체 상태.
# Deployment 없으면 (첫 배포 전) Secret만 만들고 끝 — 다음 첫 배포 때 자연 적용.
# restart=False면 Secret만 바꾼다 — v2 앱은 Pod 재시작을 Argo가 하므로(envRevision 커밋) core가 Deployment를 건드리지 않는다.
def set_env(tenant_id: str, app_name: str, env: dict[str, str], restart: bool = True) -> None:
    validate_env(env)

    name = _secret_name(app_name)
    body = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": name,
            "namespace": tenant_id,
            "labels": {
                "app": app_name,
                "tenant": tenant_id,
                "managed-by": "kodeploy",
            },
        },
        "type": "Opaque",
        "stringData": env,  # K8s가 자동 base64 인코딩
    }

    core = k8s.core_v1()
    try:
        core.read_namespaced_secret(name=name, namespace=tenant_id)
        core.replace_namespaced_secret(name=name, namespace=tenant_id, body=body)
    except ApiException as e:
        if e.status != 404:
            raise
        core.create_namespaced_secret(namespace=tenant_id, body=body)

    if not restart:
        return

    # rolling update 트리거 — template.annotations에 매번 다른 timestamp.
    # kubectl rollout restart가 내부적으로 동일 patch를 호출함.
    patch = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "kodeploy.com/env-updated-at": datetime.now(
                            timezone.utc
                        ).isoformat()
                    }
                }
            }
        }
    }
    try:
        k8s.apps_v1().patch_namespaced_deployment(
            name=app_name, namespace=tenant_id, body=patch
        )
    except ApiException as e:
        if e.status != 404:  # 첫 배포 전이면 Deployment 없음 — 정상
            raise
