"""kodeploy-exec 내부 문 클라이언트.

core는 이 클라이언트로 테넌트 ns의 exec Pod(kodeploy-exec.<ns>.svc:8081)에 서명한 요청을 보낸다.
- 서명: app/builder/sign.py 그대로 (path는 쿼리를 뺀 원문). 그래서 작업 이름과 restore 기대값은 경로에 싣는다.
- 키: 테넌트 ns의 kodeploy-exec-hmac Secret (core가 ns를 만들 때 같이 만든다).
- 프레임: 첫 바이트가 채널 번호 (0 stdin, 1 stdout, 2 stderr, 3 종료 상태, 255 CLOSE).
- X-Kodeploy-Request-Id: 서명 밖 값이라 exec는 기록에만 쓴다. core 로그에도 같은 id를 남겨 두 기록을 잇는다.
"""

import asyncio
import base64
import hashlib
import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone

import aiohttp
from kubernetes_asyncio import client
from kubernetes_asyncio.client.exceptions import ApiException

from app.builder import sign
from app.deploy.stack.manifests.tenant import EXEC_HMAC_KEY, EXEC_HMAC_SECRET

PROTOCOL = "kodeploy.exec.v1"
HEADER_REQUEST_ID = "X-Kodeploy-Request-Id"
INTERNAL_PORT = 8081

# exec ops.QueryMaxBytes·BrowseMaxBytes와 같은 값. browse는 core가 SQL을 만든 직후 같은 값으로 검사한다.
QUERY_MAX_BYTES = 80000
BROWSE_MAX_BYTES = 128000

CH_STDIN, CH_STDOUT, CH_STDERR, CH_STATUS, CH_CLOSE = 0, 1, 2, 3, 255
_CHUNK = 256 * 1024
_KEY_TTL = 300.0
_TIMEOUT = aiohttp.ClientTimeout(total=None, sock_connect=10)

# exec close 코드 → 사용자에게 보여 줄 말 (exec proto.go)
_CLOSE_TEXT = {
    1003: "exec 프로토콜 오류 (텍스트 프레임)",
    1006: "exec 연결이 끊겼습니다",
    1011: "exec를 시작하지 못했습니다",
    4400: "입력이 너무 깁니다",
    4401: "티켓이 없거나 만료됐습니다",
    4403: "허용되지 않은 작업입니다",
    4404: "대상 Pod가 없습니다 — 앱(또는 DB)이 실행 중인지 확인하세요",
    4408: "유휴 시간이 지나 연결을 닫았습니다",
    4413: "최대 실행 시간을 넘었습니다",
    4429: "동시에 열 수 있는 세션 수를 넘었습니다 — 잠시 뒤 다시 시도하세요",
    4503: "exec 서비스가 재시작 중입니다 — 잠시 뒤 다시 시도하세요",
}


class ExecError(Exception):
    """exec 호출 실패. code는 WebSocket close 코드(있으면), http_status는 core가 돌려줄 HTTP 상태."""

    def __init__(self, message: str, *, code: int | None = None, http_status: int = 502):
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status


def _close_error(code: int | None) -> ExecError:
    status = {4404: 404, 4429: 429, 4400: 400, 4503: 503}.get(code or 0, 502)
    return ExecError(_CLOSE_TEXT.get(code or 0, f"exec 세션이 비정상 종료됐습니다 ({code})"), code=code, http_status=status)


# --- core 쪽 기록 (exec 감사 기록과 같은 필드 이름) ---------------------------
# core는 로깅 설정이 없어 root가 WARNING이다. 발급·세션 기록은 따로 stdout에 한 줄 JSON으로 남긴다.
_log = logging.getLogger("kodeploy.exec")
if not _log.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(message)s"))
    _log.addHandler(_h)
    _log.setLevel(logging.INFO)
    _log.propagate = False


def log_event(**fields) -> None:
    _log.info(json.dumps(fields, ensure_ascii=False, separators=(",", ":")))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_request_id() -> str:
    return uuid.uuid4().hex


def base_url(ns: str) -> str:
    return f"http://kodeploy-exec.{ns}.svc:{INTERNAL_PORT}"


