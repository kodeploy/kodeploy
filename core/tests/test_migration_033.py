"""033 마이그레이션 — ci_builds (빌드 하나에 행 하나, build_id 기본키)."""

import importlib.util
import io
from datetime import datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "033_ci_builds.py"


def load():
    spec = importlib.util.spec_from_file_location("m033", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_revision_chain():
    mod = load()
    assert (mod.revision, mod.down_revision) == ("033", "032")


def test_mysql_sql():
    buf = io.StringIO()
    ctx = MigrationContext.configure(dialect_name="mysql", opts={"as_sql": True, "output_buffer": buf})
    mod = load()
    with Operations.context(ctx):
        mod.upgrade()
        mod.downgrade()
    sql = buf.getvalue()
    assert "CREATE TABLE ci_builds" in sql
    assert "PRIMARY KEY (build_id)" in sql
    assert "CREATE INDEX ix_ci_builds_app_id ON ci_builds (app_id)" in sql
    assert "DROP TABLE ci_builds" in sql


def test_sqlite_roundtrip():
    eng = sa.create_engine("sqlite://")
    mod = load()
    with eng.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            mod.upgrade()
        cols = {c["name"] for c in sa.inspect(conn).get_columns("ci_builds")}
        assert cols == {"build_id", "app_id", "token_id", "repository", "ref", "sha", "run_id", "run_attempt",
                        "actor", "workflow", "event_name", "created_at"}
    table = sa.Table("ci_builds", sa.MetaData(), autoload_with=eng)
    row = dict(build_id="a0000001", app_id="x" * 32, repository="acme/api", ref="refs/heads/main", sha="0" * 40,
               event_name="push", created_at=datetime(2026, 11, 21))
    with eng.begin() as conn:
        conn.execute(table.insert().values(**row))
    with pytest.raises(sa.exc.IntegrityError):
        with eng.begin() as conn:
            conn.execute(table.insert().values(**row))
    with eng.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            mod.downgrade()
        assert "ci_builds" not in sa.inspect(conn).get_table_names()
