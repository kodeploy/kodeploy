"""kodeploy-exec 연결 고정 — 내부 문 클라이언트와 플래그 경로.

가짜 exec 내부 문(aiohttp 서버)을 띄워 서명·경로·서브프로토콜·프레임·close 코드를 실제 연결로 확인한다.
query·browse는 같은 출력이 오면 옛 경로와 결과가 같아야 한다(같은 SQL, 같은 파서).
"""

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest
from aiohttp import web

from app import config
from app.builder import sign
from app.deploy.console import dbbrowse, dbquery, snapshots
from app.exec import client as exec_client

KEY = b"k" * 32
NS = "tenant-0a1b2c3d"


@pytest.fixture(autouse=True)
def canary(monkeypatch):
    """테스트 ns를 카나리 목록에 넣는다 (플래그를 켠 테스트가 새 경로로 가게)."""
    monkeypatch.setattr(config, "EXEC_AGENT_NAMESPACES", frozenset({NS}))


class FakeExec:
    """exec 내부 문 흉내. op마다 돌려줄 프레임·close 코드를 정해 둔다."""

    def __init__(self):
        self.calls = []                 # (method, path, headers, body/stdin)
        self.reply = {}                 # op → (frames[(ch, bytes)], close_code)
        self.ticket_status = 201

    def _verified(self, req, body: bytes) -> bool:
        return sign.verify(KEY, req.headers.get(sign.HEADER_TIMESTAMP), req.headers.get(sign.HEADER_SIGNATURE),
                           req.method, req.rel_url.raw_path, body)

    async def tickets(self, req):
        body = await req.read()
        self.calls.append(("POST", req.rel_url.raw_path, dict(req.headers), body))
        if not self._verified(req, body):
            return web.Response(status=401)
        if self.ticket_status != 201:
            return web.Response(status=self.ticket_status)
        return web.json_response({"ticket": "T" * 43, "expires_at": "2026-11-12T00:00:30Z"}, status=201)

    async def session(self, req):
        if not self._verified(req, b""):
            return web.Response(status=401)
        ws = web.WebSocketResponse(protocols=(exec_client.PROTOCOL,))
        await ws.prepare(req)
        op = req.match_info.get("op", "restore")
        stdin = bytearray()
        if op != "dump":
            async for msg in ws:
                data = msg.data
                if data[:1] == b"\x00":
                    stdin += data[1:]
                elif data == bytes([255, 0]):
                    break
        self.calls.append(("WS", req.rel_url.raw_path, dict(req.headers), bytes(stdin)))
        frames, code = self.reply[op if op in self.reply else "restore"]
        for ch, payload in frames:
            await ws.send_bytes(bytes([ch]) + payload)
        await ws.close(code=code)
        return ws


def status(code: int) -> bytes:
    if code == 0:
        return json.dumps({"status": "Success"}).encode()
    return json.dumps({"status": "Failure", "details": {"causes": [{"reason": "ExitCode", "message": str(code)}]}}).encode()


def run(fake: FakeExec, coro_fn, monkeypatch):
    """가짜 서버를 띄우고 그 주소로 클라이언트를 돌린다."""

    async def main():
        app = web.Application()
        app.router.add_post("/internal/v1/tickets", fake.tickets)
        app.router.add_get("/internal/v1/sessions/{op}", fake.session)
        app.router.add_get("/internal/v1/sessions/restore/{size}/{sha}", fake.session)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        monkeypatch.setattr(exec_client, "base_url", lambda ns: f"http://127.0.0.1:{port}")
        try:
            return await coro_fn()
        finally:
            await runner.cleanup()

    return asyncio.run(main())


@pytest.fixture
def wired(monkeypatch):
    """키·DB 종류·core 기록을 가짜로 바꾼다. kind를 바꾸면 db_type이 따라간다."""
    state = SimpleNamespace(kind="mysql", events=[])

    async def key(ns):
        return KEY

    async def db_type(ns):
        return state.kind

    monkeypatch.setattr(exec_client, "_key", key)
    monkeypatch.setattr(exec_client, "db_type", db_type)
    monkeypatch.setattr(exec_client, "log_event", lambda **f: state.events.append(f))
    return state


# --- 티켓 ----------------------------------------------------------------------

