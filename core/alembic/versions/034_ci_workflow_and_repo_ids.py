"""apps.ci_workflow, apps.ci_repo_ids — CI 토큰 교환에서 워크플로 파일과 저장소 ID를 확인한다.

- ci_workflow: 교환을 허용할 워크플로 파일 경로(.github/workflows/<이름>.yml). 비면 지금처럼 어느 워크플로든 받는다.
- ci_repo_ids: {"owner/repo"(소문자): "<GitHub 저장소 ID>"}. 허용을 켤 때 GitHub에서 받아 두고, 받지 못한 저장소는
  첫 교환의 repository_id 클레임으로 채운다. 같은 이름으로 다시 만든 저장소를 구분하려는 값이다.

둘 다 nullable 추가라 옛 이미지로 되돌려도 내릴 필요가 없다.

Revision ID: 034
Revises: 033
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "034"
down_revision: Union[str, None] = "033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("apps", sa.Column("ci_workflow", sa.String(255), nullable=True))
    op.add_column("apps", sa.Column("ci_repo_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("apps", "ci_repo_ids")
    op.drop_column("apps", "ci_workflow")
