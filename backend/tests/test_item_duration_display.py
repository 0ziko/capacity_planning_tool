from datetime import date
import pytest
from app.models import Item, RoutingOperation, WorkCenter
from app.services.requirements import item_total_hours


def test_missing_line_interval_is_not_zero_and_totals_keep_resource_units(db):
    item = Item(code='6-DURATION-DISPLAY', name='Test')
    line = WorkCenter(code='DURATION-LINE', name='Tavlama', planning_mode='line')
    labor = WorkCenter(code='DURATION-LABOR', name='Montaj')
    db.add_all([item, line, labor]); db.flush()
    a = RoutingOperation(item=item, work_center=line, seq=10, operation_name='TAVLAMA', cycle_time_sec=1200, units_per_cycle=1)
    b = RoutingOperation(item=item, work_center=labor, seq=20, operation_name='MONTAJ', cycle_time_sec=3600)
    db.add_all([a,b]); db.commit()
    result = item_total_hours(db, item.code, 1)
    assert result['operations'][0]['hours'] is None
    assert result['operations'][0]['missing_reason']
    assert result['line_hours'] is None and result['total_hours'] is None
    assert result['labor_hours'] == 1
    assert result['missing_operation_count'] == 1
    # Reading never fills in the Excel-only assumption or changes legacy time.
    assert a.line_interval_sec is None and a.cycle_time_sec == 1200
    a.line_interval_sec = 5
    a.units_per_cycle = 10
    db.commit()
    result = item_total_hours(db, item.code, 1)
    assert result['operations'][0]['hours'] == pytest.approx(1200/3600)
    assert result['line_hours'] == pytest.approx(1200/3600)
    assert result['labor_hours'] == 1
    assert result['missing_operation_count'] == 0
    result = item_total_hours(db, item.code, 10)
    assert result['line_hours'] == pytest.approx(1200/3600)
    assert result['labor_hours'] == 10
