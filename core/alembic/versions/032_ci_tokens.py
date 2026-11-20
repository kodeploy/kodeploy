"""apps.ci_deploy_enabled, ci_tokens — GitHub Actions OIDC로 재배포하는 CI 경로.

ci_deploy_enabled: 앱 주인이 켜는 "GitHub Actions 배포 허용". 기본 꺼짐.
ci_tokens: GitHub ID 토큰을 교환해 발급한 CI 토큰. 원문은 응답으로만 내보내고 여기에는 sha256만 둔다.
  jti는 ID 토큰 한 장을 한 번만 교환하게 하는 칸이라 유일하다. 나머지 칸은 그 ID 토큰의 클레임(어느 저장소·브랜치·커밋·실행)이다.

Revision ID: 032
Revises: 031
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "032"
down_revision: Union[str, None] = "031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "apps",
        sa.Column("ci_deploy_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "ci_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("jti", sa.String(128), nullable=False),
        sa.Column("app_id", sa.Uuid(), nullable=False),
        sa.Column("repository", sa.String(200), nullable=False),
        sa.Column("ref", sa.String(255), nullable=False),
        sa.Column("sha", sa.String(40), nullable=False),
        sa.Column("run_id", sa.String(32), nullable=True),
        sa.Column("run_attempt", sa.String(10), nullable=True),
        sa.Column("actor", sa.String(100), nullable=True),
        sa.Column("workflow", sa.String(255), nullable=True),
        sa.Column("event_name", sa.String(30), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_ci_tokens_token_hash"),
        sa.UniqueConstraint("jti", name="uq_ci_tokens_jti"),
    )
    op.create_index("ix_ci_tokens_app_id", "ci_tokens", ["app_id"])


def downgrade() -> None:
    op.drop_index("ix_ci_tokens_app_id", table_name="ci_tokens")
    op.drop_table("ci_tokens")
    op.drop_column("apps", "ci_deploy_enabled")
