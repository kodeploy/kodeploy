"""관리자 통계/조회 — 플랫폼 DB 집계 + 노드 리소스.

노드 CPU/메모리/디스크는 VictoriaMetrics를 안 거치고 K8s API proxy의
kubelet stats/summary JSON(/api/v1/nodes/{name}/proxy/stats/summary)을 직접 읽는다 —
라벨 추측 없이 결정적이고, core가 이미 가진 K8s 클라이언트로 끝남.
(RBAC에 nodes + nodes/proxy 읽기 권한 필요 — deploy/k8s/core/rbac.yaml)
"""

import uuid
from datetime import datetime, timedelta, timezone

from kubernetes.client.exceptions import ApiException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.apps import service as apps_service
from app.apps.model import App, Tier
from app.auth.model import User
from app.deploy.model import Build, BuildRecord
from app.shared import k8s


# --- 플랫폼 통계 ---------------------------------------------------------------

def overview(db: Session) -> dict:
    # DB datetime은 naive UTC로 저장됨 — 비교도 naive로.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)

    user_count = db.query(func.count(User.id)).scalar() or 0
    users_with_app = db.query(func.count(func.distinct(App.owner_id))).scalar() or 0
    signups_7d = (
        db.query(func.count(User.id)).filter(User.created_at >= week_ago).scalar() or 0
    )

    builds_total = db.query(func.count(BuildRecord.id)).scalar() or 0
    by_status = dict(
        db.query(BuildRecord.status, func.count(BuildRecord.id))
        .group_by(BuildRecord.status)
        .all()
    )
    builds_24h = (
        db.query(func.count(BuildRecord.id))
        .filter(BuildRecord.started_at >= day_ago)
        .scalar()
        or 0
    )
    # 성공 빌드(최종 running)의 평균 전체 소요시간 — 빌드 체감 속도 지표.
    avg_total = (
        db.query(func.avg(BuildRecord.total_seconds))
        .filter(BuildRecord.status == "running")
        .scalar()
    )

    return {
        "users": {
            "total": user_count,
            "with_app": users_with_app,
            "signups_7d": signups_7d,
        },
        "builds": {
            "total": builds_total,
            "succeeded": by_status.get("running", 0),   # 최종 status "running" = 롤아웃까지 성공
            "failed": by_status.get("failed", 0),
            "cancelled": by_status.get("cancelled", 0),
            "last_24h": builds_24h,
            "avg_success_seconds": round(float(avg_total), 1) if avg_total is not None else None,
        },
    }


# 가입자 목록 + 유저별 빌드 집계 (build_records 기준 — 앱 삭제돼도 카운트 유지).
def list_users(db: Session) -> list[dict]:
    rows = (
        db.query(
            BuildRecord.user_id,
            func.count(BuildRecord.id),
            func.max(BuildRecord.started_at),
        )
        .group_by(BuildRecord.user_id)
        .all()
    )
    build_stats = {uid: (count, last) for uid, count, last in rows}

    users = db.query(User).order_by(User.created_at.desc()).all()
    app_of = {}   # 유저의 첫 앱 (목록 한 줄에 대표로 보인다)
    app_counts: dict = {}
    for a in db.query(App).order_by(App.created_at, App.id).all():
        app_of.setdefault(a.owner_id, a)
        app_counts[a.owner_id] = app_counts.get(a.owner_id, 0) + 1
    out = []
    for u in users:
        count, last = build_stats.get(u.id, (0, None))
        app = app_of.get(u.id)
        out.append({
            "id": str(u.id),
            "login": u.login,
            "email": u.email,
            "avatar_url": u.avatar_url,
            "role": u.role,
            "tier": u.tier,
            "app_count": app_counts.get(u.id, 0),
            "app_name": app.name if app else None,
            # 앱 있는 유저만 실제 ns 존재 (lazy provisioning)
            "tenant_id": app.namespace if app else None,
            "custom_domain": app.custom_domain if app else None,
            "build_count": count,
            "last_build_at": last.isoformat() if last else None,
            "created_at": u.created_at.isoformat(),
        })
    return out