# --- 키 ---------------------------------------------------------------------
_keys: dict[str, tuple[bytes, float]] = {}


async def _ensure_loaded() -> None:
    from app.deploy.console.snapshots import _ensure_loaded as load

    await load()


async def _key(ns: str) -> bytes:
    hit = _keys.get(ns)
    if hit and time.monotonic() - hit[1] < _KEY_TTL:
        return hit[0]
    await _ensure_loaded()
    async with client.ApiClient() as api:
        try:
            secret = await client.CoreV1Api(api).read_namespaced_secret(name=EXEC_HMAC_SECRET, namespace=ns)
        except ApiException as e:
            raise ExecError("exec 키(kodeploy-exec-hmac)를 읽지 못했습니다", http_status=502) from e
    raw = (secret.data or {}).get(EXEC_HMAC_KEY)
    if not raw:
        raise ExecError("exec 키(kodeploy-exec-hmac)가 비어 있습니다", http_status=502)
    key = base64.b64decode(raw)
    _keys[ns] = (key, time.monotonic())
    return key


def _headers(key: bytes, method: str, path: str, body: bytes, request_id: str) -> dict[str, str]:
    return {**sign.headers(key, method, path, body), HEADER_REQUEST_ID: request_id}


# DB 종류 — 파서·SQL 인용 규칙을 고르려고 core가 알아야 한다. pods list만 쓴다 (Secret을 읽지 않는다).
# exec가 고르는 규칙과 같다: app=mysql Running이 있으면 mysql, 없으면 postgres.
async def db_type(ns: str) -> str | None:
    await _ensure_loaded()
    async with client.ApiClient() as api:
        core = client.CoreV1Api(api)
        for kind in ("mysql", "postgres"):
            pods = await core.list_namespaced_pod(namespace=ns, label_selector=f"app={kind}")
            if any(p.status and p.status.phase == "Running" for p in pods.items):
                return kind
    return None


# exec Pod가 Ready인지 — 첫 배포의 초기 복원은 exec Pod와 DB가 같이 뜨는 중에 시작된다. pods list만 쓴다.
EXEC_SELECTOR = "kodeploy.com/component=exec"
READY_TIMEOUT = 120.0
_READY_POLL = 2.0


def _pod_ready(p) -> bool:
    if p.metadata is not None and p.metadata.deletion_timestamp is not None:
        return False                                             # 종료 중 (SIGTERM 뒤 503)
    if not p.status or p.status.phase != "Running":
        return False
    return any(c.type == "Ready" and c.status == "True" for c in p.status.conditions or [])


async def wait_ready(ns: str, timeout: float = READY_TIMEOUT) -> bool:
    """ns의 kodeploy-exec Pod 하나가 Ready가 될 때까지 기다린다. timeout 안에 안 되면 False."""
    await _ensure_loaded()
    deadline = time.monotonic() + timeout
    async with client.ApiClient() as api:
        core = client.CoreV1Api(api)
        while True:
            try:
                pods = await core.list_namespaced_pod(namespace=ns, label_selector=EXEC_SELECTOR)
                if any(_pod_ready(p) for p in pods.items):
                    return True
            except ApiException:
                pass                                             # 잠깐의 apiserver 오류는 다음 확인에서
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(min(_READY_POLL, max(0.0, deadline - time.monotonic())))


# --- 티켓 (공개 문용) ----------------------------------------------------------

