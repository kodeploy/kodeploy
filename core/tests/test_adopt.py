"""v1 앱 넘겨받기 — 떠 있는 digest 읽기, set-image 본문, core field manager 정리, v2 전환."""

import asyncio
import uuid
from types import SimpleNamespace as NS

import pytest

from app.apps.model import App
from app.auth.model import User
from app.deploy.build import adopt, v2
from app.deploy.model import Build

OWNER = uuid.UUID("b1545e9c-5fce-42e5-9ffd-aaad773d6654")
APP_ID = uuid.UUID("11111111-2222-4333-8444-555555555555")
NSNAME = "app-1f3e0465"
DIGEST = "sha256:" + "ab" * 32


def _pod(image_id, name="app"):
    return NS(status=NS(container_statuses=[NS(name=name, image_id=image_id)]))


@pytest.fixture
def cluster(monkeypatch):
    state = NS(image="ghcr.io/yuntyu01/1f3e0465/modo:b5076b2d", pods=[_pod(f"ghcr.io/yuntyu01/1f3e0465/modo@{DIGEST}")])
    dep = lambda: NS(spec=NS(template=NS(spec=NS(containers=[NS(image=state.image)]))))
    monkeypatch.setattr(adopt.k8s, "apps_v1", lambda: NS(read_namespaced_deployment=lambda name, namespace: dep()))
    monkeypatch.setattr(adopt.k8s, "core_v1", lambda: NS(
        list_namespaced_pod=lambda namespace, label_selector: NS(items=state.pods)))
    return state


# --- 떠 있는 이미지 ---

def test_live_image_pins_the_running_digest(cluster):
    assert adopt.live_image(NSNAME, "modo") == f"ghcr.io/yuntyu01/1f3e0465/modo:b5076b2d@{DIGEST}"


@pytest.mark.parametrize("setup,reason", [
    (lambda s: setattr(s, "pods", []), "하나로 정하지"),                                          # Pod이 없다
    (lambda s: s.pods.append(_pod("ghcr.io/yuntyu01/1f3e0465/modo@sha256:" + "cd" * 32)), "하나로 정하지"),  # 롤아웃 중
    (lambda s: setattr(s, "pods", [_pod(f"ghcr.io/other/modo@{DIGEST}")]), "Deployment와 다릅니다"),
    (lambda s: setattr(s, "image", "ghcr.io/yuntyu01/1f3e0465/modo"), "태그가 없습니다"),
])
def test_live_image_refuses_ambiguous_state(cluster, setup, reason):
    setup(cluster)
    with pytest.raises(ValueError, match=reason):
        adopt.live_image(NSNAME, "modo")


# --- 원본 행: 떠 있는 이미지 태그 = v1 build_id ---

def _db(rows):
    # query(Build).filter(...).first() — build_id 조건만 흉내 낸다
    class Q:
        def filter(self, *conds):
            want = next(c.right.value for c in conds if c.left.key == "build_id")
            return NS(first=lambda: next((r for r in rows if r.build_id == want), None))
    return NS(query=lambda model: Q())


def test_source_is_the_row_that_built_the_live_image_even_if_marked_failed(monkeypatch):
    # 기동이 느려 failed로 찍혔지만 그 이미지가 떠 있다 — 그 행이 원본이다 (runboo)
    monkeypatch.setattr(adopt, "live_image", lambda ns, name: f"ghcr.io/yuntyu01/1f3e0465/modo:b5076b2d@{DIGEST}")
    rows = [_row(build_id="b5076b2d", status="failed"), _row(build_id="c0ffee00", status="running")]
    src, image = adopt._live_source(_db(rows), _app(), "modo")
    assert src.build_id == "b5076b2d" and image.endswith(DIGEST)


def test_source_none_when_slot_is_not_running(monkeypatch):
    def gone(ns, name):
        raise adopt.ApiException(status=404)
    monkeypatch.setattr(adopt, "live_image", gone)
    assert adopt._live_source(_db([]), _app(), "modo-static") is None


def test_source_refuses_image_without_history(monkeypatch):
    monkeypatch.setattr(adopt, "live_image", lambda ns, name: f"ghcr.io/yuntyu01/1f3e0465/modo:deadbeef@{DIGEST}")
    with pytest.raises(ValueError, match="배포 이력이 없습니다"):
        adopt._live_source(_db([_row(build_id="b5076b2d")]), _app(), "modo")


# --- set-image 본문 ---

def _app(**kw):
    return App(id=APP_ID, owner_id=OWNER, name="modo", namespace=NSNAME, site_enabled=kw.get("site_enabled", False),
               pipeline="v1", custom_domain=None, extra_hostnames=None)


