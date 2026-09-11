"""FG BOM patlatma: hammadde, yari mamul adimi ve montaj baglantilari."""

from app.models import BomLine, Item, RoutingOperation, WorkCenter
from app.services.bom_tree import is_raw_material, is_wip_asm_link, is_wip_step
from app.services.production_bom import Branch, MaterialRow, ParsedFG, ParsedOp, import_parsed_fg


def _empty_ctx(db):
    import uuid

    wc = WorkCenter(code=f"WC-{uuid.uuid4().hex[:8]}", name="Test WC", is_planned=True, is_active=True, default_efficient_hours=8)
    db.add(wc)
    db.flush()
    return {"cache": {}, "wc_idx": {}, "machines": {}, "counters": {"created": 0, "updated": 0, "routes": 0, "bom": 0}, "warnings": []}


def test_fg_recipe_includes_raw_materials_and_wip_steps(db):
    ctx = _empty_ctx(db)
    branch = Branch(
        wip="5001848-15",
        instance=1,
        ops=[
            ParsedOp(
                listing_sira=10,
                wip="5001848",
                wip_op_code="5001848-23",
                wip_name="5001848-DIKIS KAYNAK",
                op_code="23",
                op_name="DIKIS KAYNAK",
                sure_dk=5.0,
                station="",
                alt_station="",
                wc_name="KAYNAK",
                materials=[MaterialRow(level="5", code="1001544", name="SAC", qty=2.0)],
            ),
            ParsedOp(
                listing_sira=11,
                wip="5001848",
                wip_op_code="5001848-15",
                wip_name="5001848-YIKAMA",
                op_code="15",
                op_name="YIKAMA",
                sure_dk=3.0,
                station="",
                alt_station="",
                wc_name="YIKAMA",
                materials=[],
            ),
        ],
    )
    finish = Branch(
        wip="5904145-01",
        instance=1,
        ops=[
            ParsedOp(
                listing_sira=1,
                wip="5904145",
                wip_op_code="5904145-01",
                wip_name="MONTAJ",
                op_code="01",
                op_name="MONTAJ",
                sure_dk=10.0,
                station="",
                alt_station="",
                wc_name="MONTAJ",
                materials=[MaterialRow(level="2", code="2000065", name="PUL", qty=4.0)],
            ),
        ],
    )
    parsed = ParsedFG(fg="6000006", name="Test FG", branches=[branch, finish], finish_wip="5904145-01")
    import_parsed_fg(db, parsed, **ctx)
    db.commit()

    fg = db.query(Item).filter(Item.code == "6000006").first()
    assert fg is not None
    lines = fg.bom_lines

    asm = [bl for bl in lines if is_wip_asm_link(bl.component_code, bl.source_wip, bl.recipe_seq)]
    steps = [
        bl
        for bl in lines
        if is_wip_step(bl.component_code) and not is_wip_asm_link(bl.component_code, bl.source_wip or "", bl.recipe_seq)
    ]
    raws = [bl for bl in lines if is_raw_material(bl.component_code)]

    assert len(asm) == 1
    assert asm[0].component_code == "5001848-15"
    assert any(bl.component_code == "5001848-23" for bl in steps)
    assert not any(bl.component_code == "5001848-15" for bl in steps)
    assert any(bl.component_code == "1001544" and bl.source_wip == "5001848-15" for bl in raws)
    assert any(bl.component_code == "2000065" and bl.source_wip == "5904145-01" for bl in raws)


def test_pre_first_op_materials_kept(db):
    ctx = _empty_ctx(db)
    from app.services.production_bom import _parse_fg_records

    header = ["KOD", "FG", "SIRA", "STOK", "AD", "MIK", "OPK", "OPIS", "SURE", "IST", "ALT", "M3", "M4", "WC"]
    recs = [
        {"KOD": "2", "FG": "6000099", "SIRA": 1, "STOK": "2000001", "AD": "CIVATA", "MIK": 2, "OPK": "", "OPIS": "", "SURE": None, "IST": "", "ALT": "", "M3": "", "M4": "", "WC": ""},
        {"KOD": "5", "FG": "6000099", "SIRA": 2, "STOK": "5000099-01", "AD": "WIP-OP", "MIK": 1, "OPK": "01", "OPIS": "LAZER", "SURE": 5, "IST": "", "ALT": "", "M3": "", "M4": "", "WC": "LAZER"},
    ]
    parsed = _parse_fg_records("6000099", recs, header)
    assert len(parsed.branches) == 1
    assert len(parsed.branches[0].ops[0].materials) == 1
    assert parsed.branches[0].ops[0].materials[0].code == "2000001"
