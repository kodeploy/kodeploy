"""Alembic env — online migration only (offline 불필요)."""

from alembic import context

from app.admin import model as _admin_model  # noqa: F401
from app.apps import model as _apps_model  # noqa: F401
from app.auth import model as _auth_model  # noqa: F401
from app.community import model as _community_model  # noqa: F401
from app.deploy import model as _deploy_model  # noqa: F401
from app.shared.db import Base, engine

target_metadata = Base.metadata


def run_migrations_online() -> None:
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
