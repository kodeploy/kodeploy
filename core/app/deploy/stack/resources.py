"""테넌트 K8s 리소스 apply/teardown — ns · DB · Redis · R2 · PVC · Deployment."""

import base64
import time

from kubernetes.client import V1ServicePort
from kubernetes.client.exceptions import ApiException

from app import config
from app.auth.model import User
from app.deploy.stack import manifests, r2
from app.deploy.model import Build
from app.shared import k8s

# 테넌트 ns 프로비저닝 (Namespace + ghcr-auth Secret). idempotent create(409 skip).
# ResourceQuota는 제거됨 — API-mediated 구조에선 컴포넌트·limit이 고정이라 ns 상한이
# 구조적으로 결정되고, 슬롯별 배포에서 quota 덮어쓰기 사고만 만들었음 (runtimes.py 주석).
def _ensure_tenant_ns(build: Build) -> None:
    if build.user_id is None:
        return

    core = k8s.core_v1()
    ns = build.tenant_id

    # 직전 delete_app으로 ns가 terminating 중일 수 있음 — 사라질 때까지 잠시 대기.
    # 60초 안에 안 끝나면 그대로 진행해서 create_namespace의 에러로 표면화.
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            existing = core.read_namespace(name=ns)
        except ApiException as e:
            if e.status == 404:
                break  # 사라짐 — 새로 만들면 됨
            raise
        if existing.status.phase != "Terminating":
            break  # Active — 재사용
        time.sleep(2)

    secret = core.read_namespaced_secret(
        name=config.GHCR_AUTH_SECRET_NAME,
        namespace=config.BUILD_NAMESPACE,
    )
    dockerconfigjson_b64 = secret.data[".dockerconfigjson"]

    docs = manifests.tenant(
        tenant_id=ns,
        user_id=build.user_id_str,
        dockerconfigjson_b64=dockerconfigjson_b64,
    )

    for doc in docs:
        kind = doc["kind"]
        try:
            if kind == "Namespace":
                core.create_namespace(body=doc)
            elif kind == "Secret":
                core.create_namespaced_secret(namespace=ns, body=doc)
        except ApiException as e:
            if e.status != 409:
                raise

    # 옛 ResourceQuota 잔재 정리 — 라이브 테넌트에 남은 quota가 admission을 계속 물면
    # 정체불명 배포 실패가 되므로, 배포마다 멱등 delete (테넌트별 자가 정리).
    try:
        core.delete_namespaced_resource_quota(name=ns, namespace=ns)
    except ApiException as e:
        if e.status != 404:
            raise


# 선택된 db (mysql / postgres) 같은 ns에 프로비저닝. 409(이미 존재)는 skip.
# 재배포 시 다시 호출돼도 안전 — 첫 호출의 비번 + PVC가 영구 유지되고,
# 옛 PVC가 남아있으면 새 StatefulSet이 자동 재바인딩해서 데이터 자연 복원.
def _ensure_one_db(build: Build, db_type: str) -> None:
    if db_type not in ("mysql", "postgres"):
        return
    apps = k8s.apps_v1()
    core = k8s.core_v1()
    ns = build.tenant_id

    docs = (
        manifests.mysql(tenant_id=ns, user_id=build.user_id_str)
        if db_type == "mysql"
        else manifests.postgres(tenant_id=ns, user_id=build.user_id_str)
    )
    for doc in docs:
        kind = doc["kind"]
        try:
            if kind == "Secret":
                core.create_namespaced_secret(namespace=ns, body=doc)
            elif kind == "Service":
                core.create_namespaced_service(namespace=ns, body=doc)
            elif kind == "StatefulSet":
                apps.create_namespaced_stateful_set(namespace=ns, body=doc)
        except ApiException as e:
            if e.status != 409:
                raise


# 특정 db의 StatefulSet + Service만 삭제. PVC/Secret은 의도적으로 보존.
# 다시 그 db로 토글하면 새 StatefulSet이 옛 PVC + 옛 Secret에 자동 바인딩 → 복원.
# ns 기반 — 서버 슬롯 teardown(_teardown_server)에서도 Build 없이 호출 가능.
def _teardown_one_db(ns: str, db_type: str) -> None:
    apps = k8s.apps_v1()
    core = k8s.core_v1()
    for delete_call in (
        lambda: apps.delete_namespaced_stateful_set(name=db_type, namespace=ns),
        lambda: core.delete_namespaced_service(name=db_type, namespace=ns),
    ):
        try:
            delete_call()
        except ApiException as e:
            if e.status != 404:
                raise


