"""Gunluk veri guncellik testleri."""

from datetime import date, datetime

import pytest
from fastapi import HTTPException

from app.models import DailyDataAck, ImportLog
from app.services.data_freshness import confirm_no_change, daily_freshness


def _clear_import_logs(db):
    db.query(DailyDataAck).delete()
    db.query(ImportLog).delete()
    db.commit()


def test_freshness_missing_when_no_import(db):
    _clear_import_logs(db)
    out = daily_freshness(db, today=date(2026, 9, 8))
    assert out["needs_attention"] is True
    assert all(c["status"] == "missing" for c in out["checkpoints"])
    assert all(c["can_confirm_no_change"] is False for c in out["checkpoints"])


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
    assert by_key["open_orders"]["update_source"] == "import"
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
    assert orders_cp["can_confirm_no_change"] is True
    assert out["needs_attention"] is True


def test_confirm_no_change_marks_stale_ok(db):
    _clear_import_logs(db)
    today = date(2026, 9, 8)
    yesterday = datetime(2026, 9, 7, 16, 0, 0)
    for kind in ("orders", "production", "stock_receipts"):
        db.add(ImportLog(kind=kind, filename=f"{kind}.xlsx", username="admin", inserted=1, errors="", created_at=yesterday))
    db.commit()

    out = confirm_no_change(db, "open_orders", "ozan.deniz", today=today)
    orders_cp = next(c for c in out["checkpoints"] if c["key"] == "open_orders")
    assert orders_cp["status"] == "ok"
    assert orders_cp["confirmed_no_change_today"] is True
    assert orders_cp["confirmed_no_change_by"] == "ozan.deniz"
    assert "değişiklik yok" in orders_cp["detail"].lower()

    prod_cp = next(c for c in out["checkpoints"] if c["key"] == "production_output")
    assert prod_cp["status"] == "stale"


def test_confirm_no_change_wip_independent_from_production(db):
    _clear_import_logs(db)
    today = date(2026, 9, 8)
    yesterday = datetime(2026, 9, 7, 16, 0, 0)
    db.add(ImportLog(kind="production", filename="p.xlsx", username="admin", inserted=1, errors="", created_at=yesterday))
    db.commit()

    out = confirm_no_change(db, "wip_stock", "ozan.deniz", today=today)
    by_key = {c["key"]: c for c in out["checkpoints"]}
    assert by_key["wip_stock"]["status"] == "ok"
    assert by_key["production_output"]["status"] == "stale"


def test_confirm_no_change_rejects_missing(db):
    _clear_import_logs(db)
    today = date(2026, 9, 8)
    with pytest.raises(HTTPException) as exc:
        confirm_no_change(db, "open_orders", "ozan.deniz", today=today)
    assert exc.value.status_code == 400


def test_confirm_no_change_invalid_key(db):
    _clear_import_logs(db)
    with pytest.raises(HTTPException) as exc:
        confirm_no_change(db, "invalid_key", "ozan.deniz", today=date(2026, 9, 8))
    assert exc.value.status_code == 404
