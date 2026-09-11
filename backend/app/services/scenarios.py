"""Senaryo matrisi: operasyon gecis kurallari (urun grubu geneli / stok koduna ozel).

Kural, ardisik iki operasyon arasinda sonraki operasyonun ne zaman baslayabilecegini soyler:
  finish : onceki operasyon bitince (+ bekleme dk)                      [varsayilan]
  cycles : onceki operasyon N cevrim (adet) tamamlayinca (+ bekleme dk); N=0 => birlikte basla
Stok koduna ozel kural, urun grubu kuralini ezer. Eslesme operasyon ADI ve opsiyonel yarimamul kodu ile yapilir.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session, joinedload

from app.models import Item, OpTransitionRule, RoutingOperation, norm_op, norm_wip


@dataclass
class Rule:
    rule: str = "finish"
    lag_cycles: float = 0.0
    wait_minutes: float = 0.0
    source: str = "default"  # default / group / item
    id: int | None = None
    note: str = ""

    def describe(self) -> str:
        if self.rule == "cycles":
            base = "birlikte başlar" if self.lag_cycles <= 0 else f"{self.lag_cycles:g} çevrim sonra"
        else:
            base = "önceki bitince"
        if self.wait_minutes:
            base += f" + {self.wait_minutes:g} dk bekleme"
        return base


def _to_rule(r: OpTransitionRule) -> Rule:
    return Rule(rule=r.rule, lag_cycles=r.lag_cycles or 0.0, wait_minutes=r.wait_minutes or 0.0, source=r.scope, id=r.id, note=r.note or "")


def _rule_key(from_op: str, to_op: str, from_wip: str = "", to_wip: str = "") -> tuple[str, str, str, str]:
    return norm_op(from_op), norm_op(to_op), norm_wip(from_wip), norm_wip(to_wip)


class RuleLookup:
    """Tum kurallari bir kez yukler; operasyon + opsiyonel yarimamul kodu ile etkin kurali verir."""

    def __init__(self, db: Session):
        self.group: dict[tuple[str, str, str, str, str], OpTransitionRule] = {}
        self.item: dict[tuple[int, str, str, str, str], OpTransitionRule] = {}
        for r in db.query(OpTransitionRule).all():
            key_tail = (r.from_op_norm, r.to_op_norm, r.from_wip_norm or "", r.to_wip_norm or "")
            if r.scope == "item" and r.item_id is not None:
                self.item[(r.item_id, *key_tail)] = r
            else:
                self.group[(norm_op(r.product_group), *key_tail)] = r

    def _pick(self, item: Item, f: str, t: str, fw: str, tw: str) -> OpTransitionRule | None:
        # 1) tam eslesme (op + wip)
        if fw or tw:
            r = self.item.get((item.id, f, t, fw, tw))
            if r:
                return r
            r = self.group.get((norm_op(item.product_group), f, t, fw, tw))
            if r:
                return r
        # 2) yalnizca operasyon (kuralda wip bos)
        for fw2, tw2 in [(fw, tw), ("", ""), (fw, ""), ("", tw)]:
            r = self.item.get((item.id, f, t, fw2, tw2))
            if r and not (r.from_wip_norm or r.to_wip_norm):
                return r
        r = self.item.get((item.id, f, t, "", ""))
        if r:
            return r
        for fw2, tw2 in [(fw, tw), ("", ""), (fw, ""), ("", tw)]:
            r = self.group.get((norm_op(item.product_group), f, t, fw2, tw2))
            if r and not (r.from_wip_norm or r.to_wip_norm):
                return r
        return self.group.get((norm_op(item.product_group), f, t, "", ""))

    def get(self, item: Item, prev_op: RoutingOperation | str, next_op: RoutingOperation | str) -> Rule:
        if isinstance(prev_op, str):
            f, fw = norm_op(prev_op), ""
        else:
            f, fw = norm_op(prev_op.operation_name), norm_wip(prev_op.semi_finished_code)
        if isinstance(next_op, str):
            t, tw = norm_op(next_op), ""
        else:
            t, tw = norm_op(next_op.operation_name), norm_wip(next_op.semi_finished_code)
        r = self._pick(item, f, t, fw, tw)
        return _to_rule(r) if r else Rule()

    def group_rule(self, product_group: str, f: str, t: str, fw: str = "", tw: str = "") -> Rule | None:
        r = self.group.get((norm_op(product_group), norm_op(f), norm_op(t), norm_wip(fw), norm_wip(tw)))
        if r:
            return _to_rule(r)
        r = self.group.get((norm_op(product_group), norm_op(f), norm_op(t), "", ""))
        return _to_rule(r) if r else None

    def item_rule(self, item_id: int, f: str, t: str, fw: str = "", tw: str = "") -> Rule | None:
        r = self.item.get((item_id, norm_op(f), norm_op(t), norm_wip(fw), norm_wip(tw)))
        if r:
            return _to_rule(r)
        r = self.item.get((item_id, norm_op(f), norm_op(t), "", ""))
        return _to_rule(r) if r else None


# ---------------- Akis semasi (flow) ----------------

def _merge_sequences(seqs: list[list[str]]) -> list[str]:
    """Farkli stoklarin operasyon adi dizilerini sirayi koruyarak tek listede birlestirir."""
    if not seqs:
        return []
    seqs = sorted(seqs, key=len, reverse=True)
    merged: list[str] = list(dict.fromkeys(seqs[0]))
    for s in seqs[1:]:
        prev_pos = -1
        for name in s:
            if name in merged:
                prev_pos = merged.index(name)
                continue
            merged.insert(prev_pos + 1, name)
            prev_pos += 1
    return merged


def _wip_between(items: list[Item], from_name: str, to_name: str, item: Item | None) -> tuple[str, str]:
    if item:
        prev = next((o for o in item.operations if norm_op(o.operation_name or f"Op {o.seq}") == norm_op(from_name)), None)
        nxt = next((o for o in item.operations if norm_op(o.operation_name or f"Op {o.seq}") == norm_op(to_name)), None)
        return (prev.semi_finished_code if prev else "") or "", (nxt.semi_finished_code if nxt else "") or ""
    fw_codes: set[str] = set()
    tw_codes: set[str] = set()
    for it in items:
        for op in it.operations:
            nn = norm_op(op.operation_name or f"Op {op.seq}")
            if nn == norm_op(from_name) and op.semi_finished_code:
                fw_codes.add(op.semi_finished_code)
            if nn == norm_op(to_name) and op.semi_finished_code:
                tw_codes.add(op.semi_finished_code)
    fw = next(iter(fw_codes)) if len(fw_codes) == 1 else ""
    tw = next(iter(tw_codes)) if len(tw_codes) == 1 else ""
    return fw, tw


def _group_key(it: Item) -> str:
    if it.main_group and it.sub_group:
        return f"{it.main_group}|{it.sub_group}"
    if it.main_group:
        return it.main_group
    return it.product_group or ""


def groups(db: Session) -> list[dict]:
    items = db.query(Item).options(joinedload(Item.operations)).all()
    by_group: dict[str, list[Item]] = {}
    for it in items:
        if not it.operations:
            continue
        by_group.setdefault(_group_key(it), []).append(it)
    rules = db.query(OpTransitionRule).all()
    rule_count: dict[str, int] = {}
    for r in rules:
        key = norm_op(r.product_group) if r.scope == "group" else norm_op(r.item.product_group if r.item else "")
        rule_count[key] = rule_count.get(key, 0) + 1
    out = []
    for g, its in sorted(by_group.items(), key=lambda kv: kv[0]):
        names = _merge_sequences([[op.operation_name or f"Op {op.seq}" for op in it.operations] for it in its])
        out.append({"product_group": g, "item_count": len(its), "operations": names, "rule_count": rule_count.get(norm_op(g), 0)})
    return out


def flow(db: Session, product_group: str, item_code: str | None = None) -> dict:
    q = db.query(Item).options(joinedload(Item.operations).joinedload(RoutingOperation.work_center))
    if item_code:
        items = q.filter(Item.code == item_code).all()
        if not items:
            raise ValueError("Stok kodu bulunamadi")
        product_group = items[0].product_group or product_group
        group_items = q.filter(Item.product_group == product_group).all()
    else:
        items = q.filter(Item.product_group == product_group).all()
        group_items = items
    items = [it for it in items if it.operations]
    lookup = RuleLookup(db)

    seq_names = [[op.operation_name or f"Op {op.seq}" for op in it.operations] for it in items]
    names = _merge_sequences(seq_names)
    nodes = []
    for n in names:
        wcs: dict[str, int] = {}
        cts: list[float] = []
        wips: set[str] = set()
        cnt = 0
        for it in items:
            for op in it.operations:
                if norm_op(op.operation_name or f"Op {op.seq}") == norm_op(n):
                    cnt += 1
                    code = op.work_center.code if op.work_center else "?"
                    wcs[code] = wcs.get(code, 0) + 1
                    cts.append(op.cycle_time_sec)
                    if op.semi_finished_code:
                        wips.add(op.semi_finished_code)
        nodes.append({
            "name": n,
            "work_centers": sorted(wcs, key=lambda c: -wcs[c]),
            "item_count": cnt,
            "cycle_time_sec": round(sum(cts) / len(cts), 1) if cts else None,
            "semi_finished_codes": sorted(wips),
        })

    transitions = []
    item = items[0] if item_code and items else None
    for a, b in zip(names, names[1:]):
        fw, tw = _wip_between(items, a, b, item)
        g = lookup.group_rule(product_group, a, b, fw, tw)
        i = lookup.item_rule(item.id, a, b, fw, tw) if item else None
        eff = i or g or Rule()
        transitions.append({
            "from_op": a,
            "to_op": b,
            "from_wip_code": fw,
            "to_wip_code": tw,
            "effective": eff,
            "group_rule": g,
            "item_rule": i,
        })

    backbone = " > ".join(norm_op(n) for n in _merge_sequences([[op.operation_name or f"Op {op.seq}" for op in it.operations] for it in group_items if it.operations]))
    item_rows = []
    for it in sorted(group_items, key=lambda x: x.code):
        if not it.operations:
            continue
        own = " > ".join(norm_op(op.operation_name or f"Op {op.seq}") for op in it.operations)
        n_rules = sum(1 for (iid, _, _, _, _) in lookup.item.keys() if iid == it.id)
        item_rows.append({
            "code": it.code,
            "name": it.name,
            "operations": [op.operation_name or f"Op {op.seq}" for op in it.operations],
            "semi_finished_codes": [op.semi_finished_code for op in it.operations],
            "differs": own != backbone,
            "item_rules": n_rules,
        })

    return {
        "product_group": product_group,
        "item_code": item.code if item else None,
        "item_name": item.name if item else None,
        "nodes": nodes,
        "transitions": transitions,
        "items": item_rows,
    }


def upsert_rule(
    db: Session,
    scope: str,
    product_group: str,
    item_code: str | None,
    from_op: str,
    to_op: str,
    rule: str,
    lag_cycles: float,
    wait_minutes: float,
    note: str,
    from_wip_code: str = "",
    to_wip_code: str = "",
) -> OpTransitionRule:
    item = None
    if scope == "item":
        item = db.query(Item).filter(Item.code == (item_code or "")).first()
        if not item:
            raise ValueError("Stok kodu bulunamadi")
        product_group = item.product_group or product_group
    if rule not in ("finish", "cycles"):
        raise ValueError("Kural 'finish' veya 'cycles' olmali")
    f, t = norm_op(from_op), norm_op(to_op)
    fw, tw = norm_wip(from_wip_code), norm_wip(to_wip_code)
    if not f or not t or f == t:
        raise ValueError("Onceki ve sonraki operasyon adlari gecerli ve farkli olmali")
    q = db.query(OpTransitionRule).filter(
        OpTransitionRule.scope == scope,
        OpTransitionRule.from_op_norm == f,
        OpTransitionRule.to_op_norm == t,
        OpTransitionRule.from_wip_norm == fw,
        OpTransitionRule.to_wip_norm == tw,
    )
    q = q.filter(OpTransitionRule.item_id == item.id) if item else q.filter(OpTransitionRule.product_group == product_group, OpTransitionRule.item_id.is_(None))
    r = q.first()
    if not r:
        r = OpTransitionRule(
            scope=scope,
            product_group=product_group,
            item_id=item.id if item else None,
            from_op=from_op.strip(),
            to_op=to_op.strip(),
            from_op_norm=f,
            to_op_norm=t,
            from_wip_code=from_wip_code.strip(),
            to_wip_code=to_wip_code.strip(),
            from_wip_norm=fw,
            to_wip_norm=tw,
        )
        db.add(r)
    r.rule = rule
    r.lag_cycles = float(lag_cycles or 0.0)
    r.wait_minutes = float(wait_minutes or 0.0)
    r.note = note or ""
    r.from_wip_code = from_wip_code.strip()
    r.to_wip_code = to_wip_code.strip()
    r.from_wip_norm = fw
    r.to_wip_norm = tw
    db.flush()
    return r


def generate_from_groups(db: Session, *, replace_group: bool = False) -> dict:
    """Ana/alt grup dolu stoklar icin ardışık operasyon gecislerinde finish kurali uretir."""
    items = (
        db.query(Item)
        .options(joinedload(Item.operations))
        .filter(Item.main_group != "", Item.sub_group != "")
        .all()
    )
    created = updated = skipped = 0
    touched_groups: set[str] = set()
    for it in items:
        if len(it.operations) < 2:
            skipped += 1
            continue
        gkey = _group_key(it)
        touched_groups.add(gkey)
        ops = sorted(it.operations, key=lambda o: o.seq)
        for prev, nxt in zip(ops, ops[1:]):
            try:
                existing = db.query(OpTransitionRule).filter(
                    OpTransitionRule.scope == "group",
                    OpTransitionRule.product_group == gkey,
                    OpTransitionRule.from_op_norm == norm_op(prev.operation_name),
                    OpTransitionRule.to_op_norm == norm_op(nxt.operation_name),
                    OpTransitionRule.item_id.is_(None),
                ).first()
                if existing:
                    if replace_group:
                        existing.rule = "finish"
                        updated += 1
                    else:
                        skipped += 1
                    continue
                upsert_rule(
                    db,
                    scope="group",
                    product_group=gkey,
                    item_code=None,
                    from_op=prev.operation_name,
                    to_op=nxt.operation_name,
                    rule="finish",
                    lag_cycles=0,
                    wait_minutes=0,
                    note="Otomatik: ana/alt grup",
                    from_wip_code=prev.semi_finished_code or "",
                    to_wip_code=nxt.semi_finished_code or "",
                )
                created += 1
            except ValueError:
                skipped += 1
    db.flush()
    return {"created": created, "updated": updated, "skipped": skipped, "groups": sorted(touched_groups)}
