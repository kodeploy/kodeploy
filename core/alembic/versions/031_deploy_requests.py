"""deploy_requests — 재배포 요청의 Idempotency-Key 선점 기록.

키 하나가 빌드 여러 개(서버·정적)에 대응하고 같은 키가 동시에 두 번 와도 하나만 통과해야 해서,
builds 행이 아니라 따로 둔다. (app_id, key) 유일. build_ids가 비어 있으면 아직 처리 중이다.

Revision ID: 031
Revises: 030
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "031"
down_revision: Union[str, None] = "030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "deploy_requests",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("app_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("build_ids", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("app_id", "key", name="uq_deploy_requests_app_key"),
    )


def downgrade() -> None:
    op.drop_table("deploy_requests")
