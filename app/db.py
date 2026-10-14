from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker


def create_db_engine(database_url: str, pool_size: int = 5) -> Engine:
    # pool_pre_ping: transparently replace connections the database closed (restarts, idle timeouts).
    return create_engine(database_url, pool_size=pool_size, max_overflow=pool_size, pool_pre_ping=True)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    # expire_on_commit=False: objects stay readable after commit, for building the response.
    return sessionmaker(engine, expire_on_commit=False)


def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """One session (and transaction) per request: commit on success, roll back on error."""
    with factory() as session:
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise


def ping(engine: Engine) -> None:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
