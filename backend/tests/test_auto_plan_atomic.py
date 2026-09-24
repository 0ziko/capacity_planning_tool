"""Weekly replacement must survive a failed daily calculation unchanged."""
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from app.models import Item, PlanLine
from app.schemas import AutoPlanRequest
from app.services import planning, daily_scheduler
from app.services.plan_draft import DraftLine
from tests.test_daily_schedule_faz11 import _pilot_wc_machine, _machine_op


@pytest.mark.parametrize("fail,commit", [(True, True), (False, True), (False, False)])
def test_weekly_and_daily_share_commit(db, monkeypatch, fail, commit):
    key = f"ATOMIC-{fail}-{commit}"
    wc, machine = _pilot_wc_machine(db, key)
    item = Item(code=key + "-P", name="Atomic")
    db.add(item)
    db.commit()
    op, order = _machine_op(db, wc, machine, item)
    week = date(2026, 9, 7)
    old = PlanLine(order_id=order.id, operation_id=op.id, work_center_id=wc.id,
                   week_start=week, planned_hours=3, planned_qty=3, mode="auto")
    db.add(old)
    db.commit()
    sim = planning.Simulation(mode="due_date", start=week, weeks=[week], work_centers=[wc],
        lines=[DraftLine(order=order, order_id=order.id, operation_id=op.id,
                         work_center_id=wc.id, week_start=week, planned_hours=1, planned_qty=2)],
        unplanned=[], skipped=[], capacity_hours=10, orders=[order])
    monkeypatch.setattr(planning, "simulate", lambda *_: sim)
    commits = []
    def committed(session):
        commits.append(True)
    event.listen(db, "after_commit", committed)
    def daily(session, *args, **kwargs):
        assert commits == []
        assert session.query(PlanLine).filter_by(work_center_id=wc.id).one().planned_qty == 2
        session.add(Item(code=key + "-DAILY", name="Daily transaction marker"))
        session.flush()
        if fail:
            raise ValueError("daily failure")
        return SimpleNamespace(version_id=1, segments_created=1, skipped=[], remaining_qty=[])
    monkeypatch.setattr(daily_scheduler, "build_daily_schedule", daily)
    req = AutoPlanRequest(start_week=week, weeks=1, work_center_ids=[wc.id],
                          replace_existing=True, planning_granularity="daily_detailed")
    try:
        if fail:
            with pytest.raises(ValueError, match="daily failure"):
                planning.auto_plan(db, req, "test", commit=commit)
        else:
            result = planning.auto_plan(db, req, "test", commit=commit)
            assert result["daily_schedule"]["segments_created"] == 1
        assert len(commits) == int(commit and not fail)
        if not commit:
            db.rollback()
        restored = fail or not commit
        assert db.query(PlanLine).filter_by(work_center_id=wc.id).one().planned_qty == (3 if restored else 2)
        assert (db.query(Item).filter_by(code=key + "-DAILY").first() is None) == restored
    finally:
        event.remove(db, "after_commit", committed)
