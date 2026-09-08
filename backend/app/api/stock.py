"""Bitmis urun stogu: depo girisi, rezervasyon (manuel/otomatik), sevk."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import require_poweruser, require_user
from app.db.session import get_db
from app.models import User
from app.schemas import (
    AutoReserveRequest,
    AutoReserveResult,
    OrderStockRow,
    ReceiptIn,
    ReceiptOut,
    ReservationIn,
    ReservationOut,
    ShipIn,
    ShipmentOut,
    StockRow,
)
from app.services import stock
from app.services import orders as orders_svc

router = APIRouter(prefix="/api/stock", tags=["stock"])


def _run(db: Session, fn, *args):
    try:
        out = fn(db, *args)
        db.commit()
        return out
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))


@router.get("/summary", response_model=list[StockRow])
def summary(only_with_stock: bool = Query(False), db: Session = Depends(get_db), _=Depends(require_user)):
    return stock.stock_summary(db, only_with_stock)


@router.get("/orders", response_model=list[OrderStockRow])
def orders(item_id: int | None = None, include_closed: bool = False, position: str | None = None, db: Session = Depends(get_db), _=Depends(require_user)):
    rows = stock.order_rows(db, item_id, include_closed, position)
    # plan sonucu tahmini bitis (varsa)
    try:
        sched = {s.order_id: s.planned_end for s in orders_svc.order_schedule(db, None)}
    except Exception:  # noqa: BLE001 - plan yoksa/hatasa sadece bos gecilir
        sched = {}
    for r in rows:
        r.planned_end = sched.get(r.order_id)
    return rows


# ---- depo girisi ----
@router.get("/receipts", response_model=list[ReceiptOut])
def receipts(item_id: int | None = None, db: Session = Depends(get_db), _=Depends(require_user)):
    return stock.list_receipts(db, item_id)


@router.post("/receipts", response_model=ReceiptOut, status_code=201)
def add_receipt(data: ReceiptIn, db: Session = Depends(get_db), user: User = Depends(require_poweruser)):
    r = _run(db, stock.add_receipt, data.item_code, data.receipt_date, data.quantity, data.lot, data.note, user.username)
    db.refresh(r)
    return stock._receipt_out(r)


@router.delete("/receipts/{receipt_id}", status_code=204)
def delete_receipt(receipt_id: int, db: Session = Depends(get_db), _=Depends(require_poweruser)):
    _run(db, stock.delete_receipt, receipt_id)


# ---- rezervasyon ----
@router.get("/reservations", response_model=list[ReservationOut])
def reservations(item_id: int | None = None, order_id: int | None = None, position: str | None = None, db: Session = Depends(get_db), _=Depends(require_user)):
    return stock.list_reservations(db, item_id, order_id, position)


@router.post("/reservations", response_model=ReservationOut, status_code=201)
def reserve(data: ReservationIn, db: Session = Depends(get_db), user: User = Depends(require_poweruser)):
    r = _run(db, stock.reserve_manual, data.item_id, data.item_code, data.order_id, data.quantity, data.note, user.username)
    db.refresh(r)
    return stock._res_out(r)


@router.post("/reservations/auto", response_model=AutoReserveResult)
def auto(req: AutoReserveRequest, db: Session = Depends(get_db), user: User = Depends(require_poweruser)):
    return _run(db, stock.auto_reserve, req.item_ids, user.username)


@router.delete("/reservations/{res_id}", status_code=204)
def release(res_id: int, db: Session = Depends(get_db), _=Depends(require_poweruser)):
    _run(db, stock.release, res_id)


@router.patch("/reservations/{res_id}/move", response_model=ReservationOut)
def move(res_id: int, order_id: int = Query(...), db: Session = Depends(get_db), user: User = Depends(require_poweruser)):
    r = _run(db, stock.move, res_id, order_id, user.username)
    db.refresh(r)
    return stock._res_out(r)


@router.post("/reservations/{res_id}/ship", response_model=ShipmentOut, status_code=201)
def ship(res_id: int, data: ShipIn, db: Session = Depends(get_db), user: User = Depends(require_poweruser)):
    s = _run(db, stock.ship_reservation, res_id, data.quantity, data.ship_date or date.today(), data.note, user.username)
    db.refresh(s)
    return stock._ship_out(s)


# ---- sevk ----
@router.get("/shipments", response_model=list[ShipmentOut])
def shipments(item_id: int | None = None, order_id: int | None = None, position: str | None = None, db: Session = Depends(get_db), _=Depends(require_user)):
    return stock.list_shipments(db, item_id, order_id, position)


@router.delete("/shipments/{shipment_id}", status_code=204)
def undo_shipment(shipment_id: int, db: Session = Depends(get_db), user: User = Depends(require_poweruser)):
    _run(db, stock.undo_shipment, shipment_id, user.username)
