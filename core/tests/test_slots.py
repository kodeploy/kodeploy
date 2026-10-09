"""두 슬롯 호스트 규칙 + 테넌트 파생 고정 — 라우팅 진실원의 안전망.

_slot_hostnames는 모든 HTTPRoute reconcile의 입력이라, 순서까지 포함해 현행 그대로 고정한다.
tenant_id 파생은 P1(신원 경계)의 공식 — 다른 곳에서 절대 다른 공식이 나오면 안 된다.
"""

import uuid

from app import config
from app.apps.model import App
from app.deploy.build import pipeline
from app.deploy.routing import hostnames
from app.deploy.model import Build


def make_app(name="foo", site_enabled=False, custom_domain=None, extra_hostnames=None):
    # SQLAlchemy 컬럼 default는 INSERT 시점 적용이라 인스턴스 생성 시 명시 세팅 필수
    return App(
        name=name,
        site_enabled=site_enabled,
        custom_domain=custom_domain,
        extra_hostnames=extra_hostnames,
    )


# --- _slot_hostnames — (서버 호스트들, 정적 호스트들) ---

def test_server_only_gets_both_hosts():
    server, site = hostnames._slot_hostnames(make_app(site_enabled=False))
    assert server == ["foo.kodeploy.com", "foo-api.kodeploy.com"]
    assert site == []


def test_static_enabled_splits_hosts():
    server, site = hostnames._slot_hostnames(make_app(site_enabled=True))
    assert server == ["foo-api.kodeploy.com"]   # -api는 정적 유무와 무관하게 항상 서버
    assert site == ["foo.kodeploy.com"]


def test_custom_domain_goes_to_server_when_no_static():
    server, site = hostnames._slot_hostnames(
        make_app(site_enabled=False, custom_domain="x.example.com")
    )
    assert server == ["foo.kodeploy.com", "foo-api.kodeploy.com", "x.example.com"]
    assert site == []


def test_custom_domain_goes_to_static_when_enabled():
    server, site = hostnames._slot_hostnames(
        make_app(site_enabled=True, custom_domain="x.example.com")
    )
    assert server == ["foo-api.kodeploy.com"]
    assert site == ["foo.kodeploy.com", "x.example.com"]


def test_extra_hostnames_ordering_before_custom_domain():
    server, _ = hostnames._slot_hostnames(
        make_app(custom_domain="x.example.com", extra_hostnames="a.com,b.com")
    )
    assert server == [
        "foo.kodeploy.com", "foo-api.kodeploy.com", "a.com", "b.com", "x.example.com",
    ]


# --- _extra_hostnames — 콤마 구분 텍스트 파싱 (운영자 DB 직접 등록 값) ---

def test_extra_hostnames_none_is_empty():
    assert hostnames._extra_hostnames(make_app(extra_hostnames=None)) == []


def test_extra_hostnames_strips_lowers_drops_empty():
    u = make_app(extra_hostnames=" A.com , ,b.COM ")
    assert hostnames._extra_hostnames(u) == ["a.com", "b.com"]


# --- Build.tenant_id / user_id_str — P1 파생 공식 ---

def test_tenant_id_uses_stored_namespace():
    uid = uuid.UUID("12345678-0000-0000-0000-000000000000")
    assert Build(user_id=uid, namespace="app-9abcdef0").tenant_id == "app-9abcdef0"


def test_tenant_id_derived_from_user_id_for_old_rows():
    uid = uuid.UUID("12345678-0000-0000-0000-000000000000")
    assert Build(user_id=uid).tenant_id == "tenant-12345678"


def test_tenant_id_none_falls_back_to_default_ns():
    assert Build(user_id=None).tenant_id == config.DEFAULT_TENANT_NS


def test_user_id_str_hex_or_anonymous():
    uid = uuid.UUID("12345678-0000-0000-0000-000000000000")
    assert Build(user_id=uid).user_id_str == uid.hex
    assert Build(user_id=None).user_id_str == "anonymous"


# --- _build_job_name — Job 템플릿 이름 규칙과 일치해야 로그 조회/취소가 Job을 찾음 ---

def test_build_job_name_truncates_user_to_8():
    uid_str = uuid.UUID("deadbeef-0000-0000-0000-000000000000").hex
    assert pipeline._build_job_name("ab12cd34", uid_str) == "build-deadbeef-ab12cd34"


def test_build_job_name_anonymous():
    assert pipeline._build_job_name("ab12cd34", "anonymous") == "build-anonymou-ab12cd34"
