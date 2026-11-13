"""DB 스냅샷 — 테넌트 DB(MySQL/PostgreSQL)를 빼고(export) 덤프를 적재(restore).

R2/Job/PVC 없이 v1: 기존 터미널과 동일하게 **DB 파드에 직접 exec**한다.
DB 타입은 ns 안의 파드를 보고 자동 감지(mysql 우선, 없으면 postgres).
- export : `mysqldump|gzip` 또는 `pg_dump|gzip` stdout을 스트림으로 다운로드.
- restore: 업로드 파일을 파드 /tmp에 stdin으로 쓴 뒤 `mysql`/`psql`로 적재 (파괴적).

자격증명은 {db}-secret에서 읽는다 (mysql=root, postgres=DB_USER).
exec WebSocket은 binary 프레임에 1바이트 채널 prefix(0=stdin,1=stdout,2=stderr)를 붙임.
stdin EOF는 ws close로 신호(터미널/`kubectl cp`와 동일한 방식).
"""

import asyncio
import base64
import hashlib
import json
import os
import re
import shlex
import time
import uuid

import aiohttp
from kubernetes_asyncio import client, config
from kubernetes_asyncio.stream import WsApiClient

from app import config as app_config

_RESTORE_PATH = "/tmp/kd_restore.dump"
_EXIT_MARK = "__KD_EXIT__"

# 배포 직전 폼에서 받은 초기 덤프를 mysql Ready까지 잠깐 보관하는 core 로컬 디렉토리.
# 복원 성공/실패 후 즉시 삭제 — 누적되지 않음.
_STAGE_DIR = "/tmp/kd_staged_dumps"
_TOKEN_RE = re.compile(r"[0-9a-f]{32}")

# 업로드(stage·복원) 덤프 최대 크기. 복원 시 DB 파드 /tmp에 잠깐 머무는 양이라
# 노드 디스크 보호용 상한. Dailo/일반 앱 덤프(수십 MB)엔 충분하고 여유도 둠.
# .sql.gz(압축)면 원본 기준 GB급까지 커버. 넘으면 거부 — 거대 업로드로 디스크 폭주 방지.
MAX_DUMP_BYTES = 200 * 1024 * 1024  # 200 MiB

_loaded = False


class SnapshotError(Exception):
    """사용자에게 그대로 보여줄 수 있는 스냅샷 작업 실패."""


# 청크 스트림을 감싸 누적 바이트가 MAX_DUMP_BYTES를 넘으면 즉시 중단(거부).
# stage(로컬 저장)·복원(파드 stdin) 양쪽 입력 경로에 적용 — 디스크 폭주 1차 차단.
async def _limited(chunks):
    total = 0
    async for chunk in chunks:
        total += len(chunk)
        if total > MAX_DUMP_BYTES:
            raise SnapshotError(
                f"덤프가 너무 큽니다 (최대 {MAX_DUMP_BYTES // (1024 * 1024)}MB)"
            )
        yield chunk


async def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    try:
        config.load_incluster_config()
    except config.ConfigException:
        await config.load_kube_config()
    _loaded = True


# 지원 DB별 메타. mysql 우선 감지 → 없으면 postgres.
_DB_ORDER = ("mysql", "postgres")
_DB_META = {
    "mysql":    {"container": "mysql",    "secret": "mysql-secret"},
    "postgres": {"container": "postgres", "secret": "postgres-secret"},
}


# 테넌트 ns에서 실행 중인 DB 파드를 자동 감지 + 자격증명/DB명 조회.
# 반환: dict(db_type, pod, container, user, pw, db) — 없으면 None.
async def _detect_target(core: client.CoreV1Api, ns: str):
    for db_type in _DB_ORDER:
        meta = _DB_META[db_type]
        pods = await core.list_namespaced_pod(
            namespace=ns, label_selector=f"app={db_type}",
        )
        running = [p for p in pods.items if p.status.phase == "Running"]
        if not running:
            continue
        pod = running[0].metadata.name
        secret = await core.read_namespaced_secret(name=meta["secret"], namespace=ns)

        def dec(key: str, default: str = "") -> str:
            v = secret.data.get(key) if secret.data else None
            return base64.b64decode(v).decode("utf-8") if v else default

        db = dec("DB_NAME", "app")
        if db_type == "mysql":
            user, pw = "root", dec("MYSQL_ROOT_PASSWORD")
        else:
            user, pw = dec("DB_USER", "app"), dec("DB_PASSWORD")
        return {
            "db_type": db_type, "pod": pod, "container": meta["container"],
            "user": user, "pw": pw, "db": db,
        }
    return None


