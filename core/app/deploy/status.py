"""앱 실시간 상태 · 빌드 조회 · 앱 완전 삭제."""

import uuid

from kubernetes.client.exceptions import ApiException
from sqlalchemy.orm import Session

from app.auth.model import User
from app.deploy import crud
from app.deploy.routing import domains
from app.deploy.stack import r2
from app.deploy.model import Build, SavedQuery
from app.deploy.stack.resources import _read_r2_token_id
from app.shared import k8s

# build_id로 단건 빌드 상태 조회 — user_id 주면 본인 빌드만
def get_state(
    db: Session, build_id: str, user_id: uuid.UUID | None = None,
) -> Build | None:
    return crud.get_build(db, build_id, user_id=user_id)


# 빌드 목록 (user_id 주면 본인 것만)
def list_builds(db: Session, user_id: uuid.UUID | None = None) -> list[Build]:
    return crud.list_builds(db, user_id=user_id)


# 현재 user 앱의 Pod 상태 — 빌드와 무관한 실시간 표시용.
# build.status는 그 빌드 시점의 영구 기록이고, 이 함수는 "지금 살아있나"만 본다.
#
# 분류:
#   "running"  — Pod Running + Ready 조건 True
#   "pending"  — 스케줄링/이미지 pull/부팅 중 또는 Running but not ready
#   "crashing" — CrashLoopBackOff / ImagePullBackOff / Failed phase / restart 폭주
#   "missing"  — Deployment 없음 또는 Pod 0 (첫 배포 전 또는 삭제 후)
_CRASH_REASONS = (
    "CrashLoopBackOff",
    "ImagePullBackOff",
    "ErrImagePull",
    "CreateContainerError",
    "CreateContainerConfigError",
)


# 한 슬롯(라벨 셀렉터)의 Pod 상태 분류 — {"status", "started_at"}.
def _slot_pod_status(core, tenant_id: str, app_label: str) -> dict:
    try:
        pods = core.list_namespaced_pod(
            namespace=tenant_id, label_selector=f"app={app_label}",
        )
    except ApiException as e:
        if e.status == 404:
            return {"status": "missing", "started_at": None}
        raise
    if not pods.items:
        return {"status": "missing", "started_at": None}

    pod = max(
        pods.items,
        key=lambda p: p.status.start_time.timestamp() if p.status.start_time else 0,
    )
    started_at = (
        pod.status.start_time.isoformat() if pod.status.start_time else None
    )

    phase = pod.status.phase
    if phase == "Running":
        conditions = pod.status.conditions or []
        ready = any(c.type == "Ready" and c.status == "True" for c in conditions)
        if ready:
            return {"status": "running", "started_at": started_at}
        for cs in pod.status.container_statuses or []:
            if cs.state and cs.state.waiting:
                if (cs.state.waiting.reason or "") in _CRASH_REASONS:
                    return {"status": "crashing", "started_at": started_at}
        return {"status": "pending", "started_at": started_at}

    if phase == "Failed":
        return {"status": "crashing", "started_at": started_at}

    for cs in pod.status.container_statuses or []:
        if cs.state and cs.state.waiting:
            if (cs.state.waiting.reason or "") in _CRASH_REASONS:
                return {"status": "crashing", "started_at": started_at}
    return {"status": "pending", "started_at": started_at}


# 슬롯별 상태 + 대표 상태. 응답:
#   {status, started_at,            ← 대표 (서버 우선, 없으면 정적 — 위젯 최소화 라벨 등)
#    server: {status, started_at},  ← 서버 슬롯 (정적 단독이면 "missing")
#    site:   {status, started_at} | null}  ← site_enabled 아닐 땐 null
def get_app_status(user: User) -> dict:
    if not user.app_name:
        return {"status": "missing", "started_at": None, "server": None, "site": None}
    tenant_id = f"tenant-{user.id.hex[:8]}"
    core = k8s.core_v1()

    server = _slot_pod_status(core, tenant_id, user.app_name)
    site = (
        _slot_pod_status(core, tenant_id, f"{user.app_name}-static")
        if user.site_enabled
        else None
    )
    rep = server if server["status"] != "missing" else (site or server)
    return {**rep, "server": server, "site": site}


# 앱 완전 삭제 — tenant namespace 통째로 삭제하면 K8s가 cascade로
# 안의 모든 자원(Deployment/Service/HTTPRoute/STS/PVC/Secret/ResourceQuota) 자동 정리.
# 같은 user_id로 재배포하면 같은 tenant_id 쓰는데, ns가 terminating이면 _ensure_tenant_ns가
# 사라질 때까지 잠시 대기 후 새 create. GHCR 이미지는 보존 (별도 정리).
def delete_app(db: Session, user: User) -> None:
    if not user.app_name:
        raise ValueError("삭제할 앱이 없습니다")

    tenant_id = f"tenant-{user.id.hex[:8]}"

    # R2는 외부(CF) 리소스라 ns 삭제로 정리 안 됨 — ns 지우기 전에 토큰 id를 읽어둔다.
    app_name = user.app_name
    r2_token_id = None
    try:
        r2_token_id = _read_r2_token_id(tenant_id)
    except ApiException:
        pass  # 정리 정보 조회 실패가 앱 삭제를 막지 않게

    # ns 삭제는 비동기 — terminating 상태로 들어가고 finalizer 정리 끝나면 사라짐.
    # 사용자에겐 즉시 응답하고 다음 배포 시점에 대기.
    try:
        k8s.core_v1().delete_namespace(name=tenant_id)
    except ApiException as e:
        if e.status != 404:
            raise

    # R2 토큰 revoke + 버킷 삭제 (앱 완전 삭제이므로 데이터도 정리). best-effort.
    if r2_token_id or app_name:
        try:
            r2.deprovision(
                r2_token_id,
                bucket=r2.bucket_name(app_name) if app_name else None,
            )
        except r2.R2Error:
            pass  # 외부 정리 실패가 DB/응답을 막지 않게 — orphan은 sweeper(백로그)

    # 커스텀 도메인 CF custom hostname 정리 (외부 리소스라 ns 삭제로 안 사라짐). best-effort.
    if user.custom_domain:
        try:
            domains.delete(user.custom_domain)
        except domains.DomainError:
            pass

    # DB: builds 히스토리 + user.app_name/custom_domain/슬롯 선언 리셋. 다음 배포는 첫 배포 흐름.
    # build_records(빌드 행위 영구 기록)는 운영 분석용 append-only라 의도적으로 보존.
    # extra_hostnames도 클리어 — 옛 앱용 hostname이 다음(다른) 앱 route에 자동 주입되면 안 됨.
    db.query(Build).filter(Build.user_id == user.id).delete()
    # DB 콘솔의 저장된 쿼리도 같이 — 앱이 사라지면 그 앱/DB 스코프는 다시 안 온다
    # (다음 배포는 새 app_name). 남겨두면 아무도 못 읽는 orphan row만 쌓인다.
    db.query(SavedQuery).filter(SavedQuery.user_id == user.id).delete()
    user.app_name = None
    user.custom_domain = None
    user.custom_domain_status = None
    user.extra_hostnames = None
    user.site_enabled = False
    db.commit()
