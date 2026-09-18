"""deploy 도메인 입출력 스키마."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, HttpUrl


# 서버 슬롯 런타임 — "none"이면 서버 없음 (정적 사이트 단독).
# static은 별도 슬롯(use_static)으로 분리 — 런타임 선택지가 아님.
ServerRuntime = Literal["python", "java", "php", "javascript", "none"]
BuildMode = Literal["detect", "dockerfile", "auto"]      # detect=Dockerfile 자동감지(기본) / auto=nixpacks 자동생성
DbType = Literal["none", "mysql", "postgres"]            # 한 앱에 한 DB만 — 동시 사용 X
# 영속 저장소 — 런타임 무관 공통 옵션. 단일 셀렉터로 상호배타.
#   none   = ephemeral만 (기본)
#   local  = 앱당 PVC를 mount_path에 추가 마운트 (ephemeral은 그대로)
#   object = R2 오브젝트 스토리지(앱당 버킷 + S3 자격증명 주입) — 기존 use_storage 경로
StorageMode = Literal["none", "local", "object"]


# POST /deploy 요청 입력 — 원하는 스택 전체를 선언 (서버 슬롯 + 정적 슬롯).
# 슬롯 규칙: 정적 있으면 {app}=정적·{app}-api=서버·커스텀 도메인→정적,
#           없으면 서버가 {app}·{app}-api 둘 다 + 커스텀 도메인.
class DeployRequest(BaseModel):
    repo_url: HttpUrl                                    # 서버 repo. 정적 단독이면 정적 repo로도 사용(static_repo_url 비었을 때)
    branch: str = "main"
    port: int = 80
    runtime: ServerRuntime                               # 서버 슬롯 — "none"이면 서버 없음(정적 필수)
    name: str | None = None                              # K8s 리소스 이름 + 서브도메인. None이면 서버가 app-<hex8> 자동 생성
    db_type: DbType = "none"                             # "none" | "mysql" | "postgres" (서버 슬롯 전용)
    use_redis: bool = False
    # 영속 저장소(런타임 무관) — 단일 셀렉터. object=R2 / local=PVC / none=ephemeral.
    storage: StorageMode = "none"
    volume_mount_path: str = ""                          # local 전용 — PVC 마운트 절대경로 (예: /var/www/html/data)
    volume_storage_class: str = "local-path"             # local 전용 — 동적 프로비저너 이름
    volume_size: str = "5Gi"                             # local 전용 — PVC 요청 용량
    build_mode: BuildMode = "detect"                     # 서버 슬롯 — detect=자동감지(기본) / dockerfile=유저 Dockerfile / auto=nixpacks
    dockerfile_path: str = "Dockerfile"                  # dockerfile 모드일 때 — "Dockerfile.multi", "subdir/Dockerfile" 등
    project_path: str = ""                               # 서버 auto 모드 — 서브디렉토리 (예: "backend"). 빈 값=repo root
    env: dict[str, str] = {}                             # 서버 슬롯 — 첫 배포 시 Secret 생성, 재배포면 replace
    init_dump_token: str | None = None                   # /deploy/db/stage-dump가 발급한 토큰. DB Ready 후 자동 복원.
    # --- 정적 슬롯 ---
    use_static: bool = False                             # 정적 사이트 토글 — off면 기존 사이트 teardown (DB 토글과 동일 철학)
    static_repo_url: str = ""                            # 빈 값이면 repo_url 사용 (모노레포/단일 repo)
    static_branch: str = ""                              # 빈 값이면 branch 사용
    static_project_path: str = ""                        # 정적 빌드 기준 서브디렉토리 (모노레포 프론트 폴더)
    build_cmd: str = ""                                  # 정적 빌드 커맨드. 빈 값=빌드 없이 repo 그대로 서빙
    output_dir: str = ""                                 # 산출물 디렉토리. build_cmd 있고 빈 값이면 "dist"
    static_env: dict[str, str] = {}                      # 빌드 타임 변수 (VITE_* 등 — 번들에 공개됨, 시크릿 금지)


# POST /deploy 직후 응답 — 제출이 만든 빌드들 (서버/정적 슬롯당 최대 1개)
class DeployBuildRef(BaseModel):
    build_id: str
    runtime: str                                         # "python" | "java" | "static"
    status: str


class DeployResponse(BaseModel):
    app_name: str
    builds: list[DeployBuildRef]


# POST /deploy/app/db/query 입력 — DB 콘솔에서 실행할 단발 SQL.
# offset: SELECT/WITH 쿼리의 페이지네이션 시작 위치(다음 500개 보기). 그 외 쿼리는 무시.
class DbQueryRequest(BaseModel):
    sql: str
    offset: int = 0


# 저장된 쿼리 (DB 콘솔) — 스코프(user/app/db)는 **요청에 없다**. 세션 user와 최신 서버
# 빌드에서만 서버가 뽑는다(router._db_scope). 클라이언트가 스코프를 말할 수 있으면
# 남의 앱/DB 칸에 쓰거나 읽을 여지가 생기므로, 입력 스키마에 그 축을 두지 않는다.
class SavedQueryCreate(BaseModel):
    name: str
    sql: str


# 부분 수정 — 준 필드만 바뀐다 (이름만 고치는 경우가 대부분).
class SavedQueryUpdate(BaseModel):
    name: str | None = None
    sql: str | None = None


class SavedQueryOut(BaseModel):
    id: int
    name: str
    sql: str                                             # 저장은 sql_text 컬럼 (예약어 회피)
    created_at: datetime
    updated_at: datetime


# PUT /deploy/domain — 유저 커스텀 도메인 연결 (CF for SaaS custom hostname).
class DomainRequest(BaseModel):
    domain: str


# GET/PUT /deploy/env 입출력 — 전체 dict를 통째로 다룸 (부분 patch 안 함).
class EnvVarsRequest(BaseModel):
    env: dict[str, str]


class EnvVarsResponse(BaseModel):
    env: dict[str, str]


# GET /deploy 상태 조회 응답
class StatusResponse(BaseModel):
    build_id: str
    status: str
    repo_url: str
    branch: str
    app_name: str
    runtime: str
    build_mode: str
    port: int = 80
    db_type: str = "none"                                # "none" | "mysql" | "postgres"
    use_redis: bool = False
    use_storage: bool = False                            # R2 오브젝트 스토리지 토글 (storage=="object"와 동치)
    storage: str = "none"                                # "none" | "local" | "object" — 재배포 폼 prefill용 (use_storage/volume_mount_path에서 파생)
    volume_mount_path: str = ""                          # local 전용 — 재배포 폼 prefill용
    volume_storage_class: str = "local-path"             # local 전용 — 재배포 폼 prefill용
    volume_size: str = "5Gi"                             # local 전용 — 재배포 폼 prefill용
    kind: str = "build"                                  # "build" | "env_change". 옛 row는 기본 "build".
    dockerfile_path: str = "Dockerfile"
    project_path: str = ""
    build_cmd: str = ""                                  # static 전용 — 재배포 폼 prefill용
    output_dir: str = ""                                 # static 전용 — 재배포 폼 prefill용
    static_env: dict[str, str] = {}                      # static 전용 — 빌드 타임 변수 (재배포 폼 prefill용)
    dockerfile_content: str | None = None                # 실제 빌드에 쓰인 Dockerfile. UI에서 코드 블록으로 표시.
    error: str | None = None
    env_change_summary: str | None = None            # kind="env_change" 전용 — 바뀐 키 목록
    ai_analysis: str | None = None                   # 실패 진단 JSON (실패 + 기능 ON일 때만)
    ai_status: str | None = None                     # 진단 진행 상태: None | "pending" | "done" (model.Build.ai_status)
    logs: str | None = None
    # 빌드 총 소요시간 (BuildRecord에서 — 빌드 완료 후 채워짐, 진행 중/env_change면 None).
    # 단계별(nixpacks/buildkit)은 내부 도구명이라 사용자에게 안 보임 — BuildRecord에만 남겨 운영 분석용.
    total_seconds: float | None = None
    created_at: datetime
    updated_at: datetime
