from datetime import date, timedelta
from app.models import Order
from tests.test_revenue_modes import _setup


def test_leadtime_material_gate_and_saved_forecast(client, auth, db):
    wc = _setup(client, auth)
    base = dict(item_code='UCUZ', quantity=1, start='2026-09-07', horizon_days=30, work_center_ids=[wc])
    for policy in ('conditional','strict'):
        expected=client.post('/api/plan/leadtime',headers=auth,json={**base,'material_status':'expected','material_ready_date':'2026-09-09','material_policy':policy})
        assert expected.status_code==200,expected.text
        assert expected.json()['start'][:10]>='2026-09-09'
    blocked=client.post('/api/plan/leadtime',headers=auth,json={**base,'material_policy':'strict'}).json()
    assert blocked['status']=='infeasible' and not blocked['end'] and not blocked['steps']
    conditional=client.post('/api/plan/leadtime',headers=auth,json=base).json()
    assert conditional['status']=='complete' and conditional['material_unverified'] and conditional['material_note']
    payload={k:conditional[k] for k in ('item_code','quantity','steps','status','material_status','material_ready_date','material_policy')}
    saved=client.post('/api/plan/leadtime/forecast',headers=auth,json={**payload,'label':'D4-CONDITIONAL'})
    assert saved.status_code==200,saved.text
    db.expire_all()
    order=db.query(Order).filter_by(order_no='D4-CONDITIONAL').one()
    assert order.material_status=='unknown' and 'koşullu' in order.note
    listed=client.get('/api/plan/forecast',headers=auth).json()
    assert next(r for r in listed if r['order_no']=='D4-CONDITIONAL')['material_status']=='unknown'
    assert client.post('/api/plan/leadtime/forecast',headers=auth,json={**payload,'material_policy':'strict'}).status_code==400
    assert client.post('/api/plan/leadtime/forecast',headers=auth,json={**payload,'material_status':'expected','material_ready_date':'2026-09-15'}).status_code==400


def test_leadtime_rejects_contradictory_material(client,auth):
    _setup(client,auth)
    base=dict(item_code='UCUZ',quantity=1,start='2026-09-07')
    for fields in [dict(material_status='expected'),dict(material_status='ready',material_ready_date=str(date.today()+timedelta(days=5)))]:
        response=client.post('/api/plan/leadtime',headers=auth,json={**base,**fields})
        assert response.status_code==400,response.text
