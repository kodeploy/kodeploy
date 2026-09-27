"""021 마이그레이션 — 칸 추가만, downgrade로 원상복구.

env.py가 offline 모드를 지원하지 않고 로컬에 MySQL이 없어서, 여기서 두 가지로 본다:
MySQL 방언으로 렌더한 SQL 문장, 그리고 SQLite에서 실제 upgrade → downgrade.
"""

import importlib.util
import io
from pathlib import Path

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "021_builder_pipeline.py"


def load():
    spec = importlib.util.spec_from_file_location("m021", PATH)
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
    assert "ALTER TABLE users ADD COLUMN pipeline VARCHAR(2) NOT NULL DEFAULT 'v1'" in sql
    assert "ALTER TABLE builds ADD COLUMN last_event_seq BIGINT" in sql
    assert "ALTER TABLE build_records ADD COLUMN git_committed_at DATETIME" in sql
    assert "ALTER TABLE build_records ADD COLUMN argo_synced_at DATETIME" in sql
    assert sql.count("DROP COLUMN") == 4
    assert "UPDATE" not in sql.upper().replace("UPDATE ALEMBIC", "")  # 데이터는 안 건드린다


def test_sqlite_roundtrip():
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE builds (build_id TEXT PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE build_records (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("INSERT INTO users (id) VALUES (1)")
        mod = load()
        with Operations.context(MigrationContext.configure(conn)):
            mod.upgrade()
        assert conn.exec_driver_sql("SELECT pipeline FROM users").scalar() == "v1"   # 기존 행은 v1
        cols = {c["name"] for c in sa.inspect(conn).get_columns("build_records")}
        assert {"git_committed_at", "argo_synced_at"} <= cols
        with Operations.context(MigrationContext.configure(conn)):
            mod.downgrade()
        insp = sa.inspect(conn)
        assert [c["name"] for c in insp.get_columns("users")] == ["id"]
        assert [c["name"] for c in insp.get_columns("builds")] == ["build_id"]
        assert [c["name"] for c in insp.get_columns("build_records")] == ["id"]
