"""A signed, short-lived reservation proposal; confirmation revalidates under a write lock."""

import hashlib
import json
from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import ALGORITHM
from app.models import Reservation
from app.schemas import AutoReserveLine, AutoReservePreview, AutoReserveResult
from app.services import stock


class StalePreview(ValueError):
    pass


def _signing_key() -> bytes:
    # Separate from login tokens: a preview token must never authenticate an API request.
    return hashlib.sha256(("stock-auto-preview:" + get_settings().secret_key).encode()).digest()


def lock_stock(db: Session):
    # Covers every stock API write, including simultaneous confirmation requests.
    # NOWAIT keeps an operator from waiting behind a long import/transaction.
    if db.bind.dialect.name == "postgresql":
        db.execute(text("LOCK TABLE orders, stock_receipts, reservations, shipments IN SHARE ROW EXCLUSIVE MODE NOWAIT"))
    elif db.bind.dialect.name == "sqlite":
        if not db.connection().connection.driver_connection.in_transaction:
            db.execute(text("BEGIN IMMEDIATE"))


def _proposal(db: Session, item_ids: list[int] | None):
    stocks = stock.stock_summary(db)
    if item_ids:
        stocks = [s for s in stocks if s.item_id in item_ids]
    by_item = {s.item_id: s for s in stocks}
    orders = [o for o in stock.order_rows(db) if o.item_id in by_item]
    orders.sort(key=lambda o: (o.due_date, o.order_no, o.order_id))
    free = {s.item_id: s.free for s in stocks}
    lines = []
    for order in orders:
        before = free[order.item_id]
        take = round(min(max(order.remaining, 0), max(before, 0)), 3)
        if take <= 0:
            continue
        free[order.item_id] = round(before - take, 3)
        lines.append(AutoReserveLine(**order.model_dump(), allocate=take,
                                    remaining_after=round(order.remaining - take, 3),
                                    free_before=before, free_after=free[order.item_id]))
    state = {"stocks": [s.model_dump(mode="json") for s in sorted(stocks, key=lambda s: s.item_id)],
             "orders": [o.model_dump(mode="json") for o in orders]}
    digest = hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return lines, digest


def preview(db: Session, item_ids: list[int] | None, username: str) -> AutoReservePreview:
    try:
        lines, digest = _proposal(db, item_ids)
        token = jwt.encode({"kind": "stock-auto-preview-v1", "sub": username,
                            "item_ids": sorted(set(item_ids)) if item_ids else None, "digest": digest,
                            "exp": datetime.now(timezone.utc) + timedelta(minutes=15)},
                           _signing_key(), algorithm=ALGORITHM)
        return AutoReservePreview(preview_token=token, rows=lines,
                                  reserved_qty=round(sum(r.allocate for r in lines), 3),
                                  items=len({r.item_id for r in lines}))
    finally:
        # stock_summary projects production receipts. Even those must not persist on preview/cancel.
        db.rollback()


def confirm(db: Session, token: str, username: str) -> AutoReserveResult:
    try:
        claims = jwt.decode(token, _signing_key(), algorithms=[ALGORITHM],
                            options={"require": ["exp", "sub", "kind", "digest"]})
        if claims["kind"] != "stock-auto-preview-v1" or claims["sub"] != username or "item_ids" not in claims:
            raise ValueError("wrong preview")
    except (jwt.PyJWTError, ValueError) as exc:
        raise StalePreview("Önizleme geçersiz veya süresi dolmuş. Önizlemeyi yenileyin.") from exc
    lines, digest = _proposal(db, claims["item_ids"])
    if digest != claims["digest"]:
        raise StalePreview("Stok veya sipariş bilgileri değişti. Hiçbir rezervasyon yapılmadı; önizlemeyi yenileyin.")
    for row in lines:
        db.add(Reservation(item_id=row.item_id, order_id=row.order_id, quantity=row.allocate,
                           source="auto", note="otomatik (termin sirasi, onayli)", created_by=username))
    db.flush()
    total = round(sum(r.allocate for r in lines), 3)
    items = len({r.item_id for r in lines})
    return AutoReserveResult(created=len(lines), reserved_qty=total, items=items,
                             message=f"{len(lines)} rezervasyon oluşturuldu ({total:g} adet, {items} ürün).")
