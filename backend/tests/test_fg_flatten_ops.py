"""6xxxxx mamulde birlestirilmis operasyon listesi."""

from app.db.session import SessionLocal
from app.models import Item
from app.services.bom_tree import flatten_fg_operations
from app.services.requirements import item_total_hours
from sqlalchemy.orm import joinedload


def test_6000009_shows_all_operations(db):
    item = (
        db.query(Item)
        .options(joinedload(Item.bom_lines), joinedload(Item.operations))
        .filter(Item.code == "6000009")
        .first()
    )
    if not item:
        return  # BOM import yoksa atla
    flat = flatten_fg_operations(db, item)
    assert len(flat) > 10, f"beklenen cok op, gelen {len(flat)}"
    assert flat[-1].operation.operation_name  # bitis rotasi
    names = [f.operation.operation_name for f in flat]
    assert "PAKETLEME" in names
    assert flat[0].operation.operation_name in ("LAZER KESME", "DELIK DELME", "PRES BASKI", "SIVAMA")
    assert names.index("PAKETLEME") == len(names) - 1

    hrs = item_total_hours(db, "6000009", 1)
    assert hrs["total_hours"] > 1.0
    assert len(hrs["operations"]) == len(flat)
