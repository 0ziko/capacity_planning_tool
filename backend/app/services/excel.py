"""Excel import (sablon + yukleme) ve export (yedek, raporlar)."""

from __future__ import annotations

import io
import re
import unicodedata
from datetime import date, datetime, time
from typing import Any, Callable

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy.orm import Session

from app.models import (
    BomLine,
    Downtime,
    Employee,
    ImportLog,
    Item,
    Order,
    PlanLine,
    ProductionActual,
    RoutingOperation,
    User,
    WorkCenter,
    WorkCenterShift,
)
from app.schemas import ImportResult

TR_MAP = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")


def norm(s: Any) -> str:
    s = str(s or "").translate(TR_MAP)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


# ---- Sablon tanimlari: kind -> (baslik listesi, ornek satir, zorunlu alanlar) ----
# key -> (Excel basligi, kabul edilen alternatif basliklar)
TEMPLATES: dict[str, dict] = {
    "workcenters": {
        "title": "İş Merkezleri",
        "columns": [
            ("code", "İş Merkezi Kodu", ["kod", "ismerkezi"]),
            ("name", "İş Merkezi Adı", ["ad", "adi"]),
            ("description", "Açıklama", []),
            ("is_active", "Aktif (E/H)", ["aktif"]),
            ("is_planned", "Planlanıyor (E/H)", ["planlaniyor", "pilot"]),
            ("capacity_unit_hours", "Birim Saat", ["kapasitebirimi", "birim"]),
            ("default_efficient_hours", "Kişi Başı Verimli Saat", ["verimlisaat", "verimlisure"]),
        ],
        "example": ["TZG-A", "A Tezgahı", "", "E", "E", 10, 4],
        "required": ["code", "name"],
    },
    "shifts": {
        "title": "Vardiyalar",
        "columns": [
            ("wc_code", "İş Merkezi Kodu", ["ismerkezi", "ismerkezikodu"]),
            ("name", "Vardiya", ["vardiyaadi", "ad"]),
            ("weekdays", "Günler (Pzt=0..Paz=6)", ["gunler"]),
            ("start_time", "Başlangıç", ["baslangicsaati", "baslama"]),
            ("end_time", "Bitiş", ["bitissaati"]),
            ("headcount", "Kişi Sayısı", ["kisi", "kisisayisi"]),
            ("efficient_hours_per_person", "Kişi Başı Verimli Saat", ["verimlisaat", "verimlisure"]),
        ],
        "example": ["TZG-A", "Gündüz", "0,1,2,3,4", "08:00", "18:00", 10, 4],
        "required": ["wc_code"],
    },
    "employees": {
        "title": "Personel",
        "columns": [
            ("code", "Sicil No", ["sicil", "kod", "personelkodu"]),
            ("name", "Ad Soyad", ["ad", "adsoyad", "personel"]),
            ("wc_code", "İş Merkezi Kodu", ["ismerkezi", "ismerkezikodu"]),
            ("is_active", "Aktif (E/H)", ["aktif"]),
        ],
        "example": ["1001", "Ahmet Yılmaz", "TZG-A", "E"],
        "required": ["code", "name"],
    },
    "items": {
        "title": "Stok Kodları",
        "columns": [
            ("code", "Stok Kodu", ["stokkodu", "kod", "malzeme"]),
            ("name", "Stok Adı", ["stokadi", "ad", "aciklama"]),
            ("product_group", "Ürün Grubu", ["grup", "urungrubu"]),
            ("unit", "Birim", []),
        ],
        "example": ["MAM-0001", "Endüstriyel Ocak 4 Gözlü", "OCAK", "AD"],
        "required": ["code"],
    },
    "bom": {
        "title": "BOM (Hammadde)",
        "columns": [
            ("item_code", "Stok Kodu", ["stokkodu", "mamul", "anastok"]),
            ("component_code", "Bileşen Kodu", ["hammaddekodu", "bilesen", "hammadde"]),
            ("component_name", "Bileşen Adı", ["hammaddeadi", "bilesenadi"]),
            ("quantity", "Miktar", ["kullanimmiktari"]),
            ("unit", "Birim", []),
        ],
        "example": ["MAM-0001", "HM-SAC-2MM", "Paslanmaz Sac 2mm", 3.5, "KG"],
        "required": ["item_code", "component_code"],
    },
    "routing": {
        "title": "Rota / Çevrim Süreleri",
        "columns": [
            ("item_code", "Stok Kodu", ["stokkodu", "mamul"]),
            ("seq", "Sıra", ["operasyonsira", "sira", "asama"]),
            ("operation_name", "Operasyon", ["operasyonadi", "islem"]),
            ("wc_code", "İş Merkezi Kodu", ["ismerkezi", "tezgah", "tezgahkodu"]),
            ("cycle_time_sec", "Çevrim Süresi (sn)", ["cycletime", "cevrimsuresi", "cevrimsuresisn", "cevrim"]),
            ("setup_time_min", "Setup (dk)", ["setup", "hazirlik", "setupsuresi"]),
        ],
        "example": ["MAM-0001", 10, "Kesim", "TZG-A", 50, 15],
        "required": ["item_code", "seq", "wc_code", "cycle_time_sec"],
    },
    "orders": {
        "title": "Siparişler",
        "columns": [
            ("order_no", "Sipariş No", ["siparis", "siparisno", "belgeno"]),
            ("customer", "Müşteri", ["musteriadi", "cari"]),
            ("due_date", "Termin", ["termintarihi", "teslimtarihi", "tarih"]),
            ("item_code", "Stok Kodu", ["stokkodu", "malzeme"]),
            ("quantity", "Miktar", ["adet"]),
        ],
        "example": ["SIP-2026-001", "ABC Otel", "2026-10-15", "MAM-0001", 40],
        "required": ["order_no", "due_date", "item_code", "quantity"],
    },
    "production": {
        "title": "Günlük Üretim",
        "columns": [
            ("prod_date", "Tarih", ["uretimtarihi", "gun"]),
            ("wc_code", "İş Merkezi Kodu", ["ismerkezi", "tezgah"]),
            ("item_code", "Stok Kodu", ["stokkodu", "malzeme"]),
            ("operation_seq", "Operasyon Sıra", ["sira", "operasyon"]),
            ("order_no", "Sipariş No", ["siparis", "siparisno"]),
            ("quantity", "Miktar", ["adet", "uretilen", "uretimmiktari"]),
            ("reported_hours", "Fiili Süre (saat)", ["fiilisure", "calismasuresi", "sure"]),
        ],
        "example": ["2026-09-06", "TZG-A", "MAM-0001", 10, "SIP-2026-001", 120, ""],
        "required": ["prod_date", "wc_code", "item_code", "quantity"],
    },
    "downtime": {
        "title": "Günlük Duruşlar",
        "columns": [
            ("dt_date", "Tarih", ["durustarihi", "gun"]),
            ("wc_code", "İş Merkezi Kodu", ["ismerkezi", "tezgah"]),
            ("reason_code", "Sebep Kodu", ["sebepkodu", "kod"]),
            ("reason_desc", "Sebep", ["sebepaciklama", "aciklama", "durussebebi"]),
            ("minutes", "Süre (dk)", ["sure", "dakika", "durussuresi"]),
        ],
        "example": ["2026-09-06", "TZG-A", "MLZ", "Malzeme bekleme", 45],
        "required": ["dt_date", "wc_code", "minutes"],
    },
}