def test_issue_ticket_is_signed_and_logged(wired, monkeypatch):
    fake = FakeExec()
    res = run(fake, lambda: exec_client.issue_ticket(
        NS, user_id="u1", app_id="a1", app_name="web", target="app", op="shell", cols=120, rows=32), monkeypatch)
    assert res["ticket"] == "T" * 43
    _, path, headers, body = fake.calls[0]
    assert path == "/internal/v1/tickets"
    assert json.loads(body) == {"user_id": "u1", "app_id": "a1", "app": "web", "target": "app",
                                "op": "shell", "cols": 120, "rows": 32}
    rid = headers[exec_client.HEADER_REQUEST_ID]
    ev = wired.events[0]
    # exec 감사 기록과 같은 필드 이름 + request_id. 티켓 원문은 남기지 않는다
    assert ev["event"] == "exec_ticket" and ev["request_id"] == rid
    assert ev["ticket_sha256"] == hashlib.sha256(b"T" * 43).hexdigest()
    assert {k: ev[k] for k in ("user_id", "app_id", "target", "op")} == {"user_id": "u1", "app_id": "a1", "target": "app", "op": "shell"}
    assert ev["issued_at"] and "T" * 43 not in json.dumps(ev)


@pytest.mark.parametrize("status_code, http_status", [(404, 404), (429, 429), (401, 502), (500, 502)])
def test_issue_ticket_errors(wired, monkeypatch, status_code, http_status):
    fake = FakeExec()
    fake.ticket_status = status_code
    with pytest.raises(exec_client.ExecError) as e:
        run(fake, lambda: exec_client.issue_ticket(
            NS, user_id="u", app_id="a", app_name="web", target="db", op="console", cols=80, rows=24), monkeypatch)
    assert e.value.http_status == http_status


# --- query: 옛 경로와 같은 결과 ------------------------------------------------

MYSQL_OUT = b"id\tname\n1\t\xed\x95\x9c\xea\xb8\x80\n2\ta\\tb\n"
PG_OUT = 'id,name\n1,한글\n2,"a,b"\n'.encode()


@pytest.mark.parametrize("kind, out", [("mysql", MYSQL_OUT), ("postgres", PG_OUT)])
def test_query_matches_old_path(wired, monkeypatch, kind, out):
    sql = "SELECT * FROM t WHERE a = '한글'"

    # 옛 경로: core가 직접 exec (가짜 _exec가 같은 출력을 낸다)
    async def detect(core, ns):
        return {"db_type": kind, "pod": "p", "container": kind, "user": "app", "pw": "x", "db": "app"}

    sent_old = []

    async def old_exec(ns, t, cmd):
        sent_old.append(cmd[-1])
        return out.decode(), "", 0

    async def noop():
        return None

    monkeypatch.setattr(dbquery, "_detect_target", detect)
    monkeypatch.setattr(dbquery, "_ensure_loaded", noop)
    monkeypatch.setattr(dbquery, "_exec", old_exec)
    monkeypatch.setattr(dbquery.client, "ApiClient", lambda: _NullCtx())
    monkeypatch.setattr(config, "EXEC_AGENT_QUERY", False)
    old = asyncio.run(dbquery.run_query(NS, sql))

    # 새 경로: exec 내부 문
    wired.kind = kind
    monkeypatch.setattr(config, "EXEC_AGENT_QUERY", True)
    fake = FakeExec()
    fake.reply["query"] = ([(1, out), (3, status(0))], 1000)
    new = run(fake, lambda: dbquery.run_query(NS, sql, user_id="u1", app_id="a1"), monkeypatch)

    old.pop("duration_ms"), new.pop("duration_ms")
    assert new == old
    _, path, headers, stdin = fake.calls[0]
    assert path == "/internal/v1/sessions/query"
    assert headers["Sec-WebSocket-Protocol"] == exec_client.PROTOCOL
    # 같은 SQL(페이징 감싸기까지)이 옛 경로에선 인자로, 새 경로에선 stdin으로 갔다
    assert stdin.decode().startswith("SELECT * FROM (\n" + sql + "\n) AS _kd_page")
    assert sent_old and "_kd_page" in sent_old[0]
    ev = wired.events[-1]
    assert ev["event"] == "exec_session" and ev["op"] == "query" and ev["request_id"] == headers[exec_client.HEADER_REQUEST_ID]
    assert ev["user_id"] == "u1" and ev["app_id"] == "a1" and sql not in json.dumps(ev)


