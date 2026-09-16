"""AI 진단 골든셋 — 정확도·비용·지연을 한 번에 재는 평가 하니스.

check_diagnose.py가 "계측기가 작동하는가"(2케이스)를 봤다면, 이 파일은 그 위에서
**정책을 고르기 위한 숫자**를 만든다. 모델을 바꾸거나 로그 절단 예산을 줄였을 때
"얼마나 싸지는가"와 "얼마나 나빠지는가"를 같은 판에서 비교하는 게 목적이다.

실행:
    cd core
    LLM_API_KEY='...' AI_DIAGNOSE=true .venv/bin/python golden_set.py
    # 모델 바꿔 재실행 (A/B)
    LLM_API_KEY='...' AI_DIAGNOSE=true LLM_MODEL='claude-haiku-4-5' .venv/bin/python golden_set.py
    # 로그 절단 예산 바꿔 재실행 (A/B)
    LLM_API_KEY='...' AI_DIAGNOSE=true .venv/bin/python golden_set.py --tail-chars 6000

설계 원칙 3가지:

1. **실코드 경로를 탄다.** 케이스 텍스트를 손으로 조립하지 않고 fake build 객체를
   diagnose.build_failure()/rollout_failure()에 넣는다. 그래야 _stack_summary,
   _strip_marker_blocks, _truncate, _call이 전부 운영과 같은 순서로 돈다 —
   특히 절단 예산 A/B는 프롬프트 조립 단계를 타지 않으면 아예 측정이 안 된다.
   K8s를 부르는 두 함수(_build_job_status/_app_pod_status)와 런타임 로그 조회만
   케이스가 준 캔드 텍스트로 갈아끼운다(클러스터 불필요, 결정적).

2. **판정은 sentinel로 한다.** "그럴듯한가"는 주관적이라 기준이 못 된다. 케이스마다
   정답 범주와, 정답에만 나올 문자열(any-of)을 둔다. forbid는 그 반대 —
   나오면 안 되는 문구(예: 이 플랫폼에서 무의미한 "USER를 추가하세요")로,
   _PLATFORM_CONTEXT가 틀어졌을 때 조용히 지나가지 않게 한다.

3. **한 번에 변인 하나.** 모델과 절단 예산을 동시에 바꾸면 어느 쪽이 효과인지 못 가른다.
   그래서 스위프 인자를 따로 두고, 라벨(--label)로 실행을 구분해 기록한다.

비용은 토큰 × 단가로 계산한다(컬럼으로 저장하지 않는 운영 방침과 동일). 단가는
--usd-in/--usd-out으로 넘기며, 기본값은 현재 모델(gpt-5.6-terra) 기준이다. 단위는 게이트웨이 크레딧 단가표의
"1K 토큰당 크레딧"을 그대로 넘긴다(예: terra 2 / 12) — 출력의 "$" 표기는 크레딧으로 읽을 것.
"""

import argparse
import json
import statistics
import sys
from types import SimpleNamespace

from app import config
from app.deploy.build import diagnose


# --- 로그 패딩 -----------------------------------------------------------------
# 실제 빌드 로그는 수십만 자가 예사고, 실패 신호는 거의 항상 꼬리에 있다. 절단 예산
# A/B가 의미를 가지려면 케이스 로그가 예산보다 길어야 하므로 현실적인 잡음으로 채운다.
# (짧은 로그만으로 재면 _truncate가 아무 일도 안 해서 "절단해도 정확도 그대로"라는
#  가짜 결론이 나온다.)
def _noise(kb: int, flavor: str = "pip") -> str:
    lines = []
    if flavor == "pip":
        for i in range(kb * 12):
            lines.append(
                f"Collecting package-{i % 200}=={i % 9}.{i % 7}.{i % 5}\n"
                f"  Downloading package_{i % 200}-{i % 9}.{i % 7}.{i % 5}-py3-none-any.whl "
                f"({(i % 900) + 20} kB)"
            )
    elif flavor == "npm":
        for i in range(kb * 12):
            lines.append(
                f"npm http fetch GET 200 https://registry.npmjs.org/pkg-{i % 300} "
                f"{(i % 400) + 30}ms (cache miss)"
            )
    else:  # buildkit 스텝 로그
        for i in range(kb * 12):
            lines.append(
                f"#{i % 40} {i % 60}.{i % 10} sha256:{'%064x' % i} "
                f"extracting layer {i % 40}/40 done"
            )
    return "\n".join(lines)


