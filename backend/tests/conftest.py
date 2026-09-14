import os
import sys
from pathlib import Path

_test_db = Path(__file__).resolve().parent / f"test_kapasite_{os.getpid()}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_test_db.as_posix()}"
os.environ["SECRET_KEY"] = "test-secret"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db.session import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _db():
    engine.dispose()
    if _test_db.exists():
        try:
            _test_db.unlink()
        except OSError:
            pass
    Base.metadata.create_all(bind=engine)
    yield
    engine.dispose()
    try:
        _test_db.unlink(missing_ok=True)
    except OSError:
        pass


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(autouse=True)
def _isolate_planning_artifacts(db):
    """Testler arasi plan/uretim/siparis kalintisi otomatik planlamayi etkilemesin."""
    from app.models import Downtime, Order, PlanLine, ProductionActual
    from app.models.planning import (
        OrderMaterialLog,
        PlanRevision,
        PlanRevisionChange,
        PlanRevisionEvent,
        PlanRevisionSnapshot,
        ProductionBatch,
        ProductionBatchOrder,
        Reservation,
        Shipment,
    )

    db.query(PlanRevisionEvent).delete()
    db.query(PlanRevisionSnapshot).delete()
    db.query(PlanRevisionChange).delete()
    db.query(PlanRevision).delete()
    db.query(PlanLine).delete()
    db.query(Downtime).delete()
    db.query(ProductionActual).delete()
    db.query(ProductionBatchOrder).delete()
    db.query(ProductionBatch).delete()
    db.query(Shipment).delete()
    db.query(Reservation).delete()
    db.query(OrderMaterialLog).delete()
    db.query(Order).filter(Order.merged_into_id.isnot(None)).update({Order.merged_into_id: None}, synchronize_session=False)
    db.query(Order).delete()
    db.commit()
    yield


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def auth(client):
    r = client.post("/api/auth/login", data={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