class _NullCtx:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *a):
        return False


def test_query_error_and_close_codes(wired, monkeypatch):
    monkeypatch.setattr(config, "EXEC_AGENT_QUERY", True)
    fake = FakeExec()
    fake.reply["query"] = ([(2, b"ERROR 1064 (42000): syntax"), (3, status(1))], 1000)
    with pytest.raises(dbquery.QueryError, match="ERROR 1064"):
        run(fake, lambda: dbquery.run_query(NS, "SELEC 1"), monkeypatch)

    fake.reply["query"] = ([], 4429)
    with pytest.raises(dbquery.QueryError, match="동시에 열 수 있는 세션"):
        run(fake, lambda: dbquery.run_query(NS, "SELECT 1"), monkeypatch)


# --- browse ---------------------------------------------------------------------

def test_browse_matches_old_path(wired, monkeypatch):
    spec = {"table": "items", "schema": None, "columns": ["a"], "filters": [], "sort": [], "offset": 0}
    out = b"a\nx\ny\n"

    async def target(ns):
        return {"db_type": "mysql", "pw": "x", "db": "app", "user": "app"}

    async def old_exec(ns, t, cmd):
        return out.decode(), "", 0

    monkeypatch.setattr(config, "EXEC_AGENT_BROWSE", False)
    monkeypatch.setattr(dbbrowse, "_exec", old_exec)
    real_target = dbbrowse._target
    monkeypatch.setattr(dbbrowse, "_target", target)
    old = asyncio.run(dbbrowse.browse(NS, spec))
    monkeypatch.setattr(dbbrowse, "_target", real_target)

    monkeypatch.setattr(config, "EXEC_AGENT_BROWSE", True)
    fake = FakeExec()
    fake.reply["browse"] = ([(1, out), (3, status(0))], 1000)
    new = run(fake, lambda: dbbrowse.browse(NS, spec), monkeypatch)
    old.pop("duration_ms"), new.pop("duration_ms")
    assert new == old
    assert fake.calls[0][1] == "/internal/v1/sessions/browse"
    assert fake.calls[0][3].decode() == dbbrowse.build_select("mysql", spec)[1]


@pytest.mark.parametrize("flag", [False, True])
def test_browse_sql_limit_is_a_user_error(monkeypatch, flag):
    # 컬럼 300개 × 긴 이름이면 SQL이 128000바이트를 넘는다 — exec 전에 400(QueryError)
    called = []

    async def target(ns):
        return {"db_type": "postgres"}

    async def never(*a, **k):
        called.append(a)
        raise AssertionError("must not reach exec")

    monkeypatch.setattr(config, "EXEC_AGENT_BROWSE", flag)
    monkeypatch.setattr(dbbrowse, "_target", target)
    monkeypatch.setattr(dbbrowse, "_exec", never)
    monkeypatch.setattr(exec_client, "run_capture", never)
    # core 한도(컬럼 300개, 이름 128자, 조건 10개 × 값 1000자) 안의 최악: 인용 때 두 배가 되는 따옴표 이름
    spec = {"table": "t", "schema": None, "columns": [('"' * 120) + str(i) for i in range(300)],
            "filters": [{"column": "c", "op": "eq", "value": "'" * 1000}] * 10, "sort": [], "offset": 0}
    with pytest.raises(dbquery.QueryError, match="128000"):
        asyncio.run(dbbrowse.browse(NS, spec))
    assert not called


# --- dump -----------------------------------------------------------------------

async def _collect(gen):
    out = bytearray()
    async for b in gen:
        out += b
    return bytes(out)


