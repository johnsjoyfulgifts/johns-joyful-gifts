import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_db_fd, _db_path = tempfile.mkstemp(suffix=".db")
os.close(_db_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path}"
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["ENVIRONMENT"] = "development"

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.database import Base, engine  # noqa: E402
from app import models  # noqa: E402,F401

Base.metadata.create_all(bind=engine)

from app.main import app  # noqa: E402
from app.database import SessionLocal  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _cleanup_db_file():
    yield
    engine.dispose()
    try:
        os.remove(_db_path)
    except OSError:
        pass


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
