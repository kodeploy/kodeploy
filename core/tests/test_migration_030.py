"""030 마이그레이션 — builds.source_sha·requested_sha 칸 추가·삭제."""

import importlib.util
import io
from pathlib import Path

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "030_build_source_sha.py"


def load():
    spec = importlib.util.spec_from_file_location("m030", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_revision_chain():
    mod = load()
    assert (mod.revision, mod.down_revision) == ("030", "029")


def test_mysql_sql():
    buf = io.StringIO()
    ctx = MigrationContext.configure(dialect_name="mysql", opts={"as_sql": True, "output_buffer": buf})
    mod = load()
    with Operations.context(ctx):
        mod.upgrade()
        mod.downgrade()
    sql = buf.getvalue()
    assert "ALTER TABLE builds ADD COLUMN source_sha VARCHAR(40)" in sql
    assert "ALTER TABLE builds ADD COLUMN requested_sha VARCHAR(40)" in sql
    assert "NOT NULL" not in sql
    assert "ALTER TABLE builds DROP COLUMN requested_sha" in sql
    assert "ALTER TABLE builds DROP COLUMN source_sha" in sql


def test_sqlite_roundtrip():
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE builds (build_id TEXT PRIMARY KEY)")
        conn.exec_driver_sql("INSERT INTO builds (build_id) VALUES ('3f9a2c1d')")
        mod = load()
        with Operations.context(MigrationContext.configure(conn)):
            mod.upgrade()
        cols = {c["name"]: c for c in sa.inspect(conn).get_columns("builds")}
        assert cols["source_sha"]["nullable"] and cols["requested_sha"]["nullable"]
        # 옛 행은 둘 다 비어 있다
        assert conn.exec_driver_sql("SELECT source_sha, requested_sha FROM builds").one() == (None, None)
        with Operations.context(MigrationContext.configure(conn)):
            mod.downgrade()
        assert [c["name"] for c in sa.inspect(conn).get_columns("builds")] == ["build_id"]