def test_dump_streams_and_fails_on_exit_code(wired, monkeypatch):
    monkeypatch.setattr(config, "EXEC_AGENT_DUMP", True)
    fake = FakeExec()
    fake.reply["dump"] = ([(1, b"\x1f\x8bpart1"), (1, b"part2"), (3, status(0))], 1000)
    got = run(fake, lambda: _collect(snapshots.export_stream(NS, user_id="u", app_id="a")), monkeypatch)
    assert got == b"\x1f\x8bpart1part2"

    # mysqldump 실패(pipefail로 exit 2) → 스트림이 정상 종료되지 않고 예외로 끊긴다
    fake.reply["dump"] = ([(1, b"\x1f\x8bpartial"), (3, status(2))], 1000)
    with pytest.raises(snapshots.SnapshotError, match="exit 2"):
        run(fake, lambda: _collect(snapshots.export_stream(NS)), monkeypatch)


# --- restore --------------------------------------------------------------------

def _stage(monkeypatch, tmp_path, data: bytes) -> str:
    monkeypatch.setattr(snapshots, "_STAGE_DIR", str(tmp_path))

    async def chunks():
        yield data

    return asyncio.run(snapshots.stage_dump(chunks()))


def _result(status_text, failed=None, db_changed=False):
    return json.dumps({"status": status_text, "failed_step": failed, "db_changed": db_changed,
                       "steps": [{"name": failed or "check", "ok": status_text == "Success", "detail": "d"}],
                       "tables": [{"name": "users", "rows": 3}], "output": ""}).encode()


