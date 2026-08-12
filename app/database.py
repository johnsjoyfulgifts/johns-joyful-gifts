from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()


def _pragmas(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    # WAL lets readers proceed without blocking on writers (and vice versa),
    # so ordinary page-view queries stay fast even while a checkout is writing.
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=15000")
    cursor.close()


engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False, "timeout": 15},
)
event.listens_for(engine, "connect")(_pragmas)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- Dedicated engine for the order-creation critical section only ---
#
# Every transaction opened on this second engine begins with BEGIN IMMEDIATE
# instead of the default deferred BEGIN, so it takes SQLite's write lock the
# moment it starts rather than upgrading from a read lock on first write.
# That's what makes stock read-then-decrement race-free: two concurrent
# checkouts for the same product serialize here (the second blocks until the
# first commits, then re-reads genuinely current stock) instead of both
# reading stale stock and both succeeding — the spec's "Customer A and B both
# order the last 2 units" oversell scenario.
#
# This is a *separate* engine (same file) precisely so ordinary reads
# (page views, admin lists, the idempotency pre-check) keep using plain
# deferred transactions on the main `engine` and don't compete for the
# write lock or give up WAL's concurrent-read benefit.
write_engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False, "timeout": 15},
)
event.listens_for(write_engine, "connect")(_pragmas)


@event.listens_for(write_engine, "begin")
def _begin_immediate(conn):
    conn.exec_driver_sql("BEGIN IMMEDIATE")


@contextmanager
def immediate_write_session():
    with write_engine.begin() as conn:
        session = Session(bind=conn, autoflush=False, expire_on_commit=False)
        try:
            yield session
            session.flush()
        finally:
            session.close()
