"""Veri butunlugu: bagli kaydi olan is merkezi silinemez; yetim kayitlar acilista temizlenir."""

import os
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.migrate import repair_orphans
from app.db.session import Base, engine, get_db
from app.main import app
from app.models import User
from app.core.security import hash_password
from tests.test_capacity_flow import _upload


def test_workcenter_with_routing_cannot_be_deleted(client, auth):
    _upload(client, auth, "workcenters", ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)", "Birim Saat", "Kişi Başı Verimli Saat"], [["INT-1", "Bütünlük", "E", 10, 4]])
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["INT-ITEM", "Bütünlük ürünü", "G"]])
    _upload(client, auth, "routing", ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)"], [["INT-ITEM", 10, "Op", "INT-1", 60]])
    wc = next(w for w in client.get("/api/workcenters", headers=auth).json() if w["code"] == "INT-1")

    r = client.delete(f"/api/workcenters/{wc['id']}", headers=auth)
    assert r.status_code == 400
    assert "rota operasyonu" in r.json()["detail"]

    # rota hala saglam; urun ihtiyac ekrani 500 vermez
    r = client.get("/api/requirements/item", headers=auth, params={"item_code": "INT-ITEM", "quantity": 10})
    assert r.status_code == 200 and r.json()["operations"][0]["work_center_code"] == "INT-1"

    # rota silinince is merkezi silinebilir (vardiyalar cascade)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM routing_operations WHERE work_center_id = :i"), {"i": wc["id"]})
    assert client.delete(f"/api/workcenters/{wc['id']}", headers=auth).status_code == 204
    assert all(w["code"] != "INT-1" for w in client.get("/api/workcenters", headers=auth).json())


def _sqlite_engine_with_fk(path: Path):
    eng = create_engine(f"sqlite:///{path.as_posix()}", connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return eng


def test_orphan_repair_and_tolerance(client, auth):
    """FK=OFF yalnizca izole engine uzerinde; paylasilan test engine kirletilmez."""
    iso_path = Path(__file__).resolve().parent / f"orphan_integrity_{os.getpid()}.db"
    if iso_path.exists():
        iso_path.unlink()

    iso_engine = _sqlite_engine_with_fk(iso_path)
    Base.metadata.create_all(bind=iso_engine)
    IsoSession = sessionmaker(bind=iso_engine, autoflush=False, autocommit=False, expire_on_commit=False)

    settings = get_settings()
    with IsoSession() as s:
        if not s.query(User).filter(User.username == settings.first_admin_username).first():
            s.add(
                User(
                    username=settings.first_admin_username,
                    full_name="Yonetici",
                    role="admin",
                    hashed_password=hash_password(settings.first_admin_password),
                )
            )
            s.commit()

    def override_get_db():
        db = IsoSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        _upload(client, auth, "workcenters", ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)", "Birim Saat", "Kişi Başı Verimli Saat"], [["ORP-1", "Yetim", "E", 10, 4]])
        _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["ORP-ITEM", "Yetim ürünü", "G"]])
        _upload(client, auth, "routing", ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)"], [["ORP-ITEM", 10, "Op", "ORP-1", 60]])
        wc = next(w for w in client.get("/api/workcenters", headers=auth).json() if w["code"] == "ORP-1")

        raw = iso_engine.raw_connection()
        try:
            cur = raw.cursor()
            cur.execute("PRAGMA foreign_keys=OFF")
            cur.execute("DELETE FROM work_center_shifts WHERE work_center_id = ?", (wc["id"],))
            cur.execute("DELETE FROM work_centers WHERE id = ?", (wc["id"],))
            raw.commit()
            cur.execute("PRAGMA foreign_keys=ON")
            assert cur.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        finally:
            raw.close()

        r = client.get("/api/requirements/item", headers=auth, params={"item_code": "ORP-ITEM", "quantity": 1})
        assert r.status_code == 200 and r.json()["operations"][0]["work_center_code"].startswith("(silinmi")

        removed = repair_orphans(iso_engine)
        assert removed.get("routing_operations.work_center_id") == 1
        r = client.get("/api/requirements/item", headers=auth, params={"item_code": "ORP-ITEM", "quantity": 1})
        assert r.status_code == 200 and r.json()["operations"] == []
    finally:
        app.dependency_overrides.pop(get_db, None)
        iso_engine.dispose()
        try:
            iso_path.unlink(missing_ok=True)
        except OSError:
            pass

    # Paylasilan engine FK acik kalmali (regresyon)
    with engine.connect() as conn:
        fk = conn.execute(text("PRAGMA foreign_keys")).scalar()
        assert fk == 1
