from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

# pool_pre_ping + pool_recycle: after the app has sat idle for a while (laptop
# sleep, Postgres restart, pooler dropping idle sockets) the pool can hand out
# a dead connection, failing the next request with a 500 that the Android app
# surfaces as "offline". Pre-ping transparently replaces dead connections.
engine = create_engine(settings.database_url, pool_pre_ping=True, pool_recycle=1800)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
