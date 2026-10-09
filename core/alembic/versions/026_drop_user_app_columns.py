"""users의 옛 앱 칸을 지운다 — 앱 속성은 apps 테이블로 옮겨 갔다.

app_name, site_enabled, custom_domain, custom_domain_status, extra_hostnames, pipeline은 023부터 core가 읽지도
쓰지도 않는 옛 사본이다 (이 칸들을 ORM 모델에서 뺀 배포가 먼저 나갔다 — 그래서 지금 지워도 옛 서버가 이 칸을
조회하다 깨지지 않는다).

downgrade는 칸을 다시 만들고 각 유저의 첫 앱(apps)에서 값을 채운다 (유저당 앱이 하나이던 때의 모양).

Revision ID: 026
Revises: 025
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "026"
down_revision: Union[str, None] = "025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = ("app_name", "site_enabled", "custom_domain", "custom_domain_status", "extra_hostnames", "pipeline")


def upgrade() -> None:
    for name in _COLUMNS:
        op.drop_column("users", name)


def downgrade() -> None:
    op.add_column("users", sa.Column("app_name", sa.String(50), nullable=True))
    op.add_column("users", sa.Column("site_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")))
    op.add_column("users", sa.Column("custom_domain", sa.String(253), nullable=True))
    op.add_column("users", sa.Column("custom_domain_status", sa.String(20), nullable=True))
    op.add_column("users", sa.Column("extra_hostnames", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("pipeline", sa.String(2), nullable=False, server_default="v1"))
    if op.get_context().as_sql:
        return
    first_app = "(SELECT apps.{col} FROM apps WHERE apps.owner_id = users.id ORDER BY apps.created_at LIMIT 1)"
    for col in ("name", "site_enabled", "custom_domain", "custom_domain_status", "extra_hostnames", "pipeline"):
        target = "app_name" if col == "name" else col
        sql = f"UPDATE users SET {target} = COALESCE({first_app.format(col=col)}, {target})"
        op.get_bind().execute(sa.text(sql))
    if op.get_context().dialect.name != "sqlite":   # SQLite는 ALTER로 제약을 못 더한다 (테스트용)
        op.create_unique_constraint("uq_users_app_name", "users", ["app_name"])
        op.create_unique_constraint("uq_users_custom_domain", "users", ["custom_domain"])
