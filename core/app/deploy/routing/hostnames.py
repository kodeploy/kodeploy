"""슬롯 hostname 규칙 + 커스텀 도메인(CF for SaaS) 오케스트레이션."""

import re

from kubernetes.client.exceptions import ApiException
from publicsuffixlist import PublicSuffixList
from sqlalchemy.orm import Session

from app.apps.model import App
from app.deploy.routing import domains
from app.shared import k8s

# --- 커스텀 도메인 (CF for SaaS custom hostname) -----------------------------
# domains.py(CF API)와 K8s HTTPRoute를 잇는 오케스트레이션 (r2/_apply_storage와 같은 위치).
# App엔 도메인+status만 저장(컬럼 2개), CF id는 매번 이름으로 lookup(domains.find).

_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9](?:[-a-z0-9]*[a-z0-9])?\.)+[a-z]{2,}$"
)

# Public Suffix List (패키지 동봉 스냅샷 — 네트워크 호출 없음).
# apex 판정용: privatesuffix(d)가 d 자신이면 등록 도메인(apex), 부모면 서브도메인.
# 라벨 수 세기(<3)는 co.kr 같은 복합 TLD에 구멍이 있어 PSL로 판정한다
# (example.co.kr은 라벨 3개지만 apex).
_PSL = PublicSuffixList()


def _normalize_domain(domain: str) -> str:
    d = (domain or "").strip().lower().rstrip(".")
    if not _DOMAIN_RE.match(d):
        raise ValueError("올바른 도메인 형식이 아닙니다 (예: app.example.com)")
    if d == "kodeploy.com" or d.endswith(".kodeploy.com"):
        raise ValueError("kodeploy.com 하위 도메인은 자동 제공되므로 커스텀 도메인으로 못 씁니다")
    return d


# App.extra_hostnames(콤마 구분 텍스트) → 리스트. 운영자가 DB에 직접 등록하는 값.
def _extra_hostnames(app: App) -> list[str]:
    raw = app.extra_hostnames or ""
    return [h.strip().lower() for h in raw.split(",") if h.strip()]


# 슬롯 규칙에 따른 hostname 분배 — (서버 호스트들, 정적 호스트들) 반환.
# 정적 있음: {app}=정적(+커스텀 도메인+extra), {app}-api=서버
# 정적 없음: 서버가 {app}+{app}-api(+커스텀 도메인+extra), 정적은 빈 리스트
# -api를 정적 유무와 무관하게 항상 걸어두는 이유: 나중에 정적을 켜서 {app}이 정적으로
# 넘어가도 서버 주소({app}-api)는 처음부터 유효했던 주소라 API 소비자가 안 깨진다.
def _slot_hostnames(app: App) -> tuple[list[str], list[str]]:
    name = app.name
    extras = _extra_hostnames(app)
    custom_domain = [app.custom_domain] if app.custom_domain else []
    if app.site_enabled:
        return (
            [f"{name}-api.kodeploy.com"],
            [f"{name}.kodeploy.com", *extras, *custom_domain],
        )
    return (
        [f"{name}.kodeploy.com", f"{name}-api.kodeploy.com", *extras, *custom_domain],
        [],
    )


# 앱의 모든 route(서버 쌍 + 정적 쌍) hostnames를 슬롯 규칙으로 통째 set (authoritative
# reconcile). DB(App)가 유일한 진실원 — kubectl 수동 drift는 다음 갱신 때 복원된다.
# 특수 hostname이 필요하면 patch가 아니라 extra_hostnames에 등록할 것.
# 없는 route는 404 skip (해당 슬롯 미배포/teardown 중 — 정상).
def _reconcile_route_hostnames(app: App) -> None:
    tenant_id = app.namespace
    server_hosts, site_hosts = _slot_hostnames(app)
    site_name = f"{app.name}-static"
    targets = [
        (app.name, server_hosts),
        (f"{app.name}-redirect", server_hosts),
        (site_name, site_hosts),
        (f"{site_name}-redirect", site_hosts),
    ]
    custom = k8s.custom()
    for route_name, hostnames in targets:
        if not hostnames:
            continue  # 정적 슬롯 비활성 — 그 route는 _teardown_static이 삭제 (빈 hostnames patch는 invalid)
        try:
            custom.patch_namespaced_custom_object(
                group="gateway.networking.k8s.io",
                version="v1",
                namespace=tenant_id,
                plural="httproutes",
                name=route_name,
                body={"spec": {"hostnames": hostnames}},
            )
        except ApiException as e:
            if e.status != 404:
                raise


