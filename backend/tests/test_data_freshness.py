"""Gunluk veri guncellik testleri."""

from datetime import date, datetime

from app.models import ImportLog
from app.services.data_freshness import daily_freshness


def _clear_import_logs(db):
    db.query(ImportLog).delete()
    db.commit()


def test_freshness_missing_when_no_import(db):
    _clear_import_logs(db)
    out = daily_freshness(db, today=date(2026, 9, 8))
    assert out["needs_attention"] is True
    assert all(c["status"] == "missing" for c in out["checkpoints"])


def test_freshness_ok_when_imported_today(db):
    _clear_import_logs(db)
    today = date(2026, 9, 8)
    now = datetime(2026, 9, 8, 9, 0, 0)
    for kind in ("orders", "production", "stock_receipts"):
        db.add(ImportLog(kind=kind, filename=f"{kind}.xlsx", username="admin", inserted=1, updated=0, errors="", created_at=now))
    db.commit()
    out = daily_freshness(db, today=today)
    by_key = {c["key"]: c for c in out["checkpoints"]}
    assert by_key["open_orders"]["status"] == "ok"
    assert by_key["production_output"]["status"] == "ok"
    assert by_key["wip_stock"]["status"] == "ok"
    assert by_key["finished_stock"]["status"] == "ok"
    assert out["needs_attention"] is False


def test_freshness_stale_after_calendar_day(db):
    _clear_import_logs(db)
    today = date(2026, 9, 8)
    yesterday = datetime(2026, 9, 7, 16, 0, 0)
    db.add(ImportLog(kind="orders", filename="o.xlsx", username="admin", inserted=1, errors="", created_at=yesterday))
    db.commit()
    out = daily_freshness(db, today=today)
    orders_cp = next(c for c in out["checkpoints"] if c["key"] == "open_orders")
    assert orders_cp["status"] == "stale"
    assert out["needs_attention"] is True
