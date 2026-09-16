"""환경 변수 기반 설정. 모든 외부 의존(DB/K8s/GHCR)을 한 곳에서 관리.

분류:
- 시크릿 / 환경별 다른 값 / 운영 튜닝 → env (ConfigMap·Secret으로 주입)
- 시스템 고정값 (ns 이름, 이미지 등) → 코드 상수
"""

import os

# --- DB (MySQL) — env 필수 (시크릿 + 환경별) ---
DB_USER = os.getenv("DB_USER", "kodeploy")
DB_PASSWORD = os.getenv("DB_PASSWORD", "changeme")
DB_HOST = os.getenv("DB_HOST", "mysql")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_NAME = os.getenv("DB_NAME", "kodeploy")

DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# --- K8s / GHCR ---
# 시스템 고정 ns 이름들 — env로 받을 가치 0 (시크릿/환경별/운영 튜닝 아무것도 아님)
BUILD_NAMESPACE = "kodeploy-build"                  # 모든 유저의 BuildKit Job이 들어가는 ns. user-id 라벨로 격리
DEFAULT_TENANT_NS = "default"                       # 인증 미연결(user_id=None) 빌드의 fallback ns (Phase 2에서 사라질 변수)
GHCR_AUTH_SECRET_NAME = "ghcr-auth"                 # dockerconfigjson Secret 이름 (push/pull 양쪽에서 참조)

# GHCR 운영 계정 — 환경별 다를 수 있어 env
GHCR_USER = os.getenv("GHCR_USER", "")


# --- BuildKit ---
BUILDKIT_IMAGE = "moby/buildkit:v0.30.0-rootless"   # rootless: privileged 권한 없이 빌드 가능. 클러스터에서 검증된 버전으로 고정
BUILD_TIMEOUT_SECONDS = int(os.getenv("BUILD_TIMEOUT_SECONDS", "600"))  # 운영 튜닝 가능
# Job 전체(빌드+push+cache export) 상한. build 단계는 위 값이 재지만, early-trigger 후 Job은
# 배포가 끝나도 export를 붙들고 살아 있으므로 그보다 넉넉해야 정상 빌드를 안 자른다.
# Job의 activeDeadlineSeconds로 주입 — 무한 export를 컷.
BUILD_ACTIVE_DEADLINE_SECONDS = int(
    os.getenv("BUILD_ACTIVE_DEADLINE_SECONDS", str(BUILD_TIMEOUT_SECONDS + 300))
)
# early-trigger — push 마커(이미지가 레지스트리에 올라간 시점)에 배포를 시작하고 cache export는
# 백그라운드로 넘긴다. 기본 OFF: 켜기 전 계측만 쌓고 전후 비교 후 켠다. 마커를 못 잡으면 어느
# 쪽이든 Job 완료를 기다리는 기존 경로로 자동 폴백(느려질 뿐 안 깨짐).
EARLY_TRIGGER_ENABLED = os.getenv("EARLY_TRIGGER", "false").lower() == "true"
# 레지스트리 레이어 캐시(--import/export-cache, 이미지와 같은 repo의 :buildcache 태그). 기본 OFF —
# 켜기 전 캐시 없는 기준선을 계측하고 전후 비교 후 켠다. early-trigger와 독립 플래그(캐시 없이도
# push 마커는 찍히므로 early-trigger는 동작). 켜면 매 빌드 끝에 cache export 비용이 붙는데, 그 비용을
# 응답 경로에서 빼는 게 early-trigger의 목적.
BUILD_REGISTRY_CACHE_ENABLED = os.getenv("BUILD_REGISTRY_CACHE", "false").lower() == "true"

# --- Cloudflare R2 (오브젝트 스토리지, 레벨2 = 앱당 버킷 + bucket-scoped 토큰) ---
# 시크릿이라 env 주입. 비어 있으면 storage 토글 비활성(r2.is_configured()=False).
CF_API_TOKEN = os.getenv("CF_API_TOKEN", "")        # 버킷 생성/삭제 + 토큰 발급 권한 가진 CF 토큰
CF_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID", "")
# R2 S3 API endpoint — account 단위 고정 형식. 명시 override 없으면 account id에서 파생.
R2_S3_ENDPOINT = os.getenv("R2_S3_ENDPOINT") or (
    f"https://{CF_ACCOUNT_ID}.r2.cloudflarestorage.com" if CF_ACCOUNT_ID else ""
)
# R2 권한그룹 id 목록(쉼표 구분). 비우면 r2.py가 /tokens/permission_groups에서 이름으로 자동 탐색.
CF_R2_PERMISSION_GROUP_IDS = [
    g.strip()
    for g in os.getenv("CF_R2_PERMISSION_GROUP_IDS", "").split(",")
    if g.strip()
]