# 커스텀 도메인 연결/변경 — CF custom hostname 생성 + App 저장 + 앱 route에 hostname 주입.
def set_custom_domain(db: Session, app: App, domain: str) -> dict:
    if not domains.is_configured():
        raise ValueError("커스텀 도메인이 서버에 설정되지 않았습니다")
    domain = _normalize_domain(domain)

    # 서브도메인 전용 — 루트(apex) 도메인은 CNAME 위임이 안 돼 CF for SaaS로 활성화 불가.
    # PSL 기반 판정: privatesuffix == 자기 자신이면 apex (example.com, example.co.kr 모두),
    # None이면 공용 suffix 자체(co.kr 등) — 둘 다 거부. 통과하면 진짜 서브도메인.
    registrable = _PSL.privatesuffix(domain)
    if registrable is None:
        raise ValueError("올바른 도메인 형식이 아닙니다 (예: app.example.com)")
    if registrable == domain:
        raise ValueError("서브도메인만 연결할 수 있어요 (예: app.example.com). 루트 도메인은 미지원입니다")

    other = (
        db.query(App)
        .filter(App.custom_domain == domain, App.id != app.id)
        .first()
    )
    if other:
        raise ValueError(f"이미 사용 중인 도메인: {domain}")

    # 도메인 변경이면 옛 CF custom hostname 정리
    if app.custom_domain and app.custom_domain != domain:
        domains.delete(app.custom_domain)

    try:
        summary = domains.create(domain)
    except domains.DomainError as e:
        raise ValueError(str(e))

    app.custom_domain = domain
    app.custom_domain_status = "active" if summary.get("status") == "active" else "pending"
    db.commit()

    # DB 갱신 후 reconcile — 슬롯 규칙대로 정적(있으면) 또는 서버 route에 주입.
    # 옛 도메인은 리스트에서 빠지는 걸로 자연 제거됨.
    _reconcile_route_hostnames(app)
    return {
        "domain": app.custom_domain,
        "status": app.custom_domain_status,
        "ssl_status": summary.get("ssl_status"),
    }


# CF에서 검증/cert 상태를 다시 읽어 App.custom_domain_status 갱신 (UI 폴링).
def refresh_custom_domain_status(db: Session, app: App) -> dict:
    if not app.custom_domain:
        return {"domain": None, "status": None, "ssl_status": None}
    summary = None
    try:
        summary = domains.get_status(app.custom_domain)
    except domains.DomainError:
        pass
    if summary:
        new_status = "active" if summary.get("status") == "active" else "pending"
        if new_status != app.custom_domain_status:
            app.custom_domain_status = new_status
            db.commit()
    return {
        "domain": app.custom_domain,
        "status": app.custom_domain_status,
        "ssl_status": summary.get("ssl_status") if summary else None,
    }


# 커스텀 도메인 해제 — App 클리어 후 reconcile(route에서 자연 제거) + CF custom hostname 삭제.
def clear_custom_domain(db: Session, app: App) -> None:
    if not app.custom_domain:
        return
    domain = app.custom_domain
    app.custom_domain = None
    app.custom_domain_status = None
    db.commit()
    try:
        _reconcile_route_hostnames(app)
    except ApiException:
        pass
    domains.delete(domain)