# export용 dump 명령 (stdout = gzip 바이트). db_type별 분기.
def _dump_cmd(t: dict) -> list[str]:
    if t["db_type"] == "mysql":
        inner = (
            f"export MYSQL_PWD={shlex.quote(t['pw'])}; "
            f"exec mysqldump -u root --single-transaction --routines --triggers "
            f"--no-tablespaces {shlex.quote(t['db'])} | gzip -c"
        )
    else:  # postgres — --clean --if-exists로 복원 시 기존 객체 덮어쓰기 보장
        inner = (
            f"export PGPASSWORD={shlex.quote(t['pw'])}; "
            f"exec pg_dump -U {shlex.quote(t['user'])} --clean --if-exists "
            f"{shlex.quote(t['db'])} | gzip -c"
        )
    return ["/bin/bash", "-c", inner]


# restore용 적재 명령 (/tmp 덤프 → DB). gzip이면 풀어서, 아니면 그대로.
def _load_cmd(t: dict) -> list[str]:
    if t["db_type"] == "mysql":
        pre = f"export MYSQL_PWD={shlex.quote(t['pw'])}; "
        load = f"mysql -u root {shlex.quote(t['db'])}"
    else:
        pre = f"export PGPASSWORD={shlex.quote(t['pw'])}; "
        load = f"psql -v ON_ERROR_STOP=0 -U {shlex.quote(t['user'])} -d {shlex.quote(t['db'])}"
    return [
        "/bin/bash", "-c",
        f"{pre}set -o pipefail; "
        f"if gzip -t {_RESTORE_PATH} 2>/dev/null; then "
        f"  gunzip -c {_RESTORE_PATH} | {load} 2>&1; "
        f"else "
        f"  {load} < {_RESTORE_PATH} 2>&1; "
        f"fi; code=$?; rm -f {_RESTORE_PATH}; echo \"{_EXIT_MARK}:$code\"",
    ]


# export 전 사전 검증 — DB 파드 없으면 스트리밍 시작 전에 에러로 끊기 위함.
async def ensure_db(ns: str) -> None:
    if app_config.exec_agent_on(app_config.EXEC_AGENT_DUMP, ns):
        from app.exec import client as exec_client
        t = await exec_client.db_type(ns)
    else:
        await _ensure_loaded()
        async with client.ApiClient() as api:
            t = await _detect_target(client.CoreV1Api(api), ns)
    if not t:
        raise SnapshotError("DB가 활성화돼 있지 않습니다 — DB(MySQL/PostgreSQL)를 추가한 앱에서만 스냅샷이 가능합니다.")


# dump | gzip stdout을 청크로 yield (FastAPI StreamingResponse용).
# EXEC_AGENT_DUMP가 켜져 있고 ns가 EXEC_AGENT_NAMESPACES에 들면 kodeploy-exec 내부 문(dump)으로 받는다. 거기선 pipefail이 있고,
# 채널 3 exit code가 0이 아니면 예외로 끊는다 — 응답이 정상 종료되지 않아 잘린 파일이 완료로 저장되지 않는다.
async def export_stream(ns: str, *, user_id: str = "", app_id: str = ""):
    if app_config.exec_agent_on(app_config.EXEC_AGENT_DUMP, ns):
        from app.exec import client as exec_client
        try:
            async for chunk in exec_client.dump_stream(ns, user_id=user_id, app_id=app_id):
                yield chunk
        except exec_client.ExecError as e:
            raise SnapshotError(e.message) from e
        return

    await _ensure_loaded()
    async with client.ApiClient() as api:
        t = await _detect_target(client.CoreV1Api(api), ns)
    if not t:
        raise SnapshotError("DB 파드를 찾을 수 없습니다.")

    cmd = _dump_cmd(t)
    async with WsApiClient() as ws_api:
        cm = await client.CoreV1Api(ws_api).connect_get_namespaced_pod_exec(
            name=t["pod"], namespace=ns, container=t["container"], command=cmd,
            stderr=True, stdin=False, stdout=True, tty=False, _preload_content=False,
        )
        async with cm as k8s_ws:
            async for msg in k8s_ws:
                if msg.type == aiohttp.WSMsgType.BINARY and len(msg.data) > 1:
                    if msg.data[0] == 1:                  # stdout(=gzip 바이트)만 흘림
                        yield msg.data[1:]
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR,
                ):
                    break


