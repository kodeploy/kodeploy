"""app_members, app_invites — 앱 공유 (주인이 다른 유저에게 보기/편집 권한을 준다).

멤버: (app_id, user_id) 복합키 + role(viewer|editor). 주인은 apps.owner_id가 그대로 맡는다.
초대: 받는 사람이 수락하기 전 단계. 이메일 또는 GitHub 아이디로 가리킨다.

Revision ID: 025
Revises: 024
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "025"
down_revision: Union[str, None] = "024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_members",
        sa.Column("app_id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), primary_key=True, index=True),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "app_invites",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("app_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("invited_by", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(255), nullable=True, index=True),
        sa.Column("github_login", sa.String(100), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("app_invites")
    op.drop_table("app_members")
