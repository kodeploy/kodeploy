"""apps.pipeline 기본값을 v2로 — 새 앱은 Go 빌더 → kodeploy-apps → Argo 경로로 시작한다.

기존 행은 그대로 둔다. v1 앱은 deploy/build/adopt.py로 하나씩 넘긴다.

Revision ID: 029
Revises: 028
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "029"
down_revision: Union[str, None] = "028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("apps", "pipeline", server_default="v2", existing_type=sa.String(2), existing_nullable=False)


def downgrade() -> None:
    op.alter_column("apps", "pipeline", server_default="v1", existing_type=sa.String(2), existing_nullable=False)
