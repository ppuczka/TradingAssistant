import os
from pathlib import Path

from alembic import context

from trading_assistant.adapters.database import Base, database_engine


def run(connection):
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(url="sqlite://", target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
elif "connection" in context.config.attributes:
    run(context.config.attributes["connection"])
else:
    engine = database_engine(Path(os.getenv("TRADER_DATABASE_PATH", "data/portfolio.sqlite3")))
    try:
        with engine.begin() as connection:
            run(connection)
    finally:
        engine.dispose()
