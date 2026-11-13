"""DB 탐색 — SQL을 치지 않고 테이블을 골라 행을 보고, 컬럼·필터·정렬을 화면에서 고른다.

dbquery와 같은 길을 쓴다(kodeploy-exec 내부 문 browse → DB 파드의 CLI 기계가독 출력, 새 드라이버·core→DB 직결 없음).
다른 점은 SQL을 서버가 구조화된 요청에서 만든다는 것뿐이다.

- 식별자(스키마·테이블·컬럼)는 DB 규칙대로 인용한다 — pg "…"(" 두 번), mysql `…`(` 두 번).
  없는 이름이면 DB가 오류로 답하므로 카탈로그를 먼저 읽어 대조하는 왕복을 두지 않는다.
- 값은 문자열 리터럴로 인용한다 — ' 는 두 번, mysql은 \\ 도 두 번. 인용 규칙이 접속 설정에 따라
  흔들리지 않게 pg는 standard_conforming_strings=on, mysql은 NO_BACKSLASH_ESCAPES를 뺀 세션으로 돈다.
- 탐색은 읽기 전용 + 시간 제한(15초) 세션에서만 돈다. 인용이 뚫려도 쓰기는 나가지 않게 한 겹 더 둔다.
  세션 옵션은 exec `ops.Browse`가 건다(mysql --init-command, pg PGOPTIONS).

화면의 "SQL로 보기"가 보여 주는 SQL은 실제로 돈 것과 같다(긴 값을 자르는 감싸기만 뺐다).
"""

import time

from app.deploy.console.dbquery import QueryError, _parse_csv, _parse_mysql
from app.exec import client as exec_client

# 탐색 한 페이지의 행 수 — 표를 훑는 화면이라 SQL 콘솔(500)보다 작게.
PAGE_SIZE = 100
# 셀 하나에 실어 보내는 최대 글자 수. 긴 본문·JSON·BLOB 열이 있는 표도 한 페이지가 가볍게.
CELL_LIMIT = 2000

MAX_FILTERS = 10
MAX_SORT = 4
MAX_COLUMNS = 300
MAX_VALUE_LENGTH = 1000
MAX_IDENT_LENGTH = 128

# 필터 연산 — 값이 필요 없는 것(is_null/not_null)은 value를 무시한다.
OPS = ("eq", "ne", "gt", "gte", "lt", "lte", "contains", "is_null", "not_null")
_COMPARE = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


# ── 인용 ────────────────────────────────────────────────────────────────────

def _ident(db_type: str, name: str) -> str:
    if not isinstance(name, str) or not name or "\0" in name or len(name) > MAX_IDENT_LENGTH:
        raise QueryError("잘못된 이름입니다")
    if db_type == "mysql":
        return "`" + name.replace("`", "``") + "`"
    return '"' + name.replace('"', '""') + '"'


def _literal(db_type: str, value: str) -> str:
    if "\0" in value:
        raise QueryError("값에 NUL 문자를 쓸 수 없습니다")
    if len(value) > MAX_VALUE_LENGTH:
        raise QueryError(f"값이 너무 깁니다 (최대 {MAX_VALUE_LENGTH}자)")
    if db_type == "mysql":
        value = value.replace("\\", "\\\\")
    return "'" + value.replace("'", "''") + "'"


# LIKE 패턴의 와일드카드를 글자 그대로 찾게 한다. 이스케이프 문자는 두 DB에서 같은 뜻인 '!'
# (기본값 '\'는 mysql sql_mode에 따라 뜻이 바뀐다).
def _contains_pattern(value: str) -> str:
    return "%" + value.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"


# ── 실행 ────────────────────────────────────────────────────────────────────

# 탐색 SQL 바이트 상한 (exec ops.BrowseMaxBytes와 같은 고정값). SQL은 결국 -e/-c 인자 하나로 들어가는데,
# 인자 하나의 한도(MAX_ARG_STRLEN = 32 × 페이지 크기)는 노드 커널 페이지 크기에 따라 128KiB~2MiB로 달라진다.
# 가장 작은 경우(131072바이트)보다 아래로 고정해 노드와 상관없이 같게 한다. 원본은 넘으면 exec가 E2BIG로 실패했다.
MAX_SQL_BYTES = exec_client.BROWSE_MAX_BYTES


