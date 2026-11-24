"""cli_tokens — 앱 주인이 웹에서 발급하는 CLI 토큰 (로컬 kodeploy CLI용).

원문(kdcli_…)은 발급 응답으로만 내보내고 여기에는 sha256만 둔다. expires_at은 마지막으로 쓴 뒤 15분이라 쓸 때 늘어나고,
발급 뒤 8시간 상한은 created_at에서 계산한다. 폐기·재발급으로 끝난 토큰은 지우지 않고 revoked_at을 적는다.

새 표만 만들어서 옛 이미지로 되돌려도 내릴 필요가 없다.

Revision ID: 035
Revises: 034
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "035"
down_revision: Union[str, None] = "034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cli_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("app_id", sa.Uuid(), nullable=False),
        sa.Column("issued_by", sa.Uuid(), nullable=False),
        sa.Column("issued_by_login", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_cli_tokens_token_hash"),
    )
    op.create_index("ix_cli_tokens_app_id", "cli_tokens", ["app_id"])


def downgrade() -> None:
    op.drop_index("ix_cli_tokens_app_id", table_name="cli_tokens")
    op.drop_table("cli_tokens")
