import os
import sys

import pytest
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# DATABASE_URL comes from .env (the Supabase Postgres pooler URL) unless a test
# runner already set it — row locking (with_for_update, pg_advisory_xact_lock)
# is Postgres-only, so tests can no longer run against a throwaway SQLite file.
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("ENVIRONMENT", "development")

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.database import Base, SessionLocal  # noqa: E402
from app.database import engine as app_engine  # noqa: E402
from app import models  # noqa: E402,F401

TEST_SCHEMA = "test"

# Tests run against the same free Supabase project as production (there's
# only one). Every statement issued through test_engine is compiled with an
# explicit "test" schema qualifier via schema_translate_map, so it can never
# touch "public" (where real product/order/customer data lives) no matter
# what does or doesn't already exist in either schema. An earlier version of
# this fixture relied on Postgres's search_path instead, which silently fell
# back to "public" for DROP/reflection whenever "test" didn't yet contain a
# matching table — that landmine wiped the real schema twice while this was
# being built. schema_translate_map has no such fallback.
test_engine = app_engine.execution_options(schema_translate_map={None: TEST_SCHEMA})
SessionLocal.configure(bind=test_engine)

with app_engine.connect() as _conn:
    _conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {TEST_SCHEMA}"))
    _conn.commit()

Base.metadata.drop_all(bind=test_engine)
Base.metadata.create_all(bind=test_engine)

from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _cleanup_db():
    yield
    Base.metadata.drop_all(bind=test_engine)
    app_engine.dispose()


@pytest.fixture
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def fastapi_app():
    return app
