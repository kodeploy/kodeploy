"""런타임 목록 + 리소스 상한 (실패 진단 요약용).

실제 Pod의 resources는 차트가 정한다 (kodeploy-charts charts/app/platform/runtimes.yaml).
여기 표는 AI 진단이 "선언된 스택"에 상한을 적을 때만 쓴다 — 차트 값을 바꾸면 여기도 같이 바꿀 것.
런타임은 자동 감지하지 않음 — 사용자가 배포 요청 시 명시적으로 선택.
"""

# 컴포넌트별 limit (CPU: m, Memory/Storage: Mi). 차트 resources 표의 *_lim과 같다.
RUNTIME_RESOURCES: dict[str, dict[str, int]] = {
    "java":       {"lim_cpu": 512, "lim_mem": 1024, "lim_eph": 1024},
    "python":     {"lim_cpu": 256, "lim_mem": 512,  "lim_eph": 1024},
    "php":        {"lim_cpu": 256, "lim_mem": 512,  "lim_eph": 1024},
    "javascript": {"lim_cpu": 256, "lim_mem": 512,  "lim_eph": 1024},
    "go":         {"lim_cpu": 256, "lim_mem": 256,  "lim_eph": 1024},
    "static":     {"lim_cpu": 64,  "lim_mem": 64,   "lim_eph": 256},
}

# 사용자가 선택 가능한 런타임 목록 (UI dropdown 등). mysql 같은 의존성은 제외.
SELECTABLE_RUNTIMES = ("python", "java", "php", "javascript", "go", "static")