# 업로드 청크를 파드 /tmp에 기록 (cat > file). 전송 후 ws close로 stdin EOF.
async def _write_dump(ws_api, pod: str, ns: str, container: str, chunks) -> None:
    cmd = ["/bin/bash", "-c", f"cat > {shlex.quote(_RESTORE_PATH)}"]
    cm = await client.CoreV1Api(ws_api).connect_get_namespaced_pod_exec(
        name=pod, namespace=ns, container=container, command=cmd,
        stderr=True, stdin=True, stdout=True, tty=False, _preload_content=False,
    )
    async with cm as k8s_ws:
        async for chunk in chunks:
            await k8s_ws.send_bytes(b"\x00" + chunk)   # 채널0=stdin
        await k8s_ws.close()                            # EOF 신호 → cat 종료


# exec 후 stdout+stderr를 끝까지 모아 텍스트로 반환 (stdin 없음 → EOF 문제 없음).
async def _exec_capture(ws_api, pod: str, ns: str, container: str, cmd: list[str]) -> str:
    cm = await client.CoreV1Api(ws_api).connect_get_namespaced_pod_exec(
        name=pod, namespace=ns, container=container, command=cmd,
        stderr=True, stdin=False, stdout=True, tty=False, _preload_content=False,
    )
    out = bytearray()
    async with cm as k8s_ws:
        async for msg in k8s_ws:
            if msg.type == aiohttp.WSMsgType.BINARY and len(msg.data) > 1:
                # 채널 prefix: 1=stdout, 2=stderr, 3=error/status(JSON).
                # 채널 3은 K8s가 보내는 종료 상태(예: {"status":"Success"})라 출력에서 제외 —
                # 안 그러면 __KD_EXIT__ 마커 뒤에 붙어 exit code 파싱이 깨짐.
                if msg.data[0] in (1, 2):
                    out += msg.data[1:]
            elif msg.type in (
                aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING,
                aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR,
            ):
                break
    return out.decode("utf-8", errors="replace")


# 업로드 덤프(.sql 또는 .sql.gz)를 테넌트 DB에 적재. 파괴적 — 호출 전 UI 확인 필수.
async def restore(ns: str, chunks) -> dict:
    await _ensure_loaded()
    async with client.ApiClient() as api:
        t = await _detect_target(client.CoreV1Api(api), ns)
    if not t:
        raise SnapshotError("DB가 활성화돼 있지 않습니다 — 복원할 DB가 없습니다.")

    run = _load_cmd(t)
    async with WsApiClient() as ws_api:
        await _write_dump(ws_api, t["pod"], ns, t["container"], _limited(chunks))
        output = await _exec_capture(ws_api, t["pod"], ns, t["container"], run)

    body, _, tail = output.rpartition(_EXIT_MARK)
    # tail은 ":<code>" 형태. 뒤에 잡다한 게 붙어도 첫 정수 토큰만 코드로 사용.
    code = tail.lstrip(":").strip().split()[0] if tail.strip() else ""
    body = body.strip()
    if code != "0":
        raise SnapshotError(body or f"복원 실패 (exit {code or '?'})")
    return {"ok": True, "output": body}


# --- 배포 시 자동 복원 (stage → 배포 완료 후 restore) -----------------------

def _staged_path(token: str) -> str:
    # 우리가 발급한 uuid hex(32)만 허용 — path traversal 차단.
    if not token or not _TOKEN_RE.fullmatch(token):
        raise SnapshotError("잘못된 초기 데이터 토큰")
    return os.path.join(_STAGE_DIR, f"{token}.dump")


