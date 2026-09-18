"""Cloudflare R2 오브젝트 스토리지 프로비저닝 (레벨 2: 앱당 버킷 + bucket-scoped 토큰).

mysql/redis는 K8s 워크로드라 매니페스트로 띄우지만, R2는 **외부(Cloudflare) 리소스**다.
그래서 이 모듈은 K8s가 아니라 Cloudflare REST API를 호출한다. 산출물은 딱 하나 —
S3 호환 자격증명을 담은 dict. service.py가 그걸 `r2-secret` K8s Secret으로 만들어
앱 Pod에 envFrom으로 주입한다 (mysql-secret과 동일한 흡수 방식).

레벨 2 격리 모델:
- 앱마다 버킷 1개 (`kd-{app_name}`). R2 버킷 한도 100만 → 앱당 버킷은 사실상 무한.
- 버킷마다 **그 버킷에만 스코프된 API 토큰** 발급 → 테넌트 간 객체 접근 차단.
- 토큰의 id가 곧 S3 Access Key ID, 토큰 value의 SHA-256(hex)이 Secret Access Key
  (Cloudflare의 공식 파생 규칙).

⚠️ 이 모듈의 Cloudflare API 호출(엔드포인트/권한그룹/토큰→S3키 파생)은 실제 토큰으로
   한 번 검증이 필요하다. 계정/토큰이 비어 있으면 storage 토글은 ValueError로 막힌다.

참고 문서:
- 버킷 CRUD: POST/DELETE /accounts/{acct}/r2/buckets
- managed dev 도메인: PUT /accounts/{acct}/r2/buckets/{bucket}/domains/managed
- 계정 토큰: POST/DELETE /accounts/{acct}/tokens (R2 권한그룹 + 버킷 리소스 스코프)
"""

import datetime
import hashlib
import hmac
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlparse

import httpx

from app import config

_API_BASE = "https://api.cloudflare.com/client/v4"


class R2Error(Exception):
    """사용자에게 그대로 노출 가능한 R2 프로비저닝 실패."""


# 설정이 갖춰졌는지 (토글 활성 가능 여부). router/service에서 사전 차단용.
def is_configured() -> bool:
    return bool(config.CF_API_TOKEN and config.CF_ACCOUNT_ID)


def _require_configured() -> None:
    if not is_configured():
        raise R2Error(
            "R2가 설정되지 않았습니다 (CF_API_TOKEN / CF_ACCOUNT_ID 미설정)"
        )


# app_name → R2 버킷 이름. "kd-" 접두사로 (1) 최소 길이(R2는 3자 이상) 보장,
# (2) KoDeploy가 만든 버킷임을 식별 (orphan 정리/검색 용이).
def bucket_name(app_name: str) -> str:
    return f"kd-{app_name}"


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=f"{_API_BASE}/accounts/{config.CF_ACCOUNT_ID}",
        headers={
            "Authorization": f"Bearer {config.CF_API_TOKEN}",
            "Content-Type": "application/json",
        },
        timeout=30.0,
    )


# CF API 응답 봉투 검사 — success=false면 errors를 사람이 읽을 메시지로.
def _unwrap(resp: httpx.Response) -> dict:
    try:
        body = resp.json()
    except ValueError:
        raise R2Error(f"CF API 응답 파싱 실패 (status={resp.status_code})")
    if not body.get("success", False):
        errs = body.get("errors") or [{"message": resp.text}]
        msg = "; ".join(e.get("message", str(e)) for e in errs)
        raise R2Error(f"CF API 오류: {msg}")
    return body.get("result") or {}


# --- 버킷 ---

# 버킷 생성 (idempotent — 이미 있으면 통과). 같은 앱 재배포 시 데이터 보존.
def _create_bucket(client: httpx.Client, bucket: str) -> None:
    resp = client.post("/r2/buckets", json={"name": bucket})
    if resp.status_code == 409:
        return  # 이미 존재 — mysql StatefulSet 409 skip과 동일 철학
    _unwrap(resp)


# 버킷 삭제 (best-effort). R2는 빈 버킷만 삭제 가능 — 객체가 남아 있으면 실패한다.
# v1에서는 토큰 revoke로 접근 자체를 끊는 게 1차 격리이고, 비어 있으면 버킷도 정리.
# 비어있지 않아 실패하면 swallow (orphan 버킷은 백로그의 sweeper가 처리).
def _delete_bucket(client: httpx.Client, bucket: str) -> None:
    resp = client.delete(f"/r2/buckets/{bucket}")
    if resp.status_code in (200, 404):
        return
    # 비어있지 않음(409 등) — 데이터 보존 우선, 조용히 둠.


