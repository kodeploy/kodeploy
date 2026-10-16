"""DB 탐색 SQL 생성 고정 — 인용과 화면용/실행용 SQL의 차이.

탐색은 사용자가 고른 이름·값을 SQL로 옮긴다. 인용이 하나라도 새면 값이 SQL이 되므로
식별자·문자열·LIKE 패턴 인용을 DB별로 그대로 고정한다(실제 MySQL 8.4·PostgreSQL 16에서 확인한 형태).
"""

import pytest

from app.deploy.console import dbbrowse
from app.deploy.console.dbquery import QueryError


# --- 인용 ---

def test_ident_doubles_own_quote_only():
    assert dbbrowse._ident("postgres", 'we"ird `x`') == '"we""ird `x`"'
    assert dbbrowse._ident("mysql", 'we`ird "x"') == '`we``ird "x"`'


@pytest.mark.parametrize("name", ["", "a\0b", "x" * 129, None])
def test_ident_rejects(name):
    with pytest.raises(QueryError):
        dbbrowse._ident("postgres", name)


def test_literal_pg_doubles_quote_keeps_backslash():
    # pg는 standard_conforming_strings=on 세션이라 \는 글자 그대로
    assert dbbrowse._literal("postgres", "it's \\") == "'it''s \\'"


def test_literal_mysql_escapes_backslash_too():
    assert dbbrowse._literal("mysql", "it's \\") == "'it''s \\\\'"


def test_literal_rejects_nul_and_long():
    with pytest.raises(QueryError):
        dbbrowse._literal("mysql", "a\0")
    with pytest.raises(QueryError):
        dbbrowse._literal("mysql", "x" * (dbbrowse.MAX_VALUE_LENGTH + 1))


def test_contains_pattern_escapes_wildcards():
    assert dbbrowse._contains_pattern("100%_!") == "%100!%!_!!%"


# --- SELECT 생성 ---

def test_build_select_pg_full():
    shown, run = dbbrowse.build_select("postgres", {
        "table": "posts",
        "schema": "public",
        "columns": ["id", "title"],
        "filters": [
            {"column": "status", "op": "eq", "value": "open"},
            {"column": "title", "op": "contains", "value": "a'b"},
            {"column": "deleted_at", "op": "is_null", "value": "ignored"},
        ],
        "sort": [{"column": "id", "dir": "desc"}],
        "offset": 200,
    })
    assert shown == (
        'SELECT "id", "title"\n'
        'FROM "posts"\n'
        """WHERE "status" = 'open'\n"""
        """  AND "title"::text ILIKE '%a''b%' ESCAPE '!'\n"""
        '  AND "deleted_at" IS NULL\n'
        'ORDER BY "posts"."id" DESC\n'
        "LIMIT 100 OFFSET 200;"
    )
    # 실행용 — 셀을 잘라 받고, 다음 페이지 확인용으로 한 행 더
    assert run.startswith('SELECT left("id"::text, 2000) AS "id", left("title"::text, 2000) AS "title"\n')
    assert run.endswith("LIMIT 101 OFFSET 200")


def test_build_select_mysql_and_star():
    shown, run = dbbrowse.build_select("mysql", {
        "table": "posts",
        "filters": [{"column": "id", "op": "gte", "value": "3"},
                    {"column": "id", "op": "contains", "value": "1"}],
    })
    assert shown == (
        "SELECT *\n"
        "FROM `posts`\n"
        "WHERE `id` >= '3'\n"
        "  AND CAST(`id` AS CHAR) LIKE '%1%' ESCAPE '!'\n"
        "LIMIT 100;"
    )
    assert run == shown[:-1].replace("LIMIT 100", "LIMIT 101")


def test_build_select_pg_other_schema_is_qualified():
    shown, _ = dbbrowse.build_select("postgres", {"table": "items", "schema": "other"})
    assert 'FROM "other"."items"' in shown


def test_build_select_mysql_ignores_schema():
    shown, _ = dbbrowse.build_select("mysql", {"table": "items", "schema": "other"})
    assert "FROM `items`" in shown


def test_build_select_limits():
    with pytest.raises(QueryError):
        dbbrowse.build_select("postgres", {
            "table": "t",
            "filters": [{"column": "a", "op": "is_null"}] * (dbbrowse.MAX_FILTERS + 1),
        })
    with pytest.raises(QueryError):
        dbbrowse.build_select("postgres", {"table": "t", "filters": [{"column": "a", "op": "drop"}]})


# --- 카탈로그 행 → 표 목록 ---

def test_catalog_tables_pg():
    rows = [
        ["public", "posts", "table", "6", "id", "integer", "t"],
        ["public", "posts", "table", "6", "title", "text", "f"],
        ["public", "v", "view", "NULL", "id", "integer", "f"],
    ]
    out = dbbrowse._catalog_tables("postgres", rows)
    assert out == [
        {"schema": "public", "name": "posts", "kind": "table", "rows_estimate": 6,
         "columns": [{"name": "id", "type": "integer", "pk": True},
                     {"name": "title", "type": "text", "pk": False}]},
        {"schema": "public", "name": "v", "kind": "view", "rows_estimate": None,
         "columns": [{"name": "id", "type": "integer", "pk": False}]},
    ]


def test_catalog_tables_mysql():
    rows = [["posts", "table", "6", "id", "int", "1"], ["posts", "table", "6", "t", "text", "0"]]
    out = dbbrowse._catalog_tables("mysql", rows)
    assert out[0]["schema"] is None
    assert [c["pk"] for c in out[0]["columns"]] == [True, False]


# --- 라우트 — /{build_id}보다 위에 있어야 하고, 요청의 schema_name이 schema로 전달돼야 한다 ---

def test_routes_reach_browse(monkeypatch):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.deploy import router as deploy_router

    seen = {}

    async def fake_browse(ns, spec):
        seen.update(ns=ns, spec=spec)
        return {"ok": True}

    async def fake_tables(ns):
        return {"tables": [], "ns": ns}

    monkeypatch.setattr(dbbrowse, "browse", fake_browse)
    monkeypatch.setattr(dbbrowse, "list_tables", fake_tables)
    api = FastAPI()
    api.include_router(deploy_router.router)
    api.dependency_overrides[deploy_router.current_app] = lambda: SimpleNamespace(namespace="tenant-x")
    c = TestClient(api)

    assert c.get("/deploy/app/db/tables").json() == {"tables": [], "ns": "tenant-x"}
    r = c.post("/deploy/app/db/browse", json={
        "table": "items", "schema_name": "other",
        "filters": [{"column": "a", "op": "contains", "value": "x"}],
        "sort": [{"column": "a", "dir": "desc"}],
    })
    assert r.status_code == 200
    assert seen["spec"]["schema"] == "other"
    assert seen["spec"]["filters"] == [{"column": "a", "op": "contains", "value": "x"}]
    assert seen["spec"]["sort"] == [{"column": "a", "dir": "desc"}]
    # 모르는 연산은 스키마에서 422
    bad = c.post("/deploy/app/db/browse", json={"table": "t", "filters": [{"column": "a", "op": "drop"}]})
    assert bad.status_code == 422