def sheet_title(title: str) -> str:
    """Excel sayfa adi kurallari: max 31 karakter, [] : * ? / \\ yasak."""
    cleaned = re.sub(r"[\[\]:*?/\\]", "-", title).strip()
    return (cleaned or "Sayfa")[:31]


def _autosize(ws) -> None:
    for i, col in enumerate(ws.columns, start=1):
        width = max((len(str(c.value)) if c.value is not None else 0) for c in col)
        ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 10), 60)


def _style_header(ws) -> None:
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F4E78")


def build_template(kind: str) -> bytes:
    t = TEMPLATES[kind]
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title(t["title"])
    ws.append([c[1] for c in t["columns"]])
    ws.append(t["example"])
    _style_header(ws)
    _autosize(ws)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---- Parse ----

def read_rows(content: bytes, kind: str) -> tuple[list[dict], list[str]]:
    """Excel -> [ {key: value} ], hatalar. Basliklar Turkce/alias uyumlu eslenir."""
    t = TEMPLATES[kind]
    wb = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    try:
        header = next(rows)
    except StopIteration:
        return [], ["Dosya bos"]
    alias_map: dict[str, str] = {}
    for key, title, aliases in t["columns"]:
        alias_map[norm(title)] = key
        alias_map[norm(key)] = key
        for a in aliases:
            alias_map[norm(a)] = key
    col_keys: list[str | None] = [alias_map.get(norm(h)) for h in header]
    missing = [title for key, title, _ in t["columns"] if key in t["required"] and key not in col_keys]
    if missing:
        return [], [f"Zorunlu sutun(lar) bulunamadi: {', '.join(missing)}"]
    out = []
    for r_idx, row in enumerate(rows, start=2):
        if row is None or all(v is None or str(v).strip() == "" for v in row):
            continue
        rec = {"_row": r_idx}
        for key, val in zip(col_keys, row):
            if key:
                rec[key] = val
        out.append(rec)
    return out, []


