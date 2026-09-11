"""6000006: Excel vs DB — 5600606 uydurma kodu olmamali."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal
from app.models import Item
from app.services.production_bom import _parse_fg_records, load_bom_grouped, code_str, S

FG = "6000006"
BOM = Path(r"C:\Users\ozan.deniz\Desktop\BOM.xlsx")


def main() -> None:
    by_fg, header = load_bom_grouped(path=BOM)
    recs = by_fg[FG]
    k_stok, k_opis = header[3], header[7]

    excel_ops = set()
    excel_all = set()
    for r in recs:
        st = code_str(r.get(k_stok))
        excel_all.add(st)
        if S(r.get(k_opis)):
            excel_ops.add(st)

    parsed = _parse_fg_records(FG, recs, header)
    branch_outputs = [b.wip for b in parsed.branches if b.wip != parsed.finish_wip]

    db = SessionLocal()
    fg = db.query(Item).filter(Item.code == FG).first()
    asm_codes = {
        bl.component_code
        for bl in (fg.bom_lines or [])
        if bl.component_code == bl.source_wip and bl.component_code.startswith("5")
    }
    db.close()

    print("Excel'de 5600606 var mi?", "5600606" in excel_all)
    print("Excel op kodlari 5600606*:", sorted(s for s in excel_ops if "5600606" in s))
    print("Parse branch output 5600606*:", sorted(s for s in branch_outputs if "5600606" in s))
    print("DB asm link 5600606*:", sorted(s for s in asm_codes if "5600606" in s))
    print("DB'de uydurma 5600606 var mi?", "5600606" in asm_codes)

    ok = "5600606" not in asm_codes and "5600606-77" in asm_codes
    print("SONUC:", "OK" if ok else "HATA")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
