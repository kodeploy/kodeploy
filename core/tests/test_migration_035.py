"""035 마이그레이션 — cli_tokens (토큰 해시 유일, 앱 id 색인, 폐기는 지우지 않고 revoked_at)."""

import importlib.util
import io
from datetime import datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "035_cli_tokens.py"


def load():
    spec = importlib.util.spec_from_file_location("m035", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_revision_chain():
    mod = load()
    assert (mod.revision, mod.down_revision) == ("035", "034")


def test_mysql_sql():
    buf = io.StringIO()
    ctx = MigrationContext.configure(dialect_name="mysql", opts={"as_sql": True, "output_buffer": buf})
    mod = load()
    with Operations.context(ctx):
        mod.upgrade()
        mod.downgrade()
    sql = buf.getvalue()
    assert "CREATE TABLE cli_tokens" in sql
    assert "CONSTRAINT uq_cli_tokens_token_hash UNIQUE (token_hash)" in sql
    assert "CREATE INDEX ix_cli_tokens_app_id ON cli_tokens (app_id)" in sql
    assert "DROP TABLE cli_tokens" in sql


def test_sqlite_roundtrip():
    eng = sa.create_engine("sqlite://")
    mod = load()
    with eng.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            mod.upgrade()
        cols = {c["name"] for c in sa.inspect(conn).get_columns("cli_tokens")}
        assert cols == {"id", "token_hash", "app_id", "issued_by", "issued_by_login", "created_at", "last_used_at",
                        "expires_at", "revoked_at"}
    table = sa.Table("cli_tokens", sa.MetaData(), autoload_with=eng)
    row = dict(token_hash="0" * 64, app_id="x" * 32, issued_by="y" * 32, created_at=datetime(2026, 11, 25),
               expires_at=datetime(2026, 11, 25, 0, 15))
    with eng.begin() as conn:
        conn.execute(table.insert().values(**row))
    with pytest.raises(sa.exc.IntegrityError):
        with eng.begin() as conn:
            conn.execute(table.insert().values(**row))
    with eng.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            mod.downgrade()
        assert "cli_tokens" not in sa.inspect(conn).get_table_names()
