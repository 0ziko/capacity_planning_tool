"""Is merkezleri, vardiyalar, personel, stok/BOM/rota, siparisler."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.core.deps import require_poweruser, require_user
from app.db.session import get_db
from app.models import Employee, Item, Order, WorkCenter, WorkCenterShift
from app.schemas import (
    EmployeeIn,
    EmployeeOut,
    ItemDetail,
    ItemOut,
    OrderOut,
    ShiftIn,
    ShiftOut,
    WorkCenterIn,
    WorkCenterOut,
)

router = APIRouter(prefix="/api", tags=["master"])


def _wc_out(db: Session, wc: WorkCenter) -> WorkCenterOut:
    cnt = db.query(func.count(Employee.id)).filter(Employee.work_center_id == wc.id, Employee.is_active.is_(True)).scalar() or 0
    out = WorkCenterOut.model_validate(wc)
    out.employee_count = cnt
    return out


# ---- Work centers ----
@router.get("/workcenters", response_model=list[WorkCenterOut])
def list_workcenters(db: Session = Depends(get_db), _=Depends(require_user)):
    wcs = db.query(WorkCenter).options(joinedload(WorkCenter.shifts)).order_by(WorkCenter.code).all()
    return [_wc_out(db, w) for w in wcs]


@router.post("/workcenters", response_model=WorkCenterOut, status_code=201)
def create_workcenter(data: WorkCenterIn, db: Session = Depends(get_db), _=Depends(require_poweruser)):
    if db.query(WorkCenter).filter(WorkCenter.code == data.code).first():
        raise HTTPException(400, "Bu kod zaten var")
    wc = WorkCenter(**data.model_dump())
    db.add(wc)
    db.commit()
    db.refresh(wc)
    return _wc_out(db, wc)


@router.put("/workcenters/{wc_id}", response_model=WorkCenterOut)
def update_workcenter(wc_id: int, data: WorkCenterIn, db: Session = Depends(get_db), _=Depends(require_poweruser)):
    wc = db.get(WorkCenter, wc_id)
    if not wc:
        raise HTTPException(404, "Is merkezi bulunamadi")
    for k, v in data.model_dump().items():
        setattr(wc, k, v)
    db.commit()
    db.refresh(wc)
    return _wc_out(db, wc)


@router.delete("/workcenters/{wc_id}", status_code=204)
def delete_workcenter(wc_id: int, db: Session = Depends(get_db), _=Depends(require_poweruser)):
    wc = db.get(WorkCenter, wc_id)
    if not wc:
        raise HTTPException(404, "Is merkezi bulunamadi")
    db.delete(wc)
    db.commit()


@router.post("/workcenters/{wc_id}/shifts", response_model=ShiftOut, status_code=201)
def add_shift(wc_id: int, data: ShiftIn, db: Session = Depends(get_db), _=Depends(require_poweruser)):
    wc = db.get(WorkCenter, wc_id)
    if not wc:
        raise HTTPException(404, "Is merkezi bulunamadi")
    sh = WorkCenterShift(work_center_id=wc_id, **data.model_dump())
    db.add(sh)
    db.commit()
    db.refresh(sh)
    return sh


@router.put("/shifts/{shift_id}", response_model=ShiftOut)
def update_shift(shift_id: int, data: ShiftIn, db: Session = Depends(get_db), _=Depends(require_poweruser)):
    sh = db.get(WorkCenterShift, shift_id)
    if not sh:
        raise HTTPException(404, "Vardiya bulunamadi")
    for k, v in data.model_dump().items():
        setattr(sh, k, v)
    db.commit()
    db.refresh(sh)
    return sh


@router.delete("/shifts/{shift_id}", status_code=204)
def delete_shift(shift_id: int, db: Session = Depends(get_db), _=Depends(require_poweruser)):
    sh = db.get(WorkCenterShift, shift_id)
    if not sh:
        raise HTTPException(404, "Vardiya bulunamadi")
    db.delete(sh)
    db.commit()


# ---- Employees ----
@router.get("/employees", response_model=list[EmployeeOut])
def list_employees(work_center_id: int | None = None, db: Session = Depends(get_db), _=Depends(require_user)):
    q = db.query(Employee)
    if work_center_id:
        q = q.filter(Employee.work_center_id == work_center_id)
    return q.order_by(Employee.code).all()


@router.post("/employees", response_model=EmployeeOut, status_code=201)
def create_employee(data: EmployeeIn, db: Session = Depends(get_db), _=Depends(require_poweruser)):
    if db.query(Employee).filter(Employee.code == data.code).first():
        raise HTTPException(400, "Bu sicil zaten var")
    e = Employee(**data.model_dump())
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


@router.put("/employees/{emp_id}", response_model=EmployeeOut)
def update_employee(emp_id: int, data: EmployeeIn, db: Session = Depends(get_db), _=Depends(require_poweruser)):
    e = db.get(Employee, emp_id)
    if not e:
        raise HTTPException(404, "Personel bulunamadi")
    for k, v in data.model_dump().items():
        setattr(e, k, v)
    db.commit()
    db.refresh(e)
    return e


@router.delete("/employees/{emp_id}", status_code=204)
def delete_employee(emp_id: int, db: Session = Depends(get_db), _=Depends(require_poweruser)):
    e = db.get(Employee, emp_id)
    if not e:
        raise HTTPException(404, "Personel bulunamadi")
    db.delete(e)
    db.commit()


# ---- Items ----
@router.get("/items", response_model=list[ItemOut])
def list_items(q: str | None = None, limit: int = Query(200, le=2000), db: Session = Depends(get_db), _=Depends(require_user)):
    query = db.query(Item)
    if q:
        like = f"%{q}%"
        query = query.filter((Item.code.ilike(like)) | (Item.name.ilike(like)) | (Item.product_group.ilike(like)))
    return query.order_by(Item.code).limit(limit).all()


@router.get("/items/{item_id}", response_model=ItemDetail)
def get_item(item_id: int, db: Session = Depends(get_db), _=Depends(require_user)):
    it = db.query(Item).options(joinedload(Item.bom_lines), joinedload(Item.operations)).filter(Item.id == item_id).first()
    if not it:
        raise HTTPException(404, "Stok kodu bulunamadi")
    return it


# ---- Orders ----
@router.get("/orders", response_model=list[OrderOut])
def list_orders(
    status: str | None = "open",
    due_from: date | None = None,
    due_to: date | None = None,
    db: Session = Depends(get_db),
    _=Depends(require_user),
):
    q = db.query(Order).options(joinedload(Order.item))
    if status:
        q = q.filter(Order.status == status)
    if due_from:
        q = q.filter(Order.due_date >= due_from)
    if due_to:
        q = q.filter(Order.due_date <= due_to)
    out = []
    for o in q.order_by(Order.due_date, Order.order_no).all():
        row = OrderOut.model_validate(o)
        row.item_code = o.item.code
        out.append(row)
    return out


@router.patch("/orders/{order_id}/status", response_model=OrderOut)
def set_order_status(order_id: int, status: str, db: Session = Depends(get_db), _=Depends(require_poweruser)):
    o = db.query(Order).options(joinedload(Order.item)).filter(Order.id == order_id).first()
    if not o:
        raise HTTPException(404, "Siparis bulunamadi")
    if status not in ("open", "closed"):
        raise HTTPException(400, "Durum open/closed olmali")
    o.status = status
    db.commit()
    row = OrderOut.model_validate(o)
    row.item_code = o.item.code
    return row


@router.delete("/orders", status_code=204)
def delete_orders(status: str = "closed", db: Session = Depends(get_db), _=Depends(require_poweruser)):
    db.query(Order).filter(Order.status == status).delete(synchronize_session=False)
    db.commit()
