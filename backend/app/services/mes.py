"""Customer-independent MES ingestion and material movement ledger.

Read XLSX values directly: the MES exporter emits invalid Excel fill styles.
No style repair, macro execution, or changes to the source workbook are needed.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta
from io import BytesIO
import hashlib
import json
import math
import posixpath
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile, BadZipFile

from sqlalchemy import text
from sqlalchemy.orm import joinedload, selectinload
from app.models import Item, Machine, RoutingOperation, PlanLine, StockReceipt, ImportLog, norm_wip, norm_op
from app.models.mes import MesDetail, MesPlanBaseline
from app.services.bom_tree import is_fg, is_wip_output, is_wip_asm_link

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REQUIRED = {"uretim detay id": "detail_id", "tarih": "prod_date",
            "malzeme kodu": "material_code", "net uretilen miktar": "quantity",
            "is merkezi kodu": "machine_code"}


def monday(day):
    return day - timedelta(days=day.weekday())


def number(value):
    s = str(value).strip()
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    n = float(s)
    if not math.isfinite(n) or n < 0:
        raise ValueError("Miktar sonlu ve sıfır veya pozitif olmalı")
    return n


def read_mes(content):
    if len(content) > 20 * 1024 * 1024:
        raise ValueError("Dosya en fazla 20 MB olabilir")
    try:
        with ZipFile(BytesIO(content)) as z:
            if sum(i.file_size for i in z.infolist()) > 100 * 1024 * 1024:
                raise ValueError("Açılmış Excel boyutu çok büyük")
            strings = []
            if "xl/sharedStrings.xml" in z.namelist():
                strings = ["".join(t.text or "" for t in s.findall(".//m:t", NS))
                           for s in ET.fromstring(z.read("xl/sharedStrings.xml"))]
            wb = ET.fromstring(z.read("xl/workbook.xml"))
            prop = wb.find("m:workbookPr", NS)
            epoch = date(1904, 1, 1) if prop is not None and prop.get("date1904") in ("1", "true") else date(1899, 12, 30)
            sheet = wb.find("m:sheets/m:sheet", NS)
            if sheet is None:
                raise ValueError("Excel sayfası bulunamadı")
            rel_id = sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
            target = next(r.get("Target") for r in rels if r.get("Id") == rel_id)
            path = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
            xml = ET.fromstring(z.read(path))
            records, seen, header = [], set(), None
            for row in xml.findall("m:sheetData/m:row", NS):
                cells = {}
                for c in row:
                    col = re.sub(r"\d", "", c.get("r", ""))
                    v = c.find("m:v", NS)
                    value = v.text if v is not None else "".join(t.text or "" for t in c.findall(".//m:t", NS))
                    if c.get("t") == "s":
                        value = strings[int(value)]
                    cells[col] = value or ""
                if not any(cells.values()):
                    continue
                if header is None:
                    header = {c: REQUIRED[norm_op(v)] for c, v in cells.items() if norm_op(v) in REQUIRED}
                    if set(header.values()) != set(REQUIRED.values()):
                        raise ValueError("Gerekli MES sütunları: Üretim Detay Id, Tarih, Malzeme Kodu, Net Üretilen Miktar, İş Merkezi Kodu")
                    continue
                r = {key: cells.get(col, "").strip() for col, key in header.items()}
                try:
                    if not r["detail_id"] or len(r["detail_id"]) > 96 or r["detail_id"] in seen:
                        raise ValueError("Üretim Detay ID boş, uzun veya dosyada tekrarlı")
                    seen.add(r["detail_id"])
                    r["quantity"] = number(r["quantity"])
                    raw_date = r["prod_date"]
                    if re.fullmatch(r"\d+(\.\d+)?", raw_date):
                        r["prod_date"] = epoch + timedelta(days=int(float(raw_date)))
                    else:
                        parsed = None
                        for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
                            try:
                                parsed = datetime.strptime(raw_date, fmt).date()
                                break
                            except ValueError:
                                pass
                        if parsed is None:
                            raise ValueError("Tarih sütunu geçerli bir gün içermeli")
                        r["prod_date"] = parsed
                    for k in ("material_code", "machine_code"):
                        r[k] = norm_wip(r[k])
                        if not r[k] or len(r[k]) > 64:
                            raise ValueError(f"{k} boş veya çok uzun")
                except (ValueError, OverflowError) as e:
                    raise ValueError(f"Excel satırı {row.get('r')}: {e}") from e
                records.append(r)
                if len(records) > 50000:
                    raise ValueError("En fazla 50.000 MES kaydı yüklenebilir")
            if not records:
                raise ValueError("Excel veri satırı içermiyor")
            return records
    except (BadZipFile, KeyError, ET.ParseError, StopIteration, IndexError) as e:
        raise ValueError("Geçerli MES Excel dosyası okunamadı") from e


def operation_key(op):
    return f"{norm_wip(op.semi_finished_code or op.item.code)}|{op.work_center_id}|{norm_op(op.operation_name)}"


def standard_unit_hours(op):
    """Linear earned labor content, without setup or rounding each unit to a cycle."""
    if op.time_basis == "machine_seconds_per_cycle":
        return float(op.cycle_time_sec or 0) * max(op.crew_size or 1, 1) / max(op.units_per_cycle or 1, 1) / 3600
    return float(op.cycle_time_sec or 0) / 3600


def inputs_for(item, op, finished=False):
    """Consume the preceding branch output, not every intermediate BOM row."""
    lines = item.bom_lines
    code = norm_wip(op.semi_finished_code)
    current = [b for b in lines if norm_wip(b.component_code) == code and b.recipe_seq > 0]
    branch = norm_wip(current[0].source_wip) if current else code
    seq = current[0].recipe_seq if current else math.inf
    previous = [b for b in lines if norm_wip(b.source_wip) == branch
                and 0 < b.recipe_seq < seq and is_wip_output(norm_wip(b.component_code))]
    inputs = {}
    if previous:
        last = max(previous, key=lambda b: b.recipe_seq)
        denominator = float(current[0].quantity) if current else 1.0
        if denominator <= 0:
            raise ValueError("BOM operasyon katsayısı sıfır veya negatif")
        inputs[norm_wip(last.component_code)] = float(last.quantity) / denominator
    elif not is_fg(item.code) and not current:
        # Standalone WIP recipes store raw-material BOM rows and sequence their
        # intermediate outputs in the routing rather than in BomLine.
        preceding = [p for p in item.operations if p.seq < op.seq and p.semi_finished_code]
        if preceding:
            inputs[norm_wip(max(preceding, key=lambda p: p.seq).semi_finished_code)] = 1.0
    if finished:
        for b in lines:
            if is_wip_asm_link(b.component_code, b.source_wip, b.recipe_seq):
                inputs[norm_wip(b.component_code)] = float(b.quantity)
    if any(not math.isfinite(q) or q < 0 for q in inputs.values()):
        raise ValueError("Geçersiz BOM tüketim katsayısı")
    return inputs


class Mapper:
    def __init__(self, db):
        self.items = {norm_wip(i.code): i for i in db.query(Item).options(
            selectinload(Item.bom_lines), selectinload(Item.operations).joinedload(RoutingOperation.work_center)).all()}
        self.machines = {norm_wip(m.code): m for m in db.query(Machine).all()}
        self.index = defaultdict(list)
        self.finished_candidates = defaultdict(set)
        for item in self.items.values():
            if is_fg(item.code):
                for b in item.bom_lines:
                    self.finished_candidates[norm_wip(b.component_code)].add(item.code)
            for op in item.operations:
                if op.semi_finished_code:
                    self.index[norm_wip(op.semi_finished_code)].append(op)
        for item in self.items.values():
            parents = self.finished_candidates.get(norm_wip(item.code), set())
            for op in item.operations:
                self.finished_candidates[norm_wip(op.semi_finished_code)].update(parents)

    def map(self, r):
        code = r["material_code"]
        machine = self.machines.get(r["machine_code"])
        base = {"status": "unresolved", "reason": "", "inputs": {}, "candidates": [], "standard_unit_hours": 0.0}
        if machine is None:
            return {**base, "reason": "Makine kodu tanımlı değil"}
        base["work_center_id"] = machine.work_center_id
        item = self.items.get(code)
        finished = is_fg(code)
        if finished:
            if not item or not item.bom_lines or not item.operations:
                return {**base, "reason": "Bitmiş ürün BOM veya rota tanımı eksik"}
            ops = [max(item.operations, key=lambda o: o.seq)]
        else:
            ops = self.index.get(code, [])
        ops = [o for o in ops if o.work_center_id == machine.work_center_id and o.item.bom_lines]
        fg_ops = [o for o in ops if is_fg(o.item.code)]
        if fg_ops:
            # FG BOM is authoritative; generated standalone WIP route copies
            # may omit the flattened branch links and must not override it.
            ops = fg_ops
        if not ops:
            return {**base, "reason": "Malzeme ve makine iş merkezi için BOM/rota eşleşmesi yok"}
        variants = []
        for op in ops:
            try:
                inputs = inputs_for(op.item, op, finished)
            except ValueError as e:
                return {**base, "reason": str(e)}
            unit = standard_unit_hours(op)
            variants.append((operation_key(op), round(unit, 10), inputs))
        if any(v != variants[0] for v in variants[1:]):
            return {**base, "reason": "Ortak yarımamül için farklı operasyon, süre veya tüketim tanımları var",
                    "candidates": sorted({o.item.code for o in ops})}
        key, unit, inputs = variants[0]
        if not math.isfinite(unit) or unit <= 0:
            return {**base, "reason": "Standart birim işçilik süresi tanımlı değil"}
        return {"status": "mapped", "reason": "", "kind": "finished" if finished else "wip",
                "key": key, "work_center_id": machine.work_center_id, "work_center_code": ops[0].work_center.code,
                "operation_name": ops[0].operation_name, "standard_unit_hours": unit,
                "inputs": inputs, "candidates": sorted({o.item.code for o in ops if is_fg(o.item.code)} | self.finished_candidates.get(code, set())),
                "item_id": item.id if finished else None}


def detail_dict(d):
    return {"detail_id": d.detail_id, "prod_date": d.prod_date, "material_code": d.material_code,
            "machine_code": d.machine_code, "quantity": d.quantity, "mapping": d.mapping}


def balances(records, as_of=None):
    out = defaultdict(float)
    for r in records:
        if as_of and r["prod_date"] > as_of or r["mapping"]["status"] != "mapped":
            continue
        if r["mapping"]["kind"] == "wip":
            out[r["material_code"]] += r["quantity"]
        for code, ratio in r["mapping"]["inputs"].items():
            out[code] -= r["quantity"] * ratio
    return dict(out)


def preview(db, content):
    records = read_mes(content)
    mapper = Mapper(db)
    existing = {d.detail_id: detail_dict(d) for d in db.query(MesDetail).all()}
    original = sorted(existing.values(), key=lambda r: r["detail_id"])
    rows = []
    counts = {"new": 0, "updated": 0, "unchanged": 0, "unresolved": 0}
    for r in records:
        r["mapping"] = mapper.map(r)
        old = existing.get(r["detail_id"])
        action = "new" if old is None else "unchanged" if old == r else "updated"
        counts[action] += 1
        counts["unresolved"] += r["mapping"]["status"] != "mapped"
        rows.append({**r, "action": action})
        existing[r["detail_id"]] = r
    pool = balances(existing.values())
    shortages = [{"material_code": c, "missing_qty": round(-q, 4)} for c, q in sorted(pool.items()) if q < -1e-6]
    payload = {"rows": rows, "counts": counts, "shortages": shortages,
               "net_quantity": sum(r["quantity"] for r in rows),
               "standard_hours": sum(r["quantity"] * r["mapping"]["standard_unit_hours"] for r in rows)}
    # Includes persisted source state: concurrent imports invalidate reviewed previews.
    token_data = [payload, original]
    payload["token"] = hashlib.sha256(json.dumps(token_data, sort_keys=True, default=str).encode()).hexdigest()
    return payload


def live_plan(db, week):
    rows = db.query(PlanLine).options(joinedload(PlanLine.operation).joinedload(RoutingOperation.item),
                                     joinedload(PlanLine.order)).filter(
        PlanLine.week_start == week, PlanLine.mode.in_(["auto", "manual"])).all()
    return [{"key": operation_key(p.operation), "item_code": p.order.item.code,
             "material_code": norm_wip(p.operation.semi_finished_code or p.operation.item.code),
             "operation_name": p.operation.operation_name, "work_center_id": p.work_center_id,
             "quantity": float(p.planned_qty), "hours": float(p.planned_hours),
             "unit_hours": standard_unit_hours(p.operation)} for p in rows]


def apply_import(db, content, token, username, filename="mes.xlsx"):
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(76203419)"))
    result = preview(db, content)
    if result["token"] != token:
        raise ValueError("Veri veya BOM değişti. Önizlemeyi yenileyip tekrar onaylayın.")
    weeks = {monday(r["prod_date"]) + timedelta(weeks=i) for r in result["rows"] for i in range(13)}
    saved_weeks = {b.week_start for b in db.query(MesPlanBaseline).all()}
    for week in sorted(weeks - saved_weeks):
        lines = live_plan(db, week)
        if lines:
            db.add(MesPlanBaseline(week_start=week, lines=lines))
    db.flush()
    reduced_items = set()
    for r in result["rows"]:
        if r["action"] == "unchanged":
            continue
        d = db.get(MesDetail, r["detail_id"])
        if d is None:
            d = MesDetail(detail_id=r["detail_id"])
            db.add(d)
        for field in ("prod_date", "material_code", "machine_code", "quantity", "mapping"):
            setattr(d, field, r[field])
        d.updated_by = username
        receipt = db.get(StockReceipt, d.receipt_id) if d.receipt_id else None
        if receipt and (r["mapping"].get("kind") != "finished" or r["mapping"].get("item_id") != receipt.item_id or r["quantity"] < receipt.quantity):
            reduced_items.add(receipt.item_id)
        if r["mapping"].get("kind") == "finished" and r["mapping"]["status"] == "mapped":
            if receipt is None:
                receipt = StockReceipt(source="mes", lot="", created_by=username)
                db.add(receipt)
            receipt.item_id = r["mapping"]["item_id"]
            receipt.receipt_date = r["prod_date"]
            receipt.quantity = r["quantity"]
            receipt.note = f"MES detay {r['detail_id']}"
            db.flush()
            d.receipt_id = receipt.id
        elif receipt is not None:
            # Keep the audit identity while reversing an amended MES contribution.
            receipt.quantity = 0
    db.flush()
    from app.services.stock import item_free
    if any(item_free(db, item_id) < -1e-6 for item_id in reduced_items):
        raise ValueError("MES düzeltmesi rezerve veya sevk edilmiş stoğu azaltıyor. Önce stok ve rezervasyonları uzlaştırın.")
    db.add(ImportLog(kind="mes_production", filename=filename[:256], username=username,
                     inserted=result["counts"]["new"], updated=result["counts"]["updated"],
                     errors=f"{result['counts']['unresolved']} eşleşmeyen MES kaydı" if result["counts"]["unresolved"] else ""))
    return result
