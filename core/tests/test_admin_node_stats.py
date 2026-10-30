"""관리자 노드 현황 — kubelet /stats/summary 직접 조회 (nodes/proxy를 쓰지 않는다).

노드 IP로 접속하되 인증서는 노드 이름으로 검증하고(SAN = DNS:<노드>), kubelet CA 묶음으로 TLS를 확인한다.
"""

from types import SimpleNamespace as NS

import pytest

from app import config
from app.admin import service


def _node(name="worker1", ip="10.0.2.23", port=10250):
    return NS(
        metadata=NS(name=name, labels={}),
        status=NS(
            addresses=[NS(type="Hostname", address=name), NS(type="InternalIP", address=ip)],
            daemon_endpoints=NS(kubelet_endpoint=NS(port=port)),
            capacity={"cpu": "2", "memory": "4Gi"},
            conditions=[NS(type="Ready", status="True")],
        ),
    )


@pytest.fixture
def kubelet(monkeypatch, tmp_path):
    token = tmp_path / "token"
    token.write_text("tok\n")
    monkeypatch.setattr(config, "SA_TOKEN_FILE", str(token))
    monkeypatch.setattr(config, "KUBELET_CA_FILE", str(tmp_path / "ca.crt"))
    seen = NS(ctx_cafile=None, calls=[], status=200,
              body={"node": {"cpu": {"usageNanoCores": 500_000_000}, "memory": {"workingSetBytes": 1024},
                             "fs": {"capacityBytes": 10, "usedBytes": 3}}, "pods": [{}, {}]})
    monkeypatch.setattr(service.ssl, "create_default_context", lambda cafile: seen.__setattr__("ctx_cafile", cafile) or "ctx")

    class Client:
        def __init__(self, verify, timeout):
            assert verify == "ctx"                       # TLS 검증을 끄지 않는다
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def request(self, method, url, headers, extensions):
            seen.calls.append((method, url, headers, extensions))
            return NS(status_code=seen.status, json=lambda: seen.body)

    monkeypatch.setattr(service.httpx, "Client", Client)
    return seen


def test_summary_goes_to_the_kubelet_by_ip_and_verifies_the_node_name(kubelet):
    assert service._node_summary(_node())["pods"] == [{}, {}]
    method, url, headers, ext = kubelet.calls[0]
    assert (method, url) == ("GET", "https://10.0.2.23:10250/stats/summary")
    assert ext == {"sni_hostname": "worker1"} and headers == {"Authorization": "Bearer tok"}
    assert kubelet.ctx_cafile == config.KUBELET_CA_FILE


def test_node_stats_reads_usage_and_reports_failures_per_node(kubelet, monkeypatch):
    nodes = [_node("master", "10.0.2.25"), _node("worker1")]
    nodes[0].metadata.labels = {"node-role.kubernetes.io/control-plane": ""}
    monkeypatch.setattr(service.k8s, "core_v1", lambda: NS(list_node=lambda: NS(items=nodes)))
    out = service.node_stats()
    assert [e["name"] for e in out] == ["master", "worker1"]
    assert out[0]["cpu_used_cores"] == 0.5 and out[0]["pod_count"] == 2 and out[0]["disk_used_bytes"] == 3
    kubelet.status = 401
    assert service.node_stats()[0]["error"] == "stats 조회 실패 (HTTP 401)"   # 그 노드만 표시, 전체는 유지


def test_missing_ca_bundle_is_a_per_node_error(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "KUBELET_CA_FILE", str(tmp_path / "none.crt"))
    with pytest.raises(service.StatsError, match="CA"):
        service._node_summary(_node())