# managed dev 도메인(pub-*.r2.dev) 활성화 → 공개 URL 반환. 실패하면 None.
# 운영용 커스텀 도메인(cdn.kodeploy.com 등)은 백로그 — v1 비공개 테스트엔 r2.dev로 충분.
def _enable_public_url(client: httpx.Client, bucket: str) -> str | None:
    try:
        resp = client.put(
            f"/r2/buckets/{bucket}/domains/managed",
            json={"enabled": True},
        )
        result = _unwrap(resp)
    except R2Error:
        return None
    domain = result.get("domain")
    if not domain:
        return None
    return domain if domain.startswith("http") else f"https://{domain}"


# --- 스코프 토큰 ---

# R2 읽기+쓰기 권한그룹 id 목록. 운영에서 env로 명시(CF_R2_PERMISSION_GROUP_IDS)했으면
# 그걸 쓰고, 아니면 /tokens/permission_groups에서 이름으로 자동 탐색.
def _r2_permission_group_ids(client: httpx.Client) -> list[str]:
    if config.CF_R2_PERMISSION_GROUP_IDS:
        return list(config.CF_R2_PERMISSION_GROUP_IDS)
    resp = client.get("/tokens/permission_groups")
    groups = _unwrap(resp)
    if not isinstance(groups, list):
        raise R2Error("권한그룹 목록 형식이 예상과 다릅니다")
    # 이름에 "R2"가 들어가고 읽기/쓰기인 그룹만. (object read/write)
    wanted = []
    for g in groups:
        name = (g.get("name") or "")
        if "R2" in name and ("Write" in name or "Read" in name):
            wanted.append(g["id"])
    if not wanted:
        raise R2Error(
            "R2 권한그룹을 찾지 못했습니다 (CF_R2_PERMISSION_GROUP_IDS로 명시 필요)"
        )
    return wanted


# 버킷 1개에만 스코프된 계정 토큰 발급 → (token_id, secret_access_key) 반환.
# CF 규칙: token_id = S3 Access Key ID, secret = SHA-256(token_value) hex.
def _create_scoped_token(
    client: httpx.Client, bucket: str, pg_ids: list[str]
) -> tuple[str, str]:
    # 버킷 리소스 스코프 키 — jurisdiction 기본값 "default".
    resource_key = (
        f"com.cloudflare.edge.r2.bucket."
        f"{config.CF_ACCOUNT_ID}_default_{bucket}"
    )
    body = {
        "name": f"kodeploy-r2-{bucket}",
        "policies": [
            {
                "effect": "allow",
                "permission_groups": [{"id": pid} for pid in pg_ids],
                "resources": {resource_key: "*"},
            }
        ],
    }
    resp = client.post("/tokens", json=body)
    result = _unwrap(resp)
    token_id = result.get("id")
    token_value = result.get("value")
    if not token_id or not token_value:
        raise R2Error("토큰 발급 응답에 id/value가 없습니다")
    secret_access_key = hashlib.sha256(token_value.encode()).hexdigest()
    return token_id, secret_access_key


def _delete_token(client: httpx.Client, token_id: str) -> None:
    resp = client.delete(f"/tokens/{token_id}")
    if resp.status_code in (200, 404):
        return
    # 그 외 실패는 조용히 — 정리 실패가 앱 삭제 흐름을 막지 않게.


# --- 고수준 API (service.py가 호출) ---

# 앱 Pod에 주입할 S3 호환 env dict 빌더. 키 이름은 generic S3_* + AWS_* 별칭 둘 다 —
# AWS SDK(Dailo)·boto3·기타 OSS가 추가 설정 없이 자격증명을 읽도록.
def _build_env(token_id: str, secret_key: str, bucket: str, public_url: str) -> dict[str, str]:
    return {
        # generic (KoDeploy 표준 규약)
        "S3_ENDPOINT": config.R2_S3_ENDPOINT,
        "S3_REGION": "auto",                 # R2는 region "auto"
        "S3_ACCESS_KEY": token_id,
        "S3_SECRET_KEY": secret_key,
        "S3_BUCKET": bucket,
        "S3_PUBLIC_BASE_URL": public_url,
        # AWS SDK / boto3 표준 env 별칭 — 코드 수정 없이 자격증명 자동 인식
        "AWS_ACCESS_KEY_ID": token_id,
        "AWS_SECRET_ACCESS_KEY": secret_key,
        "AWS_DEFAULT_REGION": "auto",
        "AWS_REGION": "auto",
        "AWS_ENDPOINT_URL_S3": config.R2_S3_ENDPOINT,
        "AWS_ENDPOINT_URL": config.R2_S3_ENDPOINT,
    }