async def _db_type(ns: str) -> str:
    # DB 종류만 알면 된다 (비밀번호는 exec가 컨테이너 env에서 읽는다)
    kind = await exec_client.db_type(ns)
    if not kind:
        raise QueryError(
            "DB가 활성화돼 있지 않습니다 — DB(MySQL/PostgreSQL)를 추가한 앱에서만 쓸 수 있습니다."
        )
    return kind


# kodeploy-exec 내부 문(browse)으로 돌린다 — 읽기 전용·시간 제한 세션.
async def _run(ns: str, kind: str, sql: str, user_id: str = "", app_id: str = "") -> tuple[list[str], list[list[str]], int]:
    started = time.perf_counter()
    try:
        stdout, stderr, code = await exec_client.run_capture(ns, "browse", sql, user_id=user_id, app_id=app_id)
    except exec_client.ExecError as e:
        raise QueryError(e.message)
    duration_ms = int((time.perf_counter() - started) * 1000)
    if code != 0:
        raise QueryError(stderr.strip() or f"조회 실패 (exit {code})")
    if kind == "mysql":
        columns, rows = _parse_mysql(stdout)
    else:
        columns, rows = _parse_csv(stdout)
    return columns, rows, duration_ms


# ── 테이블 목록 ─────────────────────────────────────────────────────────────

# 행 수는 DB가 들고 있는 추정치다(COUNT(*)는 큰 표에서 느리다). 모르면 None.
# pg: 한 번도 ANALYZE 안 된 표는 reltuples = -1. 파티션 자식은 부모 하나로 보인다. 기본 스키마(public)가 먼저.
_PG_CATALOG = """
SELECT n.nspname, c.relname,
       CASE WHEN c.relkind IN ('v', 'm') THEN 'view' ELSE 'table' END,
       CASE WHEN c.relkind IN ('r', 'p', 'm') AND c.reltuples >= 0 THEN c.reltuples::bigint END,
       a.attname, format_type(a.atttypid, a.atttypmod),
       EXISTS (SELECT 1 FROM pg_index i
               WHERE i.indrelid = c.oid AND i.indisprimary AND a.attnum = ANY (i.indkey))
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') AND NOT c.relispartition
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND n.nspname NOT LIKE 'pg\\_toast%' AND n.nspname NOT LIKE 'pg\\_temp%'
  AND has_table_privilege(c.oid, 'SELECT')
ORDER BY n.nspname <> 'public', n.nspname, c.relname, a.attnum
"""

# mysql: TABLE_ROWS는 InnoDB 통계 추정치(뷰는 NULL).
_MYSQL_CATALOG = """
SELECT c.TABLE_NAME, IF(t.TABLE_TYPE = 'VIEW', 'view', 'table'), t.TABLE_ROWS,
       c.COLUMN_NAME, c.COLUMN_TYPE, c.COLUMN_KEY = 'PRI'
FROM information_schema.COLUMNS c
JOIN information_schema.TABLES t
  ON t.TABLE_SCHEMA = c.TABLE_SCHEMA AND t.TABLE_NAME = c.TABLE_NAME
WHERE c.TABLE_SCHEMA = DATABASE()
ORDER BY c.TABLE_NAME, c.ORDINAL_POSITION
"""


def _int_or_none(v: str) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _catalog_tables(db_type: str, rows: list[list[str]]) -> list[dict]:
    tables: dict[tuple, dict] = {}
    for r in rows:
        if db_type == "mysql":
            name, kind, est, col, typ, pk = r
            schema = None
            is_pk = pk == "1"
        else:
            schema, name, kind, est, col, typ, pk = r
            is_pk = pk == "t"
        key = (schema, name)
        if key not in tables:
            tables[key] = {
                "schema": schema,
                "name": name,
                "kind": kind,
                "rows_estimate": _int_or_none(est),
                "columns": [],
            }
        tables[key]["columns"].append({"name": col, "type": typ, "pk": is_pk})
    return list(tables.values())


async def list_tables(ns: str, *, user_id: str = "", app_id: str = "") -> dict:
    kind = await _db_type(ns)
    sql = _MYSQL_CATALOG if kind == "mysql" else _PG_CATALOG
    _, rows, _ = await _run(ns, kind, sql.strip(), user_id, app_id)
    return {"db_type": kind, "tables": _catalog_tables(kind, rows)}


# ── 행 보기 ─────────────────────────────────────────────────────────────────

