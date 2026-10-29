"""Alembic environment: uses the app's own DATABASE_URL and model metadata."""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app import models  # noqa: F401  (registers every table on Base.metadata)
from app.database import Base, engine as app_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# The app's engine already normalises postgres:// -> postgresql:// and sets
# SQLite options; reuse its URL so migrations and the app can never disagree.
DB_URL = app_engine.url.render_as_string(hide_password=False)
# Alembic's ConfigParser treats '%' specially - escape it (URL-encoded passwords).
config.set_main_option("sqlalchemy.url", DB_URL.replace("%", "%%"))


def run_migrations_offline() -> None:
    context.configure(url=DB_URL, target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"}, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # render_as_batch: SQLite can't ALTER most things; batch mode rebuilds the table.
        context.configure(connection=connection, target_metadata=target_metadata,
                          render_as_batch=True, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
