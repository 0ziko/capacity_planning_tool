from app.models import Item, RoutingOperation, WorkCenter
from app.services.scenarios import RuleLookup, groups, flow, upsert_rule
from app.api.scenarios import list_rules, group_items


def test_navigation_uses_rule_scope_when_commercial_groups_differ(db):
    wc = WorkCenter(code='NAV-WC', name='Navigation')
    item = Item(code='NAV-6005510', name='Test', product_group='NAV-GN',
                main_group='NAV GN KUVETLER', sub_group='NAV ALT')
    db.add_all([wc, item]); db.flush()
    item.operations = [RoutingOperation(seq=n, operation_name=name, work_center_id=wc.id,
                                       cycle_time_sec=120, semi_finished_code=wip)
                       for n, name, wip in [(10, 'SIVAMA', 'NAV-01'), (20, 'FORMA', 'NAV-02'), (30, 'YIKAMA', 'NAV-03')]]
    db.flush()
    group_rule = upsert_rule(db, 'group', 'NAV-GN', None, 'SIVAMA', 'FORMA', 'cycles', 5, 0, '')
    item_rule = upsert_rule(db, 'item', 'NAV-GN', item.code, 'FORMA', 'YIKAMA', 'finish', 0, 15, '', 'NAV-02', 'NAV-03')
    before = (group_rule.id, item_rule.id)
    tab = next(g for g in groups(db) if g['product_group']=='NAV-GN')
    assert tab['rule_count']==2 and tab['item_count']==1
    assert not any(g['product_group']=='NAV GN KUVETLER|NAV ALT' for g in groups(db))
    assert group_items('NAV-GN', db, None)[0]['code']==item.code
    assert {r.id for r in list_rules('NAV-GN', db, None)}==set(before)
    detail = flow(db, tab['product_group'], item.code)
    assert detail['transitions'][0]['effective'].lag_cycles==5
    assert detail['transitions'][1]['effective'].wait_minutes==15
    assert RuleLookup(db).get(item, item.operations[1], item.operations[2]).id==item_rule.id
    db.rollback()


def test_saved_group_rules_remain_visible_without_routing(db):
    rule = upsert_rule(db, 'group', 'NAV-OLD', None, 'A', 'B', 'finish', 0, 30, '')
    tab = next(g for g in groups(db) if g['product_group']=='NAV-OLD')
    assert tab['item_count']==0 and tab['rule_count']==1
    assert list_rules('NAV-OLD', db, None)[0].id==rule.id
    assert flow(db, 'NAV-OLD')['nodes']==[]
    db.rollback()