def _condition(db_type: str, f: dict) -> str:
    col = _ident(db_type, f.get("column"))
    op = f.get("op")
    value = f.get("value") or ""
    if op == "is_null":
        return f"{col} IS NULL"
    if op == "not_null":
        return f"{col} IS NOT NULL"
    if op == "contains":
        pattern = _literal(db_type, _contains_pattern(value))
        if db_type == "mysql":
            return f"CAST({col} AS CHAR) LIKE {pattern} ESCAPE '!'"
        return f"{col}::text ILIKE {pattern} ESCAPE '!'"
    if op in _COMPARE:
        return f"{col} {_COMPARE[op]} {_literal(db_type, value)}"
    raise QueryError("알 수 없는 조건입니다")


# 요청 → (화면에 보여 줄 SQL, 실제로 돌릴 SQL). 둘의 차이는 셀 자르기 감싸기와 다음 페이지 확인용 +1행뿐.
def build_select(db_type: str, spec: dict) -> tuple[str, str]:
    table = spec.get("table")
    schema = spec.get("schema")
    columns = spec.get("columns") or []
    filters = spec.get("filters") or []
    sort = spec.get("sort") or []
    offset = max(0, int(spec.get("offset") or 0))

    if len(columns) > MAX_COLUMNS:
        raise QueryError(f"컬럼은 최대 {MAX_COLUMNS}개까지 고를 수 있습니다")
    if len(filters) > MAX_FILTERS:
        raise QueryError(f"조건은 최대 {MAX_FILTERS}개까지 걸 수 있습니다")
    if len(sort) > MAX_SORT:
        raise QueryError(f"정렬은 최대 {MAX_SORT}개까지 걸 수 있습니다")

    source = _ident(db_type, table)
    # pg의 기본 스키마는 생략해 읽기 쉽게 한다. 그 외 스키마만 앞에 붙인다(mysql은 DB가 하나).
    if db_type == "postgres" and schema and schema != "public":
        source = f"{_ident(db_type, schema)}.{source}"

    if columns:
        shown = [_ident(db_type, c) for c in columns]
        if db_type == "mysql":
            clipped = [f"LEFT({c}, {CELL_LIMIT}) AS {c}" for c in shown]
        else:
            clipped = [f"left({c}::text, {CELL_LIMIT}) AS {c}" for c in shown]
        display_cols, exec_cols = ", ".join(shown), ", ".join(clipped)
    else:
        display_cols = exec_cols = "*"

    tail = []
    if filters:
        conds = [_condition(db_type, f) for f in filters]
        tail.append("WHERE " + "\n  AND ".join(conds))
    if sort:
        # 표 이름을 붙여 원래 열로 정렬한다. 붙이지 않으면 같은 이름의 출력 열(자르느라 글자로 바꾼 것)이
        # 먼저 잡혀 숫자가 글자 순(10 < 9)으로 정렬된다.
        table_ref = _ident(db_type, table)
        parts = []
        for s in sort:
            direction = "DESC" if s.get("dir") == "desc" else "ASC"
            parts.append(f"{table_ref}.{_ident(db_type, s.get('column'))} {direction}")
        tail.append("ORDER BY " + ", ".join(parts))

    def compose(cols: str, limit: int) -> str:
        page = f"LIMIT {limit}" + (f" OFFSET {offset}" if offset else "")
        return "\n".join([f"SELECT {cols}", f"FROM {source}", *tail, page])

    return compose(display_cols, PAGE_SIZE) + ";", compose(exec_cols, PAGE_SIZE + 1)


async def browse(ns: str, spec: dict, *, user_id: str = "", app_id: str = "") -> dict:
    kind = await _db_type(ns)
    shown_sql, exec_sql = build_select(kind, spec)
    if len(exec_sql.encode("utf-8")) > MAX_SQL_BYTES:
        raise QueryError(f"조회 조건이 너무 깁니다 (SQL 최대 {MAX_SQL_BYTES}바이트) — 컬럼이나 조건을 줄여 주세요")
    columns, rows, duration_ms = await _run(ns, kind, exec_sql, user_id, app_id)
    has_more = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    return {
        "db_type": kind,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "offset": max(0, int(spec.get("offset") or 0)),
        "page_size": PAGE_SIZE,
        "has_more": has_more,
        "duration_ms": duration_ms,
        "cell_limit": CELL_LIMIT,
        "sql": shown_sql,
    }
