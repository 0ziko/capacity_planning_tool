from datetime import date, timedelta
from types import SimpleNamespace
import pytest
from app.models import WorkCenter, WorkCenterWeek, RoutingOperation, Item, ResourceCalendarException, Machine, MachineWeek, RoutingOperationStation
from app.services.capacity import capacity_for_range, week_profile
from app.services.routing_resource import compute_operation_need

WK = date(2026, 9, 21)

def test_line_capacity_ignores_staffing_and_calendar(db):
    wc=WorkCenter(code='LINE-CAP',name='Yıkama',planning_mode='line',required_crew_size=3)
    db.add(wc);db.flush()
    row=WorkCenterWeek(work_center_id=wc.id,week_start=WK,working_days=5,line_hours_per_day=7,headcount=3,efficient_hours_per_person=4)
    db.add(row)
    machine=Machine(code="CAP-A",work_center=wc,required_crew_size=3)
    db.add(machine);db.flush()
    mw=MachineWeek(machine_id=machine.id,week_start=WK,working_hours=35)
    db.add(mw)
    db.add(ResourceCalendarException(resource_type='work_center',resource_id=wc.id,cal_date=WK,exception_kind='holiday'))
    db.commit()
    assert capacity_for_range(db,wc,WK,WK+timedelta(days=6)).capacity_hours==35
    row.headcount=100;row.efficient_hours_per_person=24;db.commit()
    profile=week_profile(db,wc,WK)
    assert profile['capacity_hours']==35
    assert profile['required_labor_hours']==105
    mw.working_hours=None;db.commit()
    assert week_profile(db,wc,WK)['capacity_hours']==0
    mw.working_hours=0;db.commit()
    assert week_profile(db,wc,WK)['capacity_hours']==0

def test_line_need_counts_first_group_and_crew_does_not_speed_it_up():
    wc=WorkCenter(planning_mode='line',required_crew_size=3)
    machine=Machine(required_crew_size=3)
    op=RoutingOperation(primary_machine=machine,work_center=wc,operation_name='2.ARA YIKAMA',line_interval_sec=60,units_per_cycle=5,cycle_time_sec=1200,setup_time_min=60)
    assert op.hours_for(10500)==pytest.approx(35.31666666666667)
    need=compute_operation_need(op,10500)
    assert need.machine_hours==pytest.approx(35.31666666666667) and need.labor_hours==pytest.approx(105.95)
    assert op.hours_for(6)==pytest.approx(1260/3600)
    assert op.hours_for(2)+op.hours_for(4)==pytest.approx(2400/3600)
    machine.required_crew_size=6
    assert op.hours_for(10500)==pytest.approx(35.31666666666667)
    assert compute_operation_need(op,10500).labor_hours==pytest.approx(211.9)

def test_line_weekly_scheduler_uses_line_time_for_each_pass():
    from app.services.planning import _place_quantity
    from app.services.scenarios import Rule
    wc=WorkCenter(id=999,code='LINE',planning_mode='line',required_crew_size=3)
    machine=Machine(id=998,work_center=wc,is_active=True,required_crew_size=3)
    ops=[RoutingOperation(primary_machine_id=998,id=i,seq=i,work_center_id=999,work_center=wc,operation_name=n,line_interval_sec=60,units_per_cycle=5,cycle_time_sec=1200,setup_time_min=60) for i,n in [(1,'ARA YIKAMA'),(2,'YIKAMA')]]
    item=SimpleNamespace(code='LINE-ITEM',operations=ops)
    order=SimpleNamespace(id=1,order_no='LINE',item=item)
    lines,unplanned,_=_place_quantity(order,10500,'LINE',None,{999:wc},[WK,WK+timedelta(days=7)],{(999,WK):36,(999,WK+timedelta(days=7)):36,("machine",998,WK):36,("machine",998,WK+timedelta(days=7)):36},SimpleNamespace(get=lambda *a:Rule(rule='finish')))
    assert not unplanned
    assert sum(l.planned_hours for l in lines)==pytest.approx(70.95)
    assert sum(l.planned_qty for l in lines if l.operation_id==1)==pytest.approx(10500)
    assert sum(l.planned_qty for l in lines if l.operation_id==2)==pytest.approx(10500)

