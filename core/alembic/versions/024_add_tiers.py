"""tiers 테이블 + users.tier — 유저별로 만들 수 있는 앱 수를 등급으로 정한다.

등급은 코드 상수가 아니라 테이블이라 개수는 DB(또는 관리자 API)로 조절한다. max_apps가 NULL이면 무제한.
처음 네 등급: basic 1개 · standard 3개 · pro 5개 · master 무제한. 새 유저는 basic이고,
이미 root 등급(role)인 유저는 master로 옮긴다 (운영자 계정이 앱 수에 걸리지 않게).

Revision ID: 024
Revises: 023
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "024"
down_revision: Union[str, None] = "023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TIERS = [("basic", 1), ("standard", 3), ("pro", 5), ("master", None)]


def upgrade() -> None:
    tiers = op.create_table(
        "tiers",
        sa.Column("name", sa.String(20), primary_key=True),
        sa.Column("max_apps", sa.Integer(), nullable=True),
    )
    op.bulk_insert(tiers, [{"name": n, "max_apps": m} for n, m in _TIERS])

    op.add_column("users", sa.Column("tier", sa.String(20), nullable=False, server_default="basic"))
    if op.get_context().dialect.name != "sqlite":   # SQLite는 ALTER로 제약을 못 더한다 (테스트용)
        op.create_foreign_key("fk_users_tier", "users", "tiers", ["tier"], ["name"])
    if not op.get_context().as_sql:
        op.get_bind().execute(sa.text("UPDATE users SET tier = 'master' WHERE role = 'root'"))


def downgrade() -> None:
    if op.get_context().dialect.name != "sqlite":
        op.drop_constraint("fk_users_tier", "users", type_="foreignkey")
    op.drop_column("users", "tier")
    op.drop_table("tiers")
