"""Shared pytest fixtures using an isolated in-memory SQLite database."""

import os
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import get_db
from app.main import app
from app.models.database_models import Base
from app.security.authentication import create_access_token


@pytest.fixture(autouse=True)
def ensure_offline_testing(monkeypatch):
    """Ensure automated tests run strictly offline with mock provider by default."""
    monkeypatch.setattr(settings, "llm_provider", "mock")


@pytest.fixture(scope="function")
def db_engine():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        temp_db_path = f.name

    engine = create_engine(
        f"sqlite:///{temp_db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    try:
        os.remove(temp_db_path)
    except OSError:
        pass


@pytest.fixture(scope="function")
def db_session(db_engine):
    Session = sessionmaker(
        autocommit=False, autoflush=False, expire_on_commit=False, bind=db_engine
    )
    session = Session()
    yield session
    session.close()


@pytest.fixture(scope="function")
def client(db_engine):
    """Test client with overridden DB dependency pointing to isolated in-memory DB."""
    Session = sessionmaker(
        autocommit=False, autoflush=False, expire_on_commit=False, bind=db_engine
    )

    def override_get_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def customer_token():
    return create_access_token(subject="customer1", role="customer")


@pytest.fixture
def customer2_token():
    return create_access_token(subject="customer2", role="customer")


@pytest.fixture
def reviewer_token():
    return create_access_token(subject="reviewer1", role="internal_reviewer")


@pytest.fixture
def customer_headers(customer_token):
    return {"Authorization": f"Bearer {customer_token}"}


@pytest.fixture
def customer2_headers(customer2_token):
    return {"Authorization": f"Bearer {customer2_token}"}


@pytest.fixture
def reviewer_headers(reviewer_token):
    return {"Authorization": f"Bearer {reviewer_token}"}
