"""One-pass analysis of Desktop/BOM.xlsx (RECETELER) for listing / routing / stations."""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from openpyxl import load_workbook

BOM_PATH = Path(r"C:\Users\ozan.deniz\Desktop\BOM.xlsx")
OUT = Path(__file__).resolve().parents[1] / "logs" / "bom_analysis.txt"


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


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    L: list[str] = []

    def P(*a) -> None:
        L.append(" ".join(str(x) for x in a))

    print("loading workbook...", flush=True)
    wb = load_workbook(BOM_PATH, data_only=True, read_only=True)
    ws = wb[wb.sheetnames[0]]
    P("SHEETS", wb.sheetnames)

    header = None
    by_fg: dict[str, list[dict]] = defaultdict(list)
    n = 0
    for i, row in enumerate(ws.iter_rows(values_only=True), 1):
        vals = list(row[:14])
        if i == 1:
            header = [S(x) or f"COL{j}" for j, x in enumerate(vals)]
            P("HEADER", header)
            continue
        if not any(v is not None and S(v) != "" for v in vals):
            continue
        rec = {header[j]: vals[j] if j < len(vals) else None for j in range(len(header))}
        rec["_row"] = i
        fg = code_str(rec.get(header[1]))
        by_fg[fg].append(rec)
        n += 1
        if n % 20000 == 0:
            print(f"  rows {n}", flush=True)
    wb.close()
    print(f"loaded {n} rows, {len(by_fg)} FGs", flush=True)

    k_level, k_fg, k_sira, k_stok, k_ad, k_qty, k_op, k_opis, k_sure = header[:9]
    k_ist, k_alt, k_m3, k_m4, k_wc = header[9:14]
    P("ROW COUNT", n)
    P("UNIQUE 6li kod", len(by_fg))

    levels: Counter[str] = Counter()
    pref: Counter[str] = Counter()
    ops: Counter[tuple[str, str]] = Counter()
    wcs: Counter[str] = Counter()
    sures: list[float] = []
    op_n = 0
    station_filled = alt_n = multi_n = 0
    kod_agree = 0
    col_fill = Counter()
    fg_not6 = [fg for fg in by_fg if not fg.startswith("6")]

    branch_stats: list[tuple[int, str, int, int]] = []
    sig_map: dict[tuple[str, ...], list[str]] = defaultdict(list)
    branch_sig_map: dict[tuple[str, ...], list[str]] = defaultdict(list)
    # branch signature = frozenset of (wip_base, op-name sequence)
    family_map: dict[frozenset[tuple[str, ...]], list[str]] = defaultdict(list)

    for fg, recs in by_fg.items():
        recs.sort(key=lambda r: int(r.get(k_sira) or 0))
        wips: set[str] = set()
        op_names: list[str] = []
        branch_ops: dict[str, list[str]] = defaultdict(list)
        for r in recs:
            lv = code_str(r.get(k_level))
            st = code_str(r.get(k_stok))
            levels[lv] += 1
            pref[st[:1] if st else "?"] += 1
            if lv == (st[:1] if st else ""):
                kod_agree += 1
            for col in header:
                if S(r.get(col)) not in ("", "0"):
                    col_fill[col] += 1
            op = code_str(r.get(k_op))
            on = S(r.get(k_opis))
            if op or on:
                op_n += 1
                ops[(op, on)] += 1
                op_names.append(on)
                try:
                    sures.append(float(r.get(k_sure) or 0))
                except (TypeError, ValueError):
                    pass
                wc = S(r.get(k_wc))
                wcs[wc] += 1
                sts = [S(r.get(c)) for c in (k_ist, k_alt, k_m3, k_m4)]
                sts = [x for x in sts if x and x not in ("0", "None")]
                if sts:
                    station_filled += 1
                if len(sts) >= 2:
                    multi_n += 1
                    alt_n += 1
                if st.startswith("5"):
                    wips.add(base_wip(st))
                    branch_ops[base_wip(st)].append(on)
            elif st.startswith("5"):
                wips.add(base_wip(st))
        branch_stats.append((len(wips), fg, len(recs), len(op_names)))
        sig_map[tuple(op_names)].append(fg)
        for b, seq in branch_ops.items():
            branch_sig_map[tuple(seq)].append(f"{fg}:{b}")
        fam = frozenset(tuple(seq) for seq in branch_ops.values())
        family_map[fam].append(fg)

    P("FGs starting with 6", sum(1 for fg in by_fg if fg.startswith("6")))
    P("FGs NOT starting with 6", len(fg_not6), fg_not6[:20])
    P("LEVEL/KOD distribution", dict(levels.most_common()))
    P("STOK first digit", dict(pref))
    P("KOD == stok first digit", kod_agree, "/", n, f"{100 * kod_agree / n:.1f}%")
    P("rows with OP", op_n, "without", n - op_n)
    P("unique OP_KODU+name", len(ops))
    P("top ops:")
    for (oc, on), c in ops.most_common(40):
        P(f"  {oc:>6} {on:30} {c}")
    if sures:
        P("SURE min/max/nonzero/zero", min(sures), max(sures), sum(x > 0 for x in sures), sum(x == 0 for x in sures))
        P("SURE units hint: values look like MINUTES (typical 0.3-15, packing 3.5)")
    P("column fill rates:")
    for col in header:
        P(f"  {col!r}: {col_fill[col]}/{n}")
    P("op rows with >=1 station", station_filled)
    P("op rows with >=2 stations (alternatives)", multi_n)
    P("work centers on op rows:")
    for name, c in wcs.most_common():
        P(f"  {name!r}: {c}")

    branch_stats.sort(reverse=True)
    lens = [nr for _, _, nr, _ in branch_stats]
    P("rows/FG min/median/max", min(lens), sorted(lens)[len(lens) // 2], max(lens))
    P("FGs with 0 ops", sum(1 for *_, nops in branch_stats if nops == 0))
    P("FGs with 0 WIP-5 bases", sum(1 for nb, *_ in branch_stats if nb == 0))
    P("Most branched FGs (distinct 5xxxxx bases):")
    for nb, fg, nr, nops in branch_stats[:20]:
        P(f"  {fg} wip_bases={nb} rows={nr} ops={nops}")

    def dump_fg(fg: str, limit: int = 80) -> None:
        recs = by_fg[fg]
        P(f"\n===== RECIPE {fg} n={len(recs)} =====")
        name0 = S(recs[0].get(k_ad)) if recs else ""
        P("first-row name", name0)
        for r in recs[:limit]:
            st = code_str(r.get(k_stok))
            kind = "OP" if S(r.get(k_opis)) else ("FG" if st.startswith("6") else "MAT")
            P(
                f"  sira={r.get(k_sira):>4} {kind:3} L{code_str(r.get(k_level))} "
                f"stok={st:<16} qty={r.get(k_qty)} op={code_str(r.get(k_op)) or '-':<6} "
                f"{S(r.get(k_opis))[:20]:<20} sure={r.get(k_sure)} "
                f"ist={S(r.get(k_ist))} alt={S(r.get(k_alt))} m3={S(r.get(k_m3))} m4={S(r.get(k_m4))} "
                f"wc={S(r.get(k_wc))} | {S(r.get(k_ad))[:60]}"
            )
        if len(recs) > limit:
            P(f"  ... {len(recs) - limit} more")
        # branches
        branches: dict[str, list[str]] = defaultdict(list)
        mats = []
        for r in recs:
            st = code_str(r.get(k_stok))
            if S(r.get(k_opis)):
                branches[base_wip(st)].append(
                    f"{S(r.get(k_opis))}[{S(r.get(k_wc)) or '?'} {r.get(k_sure)}dk ist={S(r.get(k_ist)) or '-'} alt={S(r.get(k_alt)) or '-'}]"
                )
            elif not st.startswith("6"):
                mats.append((code_str(r.get(k_level)), st, r.get(k_qty), S(r.get(k_ad))[:50]))
        P(f"  BRANCHES {len(branches)}  MATS {len(mats)}")
        for b, seq in branches.items():
            P(f"    WIP {b}: {'  <-  '.join(reversed(seq)) if seq else '(no ops)'}")
            P(f"      forward SIRA: {' > '.join(seq)}")
        P("  materials:")
        for m in mats[:25]:
            P(f"    {m}")

    dump_fg("6000006", 120)
    dump_fg(branch_stats[0][1], 100)

    # pick a medium product that is not the max
    mid = next((t for t in branch_stats if 4 <= t[0] <= 8), branch_stats[5])
    dump_fg(mid[1], 80)

    P("\nUnique full FG op sequences:", len(sig_map))
    clusters = sorted(sig_map.items(), key=lambda kv: -len(kv[1]))
    P("Top identical FULL sequences (same op list in SIRA order):")
    for sig, members in clusters[:15]:
        P(f"  n={len(members)} ops={len(sig)} sample={members[0]} seq={' > '.join(sig[:15])}{' ...' if len(sig) > 15 else ''}")

    P("\nUnique per-WIP-branch op sequences:", len(branch_sig_map))
    bclusters = sorted(branch_sig_map.items(), key=lambda kv: -len(kv[1]))
    P("Top identical WIP-branch sequences (groupable scenario candidates):")
    for sig, members in bclusters[:20]:
        P(f"  n={len(members)} ops={len(sig)} sample={members[0]} seq={' > '.join(sig)}")

    P("\nIdentical parallel-family signatures (frozenset of branch sequences):", len(family_map))
    fams = sorted(family_map.items(), key=lambda kv: -len(kv[1]))
    for fam, members in fams[:12]:
        P(f"  n={len(members)} branches={len(fam)} sample={members[0]}")
        for seq in list(fam)[:6]:
            P(f"     {' > '.join(seq)}")

    # SIRA order vs typical manufacturing: is listing reverse (finish first)?
    P("\n--- Listing order check (first 3 op names vs last 3) across FGs ---")
    firsts = Counter()
    lasts = Counter()
    for fg, recs in list(by_fg.items())[:]:
        names = [S(r.get(k_opis)) for r in recs if S(r.get(k_opis))]
        if names:
            firsts[names[0]] += 1
            lasts[names[-1]] += 1
    P("Most common FIRST op in SIRA (listing start):", firsts.most_common(10))
    P("Most common LAST op in SIRA (listing end):", lasts.most_common(10))
    P("Interpretation: if first is PAKETLEME/YIKAMA, listing is typically TOP-DOWN (finished first, raw last).")

    OUT.write_text("\n".join(L), encoding="utf-8")
    print("WROTE", OUT, "lines", len(L), flush=True)


if __name__ == "__main__":
    main()