# 등급 변경 (root 전용 — router가 get_root_user로 보장).
# root 등급은 운영자가 DB에서 직접 지정 — API로는 root 부여/회수 불가.
ASSIGNABLE_ROLES = ("user", "admin")


def set_role(db: Session, target_id: uuid.UUID, role: str, actor: User) -> dict:
    if role not in ASSIGNABLE_ROLES:
        raise ValueError(f"부여 가능한 등급: {', '.join(ASSIGNABLE_ROLES)}")
    target = db.query(User).filter_by(id=target_id).first()
    if not target:
        raise ValueError("유저를 찾을 수 없습니다")
    if target.id == actor.id:
        raise ValueError("자기 자신의 등급은 변경할 수 없습니다")
    if target.role == "root":
        raise ValueError("root 등급은 변경할 수 없습니다")
    target.role = role
    db.commit()
    return {"id": str(target.id), "login": target.login, "role": target.role}


# --- 앱 개수 등급 ---------------------------------------------------------------

# 등급 목록 + 그 등급인 유저 수. max_apps가 None이면 무제한.
def list_tiers(db: Session) -> list[dict]:
    counts = dict(db.query(User.tier, func.count(User.id)).group_by(User.tier).all())
    tiers = db.query(Tier).order_by(Tier.max_apps.is_(None), Tier.max_apps, Tier.name).all()
    return [{"name": t.name, "max_apps": t.max_apps, "users": counts.get(t.name, 0)} for t in tiers]


# 등급의 앱 수 조절 (root 전용 — router가 보장). None=무제한. 이미 한도를 넘게 가진 앱은 지우지 않고 새로 못 만들 뿐이다.
def set_tier_limit(db: Session, name: str, max_apps: int | None) -> dict:
    tier = db.get(Tier, name)
    if not tier:
        raise ValueError("없는 등급입니다")
    if max_apps is not None and max_apps < 1:
        raise ValueError("앱 수는 1 이상이거나 무제한(null)이어야 합니다")
    tier.max_apps = max_apps
    db.commit()
    return {"name": tier.name, "max_apps": tier.max_apps}


# 유저의 등급 변경 (root 전용).
def set_user_tier(db: Session, target_id: uuid.UUID, tier_name: str) -> dict:
    target = db.query(User).filter_by(id=target_id).first()
    if not target:
        raise ValueError("유저를 찾을 수 없습니다")
    if not db.get(Tier, tier_name):
        raise ValueError("없는 등급입니다")
    target.tier = tier_name
    db.commit()
    return {"id": str(target.id), "login": target.login, "tier": target.tier}


# --- 노드 리소스 ---------------------------------------------------------------

# K8s 리소스 수량 문자열 파서 — cpu("2"/"1500m") → cores, memory("3908020Ki") → bytes.
# 노드 capacity에 실제로 등장하는 단위만 (m / Ki·Mi·Gi). 그 외는 정수 bytes로 간주.
_BYTE_SUFFIX = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4}


def _parse_cpu_cores(v: str) -> float:
    if v.endswith("m"):
        return int(v[:-1]) / 1000
    return float(v)


def _parse_mem_bytes(v: str) -> int:
    for suffix, mul in _BYTE_SUFFIX.items():
        if v.endswith(suffix):
            return int(v[: -len(suffix)]) * mul
    return int(v)


# kubelet stats/summary 프록시 호출 (노드 1대). 응답은 dict (실패 시 ApiException 전파).
def _node_summary(core, node_name: str) -> dict:
    summary = core.api_client.call_api(
        f"/api/v1/nodes/{node_name}/proxy/stats/summary",
        "GET",
        auth_settings=["BearerToken"],
        response_type="object",
        _return_http_data_only=True,
    )
    return summary if isinstance(summary, dict) else {}


