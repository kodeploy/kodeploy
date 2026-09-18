"""add saved_queries — DB 콘솔에서 유저가 저장한 SQL.

왜 플랫폼 DB인가:
  저장된 쿼리는 "이 유저가 이 앱의 이 DB에 대해 자주 쓰는 SQL"이라는 플랫폼 메타데이터다.
  유저 앱 DB에 관리 테이블을 만들면 (1) 유저 소유 스택에 우리 스키마가 끼어들고
  (2) 덤프 복원/DB 교체 때 통째로 날아간다. 그래서 여기(관리 DB)에 둔다.

스코프가 (user_id, app_name, db_type) 세 축인 이유:
  user_id만으로는 앱을 지우고 새로 배포한 뒤 옛 앱의 쿼리가 따라붙고, app_name까지만
  걸면 mysql→postgres로 갈아탄 뒤 방언이 다른 SQL이 섞인다. 읽기 경로가 전부 이 세 축
  조합이라 그대로 인덱스로 만든다.

Revision ID: 020
Revises: 019
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "saved_queries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("app_name", sa.String(50), nullable=False),
        sa.Column("db_type", sa.String(20), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        # 컬럼명 sql은 방언에 따라 예약어로 걸릴 수 있어 sql_text로 둔다 (API 필드는 sql).
        sa.Column("sql_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_saved_queries_scope", "saved_queries", ["user_id", "app_name", "db_type"]
    )


def downgrade() -> None:
    op.drop_index("ix_saved_queries_scope", table_name="saved_queries")
    op.drop_table("saved_queries")