# --- 케이스 -------------------------------------------------------------------
# kind: "build"(빌드 Job 실패) | "rollout"(빌드는 성공, Pod 기동 실패)
# expect: 정답 cause_category
# sentinels: any-of. 하나라도 응답 JSON에 있으면 통과. 빈 리스트면 범주만 본다.
# forbid: 하나라도 있으면 실패 (플랫폼 제약 서술이 틀어졌다는 신호)
# stack: _stack_summary가 읽는 필드들 (운영의 Build row와 같은 이름)
CASES = [
    # ── 플랫폼 제약 1: 비-root 강제 ───────────────────────────────────────────
    {
        "name": "비-root — root 소유 경로 쓰기 실패(EACCES)",
        "kind": "rollout",
        "expect": "non_root",
        "sentinels": ["chown", "1000", "소유"],
        # 이 플랫폼은 securityContext가 UID를 강제하므로 이미지의 USER는 무의미하다.
        # "USER를 추가하세요"가 나오면 제약 목록이 여전히 틀렸다는 뜻.
        "forbid": ["USER를 추가", "USER 지시어를 추가"],
        "stack": {"runtime": "javascript", "build_mode": "dockerfile", "port": 3000},
        "dockerfile": (
            "FROM node:22-alpine\nWORKDIR /app\nCOPY . .\nRUN npm ci\n"
            "RUN mkdir -p /app/uploads\nEXPOSE 3000\nCMD [\"node\", \"server.js\"]"
        ),
        "pod_status": (
            "- Pod phase: Running\n- 재시작 횟수: 4\n"
            "- 현재 app: CrashLoopBackOff\n- 직전 종료: Error (exit 1)"
        ),
        "current": (
            "node:internal/fs/utils:349\n    throw err;\n    ^\n"
            "Error: EACCES: permission denied, open '/app/uploads/.keep'\n"
            "    at Object.openSync (node:fs:596:3)\n"
            "    at Object.writeFileSync (node:fs:2322:35)\n"
            "    at Object.<anonymous> (/app/server.js:12:4)"
        ),
    },
    # ── 플랫폼 제약 2: 권한 상승 차단 ─────────────────────────────────────────
    {
        "name": "권한 상승 — 컨테이너 시작 후 패키지 설치 시도",
        "kind": "rollout",
        "expect": "privilege_escalation",
        "sentinels": ["Dockerfile", "빌드"],
        "stack": {"runtime": "python", "build_mode": "dockerfile", "port": 8000},
        "dockerfile": (
            "FROM python:3.12-slim\nWORKDIR /app\nCOPY . .\n"
            "RUN pip install -r requirements.txt\n"
            "CMD [\"sh\", \"-c\", \"apt-get update && apt-get install -y ffmpeg && "
            "uvicorn main:app --host 0.0.0.0 --port 8000\"]"
        ),
        "pod_status": (
            "- Pod phase: Running\n- 재시작 횟수: 6\n"
            "- 현재 app: CrashLoopBackOff\n- 직전 종료: Error (exit 100)"
        ),
        "current": (
            "Reading package lists...\n"
            "E: Could not open lock file /var/lib/apt/lists/lock - open (13: Permission denied)\n"
            "E: Unable to lock directory /var/lib/apt/lists/\n"
            "W: Problem unlinking the file /var/cache/apt/pkgcache.bin - RemoveCaches (13: Permission denied)"
        ),
    },
    # ── 플랫폼 제약 3: 포트 일치 ──────────────────────────────────────────────
    {
        "name": "포트 불일치 — 127.0.0.1 바인딩",
        "kind": "rollout",
        "expect": "port_mismatch",
        "sentinels": ["0.0.0.0"],
        "stack": {"runtime": "python", "build_mode": "dockerfile", "port": 8000},
        "dockerfile": (
            "FROM python:3.12-slim\nWORKDIR /app\nCOPY . .\n"
            "RUN pip install -r requirements.txt\n"
            "CMD [\"uvicorn\", \"main:app\", \"--host\", \"127.0.0.1\", \"--port\", \"8000\"]"
        ),
        "pod_status": "- Pod phase: Running\n- 재시작 횟수: 0",
        "current": (
            "INFO:     Started server process [1]\n"
            "INFO:     Waiting for application startup.\n"
            "INFO:     Application startup complete.\n"
            "INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)"
        ),
    },
    # ── 플랫폼 제약 3b: 포트 일치(선언값 불일치) ──────────────────────────────
    # 3과 같은 범주지만 증상이 다르다. 로그에 0.0.0.0이 찍혀 있어서 "바인딩은 정상"이고,
    # 틀린 건 배포 폼에서 고른 포트다 — 선언 스택을 안 보면 못 짚는다.
    {
        "name": "포트 불일치 — 선언 3000 / 실제 리슨 8080",
        "kind": "rollout",
        "expect": "port_mismatch",
        "sentinels": ["3000", "8080"],
        "stack": {"runtime": "javascript", "build_mode": "auto", "port": 3000},
        "dockerfile": (
            "# nixpacks 생성\nFROM node:22\nWORKDIR /app\nCOPY . .\nRUN npm ci && npm run build\n"
            "USER 1000\nENTRYPOINT [\"/bin/sh\", \"-c\"]\nCMD [\"npm run start\"]"
        ),
        "pod_status": "- Pod phase: Running\n- 재시작 횟수: 0",
        "current": (
            "> nest-app@1.0.0 start\n> node dist/main.js\n\n"
            "[Nest] 1  - LOG [NestFactory] Starting Nest application...\n"
            "[Nest] 1  - LOG [NestApplication] Nest application successfully started\n"
            "Application is running on: http://0.0.0.0:8080"
        ),
    },
    # ── 플랫폼 제약 4: 빌드 네트워크 펜스 ─────────────────────────────────────
    {
        "name": "빌드 egress 차단 — 사내 미러(10.x) 접근",
        "kind": "build",
        "expect": "build_egress_blocked",
        "sentinels": ["사설", "10.20.30.40", "공인", "미러", "차단"],
        "stack": {"runtime": "python", "build_mode": "dockerfile", "port": 8000},
        "dockerfile": (
            "FROM python:3.12-slim\nWORKDIR /app\nCOPY . .\n"
            "RUN pip install --index-url http://10.20.30.40:8081/simple -r requirements.txt\n"
            "CMD [\"uvicorn\", \"main:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8000\"]"
        ),
        "job_status": (
            "- Job 조건: Failed / BackoffLimitExceeded — Job has reached the specified backoff limit\n"
            "- Pod phase: Failed\n- 컨테이너 buildkit: Error (exit 1)"
        ),
        "noise": (45, "pip"),
        "logs": (
            "=== buildkit (main) ===\n"
            "#8 [3/4] RUN pip install --index-url http://10.20.30.40:8081/simple -r requirements.txt\n"
            "#8 2.104 WARNING: Retrying (Retry(total=4, connect=None, read=None, redirect=None, "
            "status=None)) after connection broken by "
            "'NewConnectionError('<pip._vendor.urllib3.connection.HTTPConnection object at 0xffff8a1c2d50>: "
            "Failed to establish a new connection: [Errno 110] Connection timed out')': "
            "/simple/fastapi/\n"
            "#8 92.31 ERROR: Could not find a version that satisfies the requirement fastapi\n"
            "#8 92.31 ERROR: No matching distribution found for fastapi\n"
            "#8 ERROR: process \"/bin/sh -c pip install --index-url http://10.20.30.40:8081/simple "
            "-r requirements.txt\" did not complete successfully: exit code: 1"
        ),
    },
    # ── 플랫폼 제약 5: 임시 디스크 상한 ───────────────────────────────────────
    {
        "name": "ephemeral-storage 초과 — Pod evict",
        "kind": "rollout",
        "expect": "ephemeral_storage",
        "sentinels": ["ephemeral", "임시", "디스크", "1Gi"],
        "stack": {"runtime": "python", "build_mode": "dockerfile", "port": 8000},
        "dockerfile": (
            "FROM python:3.12-slim\nWORKDIR /app\nCOPY . .\n"
            "RUN pip install -r requirements.txt\n"
            "CMD [\"uvicorn\", \"main:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8000\"]"
        ),
        "pod_status": (
            "- Pod phase: Failed\n- 재시작 횟수: 0\n"
            "- 현재 app: Evicted (exit 0)\n"
            "- 직전 종료: Evicted — Pod ephemeral local storage usage exceeds "
            "the total limit of containers 1Gi"
        ),
        "current": (
            "INFO:     Application startup complete.\n"
            "INFO:     Uvicorn running on http://0.0.0.0:8000\n"
            "[cache] writing /app/.cache/model-weights.bin (512 MB)\n"
            "[cache] writing /app/.cache/thumbnails-batch-2.tar (480 MB)"
        ),
    },
    # ── 플랫폼 제약 6: 메모리 상한 ────────────────────────────────────────────
    # 로그는 문장 중간에서 끊길 뿐 아무 말도 안 남긴다. 구조화 신호(Pod 상태)를
    # 안 보면 절대 못 맞히는 케이스 — 그래서 골든셋에 반드시 있어야 한다.
    {
        "name": "OOMKill — 로그에 아무 단서 없음(Pod 상태로만 판별)",
        "kind": "rollout",
        "expect": "oom",
        "sentinels": ["메모리", "힙", "Xmx", "OOM"],
        "stack": {"runtime": "java", "build_mode": "auto", "port": 8080},
        "dockerfile": (
            "# nixpacks 생성\nFROM eclipse-temurin:17\nWORKDIR /app\nCOPY . .\n"
            "RUN ./gradlew bootJar\nUSER 1000\n"
            "ENTRYPOINT [\"/bin/sh\", \"-c\"]\nCMD [\"java -jar build/libs/app.jar\"]"
        ),
        "pod_status": (
            "- Pod phase: Running\n- 재시작 횟수: 3\n"
            "- 현재 app: CrashLoopBackOff\n"
            "- 직전 종료: OOMKilled (exit 137)"
        ),
        "current": (
            "  .   ____          _            __ _ _\n"
            " /\\\\ / ___'_ __ _ _(_)_ __  __ _ \\ \\ \\ \\\n"
            "( ( )\\___ | '_ | '_| | '_ \\/ _` | \\ \\ \\ \\\n"
            " \\\\/  ___)| |_)| | | | | || (_| |  ) ) ) )\n"
            "  '  |____| .__|_| |_|_| |_\\__, | / / / /\n"
            " :: Spring Boot ::                (v3.3.2)\n"
            "2026-08-15T02:11:03.442Z  INFO 1 --- [main] c.example.AppApplication : Starting AppApplication\n"
            "2026-08-15T02:11:06.918Z  INFO 1 --- [main] o.s.b.w.embedded.tomcat.TomcatWebServer : "
            "Tomcat initialized with port 8080 (http)\n"
            "2026-08-15T02:11:09.204Z  INFO 1 --- [main] o.h.e.t.j.p.i.JtaPlatformInitiator : "
            "HHH000490: Using JtaPlatform impl"
        ),
    },
    # ── 플랫폼 제약 7: 의존성 env 자동 주입 ───────────────────────────────────
    {
        "name": "의존성 env 미사용 — localhost:3306 접속",
        "kind": "rollout",
        "expect": "dependency_env",
        "sentinels": ["DB_HOST", "mysql", "localhost"],
        "stack": {
            "runtime": "java", "build_mode": "dockerfile", "port": 8080,
            "db_type": "mysql",
        },
        "dockerfile": (
            "FROM eclipse-temurin:17-jre\nWORKDIR /app\nCOPY build/libs/app.jar app.jar\n"
            "CMD [\"java\", \"-jar\", \"app.jar\"]"
        ),
        "pod_status": (
            "- Pod phase: Running\n- 재시작 횟수: 2\n"
            "- 현재 app: CrashLoopBackOff\n- 직전 종료: Error (exit 1)"
        ),
        "current": (
            "2026-08-15T02:30:11.020Z ERROR 1 --- [main] com.zaxxer.hikari.pool.HikariPool : "
            "HikariPool-1 - Exception during pool initialization.\n"
            "com.mysql.cj.jdbc.exceptions.CommunicationsException: Communications link failure\n"
            "Caused by: java.net.ConnectException: Connection refused (Connection refused)\n"
            "\tat java.base/java.net.PlainSocketImpl.socketConnect(Native Method)\n"
            "spring.datasource.url=jdbc:mysql://localhost:3306/app"
        ),
    },
    # ── 플랫폼 제약 8: 정적 산출물 경로 ───────────────────────────────────────
    {
        "name": "static 산출물 경로 불일치 — dist 선언 / 실제 build",
        "kind": "build",
        "expect": "static_output_dir",
        "sentinels": ["build", "dist", "산출물"],
        "stack": {
            "runtime": "static", "build_mode": "static", "port": 8080,
            "build_cmd": "npm ci && npm run build", "output_dir": "dist",
        },
        "dockerfile": (
            "FROM node:22-alpine AS build\nWORKDIR /app\nCOPY . .\n"
            "RUN npm ci && npm run build\n\n"
            "FROM nginxinc/nginx-unprivileged:stable-alpine\n"
            "COPY --from=build /app/dist/ /usr/share/nginx/html/"
        ),
        "job_status": (
            "- Job 조건: Failed / BackoffLimitExceeded — Job has reached the specified backoff limit\n"
            "- Pod phase: Failed\n- 컨테이너 buildkit: Error (exit 1)"
        ),
        "noise": (40, "npm"),
        "logs": (
            "=== buildkit (main) ===\n"
            "#12 [build 4/4] RUN npm ci && npm run build\n"
            "#12 41.02 > react-scripts build\n"
            "#12 58.77 Compiled successfully.\n"
            "#12 58.77 File sizes after gzip:\n"
            "#12 58.77   142.31 kB  build/static/js/main.8f2a1c9d.js\n"
            "#12 58.77    2.14 kB   build/static/css/main.1a2b3c4d.css\n"
            "#12 DONE 59.1s\n"
            "#13 [stage-1 2/2] COPY --from=build /app/dist/ /usr/share/nginx/html/\n"
            "#13 ERROR: failed to calculate checksum of ref: \"/app/dist\": not found\n"
            "ERROR: failed to solve: failed to compute cache key: "
            "failed to calculate checksum of ref: \"/app/dist\": not found"
        ),
    },
    # ── 유저 측 1: 빌드 단계 ──────────────────────────────────────────────────
    {
        "name": "유저 빌드 오류 — 존재하지 않는 의존성",
        "kind": "build",
        "expect": "user_build_error",
        "sentinels": ["requirements.txt", "버전", "패키지", "의존성"],
        "stack": {"runtime": "python", "build_mode": "dockerfile", "port": 8000},
        "dockerfile": (
            "FROM python:3.12-slim\nWORKDIR /app\nCOPY . .\n"
            "RUN pip install -r requirements.txt\n"
            "CMD [\"uvicorn\", \"main:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8000\"]"
        ),
        "job_status": (
            "- Job 조건: Failed / BackoffLimitExceeded\n"
            "- Pod phase: Failed\n- 컨테이너 buildkit: Error (exit 1)"
        ),
        "noise": (55, "pip"),
        "logs": (
            "=== buildkit (main) ===\n"
            "#8 [3/4] RUN pip install -r requirements.txt\n"
            "#8 12.44 ERROR: Could not find a version that satisfies the requirement "
            "fastapi-utilz==0.9.9 (from versions: none)\n"
            "#8 12.44 ERROR: No matching distribution found for fastapi-utilz==0.9.9\n"
            "#8 ERROR: process \"/bin/sh -c pip install -r requirements.txt\" "
            "did not complete successfully: exit code: 1"
        ),
    },
    # ── 유저 측 2: 기동 단계 ──────────────────────────────────────────────────
    {
        "name": "유저 런타임 크래시 — 설정 키 누락",
        "kind": "rollout",
        "expect": "user_runtime_crash",
        "sentinels": ["환경변수", "SECRET_KEY", "설정"],
        "stack": {"runtime": "python", "build_mode": "dockerfile", "port": 8000},
        "dockerfile": (
            "FROM python:3.12-slim\nWORKDIR /app\nCOPY . .\n"
            "RUN pip install -r requirements.txt\n"
            "CMD [\"uvicorn\", \"main:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8000\"]"
        ),
        "pod_status": (
            "- Pod phase: Running\n- 재시작 횟수: 5\n"
            "- 현재 app: CrashLoopBackOff\n- 직전 종료: Error (exit 3)"
        ),
        "current": (
            "Traceback (most recent call last):\n"
            "  File \"/usr/local/bin/uvicorn\", line 8, in <module>\n"
            "    sys.exit(main())\n"
            "  File \"/app/settings.py\", line 14, in <module>\n"
            "    SECRET_KEY = os.environ[\"SECRET_KEY\"]\n"
            "  File \"<frozen os>\", line 685, in __getitem__\n"
            "KeyError: 'SECRET_KEY'"
        ),
    },
    # ── 미상 ──────────────────────────────────────────────────────────────────
    # 로그가 신호를 안 담고 있을 때 억지로 플랫폼 제약에 끼워 맞추지 않는가.
    # 폐쇄집합 분류의 대표적 실패 모드(모르면 가장 그럴듯한 라벨을 고름)를 잡는 케이스.
    {
        "name": "미상 — 단서 없는 종료",
        "kind": "build",
        "expect": "unknown",
        "sentinels": [],  # 범주만 본다
        "stack": {"runtime": "php", "build_mode": "dockerfile", "port": 8080},
        "dockerfile": (
            "FROM php:8.3-apache\nCOPY . /var/www/html/\n"
            "RUN docker-php-ext-install pdo_mysql"
        ),
        "job_status": "(종료 정보를 확보하지 못함)",
        "logs": (
            "=== clone (init) ===\n"
            "[1/1] cloning https://github.com/u/app.git (main)...\n"
            "Cloning into '/workspace/src'...\n\n"
            "=== buildkit (main) ===\n"
            "#1 [internal] load build definition from Dockerfile\n"
            "#1 DONE 0.1s\n"
        ),
    },
]


