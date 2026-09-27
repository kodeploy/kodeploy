"""배포 입력 검증 — static/volume 필드 · dep 시크릿 예약 env 키. (빌드 소스 검증은 source.py)"""

import re

from app.deploy.stack import manifests, r2

# static 빌드 입력 검증/정규화. 보안 경계 아님(유저는 어차피 자기 이미지 빌드 내용을 전부
# 통제) — 개행 등으로 생성 Dockerfile이 조용히 깨져 정체불명 빌드 에러가 되는 걸 막는 친절벨트.
_OUTPUT_DIR_PATTERN = re.compile(r"^[A-Za-z0-9._/-]+$")


# 빌드 타임 변수 검증 — 키는 셸/Dockerfile 호환 형식(env.py와 동일 규칙), 값은 한 줄 텍스트.
# 값이 번들에 박혀 공개되는 입력이라 보안 경계는 아니고, ENV 줄이 조용히 깨지는 것만 방지.
_ENV_KEY_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_BUILD_ENV_MAX_KEYS = 20
_BUILD_ENV_MAX_VALUE = 1000


def _validate_static_env(env: dict[str, str]) -> dict[str, str]:
    if len(env) > _BUILD_ENV_MAX_KEYS:
        raise ValueError(f"빌드 타임 변수는 최대 {_BUILD_ENV_MAX_KEYS}개까지입니다")
    out: dict[str, str] = {}
    for k, v in env.items():
        k = k.strip()
        if not _ENV_KEY_PATTERN.match(k):
            raise ValueError(f"변수 이름 형식 위배: {k} (대문자/숫자/_ 만, 영문 대문자나 _로 시작)")
        if len(v) > _BUILD_ENV_MAX_VALUE:
            raise ValueError(f"{k} 값이 너무 깁니다 (최대 {_BUILD_ENV_MAX_VALUE}자)")
        if any(ch in v for ch in "\n\r"):
            raise ValueError(f"{k} 값에 줄바꿈은 쓸 수 없습니다")
        out[k] = v
    return out


def _validate_static_fields(build_cmd: str, output_dir: str) -> tuple[str, str]:
    build_cmd = (build_cmd or "").strip()
    if "\n" in build_cmd or "\r" in build_cmd:
        raise ValueError("빌드 커맨드에 줄바꿈은 쓸 수 없습니다 (&&로 이어주세요)")
    if len(build_cmd) > 300:
        raise ValueError("빌드 커맨드가 너무 깁니다 (최대 300자)")
    output_dir = (output_dir or "").strip().strip("/")
    if output_dir and (not _OUTPUT_DIR_PATTERN.match(output_dir) or ".." in output_dir):
        raise ValueError("출력 디렉토리 경로가 올바르지 않습니다 (예: dist, build)")
    if build_cmd and not output_dir:
        output_dir = "dist"
    return build_cmd, output_dir


# 로컬 볼륨 입력 검증/정규화 — 영속저장소 "local" 모드.
# mount_path는 절대경로 belt(보안 경계 아님 — 유저가 자기 이미지를 통제), storage_class는 DNS 라벨,
# size는 K8s quantity 형식. 형식이 깨져 PVC가 admission 거부되는 정체불명 실패를 사전 차단.
_VOLUME_MOUNT_PATTERN = re.compile(r"^/[A-Za-z0-9._/-]+$")
_STORAGE_CLASS_PATTERN = re.compile(r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$")
_VOLUME_SIZE_PATTERN = re.compile(r"^[1-9][0-9]*(Mi|Gi|Ti)$")


def _validate_volume_fields(
    mount_path: str, storage_class: str, size: str,
) -> tuple[str, str, str]:
    mp = (mount_path or "").strip().rstrip("/")  # 뒤 슬래시 정리 (앞 슬래시는 절대경로라 보존)
    if not mp or ".." in mp or not _VOLUME_MOUNT_PATTERN.match(mp):
        raise ValueError("마운트 경로는 절대경로여야 합니다 (예: /var/www/html/data)")
    sc = (storage_class or "local-path").strip()
    if not _STORAGE_CLASS_PATTERN.match(sc):
        raise ValueError("storage class 이름 형식이 올바르지 않습니다 (예: local-path)")
    sz = (size or "5Gi").strip()
    if not _VOLUME_SIZE_PATTERN.match(sz):
        raise ValueError("볼륨 크기 형식이 올바르지 않습니다 (예: 5Gi, 512Mi)")
    return mp, sc, sz


# --- 예약 env 키 (dep 시크릿이 자동 주입하는 키) ----------------------------------
# 유저 env가 이 키와 충돌하면 (Option A로 유저 값이 이기므로) 관리형 연결이 조용히 깨진다.
# 그래서 dep이 켜졌을 때 그 키를 거절한다. 키 목록은 하드코딩하지 않고 dep 템플릿을 렌더해
# 파생 — 템플릿이 진실원이라 키를 더해도 자동 반영. 정적(테넌트/런타임 무관)이라 1회 캐시.
_DEP_KEY_CACHE: dict[str, frozenset[str]] = {}


def _dep_secret_keys(kind: str) -> frozenset[str]:
    if kind not in _DEP_KEY_CACHE:
        if kind == "mysql":
            docs = manifests.mysql(tenant_id="_", user_id="_")
        elif kind == "postgres":
            docs = manifests.postgres(tenant_id="_", user_id="_")
        elif kind == "redis":
            docs = manifests.redis(tenant_id="_", user_id="_")
        else:
            docs = []
        _DEP_KEY_CACHE[kind] = frozenset(
            k for d in docs if d.get("kind") == "Secret"
            for k in (d.get("stringData") or {})
        )
    return _DEP_KEY_CACHE[kind]


# 선택된 dep들이 주입하는 예약 키 합집합 (POST 검증용).
def reserved_env_keys(db_type: str, use_redis: bool, use_storage: bool) -> set[str]:
    keys: set[str] = set()
    if db_type in ("mysql", "postgres"):
        keys |= _dep_secret_keys(db_type)
    if use_redis:
        keys |= _dep_secret_keys("redis")
    if use_storage:
        keys |= set(r2.INJECTED_ENV_KEYS)
    return keys


# dep별 주입 키 전체 맵 (프론트 인라인 검증용 — 토글에 따라 프론트가 로컬에서 합집합 계산).
def reserved_env_keys_map() -> dict[str, list[str]]:
    return {
        "mysql": sorted(_dep_secret_keys("mysql")),
        "postgres": sorted(_dep_secret_keys("postgres")),
        "redis": sorted(_dep_secret_keys("redis")),
        "storage": sorted(r2.INJECTED_ENV_KEYS),
    }
