"""런타임/의존성 리소스 정의 + 감지 + 합산.

매니페스트 템플릿의 resources는 모두 여기서 주입. 템플릿에는 하드코딩 X.
런타임은 자동 감지하지 않음 — 사용자가 배포 요청 시 명시적으로 선택.
"""

# 컴포넌트별 리소스 단일 진실원 (CPU: m, Memory/Storage: Mi)
# req_eph/lim_eph = 컨테이너 ephemeral-storage(노드 로컬 임시 디스크) 상한.
#   유저 런타임(python/java/php)에만 둠 — 유저 코드가 노드 디스크를 무한 점유하면
#   초과 시 그 Pod만 evict되고 노드/다른 테넌트는 보호됨. 의존성(mysql 등)은 데이터가
#   PVC라 ephemeral 폭주 위험이 낮아 제외 (tenant quota에 ephemeral을 넣으면 모든 Pod이
#   ephemeral 선언을 강제당해 admission 거부되므로, 컨테이너 limit만으로 방어).
RUNTIME_RESOURCES: dict[str, dict[str, int]] = {
    "python":   {"req_cpu": 50,  "lim_cpu": 300, "req_mem": 200, "lim_mem": 600,  "req_eph": 100, "lim_eph": 1024},
    "java":     {"req_cpu": 150, "lim_cpu": 500, "req_mem": 700, "lim_mem": 1024, "req_eph": 100, "lim_eph": 1024},
    # php = Apache(prefork) + PHP. mem lim을 python(600)보다 올림 — prefork가 요청마다 프로세스를
    #   띄우고 PHP memory_limit=256M + GD 썸네일 생성이 메모리를 확 써서 600이면 OOMKill 위험.
    "php":      {"req_cpu": 50,  "lim_cpu": 300, "req_mem": 256, "lim_mem": 768,  "req_eph": 100, "lim_eph": 1024},
    # javascript = Node.js 서버(Express/Nest/Next SSR 등). 단일 이벤트루프 + V8 힙 —
    #   python과 유사하되 힙 여유로 mem lim만 640. 정적 프론트(Next export 등)는 static 슬롯으로.
    "javascript": {"req_cpu": 50, "lim_cpu": 300, "req_mem": 200, "lim_mem": 640, "req_eph": 100, "lim_eph": 1024},
    # go = 정적 링크 바이너리 하나. 런타임 오버헤드가 작아 python보다 메모리를 낮게 잡는다.
    #   소규모 앱 가정 (GC가 limit 근처까지 힙을 키우지 않도록 lim 256).
    "go":       {"req_cpu": 50,  "lim_cpu": 300, "req_mem": 64,  "lim_mem": 256,  "req_eph": 100, "lim_eph": 1024},
    # static = nginx-unprivileged가 빌드 산출물(정적 파일)을 서빙. 유저 코드 실행 없음 —
    # idle nginx 실측 한 자릿수 MB라 요청값 최소. eph는 nginx temp 파일 방어용 소량.
    "static":   {"req_cpu": 10,  "lim_cpu": 50,  "req_mem": 16,  "lim_mem": 64,   "req_eph": 50,  "lim_eph": 256},
    "mysql":    {"req_cpu": 50,  "lim_cpu": 200, "req_mem": 300, "lim_mem": 500},
    "postgres": {"req_cpu": 50,  "lim_cpu": 200, "req_mem": 250, "lim_mem": 500},
    "redis":    {"req_cpu": 25,  "lim_cpu": 100, "req_mem": 64,  "lim_mem": 192},
}

# 사용자가 선택 가능한 런타임 목록 (UI dropdown 등). mysql 같은 의존성은 제외.
SELECTABLE_RUNTIMES = ("python", "java", "php", "javascript", "go", "static")


def get_resources(component: str) -> dict[str, int]:
    if component not in RUNTIME_RESOURCES:
        raise ValueError(f"unknown component: {component}")
    return RUNTIME_RESOURCES[component]


# (ResourceQuota 합산은 제거됨 — API-mediated 구조에선 컴포넌트 종류·limit이 고정이라
#  테넌트 상한이 구조적으로 결정되고, quota는 슬롯별 배포에서 덮어쓰기 사고만 만들었음.
#  유저가 replicas/리소스를 직접 조절하는 기능이 생기면 그때 ns 상한으로 재도입.)