# --- 실행 ---------------------------------------------------------------------

# _stack_summary가 읽는 필드 기본값. 운영 Build row와 이름을 맞춘다.
_STACK_DEFAULTS = {
    "runtime": "python",
    "build_mode": "dockerfile",
    "port": 8000,
    "db_type": "none",
    "use_redis": False,
    "use_storage": False,
    "volume_mount_path": "",
    "volume_size": "5Gi",
    "dockerfile_path": "Dockerfile",
    "project_path": "",
    "build_cmd": "",
    "output_dir": "",
}


def _fake_build(case: dict) -> SimpleNamespace:
    fields = {**_STACK_DEFAULTS, **case.get("stack", {})}
    logs = case.get("logs", "")
    noise = case.get("noise")
    if noise:
        kb, flavor = noise
        # 잡음을 머리에 두고 실패 신호를 꼬리에 둔다 — 실제 빌드 로그의 모양 그대로.
        logs = _noise(kb, flavor) + "\n" + logs
    return SimpleNamespace(
        build_id="gold" + str(abs(hash(case["name"])) % 10000).zfill(4),
        app_name="goldenset",
        tenant_id="tenant-golden0",
        dockerfile_content=case.get("dockerfile"),
        logs=logs,
        **fields,
    )


def _patch_k8s(case: dict) -> None:
    """K8s를 부르는 세 지점만 케이스의 캔드 텍스트로 교체.

    프롬프트 조립(_stack_summary/_strip_marker_blocks/_truncate)과 호출(_call)은
    건드리지 않는다 — 그 경로가 곧 측정 대상이기 때문이다.
    """
    diagnose._build_job_status = lambda build_id: case.get(
        "job_status", "(종료 정보를 확보하지 못함)"
    )
    diagnose._app_pod_status = lambda tenant_id, app_name: case.get(
        "pod_status", "(Pod 없음)"
    )
    diagnose.runtime_logs.fetch_app_logs = lambda tenant_id, app_name: {
        "current": case.get("current", "").splitlines(),
        "previous": case.get("previous", "").splitlines(),
    }


