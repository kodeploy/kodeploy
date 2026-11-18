"""031 마이그레이션 — deploy_requests 테이블과 (app_id, key) 유일 제약."""

import importlib.util
import io
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "031_deploy_requests.py"


def load():
    spec = importlib.util.spec_from_file_location("m031", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_revision_chain():
    mod = load()
    assert (mod.revision, mod.down_revision) == ("031", "030")


def test_mysql_sql():
    buf = io.StringIO()
    ctx = MigrationContext.configure(dialect_name="mysql", opts={"as_sql": True, "output_buffer": buf})
    mod = load()
    with Operations.context(ctx):
        mod.upgrade()
        mod.downgrade()
    sql = buf.getvalue()
    assert "CREATE TABLE deploy_requests" in sql
    assert "build_ids JSON" in sql
    assert "CONSTRAINT uq_deploy_requests_app_key UNIQUE (app_id, `key`)" in sql
    assert "DROP TABLE deploy_requests" in sql


def test_sqlite_roundtrip_and_uniqueness():
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        mod = load()
        with Operations.context(MigrationContext.configure(conn)):
            mod.upgrade()
        cols = {c["name"] for c in sa.inspect(conn).get_columns("deploy_requests")}
        assert cols == {"id", "app_id", "key", "build_ids", "created_at"}
    table = sa.Table("deploy_requests", sa.MetaData(), autoload_with=eng)
    app_id = uuid.uuid4().hex
    with eng.begin() as conn:
        conn.execute(table.insert().values(app_id=app_id, key="k", created_at=sa.func.now()))
        conn.execute(table.insert().values(app_id=uuid.uuid4().hex, key="k", created_at=sa.func.now()))  # 다른 앱은 같은 키 가능
    with pytest.raises(sa.exc.IntegrityError):
        with eng.begin() as conn:
            conn.execute(table.insert().values(app_id=app_id, key="k", created_at=sa.func.now()))
    with eng.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            mod.downgrade()
        assert "deploy_requests" not in sa.inspect(conn).get_table_names()
