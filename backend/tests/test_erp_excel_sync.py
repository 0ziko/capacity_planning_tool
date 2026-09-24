"""ERP Excel eşitleme: sipariş (FISNO/INCKEYNO/TERMIN/MIKTAR) + depo bakiyesi (BAKIYE, depo seçimi) -> ön izleme ve uygulama."""
import io
from datetime import date, timedelta

from openpyxl import Workbook

from tests.test_capacity_flow import _upload
from tests.test_flow_pull_forward import RT_HDR, WEEK, _clear, _wc


def _erp_xlsx(orders, stock):
    wb = Workbook()
    ws = wb.active; ws.title = "Sipariş Ana Veri"
    ws.append(["FISNO", "TARIH", "SIPHAFTA", "TERMIN", "CARI_ISIM", "STOK_KODU", "STOK_ADI", "MIKTAR", "TLTUTAR", "DTIP", "DOVIZTUTAR", "GRUP_KODU", "INCKEYNO", "REZERV"])
    for r in orders: ws.append(r)
    ws2 = wb.create_sheet("Depo - Ana Veri")
    ws2.append(["STOK_KODU", "SERI_NO", "STOK_ADI", "DEPOKOD", "HUCRE_KODU", "BAKIYE"])
    for r in stock: ws2.append(r)
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def test_erp_excel_preview_and_apply(client, auth):
    _wc(client, auth, "ERP-A", 2)
    _upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu"], [["6001001", "Evye", "ERP"], ["6001002", "Kuvet", "ERP"], ["6001003", "Rotasız", "ERP"]])
    _upload(client, auth, "routing", RT_HDR, [["6001001", 10, "Op", "ERP-A", 3600, "6001001-10"], ["6001002", 10, "Op", "ERP-A", 3600, "6001002-10"]])
    _clear(client, auth)
    # programda önceden var olan bir açık sipariş (dosyada yok -> kapanacak) ve stok girişi
    client.post("/api/orders", headers=auth, json={"order_no": "OLD-1", "due_date": (WEEK + timedelta(weeks=2)).isoformat(), "item_code": "6001001", "quantity": 5})
    _upload(client, auth, "stock_receipts", ["Tarih", "Stok Kodu", "Miktar"], [[WEEK.isoformat(), "6001001", 10], [WEEK.isoformat(), "6001002", 3]])
    due = WEEK + timedelta(weeks=3)
    content = _erp_xlsx(
        [["ERS1", WEEK, 37, due, "MUSTERI A", 6001001, "Evye", 100, 50000, "TL", 0, "YURTICI", 111, 30],
         ["ERS1", WEEK, 37, due, "MUSTERI A", 6001002, "Kuvet", 40, 0, "EUR", 800, "YURTDISI", 112, 0],
         ["ERS2", WEEK, 37, due, "MUSTERI B", 6001003, "Rotasız", 7, 0, "TL", 0, "YURTICI", 113, 0],
         ["ERS3", WEEK, 37, due, "MUSTERI B", 6009999, "Yok", 7, 0, "TL", 0, "YURTICI", 114, 0],
         ["ERS4", WEEK, 37, due, "MUSTERI B", 6001001, "Evye", 0, 0, "TL", 0, "YURTICI", 115, 0]],
        [[6001001, None, "Evye", 509, "", 24], [6001001, None, "Evye", 69, "D-1", 6], [6001002, None, "Kuvet", 509, "", 1], [6008888, None, "Yok", 509, "", 9]],
    )
    files = {"file": ("Sipariş ve Depo Miktarları.xlsx", content, "application/octet-stream")}
    pv = client.post("/api/imports/erp-excel/preview", headers=auth, files=files, params={"warehouses": ["509"]})
    assert pv.status_code == 200, pv.text
    p = pv.json()
    assert p["missing_sheets"] == []
    o = p["orders"]
    assert o["file_rows"] == 5 and o["valid_rows"] == 4 and o["skipped_count"] == 1 and o["new"] == 4 and o["existing"] == 0
    assert o["unknown_item_count"] == 1 and o["no_routing_count"] == 1 and o["would_close_count"] == 1
    st = p["stock"]
    assert [w["code"] for w in st["warehouses"]] == ["509", "69"] and st["items_in_file"] == 2 and st["unknown_item_count"] == 1
    # 509: 6001001 24 (mevcut 10 -> +14), 6001002 1 (mevcut 3 -> -2)
    assert st["adjustments"] == 2 and st["increase"] == 14 and st["decrease"] == 2
    ap = client.post("/api/imports/erp-excel/apply", headers=auth, files=files, params={"warehouses": ["509"], "close_missing": True})
    assert ap.status_code == 200, ap.text
    r = ap.json()
    assert r["orders"]["inserted"] == 2 and r["orders"]["closed"] == 1 and r["orders"]["error_count"] == 2  # rotasız + bilinmeyen stok reddedildi
    assert r["orders"]["reserved"] == 1
    assert r["stock"]["adjustments"] == 2
    orders = client.get("/api/orders", headers=auth).json()
    by = {(x["order_no"], x["position_no"]): x for x in orders}
    assert by[("ERS1", "111")]["quantity"] == 100 and by[("ERS1", "111")]["unit_price"] == 500 and by[("ERS1", "111")]["due_date"] == due.isoformat()
    assert by[("ERS1", "112")]["market"] == "export" and by[("ERS1", "112")]["unit_price"] == 20
    assert all(x["order_no"] != "OLD-1" for x in orders if x["status"] == "open")
    stock = {s["item_code"]: s for s in client.get("/api/stock/summary", headers=auth).json()}
    assert stock["6001001"]["on_hand"] == 24 and stock["6001002"]["on_hand"] == 1
    assert stock["6001001"]["reserved"] == 30 and stock["6001001"]["free"] == -6  # ERP REZERV 30 -> dış stok rezervasyonu (ihtiyaçtan düşer)
    # İkinci uygulama: fark yok, sipariş güncellenir, stok düzeltmesi 0
    ap2 = client.post("/api/imports/erp-excel/apply", headers=auth, files=files, params={"warehouses": ["509"]}).json()
    assert ap2["stock"]["adjustments"] == 0 and ap2["orders"]["inserted"] == 0 and ap2["orders"]["updated"] == 2
    fresh = client.get("/api/data-freshness", headers=auth).json()
    assert {c["key"]: c["status"] for c in fresh["checkpoints"]}["open_orders"] == "ok"
