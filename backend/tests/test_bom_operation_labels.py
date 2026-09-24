from app.services.production_bom import reconcile_operation_labels, _parse_fg_records, run_production_bom_import
from app.models import Item
from io import BytesIO
from openpyxl import Workbook

H=["level","fg","seq","stock","name","qty","opcode","operation","duration","station","alternative","x","y","wc"]
def row(seq,code,name,op,minutes):
    return dict(zip(H,[5,"6001234",seq,code,code,1,op,name,minutes,"M1","",None,None,"Heat"]))

def test_missing_label_repaired_from_same_exact_code_without_copying_process_data():
    donor=row(1,"5800001-19","Wash",19,.24)
    missing=row(21," 5800001-19 ",None,None,.24)
    missing["station"]="M2"
    source={"6001234":[row(20,"5800001-02","Heat",2,2),missing,row(22,"5800001-01","Form",1,.8)],"6001235":[donor]}
    fixed,warnings,errors=reconcile_operation_labels(source,H)
    assert not errors and len(warnings)==1
    assert source["6001234"][1]["operation"] is None
    assert fixed["6001234"][1]["station"]=="M2"
    parsed=_parse_fg_records("6001234",fixed["6001234"],H)
    assert [op.wip_op_code for op in parsed.branches[0].ops]==["5800001-01","5800001-19","5800001-02"]
    assert parsed.branches[0].wip=="5800001-02"


def test_material_reference_without_process_evidence_stays_material():
    material=row(2,"5800001-19",None,None,None)
    fixed,w,e=reconcile_operation_labels({"6001234":[row(1,"5800001-19","Wash",19,.24),material]},H)
    assert not w and not e and fixed["6001234"][1]["operation"] is None


def test_conflicting_or_missing_donor_blocks_before_master_write(db):
    rows=[row(1,"5800001-19","Wash",19,.24),row(2,"5800001-19","Heat",2,.24),row(3,"5800001-19",None,None,.24)]
    _,w,e=reconcile_operation_labels({"6001234":rows},H)
    assert e and not w
    wb=Workbook();wb.active.append(H)
    for r in rows:wb.active.append([r.get(k) for k in H])
    buffer=BytesIO();wb.save(buffer)
    before=db.query(Item).count()
    result=run_production_bom_import(db,content=buffer.getvalue())
    assert result.errors and result.fg_count==0
    assert db.query(Item).count()==before


def test_zero_filled_material_fields_are_not_missing_operations():
    material=row(2,"1000001",None,0,0)
    fixed,w,e=reconcile_operation_labels({"6001234":[material]},H)
    assert not w and not e and fixed["6001234"][0] == material


def test_targeted_import_uses_global_donors_but_not_unrelated_errors():
    source={"6001234":[row(1,"5800001-19",None,None,.24)],
            "6001235":[row(2,"5800001-19","Wash",19,.24),row(3,"5999999-01",None,None,1)]}
    fixed,w,e=reconcile_operation_labels(source,H,{"6001234"})
    assert not e and len(w)==1 and fixed["6001234"][0]["operation"]=="Wash"


def test_raw_materials_with_duration_or_operation_code_remain_consumption():
    for code in ("1000614", "2000614"):
        material = row(2, code, None, 19, 3)
        material["qty"] = 2
        source = {"6001234": [row(1, "5800001-19", "Wash", 19, .24), material],
                  "6001235": [row(3, code, "Wash", 19, 3)]}
        fixed, warnings, errors = reconcile_operation_labels(source, H)
        assert not errors and not warnings
        assert fixed["6001234"][1] == material
        parsed = _parse_fg_records("6001234", fixed["6001234"], H)
        assert len(parsed.branches[0].ops) == 1
        consumed = parsed.branches[0].ops[0].materials
        assert [(m.code, m.qty) for m in consumed] == [(code, 2)]