async def issue_ticket(ns: str, *, user_id: str, app_id: str, app_name: str, target: str, op: str,
                       cols: int, rows: int) -> dict:
    rid = new_request_id()
    path = "/internal/v1/tickets"
    body = json.dumps({"user_id": user_id, "app_id": app_id, "app": app_name, "target": target,
                       "op": op, "cols": cols, "rows": rows}, separators=(",", ":")).encode()
    key = await _key(ns)
    headers = {**_headers(key, "POST", path, body, rid), "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as http:
            async with http.post(base_url(ns) + path, data=body, headers=headers) as resp:
                text = await resp.text()
                status = resp.status
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        raise ExecError("exec 서비스에 연결할 수 없습니다 — 이 앱의 kodeploy-exec가 떠 있는지 확인하세요") from e
    if status != 201:
        if status == 401:
            _keys.pop(ns, None)                                  # 키가 바뀌었을 수 있다 — 다음 호출은 새로 읽는다
        msg = {404: "대상 Pod가 없습니다 — 앱(또는 DB)이 실행 중인지 확인하세요",
               429: "발급 대기 중인 티켓이 너무 많습니다 — 잠시 뒤 다시 시도하세요",
               503: "exec 서비스가 재시작 중입니다 — 잠시 뒤 다시 시도하세요"}.get(status, f"exec 티켓 발급 실패 ({status})")
        raise ExecError(msg, http_status=status if status in (400, 404, 429, 503) else 502)
    data = json.loads(text)
    log_event(event="exec_ticket", ns=ns, request_id=rid,
              ticket_sha256=hashlib.sha256(data["ticket"].encode()).hexdigest(),
              user_id=user_id, app_id=app_id, target=target, op=op, issued_at=_now())
    return data


# --- 내부 세션 -----------------------------------------------------------------

def parse_exit(payload: bytes | None) -> int:
    """채널 3 Status에서 exit code (dbquery._parse_exit와 같은 규칙). 상태가 없으면 1."""
    if payload is None:
        return 1
    try:
        status = json.loads(payload.decode("utf-8", errors="replace"))
    except ValueError:
        return 1
    if status.get("status") == "Success":
        return 0
    for cause in (status.get("details") or {}).get("causes") or []:
        if cause.get("reason") == "ExitCode":
            try:
                return int(cause.get("message", "1"))
            except (ValueError, TypeError):
                return 1
    return 1


class _Session:
    """내부 세션 하나: 서명 → 업그레이드 → (stdin 보내기 ∥ 프레임 받기) → close 코드."""

    def __init__(self, ns: str, path: str, op: str, user_id: str, app_id: str):
        self.ns, self.path, self.op = ns, path, op
        self.user_id, self.app_id = user_id, app_id
        self.rid = new_request_id()
        self.started = time.monotonic()
        self._http: aiohttp.ClientSession | None = None
        self.ws: aiohttp.ClientWebSocketResponse | None = None

    async def __aenter__(self):
        key = await _key(self.ns)
        self._http = aiohttp.ClientSession(timeout=_TIMEOUT)
        try:
            self.ws = await self._http.ws_connect(
                base_url(self.ns) + self.path,
                headers=_headers(key, "GET", self.path, b"", self.rid),
                protocols=(PROTOCOL,),
                autoping=True,
                max_msg_size=8 << 20,
            )
        except aiohttp.WSServerHandshakeError as e:
            await self._http.close()
            if e.status == 401:
                _keys.pop(self.ns, None)
            raise ExecError(f"exec 세션을 열지 못했습니다 ({e.status})",
                            http_status=503 if e.status == 503 else 502) from e
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            await self._http.close()
            raise ExecError("exec 서비스에 연결할 수 없습니다 — 이 앱의 kodeploy-exec가 떠 있는지 확인하세요") from e
        return self

    async def __aexit__(self, *exc):
        if self.ws is not None and not self.ws.closed:
            await self.ws.close()
        if self._http is not None:
            await self._http.close()

    async def receive(self):
        """다음 프레임 (ch, data) 또는 close 코드(int)."""
        while True:
            msg = await self.ws.receive()
            if msg.type == aiohttp.WSMsgType.BINARY:
                if msg.data:
                    return msg.data[0], msg.data[1:]
                continue
            if msg.type == aiohttp.WSMsgType.CLOSE:
                return int(msg.data)
            if msg.type in (aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                return self.ws.close_code or 1006

    async def send_stdin(self, chunks) -> None:
        """stdin을 보내고 CLOSE(255, 0)로 닫는다. exec가 먼저 닫으면(실패 결과) 조용히 멈춘다."""
        try:
            async for chunk in chunks:
                if chunk:
                    await self.ws.send_bytes(bytes([CH_STDIN]) + chunk)
            await self.ws.send_bytes(bytes([CH_CLOSE, CH_STDIN]))
        except (ConnectionError, aiohttp.ClientError, RuntimeError):
            pass

    def log(self, close_code: int, exit_code: int | None, **extra) -> None:
        log_event(event="exec_session", ns=self.ns, request_id=self.rid, user_id=self.user_id,
                  app_id=self.app_id, op=self.op, close_code=close_code, exit_code=exit_code,
                  duration_ms=int((time.monotonic() - self.started) * 1000), **extra)


async def _bytes_chunks(data: bytes):
    for i in range(0, len(data), _CHUNK):
        yield data[i:i + _CHUNK]


async def _exchange(s: _Session, chunks, on_frame) -> int:
    sender = asyncio.create_task(s.send_stdin(chunks)) if chunks is not None else None
    try:
        while True:
            got = await s.receive()
            if isinstance(got, int):
                return got
            on_frame(*got)
    finally:
        if sender is not None:
            sender.cancel()
            try:
                await sender
            except asyncio.CancelledError:
                pass


async def run_capture(ns: str, op: str, sql: str, *, user_id: str = "", app_id: str = "") -> tuple[str, str, int]:
    """query·browse: SQL을 stdin으로 보내고 stdout·stderr·exit code를 모은다 (dbquery._exec와 같은 반환)."""
    out, err = bytearray(), bytearray()
    status: list[bytes] = []

    def on(ch: int, data: bytes) -> None:
        if ch == CH_STDOUT:
            out.extend(data)
        elif ch == CH_STDERR:
            err.extend(data)
        elif ch == CH_STATUS:
            status.append(data)

    async with _Session(ns, f"/internal/v1/sessions/{op}", op, user_id, app_id) as s:
        code = await _exchange(s, _bytes_chunks(sql.encode("utf-8")), on)
        exit_code = parse_exit(status[0]) if status else None
        s.log(code, exit_code)
    if code != 1000 or exit_code is None:
        raise _close_error(code)
    return out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace"), exit_code


async def dump_stream(ns: str, *, user_id: str = "", app_id: str = ""):
    """dump: gzip 바이트를 흘린다. 채널 3 exit code가 0이 아니거나 비정상 종료면 예외로 끊는다
    (StreamingResponse가 정상 종료하지 않아 브라우저가 잘린 파일을 완료로 받지 않는다)."""
    status: list[bytes] = []
    code = 1006
    async with _Session(ns, "/internal/v1/sessions/dump", "dump", user_id, app_id) as s:
        try:
            while True:
                got = await s.receive()
                if isinstance(got, int):
                    code = got
                    break
                ch, data = got
                if ch == CH_STDOUT:
                    yield data
                elif ch == CH_STATUS:
                    status.append(data)
        finally:
            s.log(code, parse_exit(status[0]) if status else None)
    if code != 1000:
        raise _close_error(code)
    exit_code = parse_exit(status[0]) if status else 1
    if exit_code != 0:
        raise ExecError(f"덤프 실패 (exit {exit_code})")


async def restore_file(ns: str, path: str, size: int, sha256: str, *, user_id: str = "", app_id: str = "") -> dict:
    """restore: 파일을 보내고 단계식 결과(exec restore.Result)를 돌려준다. 실패도 결과로 온다."""
    result: list[bytes] = []

    async def chunks():
        with open(path, "rb") as f:
            while True:
                b = await asyncio.to_thread(f.read, _CHUNK)
                if not b:
                    break
                yield b

    def on(ch: int, data: bytes) -> None:
        if ch == CH_STATUS:
            result.append(data)

    async with _Session(ns, f"/internal/v1/sessions/restore/{size}/{sha256}", "restore", user_id, app_id) as s:
        code = await _exchange(s, chunks(), on)
        parsed = None
        if result:
            try:
                parsed = json.loads(result[0])
            except ValueError:
                parsed = None
        backup = (parsed or {}).get("backup") or {}
        extra = {"backup_pod": backup.get("pod"), "backup_path": backup.get("path")} if backup.get("path") else {}
        pending = [b.get("path") for b in (parsed or {}).get("unresolved_backups") or [] if b.get("path")]
        if pending:
            extra["unresolved_backups"] = pending
        s.log(code, 0 if parsed and parsed.get("status") == "Success" else 1, **extra)
    if parsed is None:
        raise _close_error(code)
    return parsed
