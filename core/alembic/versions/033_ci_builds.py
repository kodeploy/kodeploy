"""ci_builds — CI 토큰으로 시작한 빌드의 출처 (어느 Actions 실행이 불렀나).

빌드 하나에 행 하나 (build_id 기본키). 값은 그 CI 토큰의 클레임을 옮겨 적는다 — 토큰 행이 지워져도 남게.
deploy_requests에 칸을 더하지 않은 이유는 app/ci/model.py의 CiBuild 설명.

Revision ID: 033
Revises: 032
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "033"
down_revision: Union[str, None] = "032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ci_builds",
        sa.Column("build_id", sa.String(8), primary_key=True),
        sa.Column("app_id", sa.Uuid(), nullable=False),
        sa.Column("token_id", sa.Integer(), nullable=True),
        sa.Column("repository", sa.String(200), nullable=False),
        sa.Column("ref", sa.String(255), nullable=False),
        sa.Column("sha", sa.String(40), nullable=False),
        sa.Column("run_id", sa.String(32), nullable=True),
        sa.Column("run_attempt", sa.String(10), nullable=True),
        sa.Column("actor", sa.String(100), nullable=True),
        sa.Column("workflow", sa.String(255), nullable=True),
        sa.Column("event_name", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_ci_builds_app_id", "ci_builds", ["app_id"])


def downgrade() -> None:
    op.drop_index("ix_ci_builds_app_id", table_name="ci_builds")
    op.drop_table("ci_builds")
