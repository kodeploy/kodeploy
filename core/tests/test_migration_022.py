"""022 마이그레이션 — apps 테이블 생성, 기존 유저의 백필, downgrade 복구.

MySQL 방언으로 렌더한 SQL 문장과, SQLite에서 실제 upgrade → 백필 → 재실행 → downgrade로 본다.
"""

import importlib.util
import io
import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "022_add_apps.py"

U1 = uuid.UUID("d6d8b759-8552-4d6f-9a90-00665e7ca0da")
U2 = uuid.UUID("1e202690-0000-4000-8000-000000000002")
U3 = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000003")   # 앱 없는 유저


def load():
    spec = importlib.util.spec_from_file_location("m022", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_mysql_sql_has_no_data_statements():
    buf = io.StringIO()
    ctx = MigrationContext.configure(dialect_name="mysql", opts={"as_sql": True, "output_buffer": buf})
    mod = load()
    with Operations.context(ctx):
        mod.upgrade()
        mod.downgrade()
    sql = buf.getvalue()
    assert "CREATE TABLE apps" in sql
    for t in ("builds", "build_records", "saved_queries"):
        assert f"ALTER TABLE {t} ADD COLUMN app_id CHAR(32)" in sql
    assert "UPDATE " not in sql.upper().replace("UPDATE ALEMBIC", "")   # 데이터 문장 없음 (UPDATED_AT 칸 이름은 제외)
    assert "INSERT" not in sql.upper().replace("INSERT INTO ALEMBIC", "")
    assert "DROP TABLE apps" in sql


def _schema(conn):
    conn.exec_driver_sql(
        "CREATE TABLE users (id CHAR(32) PRIMARY KEY, app_name TEXT, site_enabled BOOLEAN, custom_domain TEXT, "
        "custom_domain_status TEXT, extra_hostnames TEXT, pipeline TEXT, created_at DATETIME, updated_at DATETIME)"
    )
    conn.exec_driver_sql("CREATE TABLE builds (build_id TEXT PRIMARY KEY, user_id CHAR(32))")
    conn.exec_driver_sql("CREATE TABLE build_records (id INTEGER PRIMARY KEY, user_id CHAR(32), app_name TEXT)")
    conn.exec_driver_sql("CREATE TABLE saved_queries (id INTEGER PRIMARY KEY, user_id CHAR(32), app_name TEXT)")
    u = sa.table(
        "users", sa.column("id", sa.Uuid()), sa.column("app_name"), sa.column("site_enabled"), sa.column("custom_domain"),
        sa.column("custom_domain_status"), sa.column("extra_hostnames"), sa.column("pipeline"),
        sa.column("created_at", sa.DateTime()), sa.column("updated_at", sa.DateTime()),
    )
    from datetime import datetime
    now = datetime(2026, 10, 10, 12, 0, 0)
    conn.execute(u.insert(), [
        dict(id=U1, app_name="kodeploy-test-spring", site_enabled=False, custom_domain=None, custom_domain_status=None,
             extra_hostnames=None, pipeline="v2", created_at=now, updated_at=now),
        dict(id=U2, app_name="dailo", site_enabled=True, custom_domain="www.dailo.app", custom_domain_status="active",
             extra_hostnames="dailoapp.com", pipeline="v1", created_at=now, updated_at=now),
        dict(id=U3, app_name=None, site_enabled=False, custom_domain=None, custom_domain_status=None,
             extra_hostnames=None, pipeline="v1", created_at=now, updated_at=now),
    ])
    for t, rows in (
        ("builds", [("b1", U1), ("b2", U2)]),
    ):
        tb = sa.table(t, sa.column("build_id"), sa.column("user_id", sa.Uuid()))
        conn.execute(tb.insert(), [dict(build_id=b, user_id=uid) for b, uid in rows])
    for t in ("build_records", "saved_queries"):
        tb = sa.table(t, sa.column("user_id", sa.Uuid()), sa.column("app_name"))
        conn.execute(tb.insert(), [
            dict(user_id=U1, app_name="kodeploy-test-spring"),
            dict(user_id=U1, app_name="old-deleted-app"),        # 삭제된 앱의 기록
            dict(user_id=U2, app_name="dailo"),
        ])


def test_sqlite_backfill_and_roundtrip():
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        _schema(conn)
        mod = load()
        with Operations.context(MigrationContext.configure(conn)):
            mod.upgrade()

        apps = {r["name"]: r for r in conn.execute(sa.text("SELECT * FROM apps")).mappings()}
        assert set(apps) == {"kodeploy-test-spring", "dailo"}                   # 앱 없는 유저는 행이 없다
        assert apps["kodeploy-test-spring"]["namespace"] == "tenant-d6d8b759"   # ns는 기존 이름 그대로
        assert apps["kodeploy-test-spring"]["pipeline"] == "v2"
        assert apps["dailo"]["custom_domain"] == "www.dailo.app" and apps["dailo"]["site_enabled"] == 1
        assert apps["dailo"]["extra_hostnames"] == "dailoapp.com"

        def app_of(table, **where):
            cond = " AND ".join(f"{k} = :{k}" for k in where)
            return conn.execute(sa.text(f"SELECT app_id FROM {table} WHERE {cond}"), where).scalar()

        spring, dailo = apps["kodeploy-test-spring"]["id"], apps["dailo"]["id"]
        assert app_of("builds", build_id="b1") == spring and app_of("builds", build_id="b2") == dailo
        for t in ("build_records", "saved_queries"):
            assert app_of(t, app_name="kodeploy-test-spring") == spring
            assert app_of(t, app_name="dailo") == dailo
            assert app_of(t, app_name="old-deleted-app") is None                # 삭제된 앱 기록은 NULL

        # 다시 돌려도 중복 행을 만들지 않는다 (2단계 배포 직전 재백필)
        mod.backfill(conn)
        assert conn.execute(sa.text("SELECT count(*) FROM apps")).scalar() == 2

        with Operations.context(MigrationContext.configure(conn)):
            mod.downgrade()
        insp = sa.inspect(conn)
        assert "apps" not in insp.get_table_names()
        for t in ("builds", "build_records", "saved_queries"):
            assert "app_id" not in {c["name"] for c in insp.get_columns(t)}
