"""Go 빌더 연결 — 앱별 pipeline 플래그, 콜백 seq, GitOps 단계 시각.

users.pipeline: 'v1'(core가 직접 빌드·배포, 지금 그대로) | 'v2'(빌더 → kodeploy-apps 커밋 → Argo).
  기존 행은 전부 'v1'. v2는 운영자가 DB에서 앱 하나씩 켠다 (넘겨받기 버튼은 아직 없음).
builds.last_event_seq: 빌더 콜백에서 마지막으로 처리한 seq. v1 빌드는 NULL, v2 빌드는 만들 때 0.
  같은 이벤트가 다시 오면(최소 한 번 전달) 이 값 이하라 무시한다. v2 빌드인지 구분하는 표시도 겸한다.
build_records.git_committed_at / argo_synced_at: 빌더가 values를 커밋한 시각, Argo가 우리 커밋으로
  Synced된 시각 (committed·deployed 이벤트). v1 기록은 NULL.

칸 추가만 한다. downgrade는 네 칸을 지운다.

Revision ID: 021
Revises: 020
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("pipeline", sa.String(2), nullable=False, server_default="v1"))
    op.add_column("builds", sa.Column("last_event_seq", sa.BigInteger(), nullable=True))
    op.add_column("build_records", sa.Column("git_committed_at", sa.DateTime(), nullable=True))
    op.add_column("build_records", sa.Column("argo_synced_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("build_records", "argo_synced_at")
    op.drop_column("build_records", "git_committed_at")
    op.drop_column("builds", "last_event_seq")
    op.drop_column("users", "pipeline")
