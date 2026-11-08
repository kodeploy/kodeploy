# ============================================================================
# network.tf — VCN, Subnet, Gateway, Route Table, Security List
# ============================================================================

# ---------------------------------------------------------------------------
# VCN (Virtual Cloud Network) — 모든 리소스가 속하는 가상 네트워크 (AWS의 VPC에 해당)
# ---------------------------------------------------------------------------
resource "oci_core_vcn" "kodeploy" {
  compartment_id = var.compartment_ocid
  cidr_blocks    = [var.vcn_cidr]
  display_name   = "${var.cluster_name}-vcn"
  dns_label      = "kodeploy"

  freeform_tags = { project = var.cluster_name }
}

# ---------------------------------------------------------------------------
# Internet Gateway — 퍼블릭 서브넷(NLB)이 인터넷과 양방향 통신하기 위한 게이트웨이
# ---------------------------------------------------------------------------
resource "oci_core_internet_gateway" "kodeploy" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.kodeploy.id
  display_name   = "${var.cluster_name}-igw"
  enabled        = true

  freeform_tags = { project = var.cluster_name }
}

# ---------------------------------------------------------------------------
# NAT Gateway — 프라이빗 서브넷 노드들이 인터넷으로 나가는 아웃바운드 전용 게이트웨이
# (패키지 설치, 컨테이너 이미지 pull 등에 사용. 외부에서 안으로 접근 불가)
# ---------------------------------------------------------------------------
resource "oci_core_nat_gateway" "kodeploy" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.kodeploy.id
  display_name   = "${var.cluster_name}-natgw"

  freeform_tags = { project = var.cluster_name }
}

# ---------------------------------------------------------------------------
# Service Gateway — 프라이빗 서브넷에서 OCI 내부 서비스(yum repo, Object Storage 등)에
# 인터넷을 거치지 않고 직접 접근하는 게이트웨이 (무료, 빠름)
# ---------------------------------------------------------------------------
resource "oci_core_service_gateway" "kodeploy" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.kodeploy.id
  display_name   = "${var.cluster_name}-sgw"

  services {
    service_id = data.oci_core_services.all.services[0].id
  }

  freeform_tags = { project = var.cluster_name }
}

# ---------------------------------------------------------------------------
# 퍼블릭 서브넷 라우트 테이블 — 모든 외부 트래픽(0.0.0.0/0)을 Internet Gateway로 보냄
# ---------------------------------------------------------------------------
resource "oci_core_route_table" "public" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.kodeploy.id
  display_name   = "${var.cluster_name}-rt-public"

  # 모든 외부 목적지 → Internet Gateway
  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.kodeploy.id
  }

  freeform_tags = { project = var.cluster_name }
}

# ---------------------------------------------------------------------------
# 프라이빗 서브넷 라우트 테이블
#   - 인터넷 아웃바운드(0.0.0.0/0) → NAT Gateway
#   - OCI 내부 서비스 → Service Gateway
# ---------------------------------------------------------------------------
resource "oci_core_route_table" "private" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.kodeploy.id
  display_name   = "${var.cluster_name}-rt-private"

  # 인터넷 아웃바운드 → NAT Gateway
  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_nat_gateway.kodeploy.id
  }

  # OCI 내부 서비스(yum repo 등) → Service Gateway
  route_rules {
    destination       = data.oci_core_services.all.services[0].cidr_block
    destination_type  = "SERVICE_CIDR_BLOCK"
    network_entity_id = oci_core_service_gateway.kodeploy.id
  }

  freeform_tags = { project = var.cluster_name }
}