# 노드별 상태 + CPU/메모리/디스크 사용량. master 먼저, 이후 이름순.
# stats/summary 실패(권한/노드 다운)는 그 노드만 error 표시 — 전체 응답은 유지.
def node_stats() -> list[dict]:
    core = k8s.core_v1()
    nodes = core.list_node()
    out = []
    for n in nodes.items:
        name = n.metadata.name
        labels = n.metadata.labels or {}
        capacity = n.status.capacity or {}
        conditions = n.status.conditions or []
        entry = {
            "name": name,
            "role": "master" if "node-role.kubernetes.io/control-plane" in labels else "worker",
            "ready": any(c.type == "Ready" and c.status == "True" for c in conditions),
            "cpu_capacity_cores": _parse_cpu_cores(capacity.get("cpu", "0")),
            "memory_capacity_bytes": _parse_mem_bytes(capacity.get("memory", "0")),
            "cpu_used_cores": None,
            "memory_used_bytes": None,
            "disk_capacity_bytes": None,
            "disk_used_bytes": None,
            "pod_count": None,
        }
        try:
            # kubelet Stats Summary — usageNanoCores/workingSetBytes/fs가 한 JSON에 다 옴
            summary = _node_summary(core, name)
            node_sum = summary.get("node", {})
            cpu = node_sum.get("cpu") or {}
            mem = node_sum.get("memory") or {}
            fs = node_sum.get("fs") or {}
            if cpu.get("usageNanoCores") is not None:
                entry["cpu_used_cores"] = round(cpu["usageNanoCores"] / 1e9, 3)
            if mem.get("workingSetBytes") is not None:
                entry["memory_used_bytes"] = mem["workingSetBytes"]
            entry["disk_capacity_bytes"] = fs.get("capacityBytes")
            entry["disk_used_bytes"] = fs.get("usedBytes")
            pods = summary.get("pods")
            if isinstance(pods, list):
                entry["pod_count"] = len(pods)
        except ApiException as e:
            entry["error"] = f"stats 조회 실패 (HTTP {e.status})"
        out.append(entry)

    out.sort(key=lambda e: (e["role"] != "master", e["name"]))
    return out


# 특정 노드의 Pod별 리소스 — 사용량은 stats/summary, 제한은 Pod spec limits 합산.
# 컨테이너 하나라도 limit이 없으면 그 자원은 사실상 무제한 → None (UI에서 "—").
def node_pod_stats(node_name: str) -> list[dict]:
    core = k8s.core_v1()

    # Pod spec에서 limit 합산 — field selector로 그 노드의 Running Pod만.
    limits: dict = {}
    try:
        pod_list = core.list_pod_for_all_namespaces(
            field_selector=f"spec.nodeName={node_name},status.phase=Running",
        )
    except ApiException:
        pod_list = None
    if pod_list:
        for p in pod_list.items:
            cpu_total, mem_total, eph_total = 0.0, 0, 0
            cpu_all = mem_all = eph_all = True
            for c in p.spec.containers:
                lim = (c.resources.limits or {}) if c.resources else {}
                if lim.get("cpu"):
                    cpu_total += _parse_cpu_cores(lim["cpu"])
                else:
                    cpu_all = False
                if lim.get("memory"):
                    mem_total += _parse_mem_bytes(lim["memory"])
                else:
                    mem_all = False
                if lim.get("ephemeral-storage"):
                    eph_total += _parse_mem_bytes(lim["ephemeral-storage"])
                else:
                    eph_all = False
            limits[(p.metadata.namespace, p.metadata.name)] = {
                "cpu": cpu_total if cpu_all else None,
                "memory": mem_total if mem_all else None,
                "ephemeral": eph_total if eph_all else None,
            }

    try:
        summary = _node_summary(core, node_name)
    except ApiException as e:
        raise ValueError(f"노드 stats 조회 실패 (HTTP {e.status})")

    out = []
    for pod in summary.get("pods") or []:
        ref = pod.get("podRef") or {}
        ns, name = ref.get("namespace", ""), ref.get("name", "")
        lim = limits.get((ns, name), {})
        cpu = (pod.get("cpu") or {}).get("usageNanoCores")
        mem = (pod.get("memory") or {}).get("workingSetBytes")
        eph = (pod.get("ephemeral-storage") or {}).get("usedBytes")
        out.append({
            "namespace": ns,
            "name": name,
            "cpu_used_cores": round(cpu / 1e9, 4) if cpu is not None else None,
            "cpu_limit_cores": lim.get("cpu"),
            "memory_used_bytes": mem,
            "memory_limit_bytes": lim.get("memory"),
            "disk_used_bytes": eph,                      # ephemeral-storage (노드 로컬 임시 디스크)
            "disk_limit_bytes": lim.get("ephemeral"),
        })
    out.sort(key=lambda e: e["memory_used_bytes"] or 0, reverse=True)
    return out


