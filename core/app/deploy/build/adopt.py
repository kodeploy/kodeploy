"""기존 v1 앱 넘겨받기 — 지금 떠 있는 이미지 그대로 v2(빌더 → kodeploy-apps 커밋 → Argo)로 옮긴다.

실행 (운영자가 앱 하나씩, core Pod 안에서):
    kubectl -n default exec deploy/kodeploy-core -- python -m app.deploy.build.adopt <앱 이름>

흐름:
  1. 떠 있는 Pod의 imageID에서 digest를 읽어, 슬롯마다 set-image를 보낸다 (values는 core 칸 전부).
     다시 빌드하지 않으니 유저 코드가 바뀌지 않는다. 이력 행은 롤백처럼 마지막 성공 배포를 복사해 만든다 —
     넘겨받은 배포가 그대로 첫 v2 롤백 대상이 된다.
  2. Argo가 values로 리소스를 그린다. 이름이 v1과 같아 새로 만들지 않고 기존 리소스에 server-side apply 한다
     (값이 다른 칸은 Argo가 가져가고, 차트 resources 표가 v1과 달라 Pod·DB가 한 번 재시작된다).
  3. deployed가 오면(콜백 → after_deployed) core 쪽 field manager를 지우고 pipeline을 v2로 바꾼다.

field manager를 지우는 이유: v1이 만든 칸 중 값이 같은 칸은 core(OpenAPI-Generator)와 Argo가 함께 갖는다.
그대로 두면 나중에 차트가 그 칸을 빼도(예: Pod 템플릿의 userId 라벨) core 소유로 남아 지워지지 않는다.
Argo가 apply한 뒤에 지워야 Argo가 단독 주인이 된다 (먼저 지우면 Argo apply가 기존 칸을 다시 공동 소유로 둔다).
값과 스펙은 바뀌지 않아 Pod 재시작은 없다.
"""

import argparse
import asyncio
import logging
import sys
import uuid

from kubernetes.client import ApiClient
from kubernetes.client.rest import ApiException

from app.apps import service as apps_service
from app.apps.model import App
from app.auth.model import User
from app.builder import client as builder
from app.deploy import crud
from app.deploy.build import v2
from app.deploy.model import Build
from app.deploy.routing.hostnames import _slot_hostnames
from app.shared import k8s
from app.shared.db import SessionLocal

logger = logging.getLogger(__name__)

# v1이 남긴 쓰기 주인 — core(파이썬 클라이언트 기본 이름)와 운영자의 kubectl 수동 수정. 컨트롤러(status 등)는 둔다.
_CORE_MANAGERS = ("OpenAPI-Generator",)
_ARGO_MANAGER = "argocd-controller"


def _is_core_manager(m: dict) -> bool:
    name = m.get("manager") or ""
    return m.get("operation") == "Update" and not m.get("subresource") and (
        name in _CORE_MANAGERS or name.startswith("kubectl")
    )


# 떠 있는 Deployment의 이미지를 <repo>:<tag>@sha256:<hex>로 — 태그는 Deployment 스펙, digest는 Pod의 imageID.
# Pod이 여러 개면 모두 같은 digest여야 한다 (롤아웃 중이면 거절).
def live_image(namespace: str, name: str) -> str:
    dep = k8s.apps_v1().read_namespaced_deployment(name=name, namespace=namespace)
    image = dep.spec.template.spec.containers[0].image
    repo, _, tag = image.split("@", 1)[0].rpartition(":")
    if not repo or "/" in tag:
        raise ValueError(f"{name}: 이미지에 태그가 없습니다 ({image})")
    pods = k8s.core_v1().list_namespaced_pod(namespace=namespace, label_selector=f"app={name}").items
    digests = set()
    for p in pods:
        for cs in (p.status.container_statuses or []):
            if cs.name == "app" and cs.image_id:
                id_repo, _, digest = cs.image_id.partition("@")
                if id_repo != repo or not digest.startswith("sha256:"):
                    raise ValueError(f"{name}: Pod 이미지가 Deployment와 다릅니다 ({cs.image_id})")
                digests.add(digest)
    if len(digests) != 1:
        raise ValueError(f"{name}: 떠 있는 Pod의 digest를 하나로 정하지 못했습니다 ({sorted(digests) or '없음'})")
    return f"{repo}:{tag}@{digests.pop()}"


