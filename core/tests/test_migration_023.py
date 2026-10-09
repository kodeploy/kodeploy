"""023 마이그레이션 — builds.namespace 칸, 그리고 022 이후 어긋난 apps를 users 기준으로 다시 맞춘다."""

import importlib.util
import io
from datetime import datetime
from pathlib import Path

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

from tests.test_migration_022 import U1, U2, U3, _schema, load as load_022

PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "023_build_namespace.py"


def load():
    spec = importlib.util.spec_from_file_location("m023", PATH)
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
    assert "ALTER TABLE builds ADD COLUMN namespace VARCHAR(63)" in sql
    assert "DELETE" not in sql.upper() and "ALTER TABLE builds DROP COLUMN namespace" in sql


def test_sqlite_resync_and_namespace():
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        _schema(conn)
        with Operations.context(MigrationContext.configure(conn)):
            load_022().upgrade()
        now = datetime(2026, 10, 11, 9, 0, 0)

        # 022 이후 core가 users만 바꾼 상황:
        #  U1: 앱 삭제 후 다른 이름으로 재배포, U2: 커스텀 도메인 해제, U3: 새로 첫 배포
        conn.execute(sa.text("UPDATE users SET app_name = 'renamed-app' WHERE id = :i"), {"i": U1.hex})
        conn.execute(sa.text("UPDATE users SET custom_domain = NULL, custom_domain_status = NULL WHERE id = :i"), {"i": U2.hex})
        conn.execute(sa.text("UPDATE users SET app_name = 'fresh', created_at = :n WHERE id = :i"), {"i": U3.hex, "n": now})
        conn.execute(sa.text("INSERT INTO builds (build_id, user_id) VALUES ('b3', :i)"), {"i": U3.hex})

        with Operations.context(MigrationContext.configure(conn)):
            load().upgrade()

        apps = {r["name"]: r for r in conn.execute(sa.text("SELECT * FROM apps")).mappings()}
        assert set(apps) == {"renamed-app", "dailo", "fresh"}              # 이름이 달라진 옛 행은 사라지고 새로 채워진다
        assert apps["dailo"]["custom_domain"] is None                       # 도메인 해제가 따라온다
        assert apps["renamed-app"]["namespace"] == "tenant-d6d8b759"

        ns = {r[0]: r[1] for r in conn.execute(sa.text("SELECT build_id, namespace FROM builds"))}
        assert ns == {"b1": "tenant-d6d8b759", "b2": "tenant-1e202690", "b3": "tenant-aaaaaaaa"}
        # 사라진 앱을 가리키던 기록은 NULL로 돌아가고, 새 앱 기록은 채워진다
        old = conn.execute(sa.text("SELECT app_id FROM build_records WHERE app_name = 'kodeploy-test-spring'")).scalar()
        assert old is None

        with Operations.context(MigrationContext.configure(conn)):
            load().downgrade()
        assert "namespace" not in {c["name"] for c in sa.inspect(conn).get_columns("builds")}
