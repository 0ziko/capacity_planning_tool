"""Extract 5 FG recipes in manufacturing order (reverse of ERP listing within each WIP)."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from openpyxl import load_workbook

BOM_PATH = Path(r"C:\Users\ozan.deniz\Desktop\BOM.xlsx")
OUT = Path(__file__).resolve().parents[1] / "logs" / "bom_five_fg.json"

FGS = ["6000006", "6012181", "6000087", "6004761", "6000705"]


def S(x) -> str:
    if x is None:
        return ""
    return str(x).strip()


def code_str(x) -> str:
    if x is None:
        return ""
    if isinstance(x, int):
        return str(x)
    if isinstance(x, float) and x == int(x):
        return str(int(x))
    s = str(x).strip()
    if s.endswith(".0"):
        try:
            return str(int(float(s)))
        except ValueError:
            pass
    return s


def base_wip(stok: str) -> str:
    return stok.split("-")[0] if "-" in stok else stok


def sure_dk(v) -> float | None:
    try:
        if v is None or S(v) in ("", "None"):
            return None
        return round(float(v), 3)
    except (TypeError, ValueError):
        return None


def main() -> None:
    wb = load_workbook(BOM_PATH, data_only=True, read_only=True)
    ws = wb[wb.sheetnames[0]]
    header = None
    wanted = {fg: [] for fg in FGS}
    for i, row in enumerate(ws.iter_rows(values_only=True), 1):
        vals = list(row[:14])
        if i == 1:
            header = [S(x) or f"COL{j}" for j, x in enumerate(vals)]
            continue
        rec = {header[j]: vals[j] if j < len(vals) else None for j in range(len(header))}
        fg = code_str(rec.get(header[1]))
        if fg in wanted:
            wanted[fg].append(rec)
    wb.close()

    k_level, _, k_sira, k_stok, k_ad, k_qty, k_op, k_opis, k_sure = header[:9]
    k_ist, k_alt, _, _, k_wc = header[9:14]

    result = []
    for fg in FGS:
        recs = sorted(wanted[fg], key=lambda r: int(r.get(k_sira) or 0))
        name = ""
        steps = []  # listing order: op + following mats
        pending_mats = []
        current = None
        for r in recs:
            st = code_str(r.get(k_stok))
            on = S(r.get(k_opis))
            lv = code_str(r.get(k_level))
            if lv == "6" or st == fg:
                name = S(r.get(k_ad))
                continue
            if on:
                if current:
                    current["materials_listing_after"] = pending_mats
                    steps.append(current)
                pending_mats = []
                ist = S(r.get(k_ist))
                alt = S(r.get(k_alt))
                wc = S(r.get(k_wc))
                current = {
                    "listing_sira": int(r.get(k_sira) or 0),
                    "wip": base_wip(st),
                    "wip_op_code": st,
                    "wip_name": S(r.get(k_ad)),
                    "op_code": code_str(r.get(k_op)),
                    "op_name": on,
                    "sure_dk": sure_dk(r.get(k_sure)),
                    "station": "" if ist in ("0", "None") else ist,
                    "alt_station": "" if alt in ("0", "None") else alt,
                    "work_center": "" if wc in ("0", "None") else wc,
                    "qty": r.get(k_qty),
                }
            else:
                mat = {
                    "level": lv,
                    "code": st,
                    "name": S(r.get(k_ad)),
                    "qty": r.get(k_qty),
                }
                if current:
                    pending_mats.append(mat)
                else:
                    # stray mat before first op — attach later to first op
                    pending_mats.append(mat)
        if current:
            current["materials_listing_after"] = pending_mats
            steps.append(current)
        elif pending_mats:
            steps.append(
                {
                    "listing_sira": 0,
                    "wip": "",
                    "wip_op_code": "",
                    "wip_name": "",
                    "op_code": "",
                    "op_name": "(operasyonsuz malzeme)",
                    "sure_dk": None,
                    "station": "",
                    "alt_station": "",
                    "work_center": "",
                    "qty": None,
                    "materials_listing_after": pending_mats,
                }
            )

        # Consecutive same-WIP runs = one physical piece. Same 5xxxxx
        # appearing again later is a second instance (left/right kit, 2nd sink).
        runs: list[list] = []
        for s in steps:
            if not runs or runs[-1][-1]["wip"] != s["wip"]:
                runs.append([s])
            else:
                runs[-1].append(s)

        branches = []
        seen: dict[str, int] = defaultdict(int)
        for ops in runs:
            wip = ops[0]["wip"]
            seen[wip] += 1
            inst = seen[wip]
            mfg = list(reversed(ops))
            for i, op in enumerate(mfg):
                op = dict(op)
                op["mfg_seq"] = i + 1
                # materials listed after this op in ERP belong to this op
                op["materials"] = op.pop("materials_listing_after", [])
                mfg[i] = op
            branches.append(
                {
                    "wip": wip,
                    "instance": inst,
                    "name": (ops[-1]["wip_name"].split("-")[0].strip() if ops else wip),
                    "listing_first_op": ops[0]["op_name"] if ops else "",
                    "listing_last_op": ops[-1]["op_name"] if ops else "",
                    "mfg_first_op": mfg[0]["op_name"] if mfg else "",
                    "mfg_last_op": mfg[-1]["op_name"] if mfg else "",
                    "ops": mfg,
                }
            )

        result.append(
            {
                "fg": fg,
                "name": name,
                "n_rows": len(recs),
                "n_ops": len(steps),
                "n_branches": len(branches),
                "listing_first_op": steps[0]["op_name"] if steps else "",
                "listing_last_op": steps[-1]["op_name"] if steps else "",
                "branches": branches,
            }
        )

    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WROTE", OUT)
    for p in result:
        print(p["fg"], p["name"][:60], "branches", p["n_branches"], "ops", p["n_ops"])
        for b in p["branches"]:
            seq = " > ".join(o["op_name"] for o in b["ops"])
            mats = sum(len(o["materials"]) for o in b["ops"])
            print(f"  {b['wip']}: {seq}  mats={mats}")


if __name__ == "__main__":
    main()