# db_type 적용 — 선택된 db 프로비저닝 + 다른 db (있다면) 정리.
def _apply_db(build: Build, db_type: str) -> None:
    for candidate in ("mysql", "postgres"):
        if candidate == db_type:
            _ensure_one_db(build, candidate)
        else:
            _teardown_one_db(build.tenant_id, candidate)


def _teardown_redis(ns: str) -> None:
    apps = k8s.apps_v1()
    core = k8s.core_v1()
    for delete_call in (
        lambda: apps.delete_namespaced_deployment(name="redis", namespace=ns),
        lambda: core.delete_namespaced_service(name="redis", namespace=ns),
    ):
        try:
            delete_call()
        except ApiException as e:
            if e.status != 404:
                raise


def _apply_redis(build: Build) -> None:
    ns = build.tenant_id
    if not build.use_redis:
        _teardown_redis(ns)
        return
    apps = k8s.apps_v1()
    core = k8s.core_v1()
    docs = manifests.redis(tenant_id=ns, user_id=build.user_id_str)
    for doc in docs:
        kind = doc["kind"]
        try:
            if kind == "Secret":
                core.create_namespaced_secret(namespace=ns, body=doc)
            elif kind == "Service":
                core.create_namespaced_service(namespace=ns, body=doc)
            elif kind == "Deployment":
                apps.create_namespaced_deployment(namespace=ns, body=doc)
        except ApiException as e:
            if e.status != 409:
                raise


# r2-secret(테넌트 ns)에서 S3 자격증명 env를 dict로 읽음. storage 미활성이면 None.
# r2.list_objects/delete_object가 이 env로 버킷에 직접 S3 호출한다 (앱당 스코프 토큰 재사용).
def _read_storage_env(ns: str) -> dict | None:
    try:
        secret = k8s.core_v1().read_namespaced_secret(name="r2-secret", namespace=ns)
    except ApiException as e:
        if e.status == 404:
            return None
        raise
    data = secret.data or {}
    return {k: base64.b64decode(v).decode("utf-8") for k, v in data.items()}


# 앱 버킷 객체 목록 — router가 호출. tenant ns는 user.id에서 파생되므로 격리 자동.
def list_storage_objects(user: User, token: str | None = None) -> dict:
    if not user.app_name:
        raise ValueError("배포된 앱이 없습니다")
    env = _read_storage_env(f"tenant-{user.id.hex[:8]}")
    if not env:
        raise ValueError("오브젝트 스토리지가 활성화돼 있지 않습니다")
    try:
        return r2.list_objects(env, continuation_token=token)
    except r2.R2Error as e:
        raise ValueError(str(e))


# 앱 버킷 객체 1개 삭제 — router가 호출.
def delete_storage_object(user: User, key: str) -> None:
    if not user.app_name:
        raise ValueError("배포된 앱이 없습니다")
    env = _read_storage_env(f"tenant-{user.id.hex[:8]}")
    if not env:
        raise ValueError("오브젝트 스토리지가 활성화돼 있지 않습니다")
    try:
        r2.delete_object(env, key)
    except r2.R2Error as e:
        raise ValueError(str(e))


# 앱 버킷 객체 1개의 본문을 텍스트로 — 미리보기 전용. router가 호출.
# 브라우저가 공개 URL로 직접 읽지 않고 core를 거치는 이유: R2 공개 버킷에는 CORS가 걸려
# 있지 않아 다른 오리진(kodeploy.com)의 fetch가 막힌다. 미리보기를 위해 버킷에 CORS를
# 여는 것보다, 이미 인가를 거치는 core가 자기 자격증명으로 읽어 넘기는 쪽이 노출면이 작다.
def read_storage_object(user: User, key: str) -> dict:
    if not user.app_name:
        raise ValueError("배포된 앱이 없습니다")
    if not key:
        raise ValueError("파일 key가 필요합니다")
    env = _read_storage_env(f"tenant-{user.id.hex[:8]}")
    if not env:
        raise ValueError("오브젝트 스토리지가 활성화돼 있지 않습니다")
    try:
        text, truncated = r2.get_object_text(env, key)
    except r2.R2Error as e:
        raise ValueError(str(e))
    return {"key": key, "text": text, "truncated": truncated}


# 현재 ns의 r2-secret 주석에서 R2 토큰 id 조회 (없으면 None). 정리/회전 시 사용.
def _read_r2_token_id(ns: str) -> str | None:
    try:
        secret = k8s.core_v1().read_namespaced_secret(name="r2-secret", namespace=ns)
    except ApiException as e:
        if e.status == 404:
            return None
        raise
    annotations = (secret.metadata.annotations or {}) if secret.metadata else {}
    return annotations.get("kodeploy.com/r2-token-id")


