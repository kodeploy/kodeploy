# ============================================================================
# outputs.tf — Terraform 실행 후 출력되는 값들
# ============================================================================

# 노드별 상세 정보 — 호스트명, 역할, 스펙, 프라이빗 IP를 한눈에 확인
output "nodes" {
  description = "노드별 Private IP, 역할, 스펙"
  # local.nodes 기준으로 돈다 — 노드를 맵에서 뺐는데 state엔 아직 인스턴스가 있을 때(-target apply 등)도 깨지지 않게
  value = {
    for k, n in local.nodes :
    k => {
      hostname   = n.hostname
      role       = n.role
      ocpus      = n.ocpus
      memory_gb  = n.memory_gb
      private_ip = oci_core_instance.node[k].private_ip
      public_ip  = oci_core_instance.node[k].public_ip
    }
  }
}

# NLB 공인 IP — Cloudflare DNS A 레코드에 등록할 주소
output "nlb_public_ip" {
  description = "NLB Public IP (Cloudflare DNS에 등록할 주소)"
  value       = oci_network_load_balancer_network_load_balancer.kodeploy.ip_addresses
}

# 클러스터 전체 요약 — 노드 수, 총 CPU/메모리, Free Tier 사용량, 네트워크 대역 등
output "cluster_summary" {
  description = "클러스터 리소스 요약"
  value = {
    name              = var.cluster_name
    region            = var.region
    node_count        = length(local.nodes)
    total_ocpus       = sum([for n in local.nodes : n.ocpus])
    total_mem_gb      = sum([for n in local.nodes : n.memory_gb])
    vcn_cidr          = var.vcn_cidr
    public_subnet     = var.public_subnet_cidr
    private_subnet    = var.private_subnet_cidr
  }
}
