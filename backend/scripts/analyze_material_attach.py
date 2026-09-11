"""Excel vs parse vs DB: malzeme hangi operasyona baglanmis."""
from __future__ import annotations

import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db.session import SessionLocal
from app.models import BomLine, Item
from app.services.production_bom import (
    S,
    _parse_fg_records,
    code_str,
    load_bom_grouped,
)

BOM = Path(r"C:\Users\ozan.deniz\Desktop\BOM.xlsx")

FOCUS_6000006 = ["2000067", "2000077", "2000078", "2000203", "2000204", "3000261"]
FOCUS_6005510 = ["3000205", "3000261"]


def excel_context(recs, header, codes: set[str]):
    k_sira, k_stok, k_ad, k_qty = header[2], header[3], header[4], header[5]
    k_opis = header[7]
    ordered = sorted(recs, key=lambda x: int(x.get(k_sira) or 0))
    last_op = None
    out = []
    for r in ordered:
        st = code_str(r.get(k_stok))
        on = S(r.get(k_opis))
        if on:
            last_op = {
                "sira": int(r.get(k_sira) or 0),
                "stok": st,
                "op": on,
                "ad": S(r.get(k_ad)),
            }
        if st in codes and not on:
            out.append(
                {
                    "sira": int(r.get(k_sira) or 0),
                    "code": st,
                    "name": S(r.get(k_ad)),
                    "qty": r.get(k_qty),
                    "prev_op": last_op,
                    "next_op": None,
                }
            )
    # fill next_op
    ops = []
    for r in ordered:
        on = S(r.get(k_opis))
        if on:
            ops.append(
                {
                    "sira": int(r.get(k_sira) or 0),
                    "stok": code_str(r.get(k_stok)),
                    "op": on,
                }
            )
    for row in out:
        nxt = next((o for o in ops if o["sira"] > row["sira"]), None)
        row["next_op"] = nxt
    return out


def parsed_home(parsed, codes: set[str]):
    found = []
    for b in parsed.branches:
        for op in b.ops:
            for m in op.materials:
                if m.code in codes:
                    found.append(
                        {
                            "code": m.code,
                            "qty": m.qty,
                            "op_code": op.wip_op_code,
                            "op_name": op.op_name,
                            "branch": b.wip,
                            "listing_sira": op.listing_sira,
                        }
                    )
    return found


def db_home(db, fg: str, codes: set[str]):
    item = db.query(Item).filter(Item.code == fg).first()
    if not item:
        return []
    rows = []
    for bl in item.bom_lines or []:
        if bl.component_code in codes:
            rows.append(
                {
                    "code": bl.component_code,
                    "qty": bl.quantity,
                    "source_wip": bl.source_wip,
                    "recipe_seq": bl.recipe_seq,
                    "branch_sira": bl.branch_listing_sira,
                    "name": bl.component_name,
                }
            )
    return rows


def print_fg(title, recs, header, parsed, db, codes):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)
    print("\n--- Excel: malzeme satiri + onceki/sonraki operasyon ---")
    for row in excel_context(recs, header, codes):
        prev = row["prev_op"]
        nxt = row["next_op"]
        print(
            f"  {row['code']} qty={row['qty']!r} sira={row['sira']} {row['name']}"
        )
        print(
            f"     ONCEKI OP: {prev['stok'] if prev else '-'} {prev['op'] if prev else ''}"
        )
        print(
            f"     SONRAKI OP: {nxt['stok'] if nxt else '-'} {nxt['op'] if nxt else ''}"
        )
    print("\n--- Parser (malzeme hangi ParsedOp.materials icinde) ---")
    for row in parsed_home(parsed, codes):
        print(
            f"  {row['code']} qty={row['qty']} -> {row['op_code']} {row['op_name']} (branch={row['branch']})"
        )
    print("\n--- DB bom_lines ---")
    for row in db_home(db, title.split()[0], codes):
        print(
            f"  {row['code']} qty={row['qty']} source={row['source_wip']} seq={row['recipe_seq']} {row['name']}"
        )

    print("\n--- Parser: dal ozeti (uretim sirasi) ---")
    for b in parsed.branches:
        mark = " FINISH" if b.wip == parsed.finish_wip else ""
        print(f"  branch={b.wip}{mark}")
        for op in b.ops:
            mats = ", ".join(f"{m.code}x{m.qty}" for m in op.materials) or "-"
            print(f"    {op.wip_op_code} {op.op_name} sira={op.listing_sira} mats=[{mats}]")