# --- Cloudflare for SaaS (커스텀 도메인 = 유저가 자기 도메인을 앱에 연결) ---
# custom hostname은 zone 단위 API (R2의 account 단위와 다른 스코프) → 별도 zone id.
# 비어 있으면 커스텀 도메인 기능 비활성(domains.is_configured()=False).
CF_ZONE_ID = os.getenv("CF_ZONE_ID", "")
# custom hostname 전용 CF 토큰 (Zone · SSL and Certificates · Edit). R2용 CF_API_TOKEN과 분리 —
# R2 토큰은 account 스코프라 zone SSL 권한이 없어서 별도 최소권한 토큰을 둔다.
CF_ZONE_API_TOKEN = os.getenv("CF_ZONE_API_TOKEN", "")
# 유저가 자기 도메인 DNS에 걸 CNAME 타깃 (CF for SaaS fallback origin). UI 안내에 노출.
CUSTOM_DOMAIN_CNAME_TARGET = os.getenv("CUSTOM_DOMAIN_CNAME_TARGET", "origin.kodeploy.com")
# CF가 오리진 요청에 붙이는 origin-verify 헤더의 공유 비밀. HTTPRoute가 이 값으로 헤더매칭해
# CF 경유 트래픽만 통과시킨다(origin-lock Layer B′, mTLS 대체). CF Transform Rule 값과 일치해야 함.
# 비어 있으면 헤더매칭 생략(미적용 — Layer A만). CF가 전 트래픽에 헤더 붙이는 걸 켠 뒤 설정할 것.
ORIGIN_VERIFY_SECRET = os.getenv("ORIGIN_VERIFY_SECRET", "")

# --- AI 실패 진단 (사내 API Gateway) ---
# 빌드/배포 실패 시 로그를 읽어 한국어 원인·조치를 내는 기능. 시크릿이라 env 주입.
# 비어 있으면 기능 비활성(diagnose.is_configured()=False) — 배포 흐름엔 영향 없음.
# 이름이 벤더 중립(LLM_)인 이유: 게이트웨이가 여러 제공자를 한 키/한 URL로 묶어주고,
# Secret도 kodeploy-ai, 플래그도 AI_DIAGNOSE라 어휘를 맞춘다.
# ⚠️ openai SDK는 인자를 생략하면 env의 OPENAI_API_KEY와 api.openai.com을 쓴다.
#    이름과 목적지가 둘 다 다르므로 diagnose._get_client()는 둘 다 명시해야 한다.
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
# 게이트웨이의 OpenAI 호환(Chat Completions) 엔드포인트. Anthropic 네이티브 경로
# (.../gateway/claude)도 있지만 SDK를 하나로 두려고 이쪽만 쓴다.
LLM_BASE_URL = os.getenv(
    "LLM_BASE_URL", "https://factchat-cloud.mindlogic.ai/v1/gateway"
)
# 실제 호출 모델. 게이트웨이가 제공자를 흡수하므로 이 문자열 하나로 Claude ↔ GPT를 옮긴다.
# 코드에 분기가 없다는 게 요점 — 바꾸고 rollout restart만 하면 된다.
# ⚠️ 바꾼 뒤엔 실패 1건으로 구조화 출력(strict json_schema)이 그 모델에서도 통과하는지
#    확인할 것. 제공자마다 지원 여부가 다르고, 안 되면 진단만 조용히 비게 된다.
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-5.6-terra")
# 기본 OFF. EARLY_TRIGGER/BUILD_REGISTRY_CACHE와 같은 방침 — 실 로그로 몇 건 돌려
# 진단 품질과 호출당 비용을 확인한 뒤 켠다. 켜도 실패한 빌드에서만 호출된다.
AI_DIAGNOSE_ENABLED = os.getenv("AI_DIAGNOSE", "false").lower() == "true"
# 일일 토큰 예산 (입력+출력 합). 0이면 무제한 = 상한 없음(기본값이 무제한인 이유는
# 이 값을 정하려면 먼저 실사용량을 봐야 하기 때문 — 계측이 정책보다 먼저다).
#
# 왜 "비용(USD)"이 아니라 "토큰"인가: 단가는 모델·제공자·계약에 따라 바뀌는 외부 값이고,
# 토큰은 우리가 직접 관측한 값이다. 코드가 외부 값에 의존하면 단가가 바뀔 때마다 상한의
# 의미가 조용히 달라진다. USD 환산은 단가를 아는 쪽(대시보드)에서 한다 —
# "원본만 저장하고 파생은 뷰에서" 라는 build_records의 방침과 같다.
#
# 초과하면 그 빌드의 진단을 건너뛰고 llm_outcome="budget_exceeded"로 남긴다.
# NULL(애초에 안 부름)과 구분되므로 "정책이 몇 번 발동했나"가 그대로 집계된다.
LLM_DAILY_TOKEN_BUDGET = int(os.getenv("LLM_DAILY_TOKEN_BUDGET", "0"))