def test_line_api_fields_validation_and_activation(client,auth,db):
    wc=WorkCenter(code='LINE-API',name='Hat')
    item=Item(code='LINE-API-ITEM',name='Test')
    db.add_all([wc,item]);db.flush()
    machine=Machine(code='API-A',work_center=wc,required_crew_size=3)
    db.add(machine);db.flush()
    op=RoutingOperation(primary_machine_id=machine.id,item_id=item.id,work_center_id=wc.id,seq=10,operation_name='TAVLAMA',cycle_time_sec=20)
    db.add(op);db.commit()
    body={'code':wc.code,'name':wc.name,'planning_mode':'line','required_crew_size':3}
    url=f'/api/workcenters/{wc.id}'
    res=client.put(url,headers=auth,json=body)
    assert res.status_code==200,res.text
    # Missing intervals must not discard the user's chosen mode or crew.
    db.expire_all()
    assert db.get(WorkCenter,wc.id).planning_mode=='line'
    saved=next(w for w in client.get('/api/workcenters',headers=auth).json() if w['id']==wc.id)
    assert saved['planning_mode']=='line' and saved['required_crew_size']==3
    from app.services.plan_preflight import _items_without_routing
    issues=_items_without_routing(db, inputs=([SimpleNamespace(item=item,order_no='LINE-MISSING')],[],{}))
    assert any(i.item_code==item.code and 'hat çıkış aralığı eksik' in i.item_name for i in issues)
    opurl=f'/api/routing-operations/{op.id}'
    for invalid in [0,-1]:
        assert client.patch(opurl,headers=auth,json={'line_interval_sec':invalid}).status_code==422
    res=client.patch(opurl,headers=auth,json={'line_interval_sec':60,'units_per_cycle':5})
    assert res.status_code==200,res.text
    assert res.json()['line_interval_sec']==60
    db.expire_all()
    assert not _items_without_routing(db, inputs=([SimpleNamespace(item=item,order_no='LINE-MISSING')],[],{}))
    res=client.put(url,headers=auth,json=body)
    assert res.status_code==200,res.text
    assert client.patch(opurl,headers=auth,json={'line_interval_sec':None}).status_code==400
    weekurl=f'/api/machines/{machine.id}/weeks/{WK}'
    for invalid in [-1,169]:
        assert client.put(weekurl,headers=auth,json={'working_hours':invalid}).status_code==422
    res=client.put(weekurl,headers=auth,json={'working_hours':35})
    assert res.status_code==200,res.text
    saved=client.get(url+f'/station-weeks?start={WK}&weeks=1',headers=auth).json()[0]['stations'][0]
    assert saved['working_hours']==35 and saved['required_crew_size']==3
    profile=client.get(url+f'/weeks?start={WK}&weeks=1',headers=auth).json()[0]
    assert profile['capacity_hours']==35 and profile['required_labor_hours']==105
    assert profile['efficient_hours_per_person']==0
    assert client.put(f'/api/machines/{machine.id}',headers=auth,json={'code':machine.code,'required_crew_size':0}).status_code==422

def test_line_preflight_requires_station_hours_and_crew(db):
    from app.services.plan_preflight import _capacity_issues
    from app.schemas import AutoPlanRequest
    wc=WorkCenter(code='LINE-PREFLIGHT',name='Hat',planning_mode='line',required_crew_size=2)
    db.add(wc);db.flush()
    machine=Machine(code="PREFLIGHT-A",work_center=wc,required_crew_size=2)
    db.add(machine);db.flush()
    db.add(MachineWeek(machine_id=machine.id,week_start=WK,working_hours=35))
    db.commit()
    _,missing=_capacity_issues(db,AutoPlanRequest(start_week=WK,weeks=2),[wc],inputs=([],[],{}))
    assert len(missing)==1
    assert missing[0].missing_fields==['PREFLIGHT-A: haftalık saat']


def test_independent_station_limits_and_alternatives():
    from app.services.planning import _place_quantity, draft_line_to_dict
    from app.services.scenarios import Rule
    wc=WorkCenter(id=900,code='TWO-LINES',planning_mode='line')
    a=Machine(id=901,work_center=wc,is_active=True,required_crew_size=3)
    b=Machine(id=902,work_center=wc,is_active=True,required_crew_size=2)
    op=RoutingOperation(id=903,seq=10,work_center=wc,work_center_id=900,primary_machine_id=901,line_interval_sec=3600,cycle_time_sec=3600,units_per_cycle=1)
    item=SimpleNamespace(code='6-LINE',operations=[op])
    order=SimpleNamespace(id=1,order_no='LINE',item=item)
    rules=SimpleNamespace(get=lambda *a:Rule(rule='finish'))
    def place(qty, room):
        return _place_quantity(order,qty,'LINE',None,{900:wc},[WK],room,rules)
    room={(900,WK):55,('machine',901,WK):35,('machine',902,WK):20}
    lines,unplanned,_=place(50,dict(room))
    assert sum(l.planned_qty for l in lines)==35 and unplanned
    assert all(l.machine_id==901 for l in lines)
    op.alt_stations=[RoutingOperationStation(machine_id=902)]
    lines,unplanned,_=place(50,room)
    assert not unplanned and [l.machine_id for l in lines]==[901,902]
    assert [l.planned_hours for l in lines]==[35,15]
    assert room[(900,WK)]==5 and room[('machine',902,WK)]==5
    assert draft_line_to_dict(lines[-1])['machine_id']==902
    # A second order competes for the same remaining station hours.
    lines,unplanned,_=place(10,room)
    assert sum(l.planned_qty for l in lines)==5 and unplanned
    assert all(l.machine_id==902 for l in lines)


