"""Is merkezleri, vardiyalar, personel, stok/BOM/rota, siparisler."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.core.deps import require_poweruser, require_user
from app.db.session import get_db
from app.models import Downtime, Employee, Item, Machine, Order, PlanLine, ProductionActual, RoutingOperation, WorkCenter, WorkCenterShift, WorkCenterWeek
from app.schemas import (
    EmployeeIn,
    EmployeeOut,
    ItemDetail,
    ItemOut,
    MachineIn,
    MachineOut,
    OrderIn,
    OrderOut,
    ShiftIn,
    ShiftOut,
    WcWeekIn,
    WcWeekOut,
    WorkCenterIn,
    WorkCenterOut,
)
from app.services import capacity
from app.services import orders as orders_svc

router = APIRouter(prefix="/api", tags=["master"])


def _machine_out(db: Session, m: Machine) -> MachineOut:
    out = MachineOut.model_validate(m)
    out.employee_count = db.query(func.count(Employee.id)).filter(Employee.machine_id == m.id, Employee.is_active.is_(True)).scalar() or 0
    return out


def _wc_out(db: Session, wc: WorkCenter) -> WorkCenterOut:
    out = WorkCenterOut.model_validate(wc)
    out.machines = [_machine_out(db, m) for m in wc.machines]
    out.employee_count = capacity.wc_employee_count(db, wc.id)
    out.machine_employee_count = capacity.machine_employee_count(db, wc.id)
    out.capacity_headcount = out.machine_employee_count if wc.capacity_source == "machines" else out.employee_count
    return out


def _employee_out(e: Employee) -> EmployeeOut:
    out = EmployeeOut.model_validate(e)
    out.machine_code = e.machine.code if e.machine else ""
    return out


def _check_machine_for_employee(db: Session, data: EmployeeIn) -> None:
    """Makine atamasi varsa makine, personelin is merkezine ait olmali (is merkezi bos ise makineninki atanir)."""
    if data.machine_id is None:
        return
    m = db.get(Machine, data.machine_id)
    if not m:
        raise HTTPException(404, "Makine bulunamadi")
    if data.work_center_id is None:
        data.work_center_id = m.work_center_id
    elif data.work_center_id != m.work_center_id:
        raise HTTPException(400, f"Makine '{m.code}' başka bir iş merkezine ait; personelin iş merkezi ile makinenin iş merkezi aynı olmalı")


# ---- Work centers ----
@router.get("/workcenters", response_model=list[WorkCenterOut])
def list_workcenters(db: Session = Depends(get_db), _=Depends(require_user)):
    wcs = db.query(WorkCenter).options(joinedload(WorkCenter.shifts), joinedload(WorkCenter.machines)).order_by(WorkCenter.code).all()
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
    # Bagli kayitlar varsa silme: yetim rota/plan/uretim kaydi olusur ve ekranlar bozulur.
    deps = {
        "rota operasyonu": db.query(func.count(RoutingOperation.id)).filter(RoutingOperation.work_center_id == wc_id).scalar(),
        "plan satırı": db.query(func.count(PlanLine.id)).filter(PlanLine.work_center_id == wc_id).scalar(),
        "üretim kaydı": db.query(func.count(ProductionActual.id)).filter(ProductionActual.work_center_id == wc_id).scalar(),
        "duruş kaydı": db.query(func.count(Downtime.id)).filter(Downtime.work_center_id == wc_id).scalar(),
    }
    used = [f"{n} {k}" for k, n in deps.items() if n]
    if used:
        raise HTTPException(
            400,
            f"'{wc.code}' silinemez; bağlı kayıtlar var: {', '.join(used)}. "
            "Önce bu kayıtları silin/taşıyın ya da iş merkezini 'Pasif' yapın (Aktif işaretini kaldırın).",
        )
    db.query(Employee).filter(Employee.work_center_id == wc_id).update({Employee.work_center_id: None, Employee.machine_id: None}, synchronize_session=False)
    db.delete(wc)  # vardiyalar ve makineler cascade ile silinir
    db.commit()


# ---- Haftalik is gucu (is merkezi x hafta istisnalari) ----
def _week_out(db: Session, wc: WorkCenter, wk: date) -> WcWeekOut:
    p = capacity.week_profile(db, wc, wk)
    ov = p.pop("override")
    return WcWeekOut(
        work_center_id=wc.id,
        **p,
        has_override=ov is not None,
        ov_headcount=ov.headcount if ov else None,
        ov_efficient_hours_per_person=ov.efficient_hours_per_person if ov else None,
        ov_working_days=ov.working_days if ov else None,
        note=ov.note if ov else "",
    )


@router.get("/workcenters/{wc_id}/weeks", response_model=list[WcWeekOut])
def list_wc_weeks(wc_id: int, start: date = Query(...), weeks: int = Query(12, ge=1, le=60), db: Session = Depends(get_db), _=Depends(require_user)):
    wc = db.get(WorkCenter, wc_id)
    if not wc:
        raise HTTPException(404, "Is merkezi bulunamadi")
    wk0 = capacity.week_start(start)
    return [_week_out(db, wc, wk0 + timedelta(weeks=i)) for i in range(weeks)]


@router.put("/workcenters/{wc_id}/weeks/{week}", response_model=WcWeekOut)
def upsert_wc_week(wc_id: int, week: date, data: WcWeekIn, db: Session = Depends(get_db), _=Depends(require_poweruser)):
    wc = db.get(WorkCenter, wc_id)
    if not wc:
        raise HTTPException(404, "Is merkezi bulunamadi")
    wk = capacity.week_start(week)
    row = db.query(WorkCenterWeek).filter(WorkCenterWeek.work_center_id == wc_id, WorkCenterWeek.week_start == wk).first()
    empty = data.headcount is None and data.efficient_hours_per_person is None and data.working_days is None and not data.note.strip()
    if empty:
        if row:
            db.delete(row)
    else:
        if not row:
            row = WorkCenterWeek(work_center_id=wc_id, week_start=wk)
            db.add(row)
        row.headcount = data.headcount
        row.efficient_hours_per_person = data.efficient_hours_per_person
        row.working_days = data.working_days
        row.note = data.note.strip()
    db.commit()
    return _week_out(db, wc, wk)


@router.delete("/workcenters/{wc_id}/weeks/{week}", status_code=204)
def delete_wc_week(wc_id: int, week: date, db: Session = Depends(get_db), _=Depends(require_poweruser)):
    wk = capacity.week_start(week)
    db.query(WorkCenterWeek).filter(WorkCenterWeek.work_center_id == wc_id, WorkCenterWeek.week_start == wk).delete(synchronize_session=False)
    db.commit()


@router.get("/wc-weeks", response_model=list[WcWeekOut])
def list_all_wc_weeks(start: date = Query(...), weeks: int = Query(12, ge=1, le=60), work_center_ids: list[int] | None = Query(None), db: Session = Depends(get_db), _=Depends(require_user)):
    """Tum (veya secili) aktif is merkezleri icin hafta profili — planlama ekraninda 'haftalik is gucu' gorunumu."""
    q = db.query(WorkCenter).filter(WorkCenter.is_active.is_(True))
    if work_center_ids:
        q = q.filter(WorkCenter.id.in_(work_center_ids))
    wk0 = capacity.week_start(start)
    out = []
    for wc in q.order_by(WorkCenter.code).all():
        out.extend(_week_out(db, wc, wk0 + timedelta(weeks=i)) for i in range(weeks))
    return out


# ---- Machines (is merkezi altindaki makineler) ----
@router.get("/machines", response_model=list[MachineOut])
def list_machines(work_center_id: int | None = None, db: Session = Depends(get_db), _=Depends(require_user)):
    q = db.query(Machine)
    if work_center_id:
        q = q.filter(Machine.work_center_id == work_center_id)
    return [_machine_out(db, m) for m in q.order_by(Machine.work_center_id, Machine.code).all()]


@router.post("/workcenters/{wc_id}/machines", response_model=MachineOut, status_code=201)
def add_machine(wc_id: int, data: MachineIn, db: Session = Depends(get_db), _=Depends(require_poweruser)):
    if not db.get(WorkCenter, wc_id):
        raise HTTPException(404, "Is merkezi bulunamadi")
    code = data.code.strip()
    if db.query(Machine).filter(func.upper(Machine.code) == code.upper()).first():
        raise HTTPException(400, f"'{code}' makine kodu zaten var")
    m = Machine(work_center_id=wc_id, **{**data.model_dump(), "code": code})
    db.add(m)
    db.commit()
    db.refresh(m)
    return _machine_out(db, m)


@router.put("/machines/{machine_id}", response_model=MachineOut)
def update_machine(machine_id: int, data: MachineIn, db: Session = Depends(get_db), _=Depends(require_poweruser)):
    m = db.get(Machine, machine_id)
    if not m:
        raise HTTPException(404, "Makine bulunamadi")
    code = data.code.strip()
    dup = db.query(Machine).filter(func.upper(Machine.code) == code.upper(), Machine.id != machine_id).first()
    if dup:
        raise HTTPException(400, f"'{code}' makine kodu zaten var")
    for k, v in data.model_dump().items():
        setattr(m, k, v)
    m.code = code
    db.commit()
    db.refresh(m)
    return _machine_out(db, m)


@router.delete("/machines/{machine_id}", status_code=204)
def delete_machine(machine_id: int, db: Session = Depends(get_db), _=Depends(require_poweruser)):
    m = db.get(Machine, machine_id)
    if not m:
        raise HTTPException(404, "Makine bulunamadi")
    db.query(Employee).filter(Employee.machine_id == machine_id).update({Employee.machine_id: None}, synchronize_session=False)
    db.delete(m)
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
    q = db.query(Employee).options(joinedload(Employee.machine))
    if work_center_id:
        q = q.filter(Employee.work_center_id == work_center_id)
    return [_employee_out(e) for e in q.order_by(Employee.code).all()]


@router.post("/employees", response_model=EmployeeOut, status_code=201)
def create_employee(data: EmployeeIn, db: Session = Depends(get_db), _=Depends(require_poweruser)):
    if db.query(Employee).filter(Employee.code == data.code).first():
        raise HTTPException(400, "Bu sicil zaten var")
    _check_machine_for_employee(db, data)
    e = Employee(**data.model_dump())
    db.add(e)
    db.commit()
    db.refresh(e)
    return _employee_out(e)


@router.put("/employees/{emp_id}", response_model=EmployeeOut)
def update_employee(emp_id: int, data: EmployeeIn, db: Session = Depends(get_db), _=Depends(require_poweruser)):
    e = db.get(Employee, emp_id)
    if not e:
        raise HTTPException(404, "Personel bulunamadi")
    _check_machine_for_employee(db, data)
    for k, v in data.model_dump().items():
        setattr(e, k, v)
    db.commit()
    db.refresh(e)
    return _employee_out(e)


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
    position: str | None = None,
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
    if position and position.strip():
        q = q.filter(Order.position_no.ilike(f"%{position.strip()}%"))
    return [orders_svc.order_out(o) for o in q.order_by(Order.due_date, Order.order_no, Order.position_no).all()]


@router.post("/orders", response_model=OrderOut, status_code=201)
def create_order(data: OrderIn, db: Session = Depends(get_db), _=Depends(require_poweruser)):
    try:
        o = orders_svc.create_order(db, data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return orders_svc.order_out(o)


@router.put("/orders/{order_id}", response_model=OrderOut)
def update_order(order_id: int, data: OrderIn, db: Session = Depends(get_db), _=Depends(require_poweruser)):
    o = db.query(Order).options(joinedload(Order.item)).filter(Order.id == order_id).first()
    if not o:
        raise HTTPException(404, "Siparis bulunamadi")
    if o.status == "merged":
        raise HTTPException(400, "Birlestirilmis siparis duzenlenemez; once birlestirmeyi geri alin")
    try:
        o = orders_svc.update_order(db, o, data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return orders_svc.order_out(o)


@router.delete("/orders/{order_id}", status_code=204)
def delete_order(order_id: int, db: Session = Depends(get_db), _=Depends(require_poweruser)):
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404, "Siparis bulunamadi")
    if db.query(Order).filter(Order.merged_into_id == order_id).first():
        raise HTTPException(400, "Bu birlesik siparisi silmek icin once birlestirmeyi geri alin")
    db.delete(o)
    db.commit()


@router.patch("/orders/{order_id}/status", response_model=OrderOut)
def set_order_status(order_id: int, status: str, db: Session = Depends(get_db), _=Depends(require_poweruser)):
    o = db.query(Order).options(joinedload(Order.item)).filter(Order.id == order_id).first()
    if not o:
        raise HTTPException(404, "Siparis bulunamadi")
    if status not in ("open", "closed"):
        raise HTTPException(400, "Durum open/closed olmali")
    if o.status == "merged":
        raise HTTPException(400, "Birlestirilmis siparisin durumu degistirilemez")
    o.status = status
    db.commit()
    return orders_svc.order_out(o)


@router.delete("/orders", status_code=204)
def delete_orders(status: str = "closed", db: Session = Depends(get_db), _=Depends(require_poweruser)):
    ids = [i for (i,) in db.query(Order.id).filter(Order.status == status).all()]
    if ids:
        # toplu silmede ORM cascade calismaz; plan satirlarini ve birlestirme referanslarini elle temizle
        db.query(PlanLine).filter(PlanLine.order_id.in_(ids)).delete(synchronize_session=False)
        db.query(Order).filter(Order.merged_into_id.in_(ids)).update({Order.merged_into_id: None}, synchronize_session=False)
        db.query(Order).filter(Order.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