def shape_issues(parsed: dict) -> list[str]:
    """화면이 기대는 모양 규칙 위반 목록. 통과 판정(범주·sentinel)과 별개로 센다.

    스키마는 타입만 강제하고 개수·짝 규칙(minItems 등)은 디코딩이 보장하지 않는다 —
    프롬프트로만 요구한 규칙이라 실제로 지켜지는지 따로 재야 한다.
    """
    issues = []
    steps = parsed["fix_steps"]
    alt_label, alt_steps = parsed["alternative_label"], parsed["alternative_steps"]
    if not 1 <= len(steps) <= 3:
        issues.append(f"fix_steps {len(steps)}개")
    if bool(alt_label.strip()) != bool(alt_steps):
        issues.append("대안 이름/단계 짝 어긋남")
    if len(alt_steps) > 2:
        issues.append(f"alternative_steps {len(alt_steps)}개")
    if not parsed["title"].strip():
        issues.append("title 비어 있음")
    for s in steps + alt_steps:
        cmd = s["command"].strip()
        # 명령어를 따로 뺐는데 문장에도 통째로 남아 있으면 화면에 두 번 보인다.
        if cmd and "\n" not in cmd and cmd in s["text"]:
            issues.append("command가 text에 중복")
            break
    return issues


def run_case(case: dict) -> dict:
    _patch_k8s(case)
    build = _fake_build(case)
    fn = diagnose.build_failure if case["kind"] == "build" else diagnose.rollout_failure
    result = fn(build)

    row = {
        "name": case["name"],
        "kind": case["kind"],
        "expect": case["expect"],
        "outcome": result.outcome,
        "model": result.model,
        "latency_ms": result.latency_ms,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "category": result.cause_category,
        "inconsistent": result.inconsistent,
        "cat_ok": None,
        "sentinel_hit": None,
        "forbid_hit": None,
        "passed": False,
        "cause": None,
        "shape_issues": None,
    }
    if result.outcome != "ok":
        return row

    payload = result.payload
    parsed = json.loads(payload)
    hits = [s for s in case["sentinels"] if s in payload]
    bad = [s for s in case.get("forbid", []) if s in payload]
    row["cat_ok"] = parsed["cause_category"] == case["expect"]
    row["sentinel_hit"] = hits
    row["forbid_hit"] = bad
    row["cause"] = parsed["cause"]
    row["title"] = parsed["title"]
    row["fix_steps"] = parsed["fix_steps"]
    row["alternative_label"] = parsed["alternative_label"]
    row["alternative_steps"] = parsed["alternative_steps"]
    row["shape_issues"] = shape_issues(parsed)
    # 통과 = 범주 일치 + (sentinel 요구가 있으면 하나 이상 적중) + 금지 문구 없음
    row["passed"] = (
        row["cat_ok"] and (not case["sentinels"] or bool(hits)) and not bad
    )
    return row


