# ============================================================================
# compute.tf — ARM A1 인스턴스 3대 (for_each)
# ----------------------------------------------------------------------------
# 합계를 A1 무료 한도(4 OCPU / 24 GB) 안에 맞춘다: master 1·4 + worker1 2·10 + worker2 1·10.
# 2026-11 managed 노드(1·4, 클러스터 밖 관리·모니터링)를 없앴다 — CPU를 2%만 쓰면서
# 1 OCPU를 차지했다. 그 역할은 master(SSH 터널·인증서 타이머·kubectl 작업)와
# 클러스터 안 모니터링(worker2)으로 옮겼다.
# ⚠️ 유료 계정의 A1 무료 한도가 2026-06 축소(2·12) 대상인지는 공식 확인 전.
# ============================================================================

# 노드 정의 맵 — 각 노드의 호스트명, CPU, 메모리, 역할을 정의
locals {
  nodes = {
    # K8s 컨트롤 플레인 노드 (API Server, etcd, scheduler 등 실행)
    master = {
      hostname  = "kodeploy-master"
      ocpus     = 1 # control plane CPU request 합계 750m. 순간 피크는 1코어를 넘을 수 있음(감수)
      # 4GB에선 kube-apiserver(0.9~1.4GB)+calico-apiserver 누수로 여유가 1GB 안팎이었고,
      # 큰 exec 스트림(200MiB 복원)에 스래싱 → apiserver 재시작까지 갔다(2026-11-14). 워커2에서 2GB를 가져온다.
      memory_gb = 6
      role      = "master"
    }
    # K8s 워커 노드 1 — 실제 파드(컨테이너)가 스케줄링되는 노드
    worker1 = {
      hostname  = "kodeploy-worker1"
      ocpus     = 2
      memory_gb = 10
      role      = "worker"
    }
    # K8s 워커 노드 2 — 워커 1과 함께 워크로드를 분산 처리
    worker2 = {
      hostname  = "kodeploy-worker2"
      ocpus     = 1
      memory_gb = 8 # 실사용 3.4GB(2026-11-14). 2GB를 master로 — 합계는 그대로 4 OCPU·24GB
      role      = "worker"
    }
  }
}

# ARM A1 Flex 인스턴스 — for_each로 위 nodes 맵의 노드를 한꺼번에 생성
# VM.Standard.A1.Flex: ARM 기반 가변 스펙 인스턴스 (OCPU/메모리 자유 조합)
resource "oci_core_instance" "node" {
  for_each = local.nodes

  compartment_id      = var.compartment_ocid
  availability_domain = var.availability_domain
  display_name        = each.value.hostname
  shape               = "VM.Standard.A1.Flex"

  # 인스턴스 스펙 설정 — OCPU 수와 메모리를 노드별로 다르게 지정
  shape_config {
    ocpus         = each.value.ocpus
    memory_in_gbs = each.value.memory_gb
  }

  # 부팅 이미지 — Oracle Linux 9 ARM 이미지로 인스턴스 생성
  source_details {
    source_type = "image"
    source_id   = var.image_ocid
  }

  # 전부 프라이빗 서브넷, 공인 IP 없음 — 나가는 건 NAT, 들어오는 건 NLB(80/443)와
  # cloudflared 터널(master, SSH)뿐
  create_vnic_details {
    subnet_id        = oci_core_subnet.private.id
    assign_public_ip = false
    hostname_label   = each.key
    display_name     = "${each.value.hostname}-vnic"
  }

  # SSH 공개키 주입 — 이 키로만 인스턴스에 SSH 접속 가능
  metadata = {
    ssh_authorized_keys = file(var.ssh_public_key_path)
  }

  # 리소스 태그 — 프로젝트명, 역할, 노드 키로 리소스 식별/필터링용
  freeform_tags = {
    project = var.cluster_name
    role    = each.value.role
    node    = each.key
  }

  # 인스턴스 삭제 시 부트 볼륨도 함께 삭제 (Free Tier 볼륨 낭비 방지)
  preserve_boot_volume = false
}
