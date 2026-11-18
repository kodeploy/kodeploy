"""builds.source_sha, builds.requested_sha — 앱 소스 저장소의 커밋.

source_sha: 빌더가 실제로 빌드한 커밋(40자 hex). 빌더 이벤트(committed·deployed·finished·failed)의 source_sha로 채운다.
requested_sha: 재배포 요청이 지정한 커밋. 비어 있으면 브랜치 최신을 빌드한 것이다.
둘 다 kodeploy-apps(GitOps)에 values를 커밋한 SHA(이벤트의 commit_sha)와는 다른 값이다.

Revision ID: 030
Revises: 029
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "030"
down_revision: Union[str, None] = "029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("builds", sa.Column("source_sha", sa.String(40), nullable=True))
    op.add_column("builds", sa.Column("requested_sha", sa.String(40), nullable=True))


def downgrade() -> None:
    op.drop_column("builds", "requested_sha")
    op.drop_column("builds", "source_sha")
