"""Son operasyon adimi atlaninca malzeme yanlis adimin altina duser mu?"""
from __future__ import annotations

import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db.session import SessionLocal
from app.models import Item
from app.services.bom_tree import is_wip_asm_link, is_wip_step
from app.services.production_bom import S, _parse_fg_records, code_str, load_bom_grouped

BOM = Path(r"C:\Users\ozan.deniz\Desktop\BOM.xlsx")


def tree_parent_of_material(lines):
    """Frontend sequential walk: malzeme hangi adimin altina gider."""
    from collections import defaultdict

    by_src = defaultdict(list)
    for bl in sorted(lines, key=lambda x: (-(x.branch_listing_sira or 0), x.recipe_seq or 99999, x.component_code or "")):
        by_src[bl.source_wip or "—"].append(bl)

    parents = []  # (mat_code, qty, parent_step, source)
    for src, bls in by_src.items():
        current_step = None
        for bl in bls:
            code = bl.component_code or ""
            if is_wip_asm_link(code, bl.source_wip or ""):
                current_step = None
                continue
            if is_wip_step(code) and not is_wip_asm_link(code, bl.source_wip or ""):
                current_step = code
                continue
            if code[:1] in "1234":
                parents.append((code, bl.quantity, current_step, src, bl.recipe_seq))
    return parents


def main():
    by_fg, header = load_bom_grouped(path=BOM)
    rng = random.Random(20260909)
    sample = rng.sample(sorted(by_fg.keys()), 100)
    db = SessionLocal()

    last_op_skipped = 0
    last_op_has_mats = 0
    last_op_mats_misparented = 0
    examples = []
    parse_prev_mismatch = 0
    parse_examples = []
    qty0_excel = 0
    koli_wrong_parent = []

    for fg in sample:
        recs = by_fg[fg]
        parsed = _parse_fg_records(fg, recs, header)
        for b in parsed.branches:
            if not b.ops:
                continue
            last = b.ops[-1]
            skipped = last.wip_op_code == b.wip
            if skipped:
                last_op_skipped += 1
            if last.materials:
                last_op_has_mats += 1

        # parse vs excel previous op, occurrence-aware
        k_sira, k_stok, k_opis = header[2], header[3], header[7]
        ordered = sorted(recs, key=lambda x: int(x.get(k_sira) or 0))
        last_op = None
        excel_pairs = []
        for r in ordered:
            st = code_str(r.get(k_stok))
            on = S(r.get(k_opis))
            if on:
                last_op = st
                continue
            if st[:1] in "1234" and last_op:
                excel_pairs.append((st, last_op, r.get(header[5])))
                try:
                    if float(r.get(header[5]) or 1) == 0:
                        qty0_excel += 1
                except (TypeError, ValueError):
                    pass

        parse_pairs = []
        for b in parsed.branches:
            for op in b.ops:
                for m in op.materials:
                    if m.code[:1] in "1234":
                        parse_pairs.append((m.code, op.wip_op_code))
        # bag by sequential same (code) occurrences
        from collections import deque

        parse_q = defaultdict(deque)
        for code, op in parse_pairs:
            parse_q[code].append(op)
        for code, excel_op, qty in excel_pairs:
            if parse_q[code]:
                pop = parse_q[code].popleft()
                if pop != excel_op:
                    parse_prev_mismatch += 1
                    if len(parse_examples) < 15:
                        parse_examples.append(f"{fg} {code}: excel_prev={excel_op} parse={pop}")

        item = db.query(Item).filter(Item.code == fg).first()
        if not item:
            continue
        parents = tree_parent_of_material(item.bom_lines or [])
        parse_last_mats = {}
        for b in parsed.branches:
            last = b.ops[-1]
            for m in last.materials:
                parse_last_mats[(m.code, b.wip)] = last.wip_op_code
        for code, qty, parent_step, src, seq in parents:
            true_op = parse_last_mats.get((code, src))
            if true_op and parent_step and parent_step != true_op:
                last_op_mats_misparented += 1
                if len(examples) < 20:
                    examples.append(
                        f"{fg} {code} qty={qty} excel/parse_op={true_op} tree_parent={parent_step} src={src}"
                    )
                ad = ""
                for bl in item.bom_lines:
                    if bl.component_code == code and bl.source_wip == src:
                        ad = (bl.component_name or "").upper()
                        break
                if "KOLI" in ad:
                    koli_wrong_parent.append(f"{fg} {code} tree={parent_step} gercek={true_op}")

    print(f"Ornek mamul: {len(sample)}")
    print(f"Atlanan son-op (wip_op_code==branch.wip): {last_op_skipped}")
    print(f"Son op malzemeli dal: {last_op_has_mats}")
    print(f"Agacta yanlis adima dusen son-op malzemesi: {last_op_mats_misparented}")
    print(f"Parse != excel onceki op (occurrence eslesmeli): {parse_prev_mismatch}")
    print(f"Excel qty=0 hammadde satiri: {qty0_excel}")
    print("\nAgac sapma ornekleri:")
    for e in examples:
        print(" ", e)
    print("\nKoli yanlis parent:")
    for e in koli_wrong_parent[:20]:
        print(" ", e)
    print("\nParse sapma ornekleri:")
    for e in parse_examples:
        print(" ", e)

    # 6000006 / 6005510 tree parents for focus codes
    for fg, codes in (
        ("6000006", {"2000067", "2000077", "2000078", "2000203", "2000204", "3000261"}),
        ("6005510", {"3000205"}),
    ):
        item = db.query(Item).filter(Item.code == fg).first()
        print(f"\n{fg} agac parent:")
        for code, qty, parent, src, seq in tree_parent_of_material(item.bom_lines or []):
            if code in codes:
                print(f"  {code} qty={qty} parent_step={parent} source={src} seq={seq}")

    db.close()


if __name__ == "__main__":
    main()
