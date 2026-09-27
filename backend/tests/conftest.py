import os
from datetime import date

import pytest

# Configure before the app is imported.
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("SEED_ON_STARTUP", "false")
os.environ.setdefault("ALLOW_DEMO_RESET", "true")
os.environ.setdefault("AI_PROVIDER", "heuristic")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import db  # noqa: E402
from app.db import Base, get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402
from app.services.ai import rate_limiter  # noqa: E402

SEED_START = date(2026, 10, 1)


@pytest.fixture()
def session_factory():
    # Default: a fresh in-memory SQLite per test. Set TEST_DATABASE_URL to run
    # the same suite against PostgreSQL (see docs/TESTING.md).
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        engine = db.make_engine(url)
        Base.metadata.drop_all(engine)
    else:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture()
def session(session_factory):
    with session_factory() as s:
        yield s


@pytest.fixture()
def client(session_factory, monkeypatch):
    def _override():
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _override
    monkeypatch.setattr(db, "SessionLocal", session_factory)
    rate_limiter.reset()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def seeded(client, session_factory):
    with session_factory() as s:
        seed(s, reset=True, start=SEED_START)
    return client


def by_title(board: dict) -> dict:
    return {t["title"]: t for t in board["tasks"]}
