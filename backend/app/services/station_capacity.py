"""Weekly station inputs and independent line budgets. No inferred staffing."""
from datetime import timedelta
from app.models import Machine, MachineWeek, PlanLine
from app.services.routing_resource import eligible_machine_ids, station_crew


def station_rows(db, wc, week):
    values = {r.machine_id: r.working_hours for r in db.query(MachineWeek).join(Machine).filter(
        Machine.work_center_id == wc.id, MachineWeek.week_start == week).all()}
    return [{"machine_id": m.id, "code": m.code, "name": m.name,
             "week_start": week, "required_crew_size": station_crew(m),
             "crew_source": "istasyon" if m.required_crew_size else ("is_merkezi" if station_crew(m) else "eksik"),
             "working_hours": values.get(m.id),
             "capacity_hours": float(values.get(m.id) or 0) if station_crew(m) else 0,
             "required_labor_hours": float(values.get(m.id) or 0) * (station_crew(m) or 0)}
            for m in wc.machines if m.is_active]


def load_budgets(db, wcs, weeks, remaining, replace_auto=False, retained_modes=None):
    from app.services.capacity import apply_planning_reserve
    centers = {w.id: w for w in wcs if w.planning_mode == "line"}
    if not centers or not weeks:
        return
    for wc in centers.values():
        for week in weeks:
            for r in station_rows(db, wc, week):
                remaining[("machine", r["machine_id"], week)] = apply_planning_reserve(wc, r["capacity_hours"])
    q = db.query(PlanLine).filter(PlanLine.work_center_id.in_(centers), PlanLine.week_start.in_(weeks))
    if retained_modes is not None:
        q = q.filter(PlanLine.mode.in_(retained_modes))
    if replace_auto:
        q = q.filter(PlanLine.mode != "auto")
    for line in q.all():
        key = ("machine", line.machine_id, line.week_start)
        if line.machine_id is None:
            # Legacy retained load has no defensible station assignment. Reserve all
            # stations in that week until the old row is replaced/reassigned.
            for m in centers[line.work_center_id].machines:
                remaining[("machine", m.id, line.week_start)] = 0.0
        elif key in remaining:
            remaining[key] = max(0.0, remaining[key] - line.planned_hours)


def station_room(op, week, remaining):
    from app.services.routing_resource import line_run_hours
    center_room = remaining.get((op.work_center_id, week), 0.0)
    valid = {m.id for m in op.work_center.machines if m.is_active and station_crew(m)}
    for mid in eligible_machine_ids(op):
        room = min(center_room, remaining.get(("machine", mid, week), 0.0))
        if mid in valid and room > 1e-6 and room + 1e-10 >= line_run_hours(op, 1):
            return mid, room
    return None, 0.0