def mismatch_stats(by_fg, header, db, fgs):
    """Excel onceki-op vs parser baglantisi."""
    excel_vs_parse = defaultdict(int)
    qty_zero = 0
    qty_mismatch = 0
    orphan_before_first = 0
    samples = []

    for fg in fgs:
        recs = by_fg[fg]
        parsed = _parse_fg_records(fg, recs, header)
        excel_rows = excel_context(
            recs,
            header,
            {code_str(r.get(header[3])) for r in recs if not S(r.get(header[7]))},
        )
        parse_map = {}
        for row in parsed_home(
            parsed, {x["code"] for x in excel_rows}
        ):
            parse_map.setdefault(row["code"], []).append(row)

        for er in excel_rows:
            prev = er["prev_op"]
            homes = parse_map.get(er["code"], [])
            parse_op = homes[0]["op_code"] if homes else None
            excel_op = prev["stok"] if prev else None
            if excel_op != parse_op:
                excel_vs_parse["mismatch"] += 1
                if len(samples) < 40:
                    samples.append(
                        {
                            "fg": fg,
                            "code": er["code"],
                            "excel_op": excel_op,
                            "excel_op_name": prev["op"] if prev else None,
                            "parse_op": parse_op,
                            "parse_op_name": homes[0]["op_name"] if homes else None,
                            "qty": er["qty"],
                        }
                    )
            else:
                excel_vs_parse["match"] += 1
            if not prev:
                orphan_before_first += 1
            try:
                q = float(er["qty"]) if er["qty"] not in (None, "") else None
            except (TypeError, ValueError):
                q = None
            if q == 0:
                qty_zero += 1

        item = db.query(Item).filter(Item.code == fg).first()
        if not item:
            continue
        for bl in item.bom_lines or []:
            if (bl.quantity or 0) == 0 and (bl.component_code or "").startswith(("1", "2", "3", "4")):
                qty_zero += 0  # counted below separately

    db_qty_zero = 0
    for fg in fgs:
        item = db.query(Item).filter(Item.code == fg).first()
        if not item:
            continue
        for bl in item.bom_lines or []:
            code = bl.component_code or ""
            if code[:1] in "1234" and float(bl.quantity or 0) == 0:
                db_qty_zero += 1

    return excel_vs_parse, orphan_before_first, qty_zero, db_qty_zero, samples


def classify_koli_ops(by_fg, header, fgs):
    """3xxxxx koli satirlari Excel onceki op adi dagilimi vs parse."""
    excel_ops = defaultdict(int)
    parse_ops = defaultdict(int)
    mismatch = 0
    pack_in_excel_wash_in_parse = []
    for fg in fgs:
        recs = by_fg[fg]
        parsed = _parse_fg_records(fg, recs, header)
        koli_codes = set()
        for r in recs:
            st = code_str(r.get(header[3]))
            ad = S(r.get(header[4])).upper()
            if st.startswith("3") and "KOLI" in ad:
                koli_codes.add(st)
        if not koli_codes:
            continue
        for er in excel_context(recs, header, koli_codes):
            prev = er["prev_op"]
            excel_ops[(prev["op"] if prev else "YOK")] += 1
        for ph in parsed_home(parsed, koli_codes):
            parse_ops[ph["op_name"]] += 1
            er_match = [e for e in excel_context(recs, header, {ph["code"]}) if e["code"] == ph["code"]]
            if er_match:
                prev = er_match[0]["prev_op"]
                excel_name = prev["op"] if prev else ""
                if excel_name and excel_name != ph["op_name"]:
                    mismatch += 1
                    if len(pack_in_excel_wash_in_parse) < 25:
                        pack_in_excel_wash_in_parse.append(
                            f"{fg} {ph['code']}: excel={excel_name}/{prev['stok'] if prev else ''} parse={ph['op_name']}/{ph['op_code']}"
                        )
    return excel_ops, parse_ops, mismatch, pack_in_excel_wash_in_parse


