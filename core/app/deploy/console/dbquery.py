"""DB 콘솔 — 단발성 SQL 실행 후 구조화된 결과(columns/rows)를 반환.

raw DB 터미널은 mysql/psql CLI에 직접 붙어 ASCII 표를 그리지만, 패널 폭에
따라 깨진다. 여기서는 CLI를 **기계가독 포맷**으로 돌려 백엔드가 파싱한 뒤 JSON으로 주고,
프론트가 HTML 표로 렌더한다. 실행은 그 테넌트의 kodeploy-exec 내부 문(query)이 DB 파드에서 한다 —
새 드라이버나 core→DB 직결 경로를 만들지 않는다. 명령은 exec `ops.Query`가 조립한다.

- mysql : `mysql --batch`     → TAB 구분(테두리 없음), 첫 줄이 컬럼명.
- pg    : `psql --csv`        → CSV(첫 줄이 컬럼명).

SQL은 stdin으로 넘어가 셸 문자열에 들어가지 않는다. 매 호출이 stateless exec이라 트랜잭션/USE
같은 세션 상태는 유지되지 않는다(그게 필요하면 raw 터미널 사용).
"""

import csv
import io
import re
import time

from app.exec import client as exec_client

# 한 페이지에 받아올 행 수. SELECT/WITH 쿼리는 OFFSET 페이지네이션으로 다음 페이지를 더
# 받을 수 있고, 그 외(SHOW/INSERT 등)는 감쌀 수 없어 이 수에서 잘리고 truncated로 알린다.
PAGE_SIZE = 500
# SQL 길이 상한 — 거대 입력 차단(거대 덤프는 restore 경로로).
MAX_SQL_LENGTH = 20000

# 선행 주석(-- ... / /* ... */)·공백 — 첫 키워드 판별 전에 제거.
_LEADING_NOISE = re.compile(r"(?:--[^\n]*\n|/\*.*?\*/|\s)*", re.DOTALL)


class QueryError(Exception):
    """사용자에게 그대로 보여줄 수 있는 쿼리 실행 실패(문법 오류 등)."""


# 파생 테이블로 감싸 LIMIT/OFFSET 페이징이 가능한 쿼리인지.
# SELECT/WITH 단일 문장만 — 다중 문장(;)이나 SHOW/INSERT 등은 감싸면 깨지므로 제외.
def _wrappable(sql: str) -> bool:
    body = sql.strip()
    while body.endswith(";"):
        body = body[:-1].rstrip()
    if ";" in body:                                   # 다중 문장 — 감쌀 수 없음
        return False
    head = body[_LEADING_NOISE.match(body).end():]
    return bool(re.match(r"(?is)(?:select|with)\b", head))


# mysql --batch 값 unescape (\t \n \\ \0). NULL은 리터럴 "NULL"로 와서 None과 구분 불가 —
# batch 모드의 알려진 한계라 빈 결과/실제 문자열 "NULL"은 그대로 문자열로 둔다.
def _unescape_mysql(field: str) -> str:
    return (
        field.replace("\\t", "\t")
        .replace("\\n", "\n")
        .replace("\\0", "\0")
        .replace("\\\\", "\\")
    )


def _parse_mysql(stdout: str) -> tuple[list[str], list[list[str]]]:
    text = stdout.rstrip("\n")
    if not text:
        return [], []
    lines = text.split("\n")
    columns = [_unescape_mysql(c) for c in lines[0].split("\t")]
    rows = [[_unescape_mysql(v) for v in ln.split("\t")] for ln in lines[1:]]
    return columns, rows


def _parse_csv(stdout: str) -> tuple[list[str], list[list[str]]]:
    reader = list(csv.reader(io.StringIO(stdout)))
    if not reader:
        return [], []
    return reader[0], reader[1:]


# 고수준 API — router가 호출. tenant ns의 DB 파드에 SQL을 돌려 구조화된 결과를 반환.
# offset: SELECT/WITH 쿼리에서 다음 페이지를 보기 위한 시작 위치(페이지네이션). 그 외는 무시.
async def run_query(ns: str, sql: str, offset: int = 0, *, user_id: str = "", app_id: str = "") -> dict:
    sql = (sql or "").strip()
    if not sql:
        raise QueryError("SQL을 입력해주세요")
    if len(sql) > MAX_SQL_LENGTH:
        raise QueryError(f"SQL이 너무 깁니다 (최대 {MAX_SQL_LENGTH}자)")
    offset = max(0, int(offset))

    # DB 종류만 알면 된다 (비밀번호는 exec가 컨테이너 env에서 읽는다)
    kind = await exec_client.db_type(ns)
    if not kind:
        raise QueryError(
            "DB가 활성화돼 있지 않습니다 — DB(MySQL/PostgreSQL)를 추가한 앱에서만 콘솔을 쓸 수 있습니다."
        )

    # 페이징 가능한 쿼리면 파생 테이블로 감싸 DB가 LIMIT/OFFSET을 처리하게 한다.
    # PAGE_SIZE+1을 받아 "다음 페이지 존재 여부"(has_more)를 한 번에 판별.
    paginated = _wrappable(sql)
    if paginated:
        inner = sql
        while inner.rstrip().endswith(";"):
            inner = inner.rstrip()[:-1]
        exec_sql = (
            f"SELECT * FROM (\n{inner}\n) AS _kd_page "
            f"LIMIT {PAGE_SIZE + 1} OFFSET {offset}"
        )
    else:
        offset = 0
        exec_sql = sql

    started = time.perf_counter()
    try:
        stdout, stderr, code = await exec_client.run_capture(ns, "query", exec_sql, user_id=user_id, app_id=app_id)
    except exec_client.ExecError as e:
        raise QueryError(e.message)
    duration_ms = int((time.perf_counter() - started) * 1000)

    if code != 0:
        raise QueryError(stderr.strip() or f"쿼리 실패 (exit {code})")

    if kind == "mysql":
        columns, rows = _parse_mysql(stdout)
    else:
        columns, rows = _parse_csv(stdout)

    if paginated:
        has_more = len(rows) > PAGE_SIZE          # +1행이 더 왔으면 다음 페이지 있음
        rows = rows[:PAGE_SIZE]
        truncated = False
    else:
        has_more = False
        truncated = len(rows) > PAGE_SIZE         # 페이징 불가 쿼리 — 그냥 자름
        if truncated:
            rows = rows[:PAGE_SIZE]

    return {
        "db_type": kind,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "paginated": paginated,
        "offset": offset,
        "page_size": PAGE_SIZE,
        "has_more": has_more,
        "truncated": truncated,
        "duration_ms": duration_ms,
        # 컬럼 없는 결과(INSERT/UPDATE/DDL 등) — 성공 메시지로 표시.
        "message": None if columns else "실행 완료",
    }