# 업로드 덤프를 core 로컬 임시 디스크에 저장하고 토큰 반환.
# 배포 요청엔 이 토큰만 실어 보내고, mysql Ready 후 restore_staged로 소비.
# stage할 때 크기와 sha256을 같이 계산해 <token>.json에 둔다 — exec 복원의 기대값(경로의 size·sha256)이다.
async def stage_dump(chunks) -> str:
    os.makedirs(_STAGE_DIR, exist_ok=True)
    token = uuid.uuid4().hex
    path = os.path.join(_STAGE_DIR, f"{token}.dump")
    h, size = hashlib.sha256(), 0
    try:
        with open(path, "wb") as f:
            async for chunk in _limited(chunks):
                f.write(chunk)
                h.update(chunk)
                size += len(chunk)
    except SnapshotError:
        # 상한 초과 — 부분 저장분 삭제 후 그대로 전파 (디스크에 잔재 안 남김)
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    with open(_meta_path(path), "w") as f:
        json.dump({"size": size, "sha256": h.hexdigest()}, f)
    return token


def _meta_path(dump_path: str) -> str:
    return dump_path[: -len(".dump")] + ".json"


# stage 때 남긴 크기·sha256. 없으면(이 코드 전에 stage된 파일) 지금 계산한다.
def _staged_meta(path: str) -> tuple[int, str]:
    try:
        with open(_meta_path(path)) as f:
            meta = json.load(f)
        return int(meta["size"]), str(meta["sha256"])
    except (OSError, ValueError, KeyError):
        h, size = hashlib.sha256(), 0
        with open(path, "rb") as f:
            for b in iter(lambda: f.read(256 * 1024), b""):
                h.update(b)
                size += len(b)
        return size, h.hexdigest()


# stage된 임시 덤프 삭제 (복원 성공/실패/취소 무관 — 누적 방지).
def discard_staged(token: str) -> None:
    try:
        path = _staged_path(token)
    except SnapshotError:
        return
    for p in (path, _meta_path(path)):
        try:
            os.remove(p)
        except OSError:
            pass


# 테넌트 ns의 DB 파드(mysql/postgres)가 Running + Ready 될 때까지 폴링. 타임아웃이면 False.
async def wait_db_ready(ns: str, db_type: str, timeout: int = 180) -> bool:
    await _ensure_loaded()
    deadline = time.time() + timeout
    async with client.ApiClient() as api:
        core = client.CoreV1Api(api)
        while time.time() < deadline:
            try:
                pods = await core.list_namespaced_pod(
                    namespace=ns, label_selector=f"app={db_type}",
                )
            except Exception:
                await asyncio.sleep(5)
                continue
            for p in pods.items:
                if p.status and p.status.phase == "Running":
                    conds = p.status.conditions or []
                    if any(c.type == "Ready" and c.status == "True" for c in conds):
                        return True
            await asyncio.sleep(5)
    return False


# stage된 덤프를 복원하고 임시 파일을 제거 (성공/실패 무관).
# restore_via_exec(ns)면 kodeploy-exec 내부 문(restore 단계식)으로 보낸다. 업로드 복원도 먼저 stage를 거친다.
# wait_exec: 새 경로면 먼저 exec Pod가 Ready가 될 때까지 최대 이만큼(초) 기다린다 — 첫 배포의 초기 복원용.
# 시간이 지나도 Ready가 아니면 그래도 한 번 보내고, 실패하면 그 실패로 끝난다.
async def restore_staged(ns: str, token: str, *, user_id: str = "", app_id: str = "", wait_exec: float = 0) -> dict:
    path = _staged_path(token)
    if not os.path.exists(path):
        raise SnapshotError("초기 데이터 파일을 찾을 수 없습니다 (만료 또는 중복 소비)")

    if restore_via_exec(ns):
        try:
            if wait_exec:
                from app.exec import client as exec_client

                if not await exec_client.wait_ready(ns, wait_exec):
                    exec_client.log_event(event="exec_not_ready", ns=ns, op="restore", waited_s=wait_exec)
            return await _restore_via_exec(ns, path, user_id, app_id)
        finally:
            discard_staged(token)

    async def _file_chunks():
        with open(path, "rb") as f:
            while True:
                b = f.read(256 * 1024)
                if not b:
                    break
                yield b

    try:
        return await restore(ns, _file_chunks())
    finally:
        discard_staged(token)


