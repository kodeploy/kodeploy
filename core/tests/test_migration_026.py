"""026 마이그레이션 — users의 옛 앱 칸을 지우고, downgrade가 apps에서 다시 채운다."""

import importlib.util
import io
from pathlib import Path

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "026_drop_user_app_columns.py"
OLD = {"app_name", "site_enabled", "custom_domain", "custom_domain_status", "extra_hostnames", "pipeline"}


def load():
    spec = importlib.util.spec_from_file_location("m026", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_mysql_sql():
    buf = io.StringIO()
    ctx = MigrationContext.configure(dialect_name="mysql", opts={"as_sql": True, "output_buffer": buf})
    mod = load()
    with Operations.context(ctx):
        mod.upgrade()
        mod.downgrade()
    sql = buf.getvalue()
    for col in OLD:
        assert f"ALTER TABLE users DROP COLUMN {col}" in sql
        assert f"ALTER TABLE users ADD COLUMN {col}" in sql


def test_sqlite_drops_columns_and_downgrade_refills_from_apps():
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE users (id TEXT PRIMARY KEY, login TEXT, app_name TEXT, site_enabled BOOLEAN DEFAULT 0, "
            "custom_domain TEXT, custom_domain_status TEXT, extra_hostnames TEXT, pipeline TEXT DEFAULT 'v1')"
        )
        conn.exec_driver_sql(
            "CREATE TABLE apps (id TEXT PRIMARY KEY, owner_id TEXT, name TEXT, site_enabled BOOLEAN, custom_domain TEXT, "
            "custom_domain_status TEXT, extra_hostnames TEXT, pipeline TEXT, created_at DATETIME)"
        )
        conn.exec_driver_sql("INSERT INTO users (id, login, app_name) VALUES ('u1', 'kim', 'stale-name'), ('u2', 'lee', NULL)")
        conn.exec_driver_sql(
            "INSERT INTO apps VALUES ('a1', 'u1', 'first', 1, 'www.first.dev', 'active', 'x.com', 'v2', '2026-09-01'), "
            "('a2', 'u1', 'second', 0, NULL, NULL, NULL, 'v1', '2026-10-01')"
        )
        mod = load()
        with Operations.context(MigrationContext.configure(conn)):
            mod.upgrade()
        assert {c["name"] for c in sa.inspect(conn).get_columns("users")} == {"id", "login"}

        with Operations.context(MigrationContext.configure(conn)):
            mod.downgrade()
        rows = {r[0]: r for r in conn.exec_driver_sql(
            "SELECT id, app_name, site_enabled, custom_domain, custom_domain_status, extra_hostnames, pipeline FROM users")}
        assert rows["u1"][1:] == ("first", 1, "www.first.dev", "active", "x.com", "v2")    # 첫 앱의 값
        assert rows["u2"][1] is None and rows["u2"][6] == "v1"                              # 앱 없는 유저는 기본값
