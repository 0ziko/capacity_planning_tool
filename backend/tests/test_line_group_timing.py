import pytest
from types import SimpleNamespace as S
from app.models import WorkCenter, Machine, RoutingOperation
from app.services.routing_resource import line_run_hours, line_quantity_for_hours, compute_operation_need
from app.services.planning import _place_quantity
from app.services.scenarios import Rule
from datetime import date, timedelta

@pytest.mark.parametrize('qty,units,cycle,seconds', [(5,1,100,120),(20,3,50,80),(1,3,50,50),(2,3,50,50),(3,3,50,50),(4,3,50,55),(0,3,50,0)])
def test_requested_group_examples(qty,units,cycle,seconds):
    op=RoutingOperation(work_center=WorkCenter(planning_mode='line'),cycle_time_sec=cycle,line_interval_sec=5,units_per_cycle=units)
    assert op.hours_for(qty)*3600 == pytest.approx(seconds)
    assert compute_operation_need(op,qty).machine_hours*3600 == pytest.approx(seconds)


def test_capacity_boundaries_and_week_restart():
    wk=date(2026,9,21)
    wc=WorkCenter(id=901,planning_mode='line',code='GROUP')
    m=Machine(id=902,work_center=wc,is_active=True,required_crew_size=2)
    op=RoutingOperation(id=903,seq=10,work_center=wc,work_center_id=901,primary_machine_id=902,cycle_time_sec=50,line_interval_sec=5,units_per_cycle=3)
    assert line_quantity_for_hours(op,49/3600)==0
    assert line_quantity_for_hours(op,50/3600)==3
    assert line_quantity_for_hours(op,54/3600)==3
    assert line_quantity_for_hours(op,55/3600)==6
    item=S(code='6-GROUP',operations=[op]);order=S(id=1,order_no='G',item=item)
    weeks=[wk,wk+timedelta(days=7)]
    room={(901,w):55/3600 for w in weeks};room.update({('machine',902,w):55/3600 for w in weeks})
    lines,missing,_=_place_quantity(order,10,'G',None,{901:wc},weeks,room,S(get=lambda *args:Rule(rule='finish')))
    assert not missing
    assert [r.planned_qty for r in lines]==[6,4]
    assert sum(r.planned_hours for r in lines)*3600==pytest.approx(110)
    assert all(v>=-1e-10 for v in room.values())

def test_leadtime_and_move_split_use_group_capacity():
    from datetime import datetime
    from app.services.planning import _schedule_line_op, DraftLine
    from app.services.job_moves import _place_hours_on_wc
    wk=date(2026,9,21)
    wc=WorkCenter(id=911,planning_mode='line',code='GROUP')
    Machine(id=912,work_center=wc,is_active=True,required_crew_size=2)
    op=RoutingOperation(id=913,seq=10,work_center=wc,work_center_id=911,primary_machine_id=912,cycle_time_sec=50,line_interval_sec=5,units_per_cycle=3)
    weeks=[wk,wk+timedelta(days=7)]
    def budget():
        return {(911,w):55/3600 for w in weeks}|{('machine',912,w):55/3600 for w in weeks}
    result=_schedule_line_op(op,op.hours_for(10),datetime(2026,9,21),weeks[-1]+timedelta(days=6),budget(),10)
    assert result.status=='scheduled'
    order=S(id=1,order_no='GROUP')
    line=DraftLine(order=order,order_id=1,operation_id=913,work_center_id=911,week_start=wk,planned_hours=op.hours_for(10),planned_qty=10)
    rows,left=_place_hours_on_wc(line,line.planned_hours,10,0,0,weeks,budget(),'GROUP',op)
    assert left==0
    assert [r.planned_qty for r in rows]==[6,4]
    assert sum(r.planned_hours for r in rows)*3600==pytest.approx(110)