def _bool(v: Any, default: bool = True) -> bool:
    if v is None or str(v).strip() == "":
        return default
    return norm(v) in {"e", "evet", "1", "true", "x", "yes", "y"}


def _float(v: Any, default: float | None = None) -> float | None:
    if v is None or str(v).strip() == "":
        return default
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    return float(s)


def _int(v: Any, default: int | None = None) -> int | None:
    f = _float(v, None)
    return default if f is None else int(round(f))


def _str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _date(v: Any) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Tarih anlasilamadi: {v!r}")


def _time(v: Any, default: time) -> time:
    if v is None or str(v).strip() == "":
        return default
    if isinstance(v, datetime):
        return v.time()
    if isinstance(v, time):
        return v
    if isinstance(v, (int, float)):  # Excel gun kesri
        total_min = int(round(float(v) * 24 * 60)) % (24 * 60)
        return time(total_min // 60, total_min % 60)
    s = str(v).strip()
    for fmt in ("%H:%M", "%H:%M:%S", "%H.%M"):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            pass
    raise ValueError(f"Saat anlasilamadi: {v!r}")


# ---- Importers ----

def _wc_lookup(db: Session) -> dict[str, WorkCenter]:
    return {w.code.upper(): w for w in db.query(WorkCenter).all()}


def _item_lookup(db: Session) -> dict[str, Item]:
    return {i.code.upper(): i for i in db.query(Item).all()}


def _get_or_create_item(db: Session, items: dict[str, Item], code: str) -> Item:
    key = code.upper()
    if key not in items:
        it = Item(code=code, name="")
        db.add(it)
        db.flush()
        items[key] = it
    return items[key]


def import_workcenters(db: Session, rows: list[dict]) -> tuple[int, int, list[str]]:
    ins = upd = 0
    errs = []
    existing = _wc_lookup(db)
    for r in rows:
        try:
            code = _str(r.get("code"))
            if not code:
                raise ValueError("Kod bos")
            wc = existing.get(code.upper())
            if not wc:
                wc = WorkCenter(code=code, name=_str(r.get("name")) or code)
                db.add(wc)
                existing[code.upper()] = wc
                ins += 1
            else:
                upd += 1
            if _str(r.get("name")):
                wc.name = _str(r.get("name"))
            wc.description = _str(r.get("description"))
            wc.is_active = _bool(r.get("is_active"), True)
            wc.is_planned = _bool(r.get("is_planned"), wc.is_planned or False)
            wc.capacity_unit_hours = _float(r.get("capacity_unit_hours"), wc.capacity_unit_hours or 10.0)
            wc.default_efficient_hours = _float(r.get("default_efficient_hours"), wc.default_efficient_hours or 4.0)
        except Exception as e:  # noqa: BLE001
            errs.append(f"Satir {r['_row']}: {e}")
    return ins, upd, errs


def import_shifts(db: Session, rows: list[dict]) -> tuple[int, int, list[str]]:
    """Ayni is merkezi + vardiya adi varsa gunceller, yoksa ekler."""
    ins = upd = 0
    errs = []
    wcs = _wc_lookup(db)
    for r in rows:
        try:
            wc = wcs.get(_str(r.get("wc_code")).upper())
            if not wc:
                raise ValueError(f"Is merkezi bulunamadi: {r.get('wc_code')}")
            name = _str(r.get("name")) or "Gunduz"
            sh = next((s for s in wc.shifts if s.name.lower() == name.lower()), None)
            if not sh:
                sh = WorkCenterShift(work_center=wc, name=name)
                db.add(sh)
                ins += 1
            else:
                upd += 1
            wd = _str(r.get("weekdays")).replace(";", ",").replace(" ", "")
            sh.weekdays = wd or "0,1,2,3,4"
            sh.start_time = _time(r.get("start_time"), time(8, 0))
            sh.end_time = _time(r.get("end_time"), time(18, 0))
            sh.headcount = _int(r.get("headcount"), 0) or 0
            sh.efficient_hours_per_person = _float(r.get("efficient_hours_per_person"), None)
        except Exception as e:  # noqa: BLE001
            errs.append(f"Satir {r['_row']}: {e}")
    return ins, upd, errs


def import_employees(db: Session, rows: list[dict]) -> tuple[int, int, list[str]]:
    ins = upd = 0
    errs = []
    wcs = _wc_lookup(db)
    existing = {e.code.upper(): e for e in db.query(Employee).all()}
    for r in rows:
        try:
            code = _str(r.get("code"))
            if not code:
                raise ValueError("Sicil bos")
            emp = existing.get(code.upper())
            if not emp:
                emp = Employee(code=code, name=_str(r.get("name")))
                db.add(emp)
                existing[code.upper()] = emp
                ins += 1
            else:
                upd += 1
            emp.name = _str(r.get("name")) or emp.name
            wc_code = _str(r.get("wc_code"))
            if wc_code:
                wc = wcs.get(wc_code.upper())
                if not wc:
                    raise ValueError(f"Is merkezi bulunamadi: {wc_code}")
                emp.work_center_id = wc.id
            emp.is_active = _bool(r.get("is_active"), True)
        except Exception as e:  # noqa: BLE001
            errs.append(f"Satir {r['_row']}: {e}")
    return ins, upd, errs


def import_items(db: Session, rows: list[dict]) -> tuple[int, int, list[str]]:
    ins = upd = 0
    errs = []
    items = _item_lookup(db)
    for r in rows:
        try:
            code = _str(r.get("code"))
            if not code:
                raise ValueError("Stok kodu bos")
            it = items.get(code.upper())
            if not it:
                it = Item(code=code)
                db.add(it)
                items[code.upper()] = it
                ins += 1
            else:
                upd += 1
            it.name = _str(r.get("name")) or it.name
            it.product_group = _str(r.get("product_group")) or it.product_group
            it.unit = _str(r.get("unit")) or it.unit or "AD"
        except Exception as e:  # noqa: BLE001
            errs.append(f"Satir {r['_row']}: {e}")
    return ins, upd, errs


def import_bom(db: Session, rows: list[dict]) -> tuple[int, int, list[str]]:
    ins = upd = 0
    errs = []
    items = _item_lookup(db)
    for r in rows:
        try:
            item = _get_or_create_item(db, items, _str(r.get("item_code")))
            comp = _str(r.get("component_code"))
            if not comp:
                raise ValueError("Bilesen kodu bos")
            line = next((b for b in item.bom_lines if b.component_code.upper() == comp.upper()), None)
            if not line:
                line = BomLine(item=item, component_code=comp)
                db.add(line)
                ins += 1
            else:
                upd += 1
            line.component_name = _str(r.get("component_name")) or line.component_name
            line.quantity = _float(r.get("quantity"), 1.0)
            line.unit = _str(r.get("unit")) or line.unit or "AD"
        except Exception as e:  # noqa: BLE001
            errs.append(f"Satir {r['_row']}: {e}")
    return ins, upd, errs


def import_routing(db: Session, rows: list[dict]) -> tuple[int, int, list[str]]:
    ins = upd = 0
    errs = []
    items = _item_lookup(db)
    wcs = _wc_lookup(db)
    for r in rows:
        try:
            item = _get_or_create_item(db, items, _str(r.get("item_code")))
            seq = _int(r.get("seq"))
            if seq is None:
                raise ValueError("Sira bos")
            wc = wcs.get(_str(r.get("wc_code")).upper())
            if not wc:
                raise ValueError(f"Is merkezi bulunamadi: {r.get('wc_code')}")
            op = next((o for o in item.operations if o.seq == seq), None)
            if not op:
                op = RoutingOperation(item=item, seq=seq, work_center_id=wc.id)
                db.add(op)
                ins += 1
            else:
                upd += 1
            op.work_center_id = wc.id
            op.operation_name = _str(r.get("operation_name")) or op.operation_name
            op.cycle_time_sec = _float(r.get("cycle_time_sec"), 0.0) or 0.0
            op.setup_time_min = _float(r.get("setup_time_min"), 0.0) or 0.0
        except Exception as e:  # noqa: BLE001
            errs.append(f"Satir {r['_row']}: {e}")
    return ins, upd, errs


def import_orders(db: Session, rows: list[dict]) -> tuple[int, int, list[str]]:
    """Ayni siparis no + stok kodu varsa gunceller."""
    ins = upd = 0
    errs = []
    items = _item_lookup(db)
    existing: dict[tuple[str, int], Order] = {(o.order_no.upper(), o.item_id): o for o in db.query(Order).all()}
    for r in rows:
        try:
            order_no = _str(r.get("order_no"))
            if not order_no:
                raise ValueError("Siparis no bos")
            item = _get_or_create_item(db, items, _str(r.get("item_code")))
            qty = _float(r.get("quantity"))
            if qty is None:
                raise ValueError("Miktar bos")
            o = existing.get((order_no.upper(), item.id))
            if not o:
                o = Order(order_no=order_no, item_id=item.id, quantity=qty, due_date=_date(r.get("due_date")))
                db.add(o)
                existing[(order_no.upper(), item.id)] = o
                ins += 1
            else:
                upd += 1
            o.customer = _str(r.get("customer")) or o.customer
            o.due_date = _date(r.get("due_date"))
            o.quantity = qty
            o.status = "open"
        except Exception as e:  # noqa: BLE001
            errs.append(f"Satir {r['_row']}: {e}")
    return ins, upd, errs


def import_production(db: Session, rows: list[dict]) -> tuple[int, int, list[str]]:
    """Ayni gun/is merkezi/stok/operasyon/siparis satiri varsa uzerine yazar (idempotent)."""
    ins = upd = 0
    errs = []
    items = _item_lookup(db)
    wcs = _wc_lookup(db)
    for r in rows:
        try:
            d = _date(r.get("prod_date"))
            wc = wcs.get(_str(r.get("wc_code")).upper())
            if not wc:
                raise ValueError(f"Is merkezi bulunamadi: {r.get('wc_code')}")
            item = items.get(_str(r.get("item_code")).upper())
            if not item:
                raise ValueError(f"Stok kodu bulunamadi: {r.get('item_code')}")
            seq = _int(r.get("operation_seq"), None)
            order_no = _str(r.get("order_no"))
            qty = _float(r.get("quantity"))
            if qty is None:
                raise ValueError("Miktar bos")
            op = None
            if seq is not None:
                op = next((o for o in item.operations if o.seq == seq), None)
            if op is None:
                op = next((o for o in item.operations if o.work_center_id == wc.id), None)
            earned = op.hours_for(qty) - (op.setup_time_min / 60.0) if op else 0.0  # gunluk uretimde setup sayilmaz
            pa = (
                db.query(ProductionActual)
                .filter(
                    ProductionActual.prod_date == d,
                    ProductionActual.work_center_id == wc.id,
                    ProductionActual.item_id == item.id,
                    ProductionActual.operation_seq == seq,
                    ProductionActual.order_no == order_no,
                )
                .first()
            )
            if not pa:
                pa = ProductionActual(prod_date=d, work_center_id=wc.id, item_id=item.id, operation_seq=seq, order_no=order_no, quantity=qty)
                db.add(pa)
                ins += 1
            else:
                pa.quantity = qty
                upd += 1
            pa.earned_hours = round(max(earned, 0.0), 4)
            pa.reported_hours = _float(r.get("reported_hours"), None)
        except Exception as e:  # noqa: BLE001
            errs.append(f"Satir {r['_row']}: {e}")
    return ins, upd, errs


def import_downtime(db: Session, rows: list[dict]) -> tuple[int, int, list[str]]:
    """Ayni gun icin is merkezinin duruslari yeniden yuklenirse eski satirlar silinir."""
    ins = 0
    errs = []
    wcs = _wc_lookup(db)
    cleared: set[tuple[int, date]] = set()
    for r in rows:
        try:
            d = _date(r.get("dt_date"))
            wc = wcs.get(_str(r.get("wc_code")).upper())
            if not wc:
                raise ValueError(f"Is merkezi bulunamadi: {r.get('wc_code')}")
            minutes = _float(r.get("minutes"))
            if minutes is None:
                raise ValueError("Sure bos")
            if (wc.id, d) not in cleared:
                db.query(Downtime).filter(Downtime.work_center_id == wc.id, Downtime.dt_date == d).delete(synchronize_session=False)
                cleared.add((wc.id, d))
            db.add(Downtime(dt_date=d, work_center_id=wc.id, reason_code=_str(r.get("reason_code")), reason_desc=_str(r.get("reason_desc")), minutes=minutes))
            ins += 1
        except Exception as e:  # noqa: BLE001
            errs.append(f"Satir {r['_row']}: {e}")
    return ins, 0, errs


IMPORTERS: dict[str, Callable[[Session, list[dict]], tuple[int, int, list[str]]]] = {
    "workcenters": import_workcenters,
    "shifts": import_shifts,
    "employees": import_employees,
    "items": import_items,
    "bom": import_bom,
    "routing": import_routing,
    "orders": import_orders,
    "production": import_production,
    "downtime": import_downtime,
}


def run_import(db: Session, kind: str, content: bytes, filename: str, username: str) -> ImportResult:
    if kind not in IMPORTERS:
        raise ValueError(f"Bilinmeyen import turu: {kind}")
    rows, errs = read_rows(content, kind)
    ins = upd = 0
    if not errs:
        ins, upd, errs = IMPORTERS[kind](db, rows)
    db.add(ImportLog(kind=kind, filename=filename, username=username, inserted=ins, updated=upd, errors="\n".join(errs)[:10000]))
    db.commit()
    return ImportResult(kind=kind, inserted=ins, updated=upd, errors=errs)


# ---- Export ----

def _ws_from_rows(wb: Workbook, title: str, header: list[str], rows: list[list[Any]]) -> None:
    ws = wb.create_sheet(sheet_title(title))
    ws.append(header)
    for r in rows:
        ws.append([v.isoformat() if isinstance(v, (date, datetime)) else (v.strftime("%H:%M") if isinstance(v, time) else v) for v in r])
    _style_header(ws)
    _autosize(ws)


def workbook_bytes(wb: Workbook) -> bytes:
    if "Sheet" in wb.sheetnames and len(wb.sheetnames) > 1:
        del wb["Sheet"]
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_backup(db: Session) -> bytes:
    """Tum tablolar tek Excel dosyasinda (her tablo bir sayfa). Sablon formatiyla uyumlu => geri yuklenebilir."""
    wb = Workbook()
    wc_code = {w.id: w.code for w in db.query(WorkCenter).all()}
    item_code = {i.id: i.code for i in db.query(Item).all()}

    _ws_from_rows(wb, "İş Merkezleri", [c[1] for c in TEMPLATES["workcenters"]["columns"]],
                  [[w.code, w.name, w.description, "E" if w.is_active else "H", "E" if w.is_planned else "H", w.capacity_unit_hours, w.default_efficient_hours] for w in db.query(WorkCenter).order_by(WorkCenter.code)])
    _ws_from_rows(wb, "Vardiyalar", [c[1] for c in TEMPLATES["shifts"]["columns"]],
                  [[wc_code.get(s.work_center_id), s.name, s.weekdays, s.start_time, s.end_time, s.headcount, s.efficient_hours_per_person] for s in db.query(WorkCenterShift).order_by(WorkCenterShift.work_center_id, WorkCenterShift.id)])
    _ws_from_rows(wb, "Personel", [c[1] for c in TEMPLATES["employees"]["columns"]],
                  [[e.code, e.name, wc_code.get(e.work_center_id), "E" if e.is_active else "H"] for e in db.query(Employee).order_by(Employee.code)])
    _ws_from_rows(wb, "Stok Kodları", [c[1] for c in TEMPLATES["items"]["columns"]],
                  [[i.code, i.name, i.product_group, i.unit] for i in db.query(Item).order_by(Item.code)])
    _ws_from_rows(wb, "BOM", [c[1] for c in TEMPLATES["bom"]["columns"]],
                  [[item_code.get(b.item_id), b.component_code, b.component_name, b.quantity, b.unit] for b in db.query(BomLine).order_by(BomLine.item_id, BomLine.id)])
    _ws_from_rows(wb, "Rota", [c[1] for c in TEMPLATES["routing"]["columns"]],
                  [[item_code.get(o.item_id), o.seq, o.operation_name, wc_code.get(o.work_center_id), o.cycle_time_sec, o.setup_time_min] for o in db.query(RoutingOperation).order_by(RoutingOperation.item_id, RoutingOperation.seq)])
    _ws_from_rows(wb, "Siparişler", [c[1] for c in TEMPLATES["orders"]["columns"]] + ["Durum"],
                  [[o.order_no, o.customer, o.due_date, item_code.get(o.item_id), o.quantity, o.status] for o in db.query(Order).order_by(Order.due_date, Order.order_no)])
    _ws_from_rows(wb, "Plan", ["Hafta", "İş Merkezi Kodu", "Sipariş No", "Stok Kodu", "Operasyon Id", "Planlanan Saat", "Planlanan Miktar", "Mod", "Oluşturan"],
                  [[p.week_start, wc_code.get(p.work_center_id), p.order.order_no, item_code.get(p.order.item_id), p.operation_id, p.planned_hours, p.planned_qty, p.mode, p.created_by] for p in db.query(PlanLine).order_by(PlanLine.week_start, PlanLine.work_center_id)])
    _ws_from_rows(wb, "Günlük Üretim", [c[1] for c in TEMPLATES["production"]["columns"]] + ["Kazanılan Saat"],
                  [[p.prod_date, wc_code.get(p.work_center_id), item_code.get(p.item_id), p.operation_seq, p.order_no, p.quantity, p.reported_hours, p.earned_hours] for p in db.query(ProductionActual).order_by(ProductionActual.prod_date)])
    _ws_from_rows(wb, "Günlük Duruşlar", [c[1] for c in TEMPLATES["downtime"]["columns"]],
                  [[d.dt_date, wc_code.get(d.work_center_id), d.reason_code, d.reason_desc, d.minutes] for d in db.query(Downtime).order_by(Downtime.dt_date)])
    _ws_from_rows(wb, "Kullanıcılar", ["Kullanıcı", "Ad Soyad", "Rol", "Aktif"],
                  [[u.username, u.full_name, u.role, "E" if u.is_active else "H"] for u in db.query(User).order_by(User.username)])
    _ws_from_rows(wb, "Import Logu", ["Tarih", "Tür", "Dosya", "Kullanıcı", "Eklenen", "Güncellenen", "Hatalar"],
                  [[l.created_at, l.kind, l.filename, l.username, l.inserted, l.updated, l.errors] for l in db.query(ImportLog).order_by(ImportLog.created_at.desc()).limit(500)])
    return workbook_bytes(wb)


def build_report(sheets: dict[str, tuple[list[str], list[list[Any]]]]) -> bytes:
    """Genel rapor: {sayfa adi: (basliklar, satirlar)}."""
    wb = Workbook()
    for title, (header, rows) in sheets.items():
        _ws_from_rows(wb, title, header, rows)
    return workbook_bytes(wb)