# 스토리지 토글 off — r2-secret 삭제 + 토큰 revoke. 버킷(데이터)은 보존.
# ns 기반 — 서버 슬롯 teardown에서도 호출.
def _teardown_storage(ns: str) -> None:
    old_token_id = _read_r2_token_id(ns)
    try:
        k8s.core_v1().delete_namespaced_secret(name="r2-secret", namespace=ns)
    except ApiException as e:
        if e.status != 404:
            raise
    if old_token_id:
        r2.deprovision(old_token_id, bucket=None)


# R2 스토리지 토글 — mysql/redis와 같은 철학이되 외부(CF) 리소스라 흐름이 다르다.
# ON  : CF API로 버킷(idempotent) + 새 bucket-scoped 토큰 발급 → r2-secret 생성/교체.
#       재배포마다 토큰을 새로 발급하고 옛 토큰은 revoke (자격증명 회전).
# OFF : r2-secret 삭제 + 토큰 revoke. 버킷(데이터)은 보존 (mysql PVC 보존과 동일 정책).
def _apply_storage(build: Build) -> None:
    if build.user_id is None:
        return
    if not build.use_storage:
        _teardown_storage(build.tenant_id)
        return
    core = k8s.core_v1()
    ns = build.tenant_id
    old_token_id = _read_r2_token_id(ns)

    # CF API로 버킷+토큰 프로비저닝 (실패 시 R2Error → 배포 실패로 표면화).
    env_vars, token_id = r2.provision(build.app_name)
    docs = manifests.storage(
        tenant_id=ns,
        user_id=build.user_id_str,
        env=env_vars,
        token_id=token_id,
    )
    for doc in docs:  # Secret 1개
        try:
            core.read_namespaced_secret(name="r2-secret", namespace=ns)
            core.replace_namespaced_secret(
                name="r2-secret", namespace=ns, body=doc
            )
        except ApiException as e:
            if e.status != 404:
                raise
            core.create_namespaced_secret(namespace=ns, body=doc)
    # 자격증명 회전 — 옛 토큰이 새 것과 다르면 revoke (버킷은 그대로).
    if old_token_id and old_token_id != token_id:
        r2.deprovision(old_token_id, bucket=None)


# 로컬 볼륨 PVC 이름 — 앱당 1개 (1유저=1앱). Deployment 볼륨 마운트가 이 이름을 참조.
def _volume_pvc_name(app_name: str) -> str:
    return f"{app_name}-data"


# 로컬 영속 볼륨(PVC) 보장 — 영속저장소 "local" 모드. mysql/redis/storage와 같은 위치의 토글 함수.
# 활성(volume_mount_path 있음)이면 PVC 생성(idempotent, 409 skip). 비활성이면 아무것도 안 함 —
# PVC는 보존하고 Deployment 재렌더가 마운트를 떼낸다 (mysql PVC 보존과 동일 철학).
# 삭제는 ns 삭제(delete_app)에 위임 — R2처럼 외부 리소스가 아니라 ns cascade로 정리됨.
def _apply_volume(build: Build) -> None:
    if build.user_id is None or not build.volume_mount_path:
        return
    docs = manifests.volume(
        tenant_id=build.tenant_id,
        user_id=build.user_id_str,
        app_name=build.app_name,
        storage_class=build.volume_storage_class or "local-path",
        size=build.volume_size or "5Gi",
    )
    core = k8s.core_v1()
    for doc in docs:  # PVC 1개
        try:
            core.create_namespaced_persistent_volume_claim(
                namespace=build.tenant_id, body=doc
            )
        except ApiException as e:
            if e.status != 409:  # 이미 존재 — 데이터 보존 (mysql StatefulSet 409 skip과 동일)
                raise


