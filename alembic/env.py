"""Alembic migration environment for product_db.

The URL comes from DATABASE_URL (validated by app.config), or from
`-x url=...` / config.attributes["url"] for tests that migrate a scratch database.
"""

from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context
from app.models import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def database_url() -> str:
    explicit = config.attributes.get("url") or context.get_x_argument(as_dictionary=True).get("url")
    if explicit:
        return explicit
    from app.config import get_settings

    return get_settings().database_url


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql`: print SQL instead of executing it (for review)."""
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
