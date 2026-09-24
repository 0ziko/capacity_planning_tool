from sqlalchemy import event
from app.models import Item
from app.services.remaining_work import produced_qty_map
from tests.test_mes import case, plan, import_rows, DAY


def test_mes_credit_does_not_load_unrelated_catalog(db, case):
    plan(db, case, 0, DAY, 100, final=True)
    import_rows(db, case, [('bounded-load', DAY, case['shared'], 30)])
    unrelated = Item(code='UNRELATED-CREDIT-CATALOG', name='No demand')
    db.add(unrelated)
    db.commit()
    unrelated_id = unrelated.id
    key = (case['orders'][0].id, case['ops'][0][0].id)
    db.expunge_all()
    loaded = set()

    def on_load(session, obj):
        if isinstance(obj, Item):
            loaded.add(obj.id)

    event.listen(db, 'loaded_as_persistent', on_load)
    try:
        credits, _ = produced_qty_map(db, as_of=DAY, include_mes=True)
    finally:
        event.remove(db, 'loaded_as_persistent', on_load)
    assert credits[key] == 30
    assert unrelated_id not in loaded
