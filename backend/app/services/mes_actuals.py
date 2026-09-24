"""MES measurements: actual output and one-time matching to current plan slots.

Matching is planning evidence, never a customer reservation. Frozen baseline
comparison remains the separate MES progress report.
"""
from collections import defaultdict
from datetime import timedelta
from sqlalchemy.orm import joinedload
from app.models import PlanLine, RoutingOperation
from app.models.mes import MesDetail
from app.services.mes import operation_key, monday


def measure(db, as_of, horizon=4):
    lines = db.query(PlanLine).options(joinedload(PlanLine.operation).joinedload(RoutingOperation.item)).filter(
        PlanLine.mode.in_(["auto", "manual"])).order_by(PlanLine.week_start,PlanLine.id).all()
    by_key=defaultdict(list)
    for line in lines:
        if line.operation:
            by_key[operation_key(line.operation)].append(line)
    matches={line.id:{"qty":0.,"hours":0.,"first_date":None,"last_date":None} for line in lines}
    daily=defaultdict(float)
    records=db.query(MesDetail).filter(MesDetail.prod_date<=as_of).order_by(MesDetail.prod_date,MesDetail.detail_id).all()
    for record in records:
        mapping=record.mapping
        if mapping.get("status")!="mapped":
            continue
        unit=float(mapping.get("standard_unit_hours") or 0)
        daily[(mapping["work_center_id"],record.prod_date)]+=record.quantity*unit
        left=record.quantity
        week=monday(record.prod_date)
        for line in by_key.get(mapping["key"],[]):
            if not week<=line.week_start<=week+timedelta(weeks=horizon):
                continue
            matched=matches[line.id]
            take=min(left,max(float(line.planned_qty or 0)-matched["qty"],0))
            if take>0:
                matched["qty"]+=take
                matched["hours"]+=take*unit
                matched["first_date"]=matched["first_date"] or record.prod_date
                matched["last_date"]=record.prod_date
                left-=take
            if left<=1e-9:
                break
    return {"lines":lines,"matches":matches,"daily":dict(daily)}


def weekly_kpis(db,wc_ids,start,end,as_of):
    measured=measure(db,as_of)
    grouped=defaultdict(list)
    for line in measured["lines"]:
        if line.work_center_id in wc_ids and start<=line.week_start<=end:
            grouped[(line.work_center_id,line.week_start)].append(line)
    output=defaultdict(float)
    for (wc,day),hours in measured["daily"].items():
        if wc in wc_ids and start<=monday(day)<=end:
            output[(wc,monday(day))]+=hours
    result={}
    for key in grouped.keys()|output.keys():
        lines=grouped[key]
        matched=sum(min(float(line.planned_hours or 0),measured["matches"][line.id]["hours"]) for line in lines)
        planned=sum(float(line.planned_hours or 0) for line in lines)
        result[key]={"planned_hours":round(planned,4),"standard_hour_equivalent_output":round(output[key],4),
                     "plan_matched_output_hours":round(matched,4),"plan_adherence_remaining_hours":round(max(planned-matched,0),4)}
    return result
