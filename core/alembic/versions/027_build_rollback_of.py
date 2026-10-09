"""builds.rollback_of — 이 배포가 어느 배포로 되돌린 것인지.

롤백은 새 배포 행으로 남고(이력에 보인다), 되돌아간 원본 배포의 build_id를 여기에 적는다. 일반 배포는 NULL.

Revision ID: 027
Revises: 026
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "027"
down_revision: Union[str, None] = "026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("builds", sa.Column("rollback_of", sa.String(8), nullable=True))


def downgrade() -> None:
    op.drop_column("builds", "rollback_of")