# storage 활성 시 앱에 주입되는 env 키 — _build_env에서 파생(드리프트 0). 예약 키 검증용.
# (R2 키는 템플릿이 아니라 provision 산출물이라 mysql/redis와 달리 여기서 키를 뽑는다.)
INJECTED_ENV_KEYS = frozenset(_build_env("", "", "", ""))


# 앱용 R2 버킷 + 스코프 토큰 프로비저닝. 앱 Pod에 주입할 S3 호환 env dict 반환.
# 재배포마다 호출돼도 안전: 버킷은 idempotent, 토큰은 새로 발급(옛 토큰은 _apply_storage가
# 교체 전 정리).
def provision(app_name: str) -> tuple[dict[str, str], str]:
    _require_configured()
    bucket = bucket_name(app_name)
    with _client() as client:
        _create_bucket(client, bucket)
        public_url = _enable_public_url(client, bucket) or ""
        pg_ids = _r2_permission_group_ids(client)
        token_id, secret_key = _create_scoped_token(client, bucket, pg_ids)

    return _build_env(token_id, secret_key, bucket, public_url), token_id


# 토큰 revoke (+ 빈 버킷이면 삭제). 토글 off / 앱 삭제 시 호출.
# bucket=None이면 버킷은 보존하고 토큰만 정리 (토글 off의 "데이터 보존" 정책).
def deprovision(token_id: str | None, bucket: str | None = None) -> None:
    if not is_configured():
        return
    with _client() as client:
        if token_id:
            _delete_token(client, token_id)
        if bucket:
            _delete_bucket(client, bucket)


# --- S3 데이터플레인 (객체 목록/삭제) ------------------------------------------
# 위 함수들은 CF 관리 API(account 스코프, CF_API_TOKEN)로 버킷/토큰을 다룬다.
# 여기서부터는 **앱당 스코프 토큰의 S3 자격증명**(r2-secret에 든 env)으로 버킷 안
# 객체를 직접 list/delete 한다. boto3 같은 SDK 대신 SigV4를 직접 서명 — 이미 쓰는
# httpx만으로 끝나고(추가 의존성 0), r2.py/domains.py의 "REST 직접 호출" 스타일과 일치.
# service.py가 r2-secret을 K8s에서 읽어 env dict를 넘긴다.

_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


# AWS SigV4 서명 키 파생 (date → region → service → aws4_request 체인).
def _signing_key(secret: str, date_stamp: str, region: str, service: str) -> bytes:
    k_date = _sign(("AWS4" + secret).encode("utf-8"), date_stamp)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, service)
    return _sign(k_service, "aws4_request")