# ---------------------------------------------------------------------------
# 퍼블릭 서브넷 보안 목록 — NLB가 받을 인바운드 트래픽 허용 규칙
#   - 아웃바운드: 전체 허용
#   - 인바운드: HTTP(80), HTTPS(443)만 허용
# ---------------------------------------------------------------------------
resource "oci_core_security_list" "public" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.kodeploy.id
  display_name   = "${var.cluster_name}-seclist-public"

  # 아웃바운드 전체 허용
  egress_security_rules {
    destination      = "0.0.0.0/0"
    destination_type = "CIDR_BLOCK"
    protocol         = "all"
    stateless        = false
  }

  # 외부 → NLB HTTP(80) — Cloudflare 엣지 IP만 허용 (오리진 잠금 레이어 A)
  dynamic "ingress_security_rules" {
    for_each = var.cloudflare_ip_cidrs
    content {
      protocol  = "6" # TCP
      source    = ingress_security_rules.value
      stateless = false
      tcp_options {
        min = 80
        max = 80
      }
      description = "Cloudflare → HTTP"
    }
  }

  # 외부 → NLB HTTPS(443) — Cloudflare 엣지 IP만 허용 (오리진 잠금 레이어 A)
  dynamic "ingress_security_rules" {
    for_each = var.cloudflare_ip_cidrs
    content {
      protocol  = "6" # TCP
      source    = ingress_security_rules.value
      stateless = false
      tcp_options {
        min = 443
        max = 443
      }
      description = "Cloudflare → HTTPS"
    }
  }

  freeform_tags = { project = var.cluster_name }
}

# ---------------------------------------------------------------------------
# 프라이빗 서브넷 보안 목록 — 노드 간 통신 + NLB→노드 트래픽 허용 규칙
#   - 아웃바운드: 전체 허용
#   - 인바운드: VCN 내부 전체 허용 + NLB에서 오는 HTTP/HTTPS/NodePort 허용
# ---------------------------------------------------------------------------
resource "oci_core_security_list" "private" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.kodeploy.id
  display_name   = "${var.cluster_name}-seclist-private"

  # 아웃바운드 전체 허용
  egress_security_rules {
    destination      = "0.0.0.0/0"
    destination_type = "CIDR_BLOCK"
    protocol         = "all"
    stateless        = false
  }

  # VCN 내부 통신 전면 허용 (K8s 노드 간 통신, 파드 네트워크, etcd 등)
  ingress_security_rules {
    protocol    = "all"
    source      = var.vcn_cidr
    stateless   = false
    description = "VCN 내부 전체 허용"
  }

  # NLB(퍼블릭 서브넷) → 워커 노드 HTTP(80)
  ingress_security_rules {
    protocol  = "6" # TCP
    source    = var.public_subnet_cidr
    stateless = false
    tcp_options {
      min = 80
      max = 80
    }
    description = "NLB → HTTP"
  }

  # NLB(퍼블릭 서브넷) → 워커 노드 HTTPS(443)
  ingress_security_rules {
    protocol  = "6" # TCP
    source    = var.public_subnet_cidr
    stateless = false
    tcp_options {
      min = 443
      max = 443
    }
    description = "NLB → HTTPS"
  }

  # NLB(퍼블릭 서브넷) → 워커 노드 NodePort 범위(30000~32767)
  # K8s NodePort 서비스가 이 포트 범위를 사용함
  ingress_security_rules {
    protocol  = "6" # TCP
    source    = var.public_subnet_cidr
    stateless = false
    tcp_options {
      min = 30000
      max = 32767
    }
    description = "NLB → NodePort range"
  }

  freeform_tags = { project = var.cluster_name }
}

# ---------------------------------------------------------------------------
# 퍼블릭 서브넷 — NLB만 배치되는 서브넷 (공인 IP 할당 가능)
# ---------------------------------------------------------------------------
resource "oci_core_subnet" "public" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.kodeploy.id
  cidr_block                 = var.public_subnet_cidr
  display_name               = "${var.cluster_name}-subnet-public"
  dns_label                  = "pub"
  route_table_id             = oci_core_route_table.public.id
  security_list_ids          = [oci_core_security_list.public.id]
  prohibit_public_ip_on_vnic = false # 공인 IP 허용

  freeform_tags = { project = var.cluster_name }
}

# ---------------------------------------------------------------------------
# 프라이빗 서브넷 — 모든 컴퓨트 노드(master/worker/managed)가 배치되는 서브넷
# (공인 IP 없음, NAT Gateway 통해서만 인터넷 접근)
# ---------------------------------------------------------------------------
resource "oci_core_subnet" "private" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.kodeploy.id
  cidr_block                 = var.private_subnet_cidr
  display_name               = "${var.cluster_name}-subnet-private"
  dns_label                  = "priv"
  route_table_id             = oci_core_route_table.private.id
  security_list_ids          = [oci_core_security_list.private.id]
  prohibit_public_ip_on_vnic = true # 공인 IP 차단

  freeform_tags = { project = var.cluster_name }
}