def _last_running(db, app: App, *, static: bool) -> Build | None:
    q = db.query(Build).filter(Build.app_id == app.id, Build.kind == "build", Build.status == "running")
    q = q.filter(Build.runtime == "static") if static else q.filter(Build.runtime != "static")
    return q.order_by(Build.created_at.desc()).first()


# 마지막 성공 배포를 복사한 이력 행 (롤백 행과 같은 모양, rollback_of는 없다).
def _adopt_row(app: App, src: Build, image: str) -> Build:
    return Build(
        build_id=uuid.uuid4().hex[:8],
        repo_url=src.repo_url,
        branch=src.branch,
        image=image,
        app_name=src.app_name,
        port=src.port,
        runtime=src.runtime,
        user_id=app.owner_id,
        app_id=app.id,
        namespace=app.namespace,
        db_type=src.db_type,
        use_redis=src.use_redis,
        use_storage=src.use_storage,
        volume_mount_path=src.volume_mount_path,
        volume_storage_class=src.volume_storage_class,
        volume_size=src.volume_size,
        build_mode=src.build_mode,
        dockerfile_path=src.dockerfile_path,
        project_path=src.project_path,
        build_cmd=src.build_cmd,
        output_dir=src.output_dir,
        build_env=src.build_env,
        kind="build",
        status="deploying",
        last_event_seq=0,                         # v2 빌드 표시 겸 콜백 seq 시작점
    )


def adopt_payload(row: Build, owner: User, app: App) -> dict:
    server_hosts, static_hosts = _slot_hostnames(app)
    payload = {
        "build_id": row.build_id,
        "namespace": row.tenant_id,
        "kind": "set-image",
        "image": row.image,
    }
    if row.runtime == "static":
        payload.update(slot="static", values=v2._static_values(row, owner, server_hosts, static_hosts))
    else:
        payload.update(
            slot="server",
            unit={"runtime": row.runtime, "port": row.port},
            values=v2.server_values(row, owner, server_hosts, app.site_enabled, static_hosts),
        )
    return payload


# 넘겨받기를 시작한다 — 슬롯마다 이력 행을 만들고 set-image를 보낸다. 못 하면 ValueError(이유).
async def start_adopt(db, app: App) -> list[Build]:
    if v2.is_v2(app):
        raise ValueError("이미 v2 앱입니다")
    v2.ensure_idle(db, app, include_static=True)
    owner = db.get(User, app.owner_id)
    if owner is None:
        raise ValueError("앱 주인을 찾을 수 없습니다")

    plan: list[tuple[Build, str]] = []     # (복사할 행, 떠 있는 이미지)
    server = _last_running(db, app, static=False)
    if server is not None:
        plan.append((server, live_image(app.namespace, server.app_name)))
    if app.site_enabled:
        static = _last_running(db, app, static=True)
        if static is None:
            raise ValueError("정적 사이트가 켜져 있는데 성공한 정적 배포가 없습니다")
        plan.append((static, live_image(app.namespace, static.app_name)))
    if not plan:
        raise ValueError("넘겨받을 배포가 없습니다 (성공한 배포가 없다)")

    rows = []
    for src, image in plan:
        row = crud.create_build(db, _adopt_row(app, src, image))
        record = v2._new_record(db, row)
        try:
            await builder.submit(adopt_payload(row, owner, app))
        except builder.BuilderError as e:
            v2._fail(db, row, record, f"빌더가 받지 않았습니다: {e}")
            raise ValueError(f"{row.app_name}: 빌더가 받지 않았습니다: {e}")
        rows.append(row)
    return rows


# ---- deployed 뒤 (콜백이 spawn_background로 부른다) ------------------------------------------------

# CustomObjectsApi.patch_*는 merge patch로만 보낸다 — managedFields 교체는 JSON patch라 직접 부른다.
def _json_patch(api_client, path: str, body: list) -> None:
    api_client.call_api(
        path, "PATCH",
        header_params={"Content-Type": "application/json-patch+json", "Accept": "application/json"},
        body=body, auth_settings=["BearerToken"], response_type="object",
    )