def _row(**kw):
    base = dict(build_id="ad0p7001", repo_url="https://github.com/u/modo", branch="main",
                image=f"ghcr.io/yuntyu01/1f3e0465/modo:b5076b2d@{DIGEST}", app_name="modo", port=8080, runtime="java",
                db_type="postgres", use_redis=False, volume_mount_path="", namespace=NSNAME, app_id=APP_ID,
                user_id=OWNER, kind="build", status="deploying", last_event_seq=0)
    base.update(kw)
    return Build(**base)


def test_server_payload_carries_all_core_values():
    p = adopt.adopt_payload(_row(), User(id=OWNER), _app())
    assert p == {
        "build_id": "ad0p7001",
        "namespace": NSNAME,
        "kind": "set-image",
        "image": f"ghcr.io/yuntyu01/1f3e0465/modo:b5076b2d@{DIGEST}",
        "slot": "server",
        "unit": {"runtime": "java", "port": 8080},
        "values": {
            "name": "modo", "userId": OWNER.hex, "db": "postgres", "redis": False, "volume": {"mountPath": ""},
            "hostnames": ["modo.kodeploy.com", "modo-api.kodeploy.com"], "static": {"enabled": False, "hostnames": []},
        },
    }


def test_static_payload_has_no_unit_or_server_values():
    row = _row(app_name="modo-static", runtime="static", image=f"ghcr.io/yuntyu01/1f3e0465/modo-static:aa11bb22@{DIGEST}")
    p = adopt.adopt_payload(row, User(id=OWNER), _app(site_enabled=True))
    assert p["slot"] == "static" and "unit" not in p
    assert p["values"] == {"name": "modo", "userId": OWNER.hex, "hostnames": ["modo-api.kodeploy.com"],
                           "static": {"enabled": True, "hostnames": ["modo.kodeploy.com"]}}


def test_start_refuses_v2_app():
    app = _app()
    app.pipeline = "v2"
    with pytest.raises(ValueError, match="이미 v2"):
        asyncio.run(adopt.start_adopt(NS(), app))


# --- field manager 정리 ---

def _mf(manager, operation="Update", subresource=None):
    m = {"manager": manager, "operation": operation, "fieldsType": "FieldsV1", "fieldsV1": {"f:spec": {}}}
    if subresource:
        m["subresource"] = subresource
    return m


def _obj(*managers):
    return {"metadata": {"name": "modo", "resourceVersion": "42", "managedFields": list(managers)}}


def test_strip_keeps_argo_and_controllers_only():
    sent = []
    obj = _obj(_mf("argocd-controller", "Apply"), _mf("OpenAPI-Generator"), _mf("kubectl-edit"),
               _mf("kube-controller-manager", subresource="status"), _mf("kube-controller-manager"))
    assert adopt._strip(obj, sent.append) is True
    test, replace = sent[0]
    assert test == {"op": "test", "path": "/metadata/resourceVersion", "value": "42"}   # 그사이 바뀌었으면 거절된다
    assert [m["manager"] for m in replace["value"]] == ["argocd-controller", "kube-controller-manager", "kube-controller-manager"]


def test_strip_waits_until_argo_has_applied():
    # Argo가 아직 apply하지 않은 리소스에서 core를 지우면 주인이 아무도 없어진다
    sent = []
    assert adopt._strip(_obj(_mf("OpenAPI-Generator")), sent.append) is False and sent == []


def test_strip_is_a_no_op_once_clean():
    sent = []
    assert adopt._strip(_obj(_mf("argocd-controller", "Apply")), sent.append) is False and sent == []


# --- deployed 뒤 ---

@pytest.fixture
def after(monkeypatch):
    app = _app()
    state = NS(app=app, build=_row(status="running"), committed=0, stripped=[])
    monkeypatch.setattr(adopt, "SessionLocal", lambda: NS(commit=lambda: setattr(state, "committed", state.committed + 1),
                                                         close=lambda: None))
    monkeypatch.setattr(adopt.crud, "get_build", lambda db, bid: state.build)
    monkeypatch.setattr(adopt.apps_service, "get_app", lambda db, aid: state.app)
    monkeypatch.setattr(adopt, "strip_core_managers", lambda ns: state.stripped.append(ns) or ["Deployment/modo"])
    return state


def test_after_deployed_strips_then_flips_a_v1_app(after):
    asyncio.run(adopt.after_deployed("ad0p7001"))
    assert after.stripped == [NSNAME] and after.app.pipeline == "v2" and after.committed == 1


def test_after_deployed_on_v2_app_only_strips(after):
    after.app.pipeline = "v2"
    asyncio.run(adopt.after_deployed("ad0p7001"))
    assert after.stripped == [NSNAME] and after.committed == 0


def test_strip_failure_does_not_block_the_flip(after, monkeypatch):
    def boom(ns):
        raise RuntimeError("403")
    monkeypatch.setattr(adopt, "strip_core_managers", boom)
    asyncio.run(adopt.after_deployed("ad0p7001"))
    assert after.app.pipeline == "v2"
