"""Excel: montaja hangi kodlar gidiyor (6000006)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.production_bom import load_bom_grouped, code_str, S, base_wip

FG = "6000006"
BOM = Path(r"C:\Users\ozan.deniz\Desktop\BOM.xlsx")
by_fg, header = load_bom_grouped(path=BOM)
recs = by_fg[FG]
k_sira, k_stok, k_ad, k_opis = header[2], header[3], header[4], header[7]

# suffix-only branches: base appears only as prefix in ops, never as material row
from collections import defaultdict
op_bases = defaultdict(set)
all_stoks = set()
for r in recs:
    st = code_str(r.get(k_stok))
    all_stoks.add(st)
    if S(r.get(k_opis)):
        op_bases[base_wip(st)].add(st)

print("=== Bases with ONLY suffixed op codes (no bare base in excel) ===")
count = 0
for base, codes in sorted(op_bases.items()):
    if base.startswith("5") and base not in all_stoks and len(codes) >= 1:
        count += 1
        if base in ("5600606", "5600605", "5600007", "5001848"):
            print(f"  {base}: ops={sorted(codes)}")

print(f"total suffix-only bases: {count}")

# Material rows with 5xxxx on finish area (low sira / near end)
print("\n=== 5600606*: all rows ===")
for r in sorted(recs, key=lambda x: int(x.get(k_sira) or 0)):
    st = code_str(r.get(k_stok))
    if "5600606" in st:
        print(f"  {r.get(k_sira)} | {st} | op={S(r.get(k_opis))} | mat")