# --- CORS ---
# 쉼표로 구분된 허용 origin 목록. 환경별 다름 (dev=localhost, 운영=Cloudflare Pages 도메인).
# Cookie 인증 쓰니까 allow_credentials=True와 함께 와일드카드(*) 금지 — 명시적 origin만.
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if o.strip()
]

# --- Auth / GitHub App ---
# GitHub App에서 발급 (Settings → Developer settings → GitHub Apps → 해당 App).
# OAuth App과 client_id/secret 사용법은 같지만, App permissions로 권한이 고정되고
# user access token이 8시간 만료(+ refresh_token)인 점이 다름.
# 콜백 URL은 GitHub App settings의 "Callback URL"과 정확히 일치해야 함.
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")
GITHUB_OAUTH_REDIRECT_URI = os.getenv(
    "GITHUB_OAUTH_REDIRECT_URI", "http://localhost:8000/auth/github/callback"
)
# App ID + private key(PEM) — private repo clone용 installation token 서명에 사용 (auth/github_app.py).
# OAuth(CLIENT_ID/SECRET)는 '로그인'용, 이건 '빌드가 repo에 접근'하는 server-to-server용. 둘 다 같은 App.
# 비어 있으면 private repo 기능 비활성(github_app.is_configured()=False) — public repo만 빌드.
# PRIVATE_KEY는 멀티라인 PEM이라 Secret으로 주입(\n 포함 통째).
GITHUB_APP_ID = os.getenv("GITHUB_APP_ID", "")
GITHUB_APP_PRIVATE_KEY = os.getenv("GITHUB_APP_PRIVATE_KEY", "")
# App 공개 슬러그(App URL의 이름) — private repo 연결 시 설치 페이지로 보내는 데 사용(시크릿 아님).
# /auth/github/install이 https://github.com/apps/{slug}/installations/new 로 redirect.
GITHUB_APP_SLUG = os.getenv("GITHUB_APP_SLUG", "")
# 로그인 성공 후 사용자를 돌려보낼 web 기본 URL.
WEB_BASE_URL = os.getenv("WEB_BASE_URL", "http://localhost:5173")

# --- Session cookie ---
# 쿠키 이름은 고정. 정책(domain/secure/samesite)은 환경별 다름.
# 로컬 dev: SameSite=Lax / Secure=False / Domain 미지정.
# 운영(cross-site): SameSite=None / Secure=True / Domain=.kodeploy.com.
SESSION_COOKIE_NAME = "kd_session"
SESSION_COOKIE_DOMAIN = os.getenv("SESSION_COOKIE_DOMAIN") or None
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "lax").lower()
SESSION_LIFETIME_DAYS = int(os.getenv("SESSION_LIFETIME_DAYS", "30"))

# OAuth state cookie (callback에서 검증 후 즉시 삭제, 짧은 만료)
OAUTH_STATE_COOKIE_NAME = "kd_oauth_state"
OAUTH_STATE_TTL_SECONDS = 600
