import os
import sys
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///./test_kapasite.db"
os.environ["SECRET_KEY"] = "test-secret"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db.session import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    engine.dispose()
    try:
        os.remove("test_kapasite.db")
    except OSError:
        pass


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def auth(client):
    r = client.post("/api/auth/login", data={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
