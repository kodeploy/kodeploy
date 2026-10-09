"""builds.namespace — 빌드가 돈 ns를 저장하고, 앱 백필을 한 번 더 한다.

Build.tenant_id가 user_id에서 파생되던 것을 앱의 ns로 바꾸기 위한 칸이다. 옛 행은 NULL이고 tenant_id가
기존 파생 규칙으로 돌려주므로 그 행들은 채우지 않아도 되지만, app_id가 있는 행은 apps.namespace로 채운다.

022의 백필은 그 시점의 사본이라 그 뒤 바뀐 것(새로 배포된 앱, 삭제·재생성된 앱, 도메인 변경)이 빠져 있다.
core가 apps를 읽기 시작하는 이 배포에서 users 기준으로 다시 맞춘다: 더는 users.app_name과 맞지 않는 apps 행은
지우고(그 app_id는 NULL), 남은 행의 속성은 users 값으로 갱신하고, 빠진 앱은 새로 채운다.

Revision ID: 023
Revises: 022
"""

import importlib.util
from pathlib import Path
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _load_022():
    path = Path(__file__).with_name("022_add_apps.py")
    spec = importlib.util.spec_from_file_location("m022_backfill", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_APP_FIELDS = ("site_enabled", "custom_domain", "custom_domain_status", "extra_hostnames", "pipeline")


def resync(conn) -> None:
    """apps를 users 기준으로 다시 맞춘다 (users는 이 배포 직전까지 쓰기 기준이었다)."""
    stale = "SELECT apps.id FROM apps WHERE NOT EXISTS (SELECT 1 FROM users WHERE users.id = apps.owner_id AND users.app_name = apps.name)"
    for table in ("builds", "build_records", "saved_queries"):
        conn.execute(sa.text(f"UPDATE {table} SET app_id = NULL WHERE app_id IN ({stale})"))
    conn.execute(sa.text(   # MySQL은 같은 테이블을 서브쿼리로 읽는 DELETE를 막아서 상관 서브쿼리를 직접 쓴다
        "DELETE FROM apps WHERE NOT EXISTS "
        "(SELECT 1 FROM users WHERE users.id = apps.owner_id AND users.app_name = apps.name)"
    ))
    for f in _APP_FIELDS:
        conn.execute(sa.text(
            f"UPDATE apps SET {f} = (SELECT users.{f} FROM users WHERE users.id = apps.owner_id)"
        ))
    _load_022().backfill(conn)


def upgrade() -> None:
    op.add_column("builds", sa.Column("namespace", sa.String(63), nullable=True))
    if not op.get_context().as_sql:
        conn = op.get_bind()
        resync(conn)
        conn.execute(sa.text(
            "UPDATE builds SET namespace = (SELECT apps.namespace FROM apps WHERE apps.id = builds.app_id) "
            "WHERE namespace IS NULL AND app_id IS NOT NULL"
        ))


def downgrade() -> None:
    op.drop_column("builds", "namespace")
