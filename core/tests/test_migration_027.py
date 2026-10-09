"""027 마이그레이션 — builds.rollback_of 칸 추가·삭제."""

import importlib.util
import io
from pathlib import Path

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "027_build_rollback_of.py"


def load():
    spec = importlib.util.spec_from_file_location("m027", PATH)
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
    assert "ALTER TABLE builds ADD COLUMN rollback_of VARCHAR(8)" in sql
    assert "ALTER TABLE builds DROP COLUMN rollback_of" in sql


def test_sqlite_roundtrip():
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE builds (build_id TEXT PRIMARY KEY)")
        mod = load()
        with Operations.context(MigrationContext.configure(conn)):
            mod.upgrade()
        assert "rollback_of" in {c["name"] for c in sa.inspect(conn).get_columns("builds")}
        with Operations.context(MigrationContext.configure(conn)):
            mod.downgrade()
        assert [c["name"] for c in sa.inspect(conn).get_columns("builds")] == ["build_id"]