# --- kodeploy-exec 복원 (EXEC_AGENT_RESTORE) ---------------------------------

def restore_via_exec(ns: str) -> bool:
    """EXEC_AGENT_RESTORE가 켜져 있고 ns가 EXEC_AGENT_NAMESPACES에 드는가 (업로드 복원 라우트도 같은 판단을 쓴다)."""
    return app_config.exec_agent_on(app_config.EXEC_AGENT_RESTORE, ns)


# db_changed=false인 실패(precheck·backup·upload·verify)면 다시 보낸다 (지시서 4-6).
# 이전 복원이 미해결 상태(unresolved_backups)면 다시 보내도 같은 거부라 보내지 않는다.
# exec에 연결하지 못한 실패는 다시 보내지 않는다 — 첫 배포처럼 exec가 막 뜨는 경우는 restore_staged(wait_exec)가 먼저 기다린다.
RESTORE_RETRIES = 2
_RETRY_DELAY = 5


async def _restore_via_exec(ns: str, path: str, user_id: str, app_id: str) -> dict:
    from app.exec import client as exec_client

    size, sha = _staged_meta(path)
    last: dict | None = None
    for attempt in range(1 + RESTORE_RETRIES):
        if attempt:
            await asyncio.sleep(_RETRY_DELAY)
        try:
            res = await exec_client.restore_file(ns, path, size, sha, user_id=user_id, app_id=app_id)
        except exec_client.ExecError as e:
            raise SnapshotError(e.message) from e
        if res.get("status") == "Success":
            return {"ok": True, "output": _restore_summary(res), "result": res}
        last = res
        if res.get("db_changed") or res.get("unresolved_backups"):
            break
    raise SnapshotError(_restore_failure(last or {}))


def _restore_summary(res: dict) -> str:
    tables = res.get("tables") or []
    lines = [f"복원 완료 — 표 {len(tables)}개"]
    lines += [f"{t.get('name')}: {t.get('rows')}행" for t in tables[:20]]
    if len(tables) > 20:
        lines.append(f"… 외 {len(tables) - 20}개")
    return "\n".join(lines)


def _restore_failure(res: dict) -> str:
    # 이전 복원이 결과를 몰라 남긴 백업이 있다: exec가 DB를 건드리지 않고 거부했다 (precheck).
    # 운영자가 hack/exec-e2e.md "복원 백업이 남았을 때"대로 정리하고 백업을 지우면 다시 복원할 수 있다.
    pending = [b for b in res.get("unresolved_backups") or [] if b.get("path")]
    if pending:
        where = ", ".join(f"{b.get('pod', '?')}:{b['path']}" for b in pending)
        return ("이전 복원이 미해결 상태라 새 복원을 하지 않았습니다 (DB는 바뀌지 않았습니다)"
                f"\n남은 백업: {where}"
                "\n운영자가 확인하고 백업을 정리해야 다시 복원할 수 있습니다")
    step = res.get("failed_step") or "?"
    detail = next((s.get("detail") for s in res.get("steps") or [] if s.get("name") == step), "")
    msg = f"복원 실패 ({step})" + (f": {detail}" if detail else "")
    if res.get("output"):
        msg += "\n" + res["output"]
    if res.get("db_changed"):
        rolled = any(s.get("name") == "rollback" and s.get("ok") for s in res.get("steps") or [])
        msg += "\n" + ("백업으로 되돌렸습니다" if rolled else "DB가 일부 바뀌었을 수 있습니다") + " (자동 재시도하지 않았습니다)"
    # 결과를 모르는 MySQL 복원(load 중 연결 끊김·rollback 실패): exec가 복원 전 백업을 DB 컨테이너에 남겼다.
    # 컨테이너 /tmp라 재시작하면 사라진다 — 수동 복구는 kodeploy hack/exec-e2e.md "복원 백업이 남았을 때".
    backup = res.get("backup") or {}
    if backup.get("path"):
        msg += (f"\n복원 결과를 알 수 없어 복원 전 백업을 남겼습니다: {backup.get('pod', '?')}:{backup['path']}"
                " — 운영자 확인이 필요합니다 (DB 컨테이너가 재시작되면 사라집니다)")
    return msg