def _strip(obj: dict, patch) -> bool:
    fields = obj["metadata"].get("managedFields") or []
    if not any(m.get("manager") == _ARGO_MANAGER for m in fields):
        return False                             # Argo가 아직 apply하지 않은 리소스 — 지우면 주인이 없어진다
    keep = [m for m in fields if not _is_core_manager(m)]
    if len(keep) == len(fields):
        return False
    patch([
        {"op": "test", "path": "/metadata/resourceVersion", "value": obj["metadata"]["resourceVersion"]},
        {"op": "replace", "path": "/metadata/managedFields", "value": keep},
    ])
    return True


# 이 ns에서 Argo가 apply한 리소스의 core field manager를 지운다. 이미 지웠으면 아무것도 안 한다 (매 v2 배포마다 불러도 된다).
def strip_core_managers(namespace: str) -> list[str]:
    apps, core, custom = k8s.apps_v1(), k8s.core_v1(), k8s.custom()
    raw = ApiClient().sanitize_for_serialization
    ns = namespace
    crd = {
        "HTTPRoute": ("gateway.networking.k8s.io", "v1", "httproutes"),
        "NetworkPolicy": ("projectcalico.org", "v3", "networkpolicies"),
    }
    targets = []   # (종류, 객체 dict, patch 함수)
    targets.append(("Namespace", raw(core.read_namespace(name=ns)),
                    lambda name, body: core.patch_namespace(name=name, body=body)))
    for kind, lister, patcher in (
        ("Deployment", apps.list_namespaced_deployment, apps.patch_namespaced_deployment),
        ("StatefulSet", apps.list_namespaced_stateful_set, apps.patch_namespaced_stateful_set),
        ("Service", core.list_namespaced_service, core.patch_namespaced_service),
    ):
        for o in lister(namespace=ns).items:
            targets.append((kind, raw(o), lambda name, body, p=patcher: p(name=name, namespace=ns, body=body)))
    for kind, (group, version, plural) in crd.items():
        for o in custom.list_namespaced_custom_object(group, version, ns, plural)["items"]:
            path = f"/apis/{group}/{version}/namespaces/{ns}/{plural}"
            targets.append((kind, o, lambda name, body, p=path: _json_patch(custom.api_client, f"{p}/{name}", body)))

    stripped = []
    for kind, obj, patch in targets:
        name = obj["metadata"]["name"]
        try:
            if _strip(obj, lambda body: patch(name, body)):
                stripped.append(f"{kind}/{name}")
        except ApiException as e:
            if e.status not in (404, 409, 422):   # 사라졌거나 그사이 바뀌었다 — 다음 배포가 다시 지운다
                raise
            logger.warning("%s: %s/%s field manager strip skipped — %s", ns, kind, name, e.status)
    return stripped


# v2 배포가 끝났다 — core field manager를 지우고, 넘겨받는 중인 앱(아직 v1인데 v2 배포가 성공)이면 v2로 바꾼다.
async def after_deployed(build_id: str) -> None:
    db = SessionLocal()
    try:
        build = crud.get_build(db, build_id)
        if build is None or build.app_id is None:
            return
        app = apps_service.get_app(db, build.app_id)
        if app is None:
            return
        try:
            stripped = await asyncio.to_thread(strip_core_managers, app.namespace)
            if stripped:
                logger.info("%s: core field managers stripped from %s", app.namespace, ", ".join(stripped))
        except Exception as e:
            logger.warning("%s: field manager strip failed — %s", app.namespace, e)
        if not v2.is_v2(app):
            app.pipeline = "v2"
            db.commit()
            logger.info("app %s adopted into v2 (build %s)", app.name, build_id)
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="v1 앱을 지금 떠 있는 이미지 그대로 v2로 넘긴다")
    ap.add_argument("app", help="앱 이름 (apps.name)")
    args = ap.parse_args(argv)
    db = SessionLocal()
    try:
        app = db.query(App).filter(App.name == args.app).first()
        if app is None:
            print(f"앱 {args.app}을 찾을 수 없습니다", file=sys.stderr)
            return 1
        try:
            rows = asyncio.run(start_adopt(db, app))
        except ValueError as e:
            print(f"{app.name}: {e}", file=sys.stderr)
            return 1
        for r in rows:
            print(f"{r.app_name}: set-image {r.image} (build {r.build_id})")
        print("deployed 콜백이 오면 field manager를 지우고 pipeline을 v2로 바꾼다. 진행은 앱 화면의 배포 이력에서 본다")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