# 빌드 기록 목록 (최신순, 최근 limit건) — "총 빌드" 카드 드릴다운용.
# build_records ⟕ users (탈퇴/anonymous면 login 없음 — user_id 표시 대체).
def list_build_records(db: Session, limit: int = 100) -> list[dict]:
    rows = (
        db.query(BuildRecord, User.login)
        .outerjoin(User, BuildRecord.user_id == User.id)
        .order_by(BuildRecord.started_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "build_id": r.build_id,
            "login": login or "anonymous",
            "seq": r.seq,
            "app_name": r.app_name,
            "runtime": r.runtime,
            "build_mode": r.build_mode,
            "started_at": r.started_at.isoformat(),
            "nixpacks_seconds": r.nixpacks_seconds,
            "buildkit_seconds": r.buildkit_seconds,
            "total_seconds": r.total_seconds,
            "status": r.status,
            "error": r.error,
        }
        for r, login in rows
    ]


# 유저 테넌트 상세 — 가입자 row 드릴다운용.
# 1) 선택 스택: 최신 빌드 row(builds, kind="build")의 runtime/db_type/use_redis/use_storage 등.
#    delete_app이 builds를 지우므로 앱 삭제 후엔 config=None (영구 기록이 필요한 건
#    build_records가 담당, 여기는 "현재 구성"이 목적이라 의도된 동작).
# 2) 실제 상태: 테넌트 ns의 Pod 목록 (phase/ready/restarts) — ns 없으면 빈 리스트.
def user_tenant_detail(db: Session, user_id: uuid.UUID) -> dict:
    user = db.query(User).filter_by(id=user_id).first()
    if not user:
        raise ValueError("유저를 찾을 수 없습니다")
    app = apps_service.get_user_app(db, user.id)
    tenant_id = app.namespace if app else None

    latest = (
        db.query(Build)
        .filter_by(app_id=app.id, kind="build")
        .order_by(Build.created_at.desc())
        .first()
        if app else None
    )
    config = None
    if latest:
        config = {
            "runtime": latest.runtime,
            "db_type": latest.db_type or "none",
            "use_redis": bool(latest.use_redis),
            "use_storage": bool(latest.use_storage),
            "volume_mount_path": latest.volume_mount_path or "",  # 영속저장소 local — 마운트 경로(있으면 "스토리지(로컬)" 칩)
            "build_mode": latest.build_mode,
            "port": latest.port,
            "repo_url": latest.repo_url,
            "branch": latest.branch,
            "status": latest.status,
            "created_at": latest.created_at.isoformat(),
        }

    pods = []
    if app:
        try:
            pod_list = k8s.core_v1().list_namespaced_pod(namespace=tenant_id)
            for p in pod_list.items:
                statuses = p.status.container_statuses or []
                conditions = p.status.conditions or []
                pods.append({
                    "name": p.metadata.name,
                    # app 라벨로 컴포넌트 구분 (앱 이름 / mysql / postgres / redis)
                    "component": (p.metadata.labels or {}).get("app", ""),
                    "phase": p.status.phase,
                    "ready": any(
                        c.type == "Ready" and c.status == "True" for c in conditions
                    ),
                    "restarts": sum(cs.restart_count or 0 for cs in statuses),
                    "started_at": (
                        p.status.start_time.isoformat() if p.status.start_time else None
                    ),
                })
        except ApiException as e:
            if e.status != 404:  # ns 없음(배포 전/삭제 직후) — 빈 리스트가 정답
                raise
        pods.sort(key=lambda x: x["name"])

    return {
        "login": user.login,
        "app_name": app.name if app else None,
        "tenant_id": tenant_id,
        "custom_domain": app.custom_domain if app else None,
        "config": config,
        "pods": pods,
    }
