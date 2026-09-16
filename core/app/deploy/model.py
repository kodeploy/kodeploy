"""deploy 도메인 ORM."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text, Uuid
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app import config
from app.shared.db import Base


# 배포 1건의 영속 상태
class Build(Base):
    __tablename__ = "builds"

    build_id: Mapped[str] = mapped_column(String(8), primary_key=True)
    repo_url: Mapped[str] = mapped_column(String(500))
    branch: Mapped[str] = mapped_column(String(100))
    image: Mapped[str] = mapped_column(String(500))
    app_name: Mapped[str] = mapped_column(String(50))
    port: Mapped[int] = mapped_column(Integer)
    runtime: Mapped[str] = mapped_column(String(20))     # 유저가 선택한 런타임 (python/java/php/javascript) — 스키마가 검증
    db_type: Mapped[str] = mapped_column(String(20), default="none")  # "none" | "mysql" | "postgres"
    use_redis: Mapped[bool] = mapped_column(Boolean, default=False)
    use_storage: Mapped[bool] = mapped_column(Boolean, default=False)  # R2 오브젝트 스토리지(앱당 버킷) 토글 — 영속저장소 "object" 모드
    # 영속저장소 "local" 모드 — 앱당 PVC를 mount_path에 추가 마운트 (ephemeral은 그대로 둠).
    # use_storage(object)와 상호배타 — 요청의 단일 storage 셀렉터(none/local/object)가 보장.
    # volume_mount_path가 비어 있으면 로컬 볼륨 비활성 (custom_domain·build_cmd와 동일하게 "" = off).
    volume_mount_path: Mapped[str] = mapped_column(String(200), default="")  # PVC 마운트 절대경로 (예: /var/www/html/data)
    volume_storage_class: Mapped[str] = mapped_column(String(64), default="local-path")  # 동적 프로비저너 이름
    volume_size: Mapped[str] = mapped_column(String(20), default="5Gi")  # PVC 요청 용량
    kind: Mapped[str] = mapped_column(String(20), default="build")  # "build"=일반 빌드 / "env_change"=환경변수 변경 이벤트
    build_mode: Mapped[str] = mapped_column(String(20), default="dockerfile")  # "dockerfile" | "auto"(nixpacks) | "static"(runtime=static이면 서버가 강제)
    dockerfile_path: Mapped[str] = mapped_column(String(200), default="Dockerfile")  # dockerfile 모드 — BuildKit filename
    project_path: Mapped[str] = mapped_column(String(200), default="")  # auto/static 모드 — repo root 기준 서브디렉토리 (빈 값=root)
    build_cmd: Mapped[str] = mapped_column(String(300), default="")  # static 전용 — node 빌드 스테이지 커맨드. 빈 값=빌드 없이 repo 그대로 서빙
    output_dir: Mapped[str] = mapped_column(String(200), default="")  # static 전용 — 빌드 산출물 디렉토리 (예: "dist"). build_cmd 없으면 무시
    # static 전용 — 빌드 타임 변수 JSON (VITE_* 등). 번들에 박혀 공개되는 값이라 Secret 아님.
    # 서버 런타임 env({app}-env Secret)와 별개 — 섞으면 서버 시크릿이 번들에 구워지는 사고가 됨.
    build_env: Mapped[str | None] = mapped_column(Text, nullable=True)
    dockerfile_content: Mapped[str | None] = mapped_column(LONGTEXT, nullable=True)  # 실제 빌드에 쓰인 Dockerfile 텍스트. UI 노출 + AI 분석용
    status: Mapped[str] = mapped_column(String(20), default="queued")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # kind="env_change" row 전용 — 어떤 키가 바뀌었나 ("KEY (추가), KEY2 (수정)").
    # 값은 저장하지 않는다(시크릿). 일반 빌드 row에서는 항상 NULL.
    env_change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 빌드/배포 실패 AI 진단 결과 (diagnose.Diagnosis의 JSON 문자열).
    # 실패하지 않았거나 AI_DIAGNOSE=false면 NULL — 프론트는 있을 때만 카드를 그린다.
    ai_analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 진단 진행 상태 — NULL(진단 대상 아님·기능 OFF·예산 초과) | "pending"(실패 커밋과 함께 표시,
    # 진단 중) | "done"(끝남 — ai_analysis가 비었으면 진단을 만들지 못한 것).
    # 프론트는 pending인 동안만 폴링을 이어 가 "분석 중" 자리를 진단으로 바꿔 끼운다.
    ai_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    logs: Mapped[str | None] = mapped_column(LONGTEXT, nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    # timezone-aware UTC 저장 — Pydantic이 응답 시 timezone offset 포함 ISO 출력 (B 컨벤션)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # tenant_id: user_id에서 파생되는 ns 이름. None이면 default ns로 fallback.
    # 컬럼이 아닌 property — user_id 진실원, 파생값 중복 저장 X.
    @property
    def tenant_id(self) -> str:
        if self.user_id is None:
            return config.DEFAULT_TENANT_NS
        return f"tenant-{self.user_id.hex[:8]}"

    # 라벨/이름에 박을 user_id 문자열. None은 "anonymous".
    @property
    def user_id_str(self) -> str:
        return self.user_id.hex if self.user_id else "anonymous"


# 빌드 행위의 영구 기록 (운영 분석용, append-only).
# builds 테이블은 delete_app 시 통째로 삭제되는 유저 대면 히스토리지만,
# 이 테이블은 의도적으로 안 지움 — "누가 언제 몇 번째 빌드를 얼마나 걸려 돌렸나"가
# 앱 삭제 후에도 남는다. API 미노출 — 운영자가 플랫폼 DB에서 직접 조회.
class BuildRecord(Base):
    __tablename__ = "build_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    build_id: Mapped[str] = mapped_column(String(8))     # builds row와 느슨한 연결 (FK 아님 — builds는 삭제될 수 있음)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    seq: Mapped[int] = mapped_column(Integer)            # 그 유저의 N번째 빌드 (1부터, 이 테이블 카운트 기준 — 앱 삭제에도 이어짐)
    app_name: Mapped[str] = mapped_column(String(50))
    runtime: Mapped[str] = mapped_column(String(20))
    build_mode: Mapped[str] = mapped_column(String(20))  # "dockerfile" | "auto"
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 단계별 소요시간 — 빌드 Pod 컨테이너 종료 정보(terminated.startedAt/finishedAt)에서 추출.
    # nixpacks는 auto 모드의 init container라 dockerfile 모드면 None.
    # 컨테이너가 비정상 종료/타임아웃으로 안 끝났으면 None (best-effort).
    nixpacks_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    buildkit_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)  # 시작→종료 전체 (배포/rollout 대기 포함)
    status: Mapped[str] = mapped_column(String(20), default="building")  # 최종: "running" | "failed" | "cancelled"
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # early-trigger + 레지스트리 캐시 계측 (config.BUILD_REGISTRY_CACHE_ENABLED / EARLY_TRIGGER_ENABLED).
    # 원칙: 원본 "시각"만 저장하고 구간(duration)은 쿼리/뷰에서 뺀다. 시각→구간은 언제든 파생되지만
    #   구간만 저장하면 원본과 어긋날 위험만 남는다(코드 바꿨을 때). 그래서 push_exposed_seconds 같은
    #   파생 컬럼은 두지 않는다 — 노출시간은 job_ended_at − push_done_at으로 뷰에서 뺀다.
    # cache_cold: 이 빌드가 import 캐시를 못 맞췄나 (로그에 "importing cache manifest" 없음 = clean 빌드).
    #   캐시 켠 직후엔 모든 앱의 "다음 빌드 한 번"이 cold — seq==1이 아니라 로그로 판정. 캐시 OFF면 None.
    # cache_export_failed: early-trigger로 배포는 성공했는데 뒤따르던 cache export(Job)가 실패/deadline.
    #   이미지는 이미 push됐으니 배포는 유효 — build.status는 안 건드리고 이 지표로만 남긴다. 캐시 OFF면 None.
    cache_cold: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    cache_export_failed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # ── early-trigger 타임라인 원본 시각 (started_at과 함께 5개; 모든 구간은 여기서 파생) ──
    # push_done_at: 이미지 push 완료(레지스트리 도착) 시각 = early-trigger 트리거 지점이자 계측점.
    #   플래그와 무관하게 마커 감지 시 기록(켜기 전 실측/대조군용). 마커 없으면 None.
    # job_ended_at: Job(=cache export) 종료 시각. job_ended_at − push_done_at = export 노출시간(파생).
    # deploy_started_at / deploy_ready_at: Deployment apply 직전 / rollout 완료(앱 뜸=사용자 대기 종료) 시각.
    #   deploy_ready_at − started_at = 사용자 체감 시간(파생).
    push_done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    job_ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deploy_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deploy_ready_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # triggered_early: early-trigger로 배포를 먼저 시작했나(True) / 폴백·플래그OFF(False). A/B 비교 축.
    triggered_early: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # ── AI 진단 호출 계측 (deploy/build/diagnose.py의 CallResult가 채움) ──
    # 진단을 안 부른 빌드(성공 / AI_DIAGNOSE=false)는 전부 NULL — llm_outcome IS NULL이
    # "애초에 안 불렀다", 값이 있으면 "불렀고 결말이 이것"이다.
    # 비용은 컬럼이 아니다 — 토큰 × 단가로 뷰에서 뺀다(원본만 저장하는 위 원칙 그대로).
    # 그래서 llm_model을 같이 남긴다: 모델을 갈아타면 단가가 바뀌므로 구간을 갈라야 한다.
    llm_model: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # "ok" | "length" | "refusal" | "parse_error" | "api_error" | "budget_exceeded"
    # length는 max_tokens에서 JSON이 잘려 진단이 조용히 빈 경우 — 로그 말고 이 축으로 센다.
    # budget_exceeded는 LLM_DAILY_TOKEN_BUDGET에 걸려 아예 호출하지 않은 경우 —
    # 호출이 없었으니 토큰·레이턴시는 NULL이지만, NULL outcome("애초에 안 부름")과는
    # 구분돼야 한다. 이 값의 건수가 곧 "비용 정책이 몇 번 발동했나"다.
    llm_outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)
    llm_prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Diagnosis.cause_category. builds.ai_analysis에도 들어 있지만 그쪽은 delete_app에
    # 지워지므로, 집계 축만 이 영구 테이블로 승격한다. outcome != "ok"면 NULL.
    llm_cause_category: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # cause_category ↔ kodeploy_specific 자기모순 — 골든셋 없이 재는 유일한 품질 신호.
    llm_inconsistent: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Grafana 패널이 전부 $__timeFilter(started_at)로 들어오고, admin 빌드 기록 목록도
    # ORDER BY started_at DESC LIMIT이다 — 두 읽기 경로가 같은 컬럼을 탄다.
    __table_args__ = (Index("ix_build_records_started_at", "started_at"),)
