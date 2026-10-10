"""admin_actions — 관리자가 남의 앱·계정에 한 동작 기록.

root는 모든 앱에서 주인과 같고 admin은 보기만 한다 (apps/sharing.py access_for). 관리자 권한으로 바꾼 것만
여기에 남는다. 앱·계정 이름은 그때 값을 적는다 — 지워진 뒤에도 읽히게.

Revision ID: 028
Revises: 027
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "028"
down_revision: Union[str, None] = "027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_actions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("actor_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("actor_login", sa.String(100), nullable=True),
        sa.Column("app_id", sa.Uuid(), nullable=True, index=True),
        sa.Column("app_name", sa.String(50), nullable=True),
        sa.Column("target_login", sa.String(100), nullable=True),
        sa.Column("action", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("admin_actions")
