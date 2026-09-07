"""Senaryo matrisi: urun grubu / stok bazinda operasyon gecis kurallari."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from app.core.deps import require_poweruser, require_user
from app.db.session import get_db
from app.models import Item, OpTransitionRule
from app.schemas import FlowOut, FlowTransition, RuleIn, RuleOut, RuleRow, ScenarioGroup
from app.services import scenarios as scen

router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])


def _rule_out(r: scen.Rule | None) -> RuleOut | None:
    if r is None:
        return None
    return RuleOut(rule=r.rule, lag_cycles=r.lag_cycles, wait_minutes=r.wait_minutes, source=r.source, id=r.id, note=r.note, description=r.describe())


def _row(r: OpTransitionRule) -> RuleRow:
    return RuleRow(
        id=r.id, scope=r.scope, product_group=r.product_group, item_code=r.item.code if r.item else None,
        from_op=r.from_op, to_op=r.to_op, from_wip_code=r.from_wip_code or "", to_wip_code=r.to_wip_code or "",
        rule=r.rule, lag_cycles=r.lag_cycles, wait_minutes=r.wait_minutes, note=r.note,
        description=scen.Rule(r.rule, r.lag_cycles, r.wait_minutes, r.scope, r.id, r.note).describe(),
    )


@router.get("/groups", response_model=list[ScenarioGroup])
def list_groups(db: Session = Depends(get_db), _=Depends(require_user)):
    return scen.groups(db)


@router.get("/flow", response_model=FlowOut)
def get_flow(product_group: str = Query(""), item_code: str | None = Query(None), db: Session = Depends(get_db), _=Depends(require_user)):
    try:
        f = scen.flow(db, product_group, item_code or None)
    except ValueError as e:
        raise HTTPException(404, str(e))
    f["transitions"] = [
        FlowTransition(
            from_op=t["from_op"], to_op=t["to_op"],
            from_wip_code=t.get("from_wip_code", ""), to_wip_code=t.get("to_wip_code", ""),
            effective=_rule_out(t["effective"]), group_rule=_rule_out(t["group_rule"]), item_rule=_rule_out(t["item_rule"]),
        )
        for t in f["transitions"]
    ]
    return FlowOut(**f)


@router.get("/rules", response_model=list[RuleRow])
def list_rules(product_group: str | None = Query(None), db: Session = Depends(get_db), _=Depends(require_user)):
    q = db.query(OpTransitionRule).options(joinedload(OpTransitionRule.item))
    rows = q.all()
    if product_group is not None:
        rows = [r for r in rows if (r.item.product_group if r.item else r.product_group) == product_group]
    rows.sort(key=lambda r: (r.product_group, r.scope, r.item.code if r.item else "", r.from_op_norm))
    return [_row(r) for r in rows]


@router.put("/rules", response_model=RuleRow)
def upsert_rule(data: RuleIn, db: Session = Depends(get_db), _=Depends(require_poweruser)):
    try:
        r = scen.upsert_rule(
            db, data.scope, data.product_group, data.item_code, data.from_op, data.to_op,
            data.rule, data.lag_cycles, data.wait_minutes, data.note,
            data.from_wip_code, data.to_wip_code,
        )
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    db.commit()
    db.refresh(r)
    return _row(r)


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: int, db: Session = Depends(get_db), _=Depends(require_poweruser)):
    r = db.get(OpTransitionRule, rule_id)
    if not r:
        raise HTTPException(404, "Kural bulunamadi")
    db.delete(r)
    db.commit()


@router.get("/items", response_model=list[dict])
def group_items(product_group: str = Query(""), db: Session = Depends(get_db), _=Depends(require_user)):
    its = db.query(Item).options(joinedload(Item.operations)).filter(Item.product_group == product_group).order_by(Item.code).all()
    return [{"code": i.code, "name": i.name, "operations": [op.operation_name for op in i.operations]} for i in its if i.operations]