def test_station_totals_wc_crew_fallback_and_inactive_excluded(db):
    # Istasyon ekibi bos ise is merkezinin 'gerekli ekip' tanimi kullanilir (99); pasif istasyon haric.
    wc=WorkCenter(code='TOTAL-LINES',name='Hat',planning_mode='line',required_crew_size=99,default_efficient_hours=24)
    db.add(wc);db.flush()
    for code,hours,crew,active in [('A',35,3,True),('B',20,2,True),('C',168,10,False),('D',60,None,True)]:
        m=Machine(code='TOTAL-'+code,work_center=wc,required_crew_size=crew,is_active=active)
        db.add(m);db.flush();db.add(MachineWeek(machine_id=m.id,week_start=WK,working_hours=hours))
    db.commit()
    p=week_profile(db,wc,WK)
    assert p['capacity_hours']==115 and p['required_labor_hours']==145+60*99
    assert p['required_crew_size']==104 and p['efficient_hours_per_person']==0

def test_simulation_persists_station_and_tracks_input_changes(db):
    from app.models import Order, PlanLine
    from app.schemas import AutoPlanRequest, ManualPlanLineIn
    from app.services.planning import simulate, write_simulation, add_manual_line, draft_line_to_dict, apply_plan_snapshot
    from app.services.plan_input_fingerprint import compute_plan_input_fingerprint
    wc=WorkCenter(code='E2E-LINE',name='Hat',planning_mode='line',is_planned=True,planning_reserve_pct=10)
    item=Item(code='6-E2E-LINE',name='Line')
    db.add_all([wc,item]);db.flush()
    m=Machine(code='E2E-A',work_center=wc,required_crew_size=3)
    db.add(m);db.flush()
    mw=MachineWeek(machine_id=m.id,week_start=WK,working_hours=10)
    op=RoutingOperation(item=item,work_center=wc,primary_machine_id=m.id,seq=10,operation_name='TAVLAMA',line_interval_sec=3600,cycle_time_sec=3600,units_per_cycle=1)
    order=Order(order_no='E2E-LINE',item=item,quantity=12,due_date=WK+timedelta(days=6),material_status='ready')
    db.add_all([mw,op,order]);db.commit()
    req=AutoPlanRequest(start_week=WK,weeks=1,work_center_ids=[wc.id])
    sim=simulate(db,req)
    assert sum(l.planned_hours for l in sim.lines)==9
    assert all(l.machine_id==m.id for l in sim.lines)
    write_simulation(db,req,sim,'test')
    saved=db.query(PlanLine).filter_by(order_id=order.id).one()
    assert saved.machine_id==m.id and saved.planned_hours==9
    with pytest.raises(ValueError,match='istasyonda'):
        add_manual_line(db,ManualPlanLineIn(order_id=order.id,operation_id=op.id,week_start=WK,planned_qty=1),'test')
    # Snapshot application retains the selected station.
    apply_plan_snapshot(db,req,[draft_line_to_dict(l) for l in sim.lines],'test',revision_id=None,replace_manual=False)
    db.flush()
    assert db.query(PlanLine).filter_by(order_id=order.id).one().machine_id==m.id
    def fp():
        db.flush();db.expire_all()
        return compute_plan_input_fingerprint(db,req)
    old=fp();m.required_crew_size=4;assert fp()!=old
    old=fp();mw.working_hours=11;assert fp()!=old
    old=fp();m.is_active=False;assert fp()!=old


def test_line_leadtime_cannot_use_other_station():
    from datetime import datetime
    from app.services.planning import _schedule_line_op
    wc=WorkCenter(id=920,code='LT-LINE',planning_mode='line')
    a=Machine(id=921,work_center=wc,is_active=True,required_crew_size=3)
    b=Machine(id=922,work_center=wc,is_active=True,required_crew_size=2)
    op=RoutingOperation(work_center_id=920,work_center=wc,primary_machine_id=921)
    result=_schedule_line_op(op,10,datetime.combine(WK,datetime.min.time()),WK+timedelta(days=6),
                             {(920,WK):25,('machine',921,WK):5,('machine',922,WK):20})
    assert result.status=='insufficient_capacity' and result.scheduled_hours==5