# 버킷 스코프 S3 자격증명(env)으로 SigV4 서명해 (url, headers)를 만든다. GET/DELETE만 — body 없음.
# key 있으면 객체 경로(/{bucket}/{key}), 없으면 버킷 경로(/{bucket}, list용).
# 요청 실행과 분리해 둔 이유: 미리보기(get_object_text)는 같은 서명으로 **스트리밍**해서
# 앞부분만 읽고 끊어야 한다 — 서명 코드를 두 벌로 두면 한쪽만 고치는 사고가 난다.
def _sign_request(
    env: dict, method: str, key: str = "", query: dict | None = None,
) -> tuple[str, dict]:
    endpoint = (env.get("S3_ENDPOINT") or "").rstrip("/")
    bucket = env.get("S3_BUCKET") or ""
    access = env.get("S3_ACCESS_KEY") or ""
    secret = env.get("S3_SECRET_KEY") or ""
    if not (endpoint and bucket and access and secret):
        raise R2Error("S3 자격증명이 불완전합니다 (r2-secret 확인)")

    host = urlparse(endpoint).netloc
    region, service = "auto", "s3"

    # canonical URI — '/'는 보존, 그 외 비예약문자는 %인코딩 (S3 SigV4 규칙).
    path = f"/{bucket}" + (f"/{key}" if key else "")
    canonical_uri = quote(path, safe="/")

    # canonical query — 키 정렬, 값은 '/' 포함 전부 인코딩(continuation-token에 / = 들어감).
    query = query or {}
    canonical_qs = "&".join(
        f"{quote(k, safe='')}={quote(str(v), safe='')}"
        for k, v in sorted(query.items())
    )

    now = datetime.datetime.now(datetime.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    canonical_headers = (
        f"host:{host}\n"
        f"x-amz-content-sha256:{_EMPTY_SHA256}\n"
        f"x-amz-date:{amz_date}\n"
    )
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join([
        method, canonical_uri, canonical_qs,
        canonical_headers, signed_headers, _EMPTY_SHA256,
    ])

    scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amz_date, scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    signature = hmac.new(
        _signing_key(secret, date_stamp, region, service),
        string_to_sign.encode("utf-8"), hashlib.sha256,
    ).hexdigest()

    headers = {
        "Authorization": (
            f"AWS4-HMAC-SHA256 Credential={access}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
        "x-amz-content-sha256": _EMPTY_SHA256,
        "x-amz-date": amz_date,
    }
    url = endpoint + canonical_uri + (f"?{canonical_qs}" if canonical_qs else "")
    return url, headers


def _s3_request(
    env: dict, method: str, key: str = "", query: dict | None = None,
    timeout: float = 30.0,
) -> httpx.Response:
    url, headers = _sign_request(env, method, key=key, query=query)
    with httpx.Client(timeout=timeout) as client:
        return client.request(method, url, headers=headers)


# 네임스페이스 무시하고 로컬 태그명만 (R2 XML이 s3 네임스페이스를 달고 옴).
def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# ListObjectsV2 XML → {objects, next}. public_base 있으면 객체별 공개 URL도 채운다.
def _parse_list_xml(text: str, public_base: str) -> dict:
    base = (public_base or "").rstrip("/")
    root = ET.fromstring(text)
    objects: list[dict] = []
    next_token = None
    is_truncated = False
    for el in root:
        name = _localname(el.tag)
        if name == "Contents":
            d = {_localname(c.tag): (c.text or "") for c in el}
            k = d.get("Key", "")
            size = d.get("Size", "0")
            objects.append({
                "key": k,
                "size": int(size) if size.isdigit() else 0,
                "last_modified": d.get("LastModified", ""),
                "url": f"{base}/{quote(k, safe='/')}" if base else None,
            })
        elif name == "NextContinuationToken":
            next_token = el.text
        elif name == "IsTruncated":
            is_truncated = (el.text or "").strip().lower() == "true"
    return {"objects": objects, "next": next_token if is_truncated else None}


# 버킷 객체 목록 (페이지당 limit개). token으로 다음 페이지 이어받기.
def list_objects(
    env: dict, continuation_token: str | None = None, limit: int = 200,
) -> dict:
    query = {"list-type": "2", "max-keys": str(limit)}
    if continuation_token:
        query["continuation-token"] = continuation_token
    resp = _s3_request(env, "GET", query=query)
    if resp.status_code != 200:
        raise R2Error(f"객체 목록 조회 실패 (status={resp.status_code})")
    return _parse_list_xml(resp.text, env.get("S3_PUBLIC_BASE_URL", ""))


# 객체 1개 삭제 (멱등 — 404도 성공 취급).
def delete_object(env: dict, key: str) -> None:
    if not key:
        raise R2Error("삭제할 key가 필요합니다")
    resp = _s3_request(env, "DELETE", key=key)
    if resp.status_code not in (200, 204, 404):
        raise R2Error(f"객체 삭제 실패 (status={resp.status_code})")


# 미리보기로 읽어올 최대 바이트. 미리보기는 "앞부분을 읽는 것"이지 파일을 통째로 들고
# 오는 게 아니다 — 로그처럼 수백 MB인 파일이 core 메모리와 응답을 통째로 먹으면 안 된다.
PREVIEW_MAX_BYTES = 256 * 1024


# 객체 앞부분을 텍스트로 읽는다 → (내용, 잘렸나).
# Range 헤더 대신 스트리밍으로 받아 상한에서 끊는다: Range를 쓰려면 서명 대상 헤더가 늘어
# _sign_request를 건드려야 하는데, 그 함수는 list/delete가 함께 쓰는 자리다.
# 바이너리를 잘못 열어도 죽지 않게 errors="replace"로 디코드한다(유저 자기 버킷의 파일이다).
def get_object_text(
    env: dict, key: str, max_bytes: int = PREVIEW_MAX_BYTES,
) -> tuple[str, bool]:
    if not key:
        raise R2Error("읽을 key가 필요합니다")
    url, headers = _sign_request(env, "GET", key=key)
    buf, truncated = bytearray(), False
    with httpx.Client(timeout=30.0) as client:
        with client.stream("GET", url, headers=headers) as resp:
            if resp.status_code == 404:
                raise R2Error("파일을 찾을 수 없습니다")
            if resp.status_code != 200:
                raise R2Error(f"파일 읽기 실패 (status={resp.status_code})")
            for chunk in resp.iter_bytes():
                buf += chunk
                if len(buf) >= max_bytes:
                    truncated = True
                    break
    return buf[:max_bytes].decode("utf-8", errors="replace"), truncated
