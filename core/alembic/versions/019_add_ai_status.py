"""builds.ai_status 추가 — AI 진단이 "진행 중"인지 화면이 알 수 있게.

왜 필요한가:
  파이프라인은 실패를 먼저 커밋하고 그 뒤에 진단을 돈다(수 초~수십 초). 프론트는 status가
  failed가 되면 폴링을 멈추므로, ai_analysis만으로는 "아직 분석 중"과 "진단이 안 붙는 빌드"
  (기능 OFF·예산 초과·LLM 실패)를 가를 수 없다. 그래서 진단이 뒤따를 때 failed와 같은 커밋에
  "pending"을 싣고, 끝나면 "done"으로 닫는다.

기존 행은 NULL로 둔다 — 이미 진단이 붙은 옛 실패 빌드는 ai_analysis만으로 카드가 그려지고,
기다릴 것이 없으니 pending으로 채울 이유가 없다.

Revision ID: 019
Revises: 018
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("builds", sa.Column("ai_status", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("builds", "ai_status")
