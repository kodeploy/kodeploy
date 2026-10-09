"""apps 테이블 + builds/build_records/saved_queries.app_id — 1유저:1앱 해제 1단계.

앱 속성(이름·도메인·슬롯 선언·파이프라인)이 users에 붙어 있던 것을 apps로 뺀다. 이 단계는 칸·테이블 추가와
백필만 한다 — users의 기존 칸은 지우지 않고, core도 아직 apps를 읽지 않는다 (2단계에서 전환).
그래서 백필은 그 시점의 사본이다. 2단계를 배포하기 직전에 다시 돌려도 안전하다 (이미 있는 앱은 건너뛴다).

백필:
- app_name이 있는 유저마다 apps 한 행. namespace는 기존 ns 이름 tenant-<유저 id hex 앞 8자> 그대로
  (클러스터 리소스를 옮기지 않으므로 PVC·DB 데이터가 그대로다).
- builds는 user_id로, build_records·saved_queries는 user_id + app_name이 같은 것만 app_id를 채운다
  (삭제된 앱의 기록은 NULL로 남는다).

downgrade는 칸과 테이블을 지운다.

Revision ID: 022
Revises: 021
"""

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "022"
down_revision: Union[str, None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_APP_ID_TABLES = ("builds", "build_records", "saved_queries")


def upgrade() -> None:
    op.create_table(
        "apps",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("name", sa.String(50), nullable=False, unique=True),
        sa.Column("namespace", sa.String(63), nullable=False, unique=True),
        sa.Column("site_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("custom_domain", sa.String(253), nullable=True, unique=True),
        sa.Column("custom_domain_status", sa.String(20), nullable=True),
        sa.Column("extra_hostnames", sa.Text(), nullable=True),
        sa.Column("pipeline", sa.String(2), nullable=False, server_default="v1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for table in _APP_ID_TABLES:
        op.add_column(table, sa.Column("app_id", sa.Uuid(), nullable=True))
        op.create_index(f"ix_{table}_app_id", table, ["app_id"])
    if not op.get_context().as_sql:   # 오프라인 SQL 출력에는 데이터 작업을 넣지 않는다
        backfill(op.get_bind())


def backfill(conn) -> None:
    """users → apps 사본을 만들고 app_id를 채운다. 이미 있는 앱(name 기준)은 건너뛰어 다시 돌려도 안전하다."""
    users = sa.table(
        "users",
        sa.column("id", sa.Uuid()), sa.column("app_name", sa.String()), sa.column("site_enabled", sa.Boolean()),
        sa.column("custom_domain", sa.String()), sa.column("custom_domain_status", sa.String()),
        sa.column("extra_hostnames", sa.Text()), sa.column("pipeline", sa.String()),
        sa.column("created_at", sa.DateTime()), sa.column("updated_at", sa.DateTime()),
    )
    apps = sa.table(
        "apps",
        sa.column("id", sa.Uuid()), sa.column("owner_id", sa.Uuid()), sa.column("name", sa.String()),
        sa.column("namespace", sa.String()), sa.column("site_enabled", sa.Boolean()),
        sa.column("custom_domain", sa.String()), sa.column("custom_domain_status", sa.String()),
        sa.column("extra_hostnames", sa.Text()), sa.column("pipeline", sa.String()),
        sa.column("created_at", sa.DateTime()), sa.column("updated_at", sa.DateTime()),
    )
    existing = {r[0] for r in conn.execute(sa.select(apps.c.name))}
    rows = conn.execute(sa.select(users).where(users.c.app_name.is_not(None))).mappings().all()
    for u in rows:
        if u["app_name"] in existing:
            continue
        conn.execute(apps.insert().values(
            id=uuid.uuid4(), owner_id=u["id"], name=u["app_name"], namespace=f"tenant-{u['id'].hex[:8]}",
            site_enabled=bool(u["site_enabled"]), custom_domain=u["custom_domain"],
            custom_domain_status=u["custom_domain_status"], extra_hostnames=u["extra_hostnames"],
            pipeline=u["pipeline"] or "v1", created_at=u["created_at"], updated_at=u["updated_at"],
        ))

    conn.execute(sa.text(
        "UPDATE builds SET app_id = (SELECT apps.id FROM apps WHERE apps.owner_id = builds.user_id) "
        "WHERE app_id IS NULL"
    ))
    for table in ("build_records", "saved_queries"):
        conn.execute(sa.text(
            f"UPDATE {table} SET app_id = (SELECT apps.id FROM apps "
            f"WHERE apps.owner_id = {table}.user_id AND apps.name = {table}.app_name) "
            "WHERE app_id IS NULL"
        ))


def downgrade() -> None:
    for table in reversed(_APP_ID_TABLES):
        op.drop_index(f"ix_{table}_app_id", table_name=table)
        op.drop_column(table, "app_id")
    op.drop_table("apps")