def summarize(rows: list[dict], usd_in: float, usd_out: float) -> dict:
    n = len(rows)
    ok = [r for r in rows if r["outcome"] == "ok"]
    lat = [r["latency_ms"] for r in rows if r["latency_ms"] is not None]
    p_in = sum(r["prompt_tokens"] or 0 for r in rows)
    p_out = sum(r["completion_tokens"] or 0 for r in rows)
    cost = p_in / 1e6 * usd_in + p_out / 1e6 * usd_out
    lat_sorted = sorted(lat)
    return {
        "cases": n,
        "call_ok": len(ok),
        "passed": sum(1 for r in rows if r["passed"]),
        "cat_ok": sum(1 for r in rows if r["cat_ok"]),
        "inconsistent": sum(1 for r in ok if r["inconsistent"]),
        "shape_bad": sum(1 for r in ok if r["shape_issues"]),
        "with_command": sum(1 for r in ok if any(s["command"].strip() for s in r["fix_steps"])),
        "with_alternative": sum(1 for r in ok if r["alternative_steps"]),
        "prompt_tokens": p_in,
        "completion_tokens": p_out,
        "cost_usd": cost,
        "cost_per_call_usd": cost / n if n else 0.0,
        "latency_avg_ms": round(statistics.mean(lat)) if lat else None,
        # 표본이 12건이라 백분위는 "가장 느린 축"을 보는 용도. 정직하게 nearest-rank.
        "latency_p95_ms": lat_sorted[min(len(lat_sorted) - 1, int(len(lat_sorted) * 0.95))]
        if lat else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default=None, help="실행 라벨 (기본: 모델명+절단예산)")
    ap.add_argument("--tail-chars", type=int, default=None, help="로그 절단 꼬리 예산 override")
    ap.add_argument("--head-chars", type=int, default=None, help="로그 절단 머리 예산 override")
    ap.add_argument("--usd-in", type=float, default=2.0, help="입력 단가 (게이트웨이 크레딧/1K 토큰 = USD/1M 자리에 그대로)")
    ap.add_argument("--usd-out", type=float, default=12.0, help="출력 단가 (게이트웨이 크레딧/1K 토큰 = USD/1M 자리에 그대로)")
    ap.add_argument("--out", default=None, help="결과 JSON 저장 경로")
    ap.add_argument("--only", default=None, help="이름에 이 문자열이 든 케이스만")
    args = ap.parse_args()

    if not diagnose.is_configured():
        print("✗ is_configured()=False — LLM_API_KEY와 AI_DIAGNOSE=true 둘 다 필요합니다.")
        return 1

    # 절단 예산 A/B — 모듈 상수를 갈아끼운다(운영 경로가 이 상수를 읽으므로 실효).
    if args.head_chars is not None:
        diagnose._HEAD_CHARS = args.head_chars
    if args.tail_chars is not None:
        diagnose._TAIL_CHARS = args.tail_chars

    label = args.label or f"{config.LLM_MODEL}/h{diagnose._HEAD_CHARS}t{diagnose._TAIL_CHARS}"
    cases = [c for c in CASES if not args.only or args.only in c["name"]]

    print(f"라벨   : {label}")
    print(f"모델   : {config.LLM_MODEL}")
    print(f"절단   : head {diagnose._HEAD_CHARS} / tail {diagnose._TAIL_CHARS}")
    print(f"케이스 : {len(cases)}건\n")

    rows = []
    for i, case in enumerate(cases, 1):
        row = run_case(case)
        rows.append(row)
        mark = "✓" if row["passed"] else "✗"
        tok = (
            f"{row['prompt_tokens']}→{row['completion_tokens']}"
            if row["prompt_tokens"] is not None
            else "usage 없음"
        )
        print(f"{mark} [{i:2}/{len(cases)}] {case['name']}")
        if row["outcome"] != "ok":
            print(f"       호출 실패: {row['outcome']} ({row['latency_ms']}ms)")
            continue
        detail = f"       {row['category']}"
        if not row["cat_ok"]:
            detail += f" (기대 {row['expect']})"
        if row["inconsistent"]:
            detail += " ⚠자기모순"
        if row["forbid_hit"]:
            detail += f" ⚠금지문구 {row['forbid_hit']}"
        if case["sentinels"] and not row["sentinel_hit"]:
            detail += " ⚠sentinel 없음"
        if row["shape_issues"]:
            detail += f" ⚠모양 {row['shape_issues']}"
        print(f"{detail}  |  {tok} tok  |  {row['latency_ms'] / 1000:.1f}s")
        print(f"       → {row['title']}  /  {row['cause']}")

    s = summarize(rows, args.usd_in, args.usd_out)
    print("\n" + "=" * 72)
    print(f"통과          : {s['passed']}/{s['cases']}   (범주 일치 {s['cat_ok']}/{s['cases']})")
    print(f"호출 성공      : {s['call_ok']}/{s['cases']}")
    print(f"자기모순       : {s['inconsistent']}/{s['call_ok']}")
    print(f"모양 위반      : {s['shape_bad']}/{s['call_ok']}")
    print(f"명령어 포함    : {s['with_command']}/{s['call_ok']}   대안 포함 {s['with_alternative']}/{s['call_ok']}")
    print(f"토큰          : 입력 {s['prompt_tokens']:,} / 출력 {s['completion_tokens']:,}")
    print(f"비용          : ${s['cost_usd']:.4f}  (건당 ${s['cost_per_call_usd']:.5f})")
    print(f"지연          : 평균 {s['latency_avg_ms']}ms / p95 {s['latency_p95_ms']}ms")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(
                {"label": label, "model": config.LLM_MODEL,
                 "head_chars": diagnose._HEAD_CHARS, "tail_chars": diagnose._TAIL_CHARS,
                 "usd_in": args.usd_in, "usd_out": args.usd_out,
                 "summary": s, "rows": rows},
                f, ensure_ascii=False, indent=2,
            )
        print(f"\n결과 저장: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
