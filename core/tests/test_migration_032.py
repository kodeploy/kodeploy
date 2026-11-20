"""032 마이그레이션 — apps.ci_deploy_enabled(기본 꺼짐)와 ci_tokens(해시·jti 유일)."""

import importlib.util
import io
import uuid
from datetime import datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "032_ci_tokens.py"


def load():
    spec = importlib.util.spec_from_file_location("m032", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_revision_chain():
    mod = load()
    assert (mod.revision, mod.down_revision) == ("032", "031")


def test_mysql_sql():
    buf = io.StringIO()
    ctx = MigrationContext.configure(dialect_name="mysql", opts={"as_sql": True, "output_buffer": buf})
    mod = load()
    with Operations.context(ctx):
        mod.upgrade()
        mod.downgrade()
    sql = buf.getvalue()
    assert "ALTER TABLE apps ADD COLUMN ci_deploy_enabled BOOL NOT NULL DEFAULT false" in sql
    assert "CREATE TABLE ci_tokens" in sql
    assert "CONSTRAINT uq_ci_tokens_token_hash UNIQUE (token_hash)" in sql
    assert "CONSTRAINT uq_ci_tokens_jti UNIQUE (jti)" in sql
    assert "CREATE INDEX ix_ci_tokens_app_id ON ci_tokens (app_id)" in sql
    assert "DROP TABLE ci_tokens" in sql
    assert "ALTER TABLE apps DROP COLUMN ci_deploy_enabled" in sql


def _apps_table(conn):
    conn.execute(sa.text("CREATE TABLE apps (id CHAR(32) PRIMARY KEY, name VARCHAR(50))"))
    conn.execute(sa.text("INSERT INTO apps (id, name) VALUES ('a1', 'old')"))


def test_sqlite_roundtrip_default_off_and_uniqueness():
    eng = sa.create_engine("sqlite://")
    mod = load()
    with eng.begin() as conn:
        _apps_table(conn)
        with Operations.context(MigrationContext.configure(conn)):
            mod.upgrade()
        # 기존 앱은 꺼진 채로 시작한다
        assert conn.execute(sa.text("SELECT ci_deploy_enabled FROM apps WHERE id='a1'")).scalar() in (0, False)
        cols = {c["name"] for c in sa.inspect(conn).get_columns("ci_tokens")}
        assert cols == {"id", "token_hash", "jti", "app_id", "repository", "ref", "sha", "run_id", "run_attempt",
                        "actor", "workflow", "event_name", "expires_at", "created_at"}

    table = sa.Table("ci_tokens", sa.MetaData(), autoload_with=eng)
    now = datetime(2026, 11, 21)

    def row(**kw):
        base = dict(token_hash="h" * 64, jti="j-1", app_id=uuid.uuid4().hex, repository="acme/api",
                    ref="refs/heads/main", sha="0" * 40, event_name="push", expires_at=now, created_at=now)
        base.update(kw)
        return base

    with eng.begin() as conn:
        conn.execute(table.insert().values(**row()))
    for dup in (dict(jti="j-2"), dict(token_hash="x" * 64)):   # 해시가 같거나 jti가 같으면 거절
        with pytest.raises(sa.exc.IntegrityError):
            with eng.begin() as conn:
                conn.execute(table.insert().values(**row(**dup)))

    with eng.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            mod.downgrade()
        assert "ci_tokens" not in sa.inspect(conn).get_table_names()
        assert "ci_deploy_enabled" not in {c["name"] for c in sa.inspect(conn).get_columns("apps")}