def main():
    print("BOM okunuyor...")
    by_fg, header = load_bom_grouped(path=BOM)
    db = SessionLocal()
    try:
        for fg, codes in (("6000006", set(FOCUS_6000006)), ("6005510", set(FOCUS_6005510))):
            recs = by_fg[fg]
            parsed = _parse_fg_records(fg, recs, header)
            print_fg(f"{fg} odak malzemeler", recs, header, parsed, db, codes)

        rng = random.Random(20260909)
        all_fgs = sorted(by_fg.keys())
        sample = rng.sample(all_fgs, 100)

        print("\n" + "=" * 80)
        print("100 mamul ornekleme")
        print("=" * 80)
        stats, orphan, excel_qty0, db_qty0, samples = mismatch_stats(by_fg, header, db, sample)
        print(f"Malzeme-op eslesme: {dict(stats)}")
        print(f"Onceki op yok (ilk op oncesi malzeme): {orphan}")
        print(f"DB hammadde qty=0: {db_qty0}")
        print("Ornek sapmalar (excel onceki op != parse op):")
        for s in samples[:20]:
            print(
                f"  {s['fg']} {s['code']} excel={s['excel_op']} {s['excel_op_name']} | parse={s['parse_op']} {s['parse_op_name']} qty={s['qty']!r}"
            )

        excel_ops, parse_ops, mismatch, ex = classify_koli_ops(by_fg, header, sample)
        print("\nKoli Excel onceki operasyon adlari:")
        for k, v in sorted(excel_ops.items(), key=lambda x: -x[1])[:15]:
            print(f"  {v:4d}  {k}")
        print("Koli parser operasyon adlari:")
        for k, v in sorted(parse_ops.items(), key=lambda x: -x[1])[:15]:
            print(f"  {v:4d}  {k}")
        print(f"Koli excel!=parse: {mismatch}")
        for line in ex:
            print(f"  {line}")

        # extra: for sampled FGs, how often reverse causes material to sit on first-mfg instead of last-mfg (pack)
        print("\n--- Reverse etkisi: excel onceki=listede erken op, parse ayni mi? ---")
        reverse_wrong = 0
        pack_names = ("PAKET", "BITIR", "MONTAJ", "ETIKET")
        wash_on_koli = 0
        for fg in sample:
            recs = by_fg[fg]
            parsed = _parse_fg_records(fg, recs, header)
            koli = {
                code_str(r.get(header[3]))
                for r in recs
                if code_str(r.get(header[3])).startswith("3")
                and "KOLI" in S(r.get(header[4])).upper()
                and not S(r.get(header[7]))
            }
            for er in excel_context(recs, header, koli):
                prev = er["prev_op"]
                homes = parsed_home(parsed, {er["code"]})
                if not homes or not prev:
                    continue
                if homes[0]["op_code"] != prev["stok"]:
                    reverse_wrong += 1
                if "YIKAMA" in (homes[0]["op_name"] or "").upper() and "YIKAMA" not in (prev["op"] or "").upper():
                    wash_on_koli += 1
        print(f"Koli parse!=excel onceki: {reverse_wrong}")
        print(f"Koli parse YIKAMA ama excel YIKAMA degil: {wash_on_koli}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
