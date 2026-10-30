"""K8s API 클라이언트 lazy-loader.

import 시점에 K8s 설정을 읽지 않고, 첫 호출에서 한 번만 로드한다.
- 클러스터 안에서 돌면 ServiceAccount 토큰 기반(in-cluster) 설정,
- 로컬 개발 등 클러스터 밖이면 ~/.kube/config로 fallback.
"""

from kubernetes import client, config

_loaded = False


# K8s 설정 lazy 로드 (in-cluster 우선, 실패 시 ~/.kube/config fallback)
def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    _loaded = True


# 빌드 Job 조회용 (실패 진단 — Job은 빌더가 만든다)
def batch_v1() -> client.BatchV1Api:
    _ensure_loaded()
    return client.BatchV1Api()


# ns·Secret·PVC·Pod 로그/exec·노드 조회용
def core_v1() -> client.CoreV1Api:
    _ensure_loaded()
    return client.CoreV1Api()


# 테넌트 ns의 core RoleBinding 생성용
def rbac_v1() -> client.RbacAuthorizationV1Api:
    _ensure_loaded()
    return client.RbacAuthorizationV1Api()


# 방금 만든 RoleBinding이 적용됐는지 확인용 (SelfSubjectAccessReview)
def authorization_v1() -> client.AuthorizationV1Api:
    _ensure_loaded()
    return client.AuthorizationV1Api()