def test_restore_staged_sends_expected_size_and_sha(wired, monkeypatch, tmp_path):
    data = b"CREATE TABLE users (id int);\n"
    token = _stage(monkeypatch, tmp_path, data)
    meta = json.loads((tmp_path / f"{token}.json").read_text())
    assert meta == {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    monkeypatch.setattr(config, "EXEC_AGENT_RESTORE", True)
    fake = FakeExec()
    fake.reply["restore"] = ([(3, _result("Success"))], 1000)
    res = run(fake, lambda: snapshots.restore_staged(NS, token, user_id="u", app_id="a"), monkeypatch)
    assert res["ok"] and "users: 3행" in res["output"]
    _, path, _, stdin = fake.calls[0]
    assert path == f"/internal/v1/sessions/restore/{len(data)}/{hashlib.sha256(data).hexdigest()}"
    assert stdin == data
    assert not list(tmp_path.iterdir())                          # 덤프·메타 둘 다 지웠다


def test_restore_retries_only_while_db_unchanged(wired, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "EXEC_AGENT_RESTORE", True)
    monkeypatch.setattr(snapshots, "_RETRY_DELAY", 0)
    fake = FakeExec()

    fake.reply["restore"] = ([(3, _result("Failure", "upload", db_changed=False))], 1000)
    token = _stage(monkeypatch, tmp_path, b"x")
    with pytest.raises(snapshots.SnapshotError, match="upload"):
        run(fake, lambda: snapshots.restore_staged(NS, token), monkeypatch)
    assert len(fake.calls) == 3                                  # 처음 + 재시도 2회

    fake.calls.clear()
    fake.reply["restore"] = ([(3, _result("Failure", "check", db_changed=True))], 1000)
    token = _stage(monkeypatch, tmp_path, b"x")
    with pytest.raises(snapshots.SnapshotError, match="자동 재시도하지 않았습니다"):
        run(fake, lambda: snapshots.restore_staged(NS, token), monkeypatch)
    assert len(fake.calls) == 1


def test_restore_kept_backup_is_reported(wired, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "EXEC_AGENT_RESTORE", True)
    res = json.loads(_result("Failure", "load", db_changed=True))
    res["backup"] = {"pod": "mysql-0", "container": "mysql",
                     "path": "/tmp/kodeploy-restore/backup-20261113T000000Z-abcdef.sql.gz"}
    fake = FakeExec()
    fake.reply["restore"] = ([(3, json.dumps(res).encode())], 1000)
    token = _stage(monkeypatch, tmp_path, b"x")
    with pytest.raises(snapshots.SnapshotError) as e:
        run(fake, lambda: snapshots.restore_staged(NS, token), monkeypatch)
    msg = str(e.value)
    assert "복원 전 백업을 남겼습니다: mysql-0:/tmp/kodeploy-restore/backup-20261113T000000Z-abcdef.sql.gz" in msg
    assert "자동 재시도하지 않았습니다" in msg and len(fake.calls) == 1
    ev = wired.events[-1]
    assert ev["backup_pod"] == "mysql-0" and ev["backup_path"].endswith("abcdef.sql.gz")


# 이전 복원이 미해결(남은 백업)이면 exec가 precheck에서 거부한다: 다시 보내지 않고, 남은 백업 위치를 알린다
def test_restore_refused_while_previous_unresolved(wired, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "EXEC_AGENT_RESTORE", True)
    monkeypatch.setattr(snapshots, "_RETRY_DELAY", 0)
    res = json.loads(_result("Failure", "precheck", db_changed=False))
    old = "/tmp/kodeploy-restore/backup-20261112T000000Z-123456.sql.gz"
    res["unresolved_backups"] = [{"pod": "mysql-0", "container": "mysql", "path": old}]
    fake = FakeExec()
    fake.reply["restore"] = ([(3, json.dumps(res).encode())], 1000)
    token = _stage(monkeypatch, tmp_path, b"x")
    with pytest.raises(snapshots.SnapshotError) as e:
        run(fake, lambda: snapshots.restore_staged(NS, token), monkeypatch)
    msg = str(e.value)
    assert msg.startswith("이전 복원이 미해결 상태") and "DB는 바뀌지 않았습니다" in msg
    assert f"mysql-0:{old}" in msg
    assert len(fake.calls) == 1                                  # db_changed=false여도 재시도하지 않는다
    assert wired.events[-1]["unresolved_backups"] == [old]
    assert not list(tmp_path.iterdir())                          # stage 파일은 지웠다


# 초기 복원: 고정 재시도 대신 exec Pod Ready를 기다린 뒤 한 번 보낸다. 연결 실패는 다시 보내지 않는다
def test_initial_restore_waits_for_exec_then_sends_once(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "EXEC_AGENT_RESTORE", True)
    monkeypatch.setattr(snapshots, "_RETRY_DELAY", 0)
    order, events = [], []

    async def wait_ready(ns, timeout):
        order.append(("wait", ns, timeout))
        return state["ready"]

    async def restore_file(ns, path, size, sha, **who):
        order.append(("send", ns))
        if not state["up"]:
            raise exec_client.ExecError("exec 서비스에 연결할 수 없습니다")
        return json.loads(_result("Success"))

    monkeypatch.setattr(exec_client, "wait_ready", wait_ready)
    monkeypatch.setattr(exec_client, "restore_file", restore_file)
    monkeypatch.setattr(exec_client, "log_event", lambda **f: events.append(f))

    state = {"ready": True, "up": True}
    token = _stage(monkeypatch, tmp_path, b"x")
    assert asyncio.run(snapshots.restore_staged(NS, token, wait_exec=120))["ok"]
    assert order == [("wait", NS, 120), ("send", NS)]

    # 120초 안에 Ready가 아니어도 한 번은 보낸다. 그래도 연결이 안 되면 재시도 없이 실패
    order.clear()
    state.update(ready=False, up=False)
    token = _stage(monkeypatch, tmp_path, b"x")
    with pytest.raises(snapshots.SnapshotError, match="연결할 수 없습니다"):
        asyncio.run(snapshots.restore_staged(NS, token, wait_exec=120))
    assert order == [("wait", NS, 120), ("send", NS)]
    assert events[-1]["event"] == "exec_not_ready" and not list(tmp_path.glob(f"{token}*"))

    # 업로드 복원(wait_exec 없음)은 기다리지 않는다
    order.clear()
    state.update(ready=True, up=True)
    token = _stage(monkeypatch, tmp_path, b"x")
    asyncio.run(snapshots.restore_staged(NS, token))
    assert order == [("send", NS)]


def test_wait_ready_reads_pod_conditions(monkeypatch):
    def pod(phase="Running", ready="True", deleting=False):
        return SimpleNamespace(
            metadata=SimpleNamespace(deletion_timestamp="2026-11-13T00:00:00Z" if deleting else None),
            status=SimpleNamespace(phase=phase, conditions=[SimpleNamespace(type="Ready", status=ready)]))

    polls = [[], [pod(ready="False")], [pod(deleting=True)], [pod()]]
    seen = []

    class Core:
        def __init__(self, api):
            pass

        async def list_namespaced_pod(self, namespace, label_selector):
            seen.append((namespace, label_selector))
            return SimpleNamespace(items=polls[min(len(seen) - 1, len(polls) - 1)])

    async def noop():
        return None

    monkeypatch.setattr(exec_client, "_ensure_loaded", noop)
    monkeypatch.setattr(exec_client.client, "ApiClient", lambda: _NullCtx())
    monkeypatch.setattr(exec_client.client, "CoreV1Api", Core)
    monkeypatch.setattr(exec_client, "_READY_POLL", 0)
    assert asyncio.run(exec_client.wait_ready(NS, 5))
    assert len(seen) == 4 and seen[0] == (NS, "kodeploy.com/component=exec")

    seen.clear()
    polls[:] = [[pod(ready="False")]]
    assert not asyncio.run(exec_client.wait_ready(NS, 0.05))


def test_flags_off_by_default(monkeypatch):
    monkeypatch.undo()                                           # canary fixture 전 값
    for name in ("EXEC_AGENT_TERMINAL", "EXEC_AGENT_QUERY", "EXEC_AGENT_BROWSE", "EXEC_AGENT_DUMP", "EXEC_AGENT_RESTORE"):
        assert getattr(config, name) is False, name
    assert config.EXEC_AGENT_NAMESPACES == frozenset()          # 기본은 어느 ns도 새 경로로 가지 않는다


# 카나리: 플래그가 켜져 있어도 EXEC_AGENT_NAMESPACES에 든 ns만 새 경로
def test_exec_agent_on_namespaces(monkeypatch):
    monkeypatch.setattr(config, "EXEC_AGENT_NAMESPACES", frozenset({NS}))
    assert config.exec_agent_on(True, NS) and not config.exec_agent_on(True, "tenant-ffffffff")
    assert not config.exec_agent_on(False, NS)
    monkeypatch.setattr(config, "EXEC_AGENT_NAMESPACES", frozenset({"*"}))
    assert config.exec_agent_on(True, "tenant-ffffffff") and not config.exec_agent_on(False, NS)
    monkeypatch.setattr(config, "EXEC_AGENT_NAMESPACES", frozenset())
    assert not config.exec_agent_on(True, NS)


def test_flag_on_outside_canary_uses_old_path(wired, monkeypatch):
    monkeypatch.setattr(config, "EXEC_AGENT_NAMESPACES", frozenset({"tenant-ffffffff"}))
    for name in ("EXEC_AGENT_QUERY", "EXEC_AGENT_BROWSE", "EXEC_AGENT_DUMP", "EXEC_AGENT_RESTORE"):
        monkeypatch.setattr(config, name, True)
    called = []

    async def db_type(ns):
        called.append(ns)
        return "mysql"

    async def detect(core, ns):
        return None                                              # 옛 경로: DB 없음으로 끝낸다

    async def noop():
        return None

    monkeypatch.setattr(exec_client, "db_type", db_type)
    for mod in (dbquery, dbbrowse, snapshots):
        monkeypatch.setattr(mod, "_detect_target", detect)
        monkeypatch.setattr(mod, "_ensure_loaded", noop)
        monkeypatch.setattr(mod.client, "ApiClient", lambda: _NullCtx())
    with pytest.raises(dbquery.QueryError, match="DB가 활성화돼 있지 않습니다"):
        asyncio.run(dbquery.run_query(NS, "SELECT 1"))
    with pytest.raises(dbquery.QueryError, match="DB가 활성화돼 있지 않습니다"):
        asyncio.run(dbbrowse._target(NS))
    with pytest.raises(snapshots.SnapshotError, match="DB가 활성화돼 있지 않습니다"):
        asyncio.run(snapshots.ensure_db(NS))
    assert not snapshots.restore_via_exec(NS) and snapshots.restore_via_exec("tenant-ffffffff")
    assert called == []                                          # exec 쪽은 부르지 않았다


# --- POST /apps/{id}/exec-tickets ------------------------------------------------

@pytest.fixture
def ticket_api(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.exec import router as exec_router

    state = SimpleNamespace(access=None, issued=[], audited=[])
    app_row = SimpleNamespace(id="11111111-1111-1111-1111-111111111111", name="web", namespace=NS, owner_id="o")

    async def issue(ns, **kw):
        state.issued.append((ns, kw))
        return {"ticket": "TK", "expires_at": "x"}

    monkeypatch.setattr(exec_router.sharing, "access_for", lambda db, user, app_id: state.access)
    monkeypatch.setattr(exec_router.exec_client, "issue_ticket", issue)
    monkeypatch.setattr(exec_router.audit, "record", lambda db, user, action, app=None: state.audited.append(action))
    monkeypatch.setattr(config, "EXEC_AGENT_TERMINAL", True)
    monkeypatch.setattr(config, "ALLOWED_ORIGINS", ["https://kodeploy.com"])
    api = FastAPI()
    api.include_router(exec_router.router)
    api.dependency_overrides[exec_router.get_current_user] = lambda: SimpleNamespace(id="u1")
    api.dependency_overrides[exec_router.get_db] = lambda: None
    state.client = TestClient(api)
    state.app = app_row
    return state


URL = "/apps/11111111-1111-1111-1111-111111111111/exec-tickets"
ORIGIN = {"Origin": "https://kodeploy.com"}


def test_exec_ticket_owner(ticket_api):
    ticket_api.access = (ticket_api.app, "owner", False)
    r = ticket_api.client.post(URL, json={"target": "db", "cols": 100, "rows": 30}, headers=ORIGIN)
    assert r.status_code == 200
    assert r.json() == {"ticket": "TK", "url": f"wss://api.kodeploy.com/exec/{NS}/v1/sessions",
                        "protocol": "kodeploy.exec.v1", "expires_in": 30}
    ns, kw = ticket_api.issued[0]
    assert ns == NS and kw["op"] == "console" and kw["app_name"] == "web" and kw["user_id"] == "u1"
    assert ticket_api.audited == []                              # 일반 발급은 admin_actions에 남기지 않는다


def test_exec_ticket_rules(ticket_api, monkeypatch):
    c = ticket_api.client
    ticket_api.access = (ticket_api.app, "owner", False)
    assert c.post(URL, json={"target": "app"}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post(URL, json={"target": "app"}).status_code == 403          # Origin 없음
    assert c.post(URL, json={"target": "shell"}, headers=ORIGIN).status_code == 422
    ticket_api.access = (ticket_api.app, "editor", False)
    assert c.post(URL, json={"target": "app"}, headers=ORIGIN).status_code == 403   # 터미널은 주인만
    ticket_api.access = None
    assert c.post(URL, json={"target": "app"}, headers=ORIGIN).status_code == 404
    # root가 남의 앱에 열면 admin_actions에도 남는다
    ticket_api.access = (ticket_api.app, "owner", True)
    assert c.post(URL, json={"target": "app"}, headers=ORIGIN).status_code == 200
    assert ticket_api.audited == ["POST /apps/{app_id}/exec-tickets"]
    # 카나리 밖 ns(exec Pod가 없다)는 404
    monkeypatch.setattr(config, "EXEC_AGENT_NAMESPACES", frozenset({"tenant-ffffffff"}))
    assert c.post(URL, json={"target": "app"}, headers=ORIGIN).status_code == 404
    monkeypatch.setattr(config, "EXEC_AGENT_NAMESPACES", frozenset({NS}))
    # 플래그 off면 엔드포인트가 없는 것과 같다
    monkeypatch.setattr(config, "EXEC_AGENT_TERMINAL", False)
    assert c.post(URL, json={"target": "app"}, headers=ORIGIN).status_code == 404


def test_exec_features_per_app(ticket_api, monkeypatch):
    c = ticket_api.client
    path = "/apps/11111111-1111-1111-1111-111111111111/exec-features"
    ticket_api.access = (ticket_api.app, "editor", False)
    assert c.get(path).json() == {"terminal": True}
    monkeypatch.setattr(config, "EXEC_AGENT_NAMESPACES", frozenset({"tenant-ffffffff"}))
    assert c.get(path).json() == {"terminal": False}             # 카나리 밖 → 웹은 옛 경로
    monkeypatch.setattr(config, "EXEC_AGENT_NAMESPACES", frozenset({"*"}))
    monkeypatch.setattr(config, "EXEC_AGENT_TERMINAL", False)
    assert c.get(path).json() == {"terminal": False}
    ticket_api.access = None
    assert c.get(path).status_code == 404