# hostnames: 이 슬롯 route에 걸 호스트 목록 (_slot_hostnames로 계산해 전달).
def _apply_deployment(build: Build, hostnames: list[str]) -> None:
    apps = k8s.apps_v1()
    core = k8s.core_v1()
    ns = build.tenant_id

    deploy = manifests.deployment(
        runtime=build.runtime,
        app_name=build.app_name,
        tenant_id=build.tenant_id,
        user_id=build.user_id_str,
        image=build.image,
        port=build.port,
        volume_mount_path=build.volume_mount_path or "",  # 영속저장소 local — 있으면 PVC 마운트 블록 렌더
        pvc_name=_volume_pvc_name(build.app_name),
    )
    svc = manifests.service(
        app_name=build.app_name,
        tenant_id=build.tenant_id,
        user_id=build.user_id_str,
        port=build.port,
    )

    # Deployment 동기화:
    # - 같은 런타임·포트 재배포(대부분): image만 strategic merge patch — 최소 변경.
    # - 런타임/포트가 바뀐 재배포: patch는 probe·리소스에 옛 템플릿이 남아
    #   (예: python 8000 probe인 채 nginx 8080 컨테이너 → 기동 불능) 새 manifest로 통째 replace.
    try:
        existing = apps.read_namespaced_deployment(name=build.app_name, namespace=ns)
        existing_runtime = (existing.metadata.labels or {}).get("runtime")
        containers = existing.spec.template.spec.containers or []
        existing_ports = containers[0].ports if containers else None
        existing_port = existing_ports[0].container_port if existing_ports else None
        # 로컬 볼륨 마운트 드리프트 감지 — 토글 on/off·mount_path 변경은 image-only patch로 반영
        # 안 됨(strategic patch는 volume을 추가만 하고 제거를 못 함). 다르면 통째 replace로 정합.
        existing_mount = next(
            (vm.mount_path for vm in (containers[0].volume_mounts or []) if vm.name == "data"),
            None,
        ) if containers else None
        desired_mount = build.volume_mount_path or None
        if (
            existing_runtime != build.runtime
            or existing_port != build.port
            or existing_mount != desired_mount
        ):
            # replace(PUT)는 optimistic concurrency용 resourceVersion 필수
            deploy["metadata"]["resourceVersion"] = existing.metadata.resource_version
            apps.replace_namespaced_deployment(
                name=build.app_name, namespace=ns, body=deploy,
            )
        else:
            deploy_patch = {
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": "app",
                                    "image": build.image,
                                    "ports": [{"containerPort": build.port}],
                                }
                            ]
                        }
                    }
                }
            }
            apps.patch_namespaced_deployment(
                name=build.app_name,
                namespace=ns,
                body=deploy_patch,
            )
    except ApiException as e:
        if e.status != 404:
            raise
        apps.create_namespaced_deployment(namespace=ns, body=deploy)

    # Service: 포트를 통째 교체 (read-modify-replace) — strategic merge patch 금지.
    # spec.ports의 strategic-merge merge key가 port라, 포트가 바뀌면(예: java 8080 →
    # javascript 3000) 옛 포트가 안 지워지고 새 포트가 추가돼 "이름 없는 2-port" Service가 된다.
    # 다중 포트 Service는 포트마다 name이 필수라 API가 422로 거부한다. → 병합하지 말고 통째 교체:
    # 읽은 객체(clusterIP 등 immutable 필드 보존)에 desired 단일 포트만 세팅해 replace. 과거 이
    # 버그로 이미 2-port가 된 서비스도 다음 배포 때 이 경로가 단일 포트로 치유한다.
    # (Deployment의 포트변경 replace와 같은 대칭 — patch로는 옛 포트를 못 지운다.)
    try:
        existing_svc = core.read_namespaced_service(name=build.app_name, namespace=ns)
        existing_svc.spec.ports = [
            V1ServicePort(
                name="http", port=build.port, target_port=build.port, protocol="TCP"
            )
        ]
        core.replace_namespaced_service(
            name=build.app_name, namespace=ns, body=existing_svc,
        )
    except ApiException as e:
        if e.status != 404:
            raise
        core.create_namespaced_service(namespace=ns, body=svc)

    # HTTPRoute: {app_name}.kodeploy.com → Service. 없으면 create, 있으면 rules만 동기화 —
    # backendRef 포트(런타임 전환 시 변경)·origin-verify 헤더매칭 드리프트가 현재 렌더값으로 복원된다.
    # hostnames는 건드리지 않음 (_set_app_route_hostnames의 reconcile이 별도 관리).
    routes = manifests.httproute(
        app_name=build.app_name,
        tenant_id=build.tenant_id,
        user_id=build.user_id_str,
        port=build.port,
        hostnames=hostnames,
    )
    custom = k8s.custom()
    for route in routes:
        try:
            custom.create_namespaced_custom_object(
                group="gateway.networking.k8s.io",
                version="v1",
                namespace=ns,
                plural="httproutes",
                body=route,
            )
        except ApiException as e:
            if e.status != 409:
                raise
            custom.patch_namespaced_custom_object(
                group="gateway.networking.k8s.io",
                version="v1",
                namespace=ns,
                plural="httproutes",
                name=route["metadata"]["name"],
                body={"spec": {"rules": route["spec"]["rules"]}},
            )

    # Calico NetworkPolicy (per-tenant): 같은 ns + Envoy ingress 허용.
    # GlobalNetworkPolicy(order 200)가 DNS허용 + 사설대역Deny + 인터넷허용 담당.
    calico_pol = manifests.networkpolicy(tenant_id=build.tenant_id)
    try:
        custom.create_namespaced_custom_object(
            group="projectcalico.org",
            version="v3",
            namespace=ns,
            plural="networkpolicies",
            body=calico_pol,
        )
    except ApiException as e:
        if e.status != 409:
            raise

