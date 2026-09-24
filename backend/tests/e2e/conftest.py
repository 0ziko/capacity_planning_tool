"""Uçtan uca (E2E) entegrasyon senaryoları için ortak yardımcılar ve rapor toplayıcı.

Üst dizindeki conftest izole SQLite veritabanı, TestClient ve admin oturumu sağlar;
her testten önce sipariş/plan/üretim kalıntıları temizlenir (master veri kalır).
Bu dosya ek olarak:
  * Excel üretme / yükleme kısayolları
  * Sabit planlama haftası (Pazartesi) ve ortak master veri kurulumu
  * Senaryo adımlarını kaydeden ``sc`` fixture'ı ve oturum sonunda
    outputs/e2e/ altına JSON + Markdown rapor yazan hook'lar
"""
from __future__ import annotations

import io
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from openpyxl import Workbook

WEEK = date(2026, 10, 5)  # Pazartesi; bugünden ileride, hafta hesapları deterministik
REPORT_DIR = Path(__file__).resolve().parents[3] / "outputs" / "e2e"


def monday(offset_weeks: int = 0) -> date:
    return WEEK + timedelta(weeks=offset_weeks)


def xlsx(header: list[str], rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(header)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def upload(client, auth, kind: str, header: list[str], rows: list[list], *, expect_errors: bool = False) -> dict:
    r = client.post(f"/api/imports/{kind}", headers=auth, files={"file": (f"{kind}.xlsx", xlsx(header, rows), "application/octet-stream")})
    assert r.status_code == 200, f"{kind}: {r.status_code} {r.text[:400]}"
    body = r.json()
    if not expect_errors:
        assert body.get("errors") in ([], None), body
    return body


def weekly_staffing(client, auth, rows: list[tuple[str, int, float, int]], weeks: int = 20, start: date = WEEK - timedelta(weeks=2)):
    """(wc_code, kişi, kişi başı verimli saat, gün) satırlarını ``weeks`` hafta boyunca yükler."""
    return upload(
        client, auth, "wc_weeks",
        ["İş Merkezi Kodu", "Hafta", "Kişi Sayısı", "Kişi Başı Verimli Saat", "Çalışma Günü"],
        [[code, (start + timedelta(weeks=i)).isoformat(), hc, hours, days] for code, hc, hours, days in rows for i in range(weeks)],
    )


def wc_by_code(client, auth, code: str) -> dict:
    return next(w for w in client.get("/api/workcenters", headers=auth).json() if w["code"] == code)


def plan_auto(client, auth, **req) -> dict:
    """Ön kontrol -> (gerekirse) eksik kişi onayı -> otomatik plan. Yanıt JSON döner."""
    body = dict(req)
    pre = client.post("/api/plan/auto/preflight", headers=auth, json=body)
    if pre.status_code == 200 and pre.json().get("missing_headcount_token"):
        body["missing_headcount_ack"] = pre.json()["missing_headcount_token"]
    r = client.post("/api/plan/auto", headers=auth, json=body)
    assert r.status_code == 200, r.text[:600]
    return r.json()


def clear_orders(client, auth):
    for st in ("open", "closed", "merged"):
        client.delete("/api/orders", headers=auth, params={"status": st})


def is_xlsx(resp) -> bool:
    return resp.status_code == 200 and resp.content[:2] == b"PK"


# --------------------------------------------------------------------------- ortak master veri
@pytest.fixture(scope="session")
def master(client, auth) -> dict:
    """E2E master verisi (idempotent upsert): 2 iş merkezi, vardiya, haftalık iş gücü, makine, stok/BOM/rota.

    E2E-PRS: 10 kişi x 4 verimli saat x 5 gün = 200 saat/hafta, birim 10 saat -> 20 birim
    E2E-MNT:  5 kişi x 4 verimli saat x 5 gün = 100 saat/hafta
    E2E-MAM rota: 10 Kesim (E2E-PRS, 50 sn) -> 20 Büküm (E2E-PRS, 30 sn) -> 30 Montaj (E2E-MNT, 40 sn)
    """
    upload(client, auth, "workcenters",
           ["İş Merkezi Kodu", "İş Merkezi Adı", "Planlanıyor (E/H)", "Birim Saat", "Kişi Başı Verimli Saat", "Alan Kodu", "Alan Adı"],
           [["E2E-PRS", "E2E Preshane", "E", 10, 4, "E2E", "E2E Alanı"],
            ["E2E-MNT", "E2E Montaj", "E", 10, 4, "E2E", "E2E Alanı"]])
    upload(client, auth, "shifts",
           ["İş Merkezi Kodu", "Vardiya", "Günler (Pzt=0..Paz=6)", "Başlangıç", "Bitiş", "Kişi Sayısı", "Kişi Başı Verimli Saat"],
           [["E2E-PRS", "Gündüz", "0,1,2,3,4", "08:00", "18:00", 10, 4],
            ["E2E-MNT", "Gündüz", "0,1,2,3,4", "08:00", "18:00", 5, 4]])
    weekly_staffing(client, auth, [("E2E-PRS", 10, 4, 5), ("E2E-MNT", 5, 4, 5)])
    upload(client, auth, "machines",
           ["İş Merkezi Kodu", "İstasyon / Makine Kodu", "İstasyon / Makine Adı", "Aktif (E/H)"],
           [["E2E-PRS", "E2E-PRS-M1", "E2E Eksantrik Pres", "E"], ["E2E-MNT", "E2E-MNT-M1", "E2E Montaj Bankı", "E"]])
    upload(client, auth, "items", ["Stok Kodu", "Stok Adı", "Ürün Grubu", "Birim"],
           [["E2E-MAM", "E2E Endüstriyel Ocak", "E2EGRP", "AD"],
            ["E2E-MAM2", "E2E Rotasız Ürün", "E2EGRP", "AD"],
            ["E2E-HM-SAC", "E2E Paslanmaz Sac 2mm", "", "KG"]])
    upload(client, auth, "bom", ["Stok Kodu", "Bileşen Kodu", "Bileşen Adı", "Miktar", "Birim"],
           [["E2E-MAM", "E2E-HM-SAC", "E2E Paslanmaz Sac 2mm", 2.5, "KG"]])
    upload(client, auth, "routing",
           ["Stok Kodu", "Sıra", "Operasyon", "İş Merkezi Kodu", "Çevrim Süresi (sn)", "Yarımamül Kodu"],
           [["E2E-MAM", 10, "Kesim", "E2E-PRS", 50, "E2E-MAM-10"],
            ["E2E-MAM", 20, "Büküm", "E2E-PRS", 30, "E2E-MAM-20"],
            ["E2E-MAM", 30, "Montaj", "E2E-MNT", 40, "E2E-MAM-30"]])
    prs = wc_by_code(client, auth, "E2E-PRS")
    mnt = wc_by_code(client, auth, "E2E-MNT")
    return {"prs": prs, "mnt": mnt, "prs_id": prs["id"], "mnt_id": mnt["id"], "wc_ids": [prs["id"], mnt["id"]]}


# --------------------------------------------------------------------------- senaryo kaydı / rapor
class Scenario:
    def __init__(self, nodeid: str, title: str):
        self.nodeid = nodeid
        self.title = title
        self.steps: list[dict] = []
        self.started = time.perf_counter()
        self.outcome = "unknown"
        self.error = ""

    def step(self, name: str, ok: bool = True, **detail):
        self.steps.append({"name": name, "ok": bool(ok), "detail": {k: _json_safe(v) for k, v in detail.items()}})
        assert ok, f"Adım başarısız: {name} {detail}"

    def info(self, name: str, **detail):
        self.steps.append({"name": name, "ok": None, "detail": {k: _json_safe(v) for k, v in detail.items()}})

    def to_dict(self) -> dict:
        return {"nodeid": self.nodeid, "title": self.title, "outcome": self.outcome, "error": self.error,
                "duration_s": round(time.perf_counter() - self.started, 2), "steps": self.steps}


def _json_safe(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v][:50]
    if isinstance(v, dict):
        return {str(k): _json_safe(x) for k, x in list(v.items())[:50]}
    if isinstance(v, float):
        return round(v, 3)
    return v if isinstance(v, (int, str, bool)) or v is None else str(v)


_RESULTS: list[dict] = []


@pytest.fixture
def sc(request):
    doc = (request.node.function.__doc__ or request.node.name).strip().splitlines()[0]
    rec = Scenario(request.node.nodeid, doc)
    request.node._e2e_scenario = rec
    yield rec


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    rec = getattr(item, "_e2e_scenario", None)
    if rec is None or rep.when != "call":
        return
    rec.outcome = rep.outcome
    if rep.failed:
        rec.error = str(rep.longrepr)[-1200:]
    _RESULTS.append(rec.to_dict())


def pytest_sessionfinish(session, exitstatus):
    if not _RESULTS:
        return
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    (REPORT_DIR / "e2e_results.json").write_text(json.dumps({"generated": stamp, "scenarios": _RESULTS}, ensure_ascii=False, indent=2), encoding="utf-8")
    passed = sum(1 for r in _RESULTS if r["outcome"] == "passed")
    lines = [f"# E2E Senaryo Sonuçları — {stamp}", "",
             f"Toplam {len(_RESULTS)} senaryo · {passed} geçti · {len(_RESULTS) - passed} başarısız", ""]
    for r in _RESULTS:
        mark = "PASS" if r["outcome"] == "passed" else "FAIL"
        lines.append(f"## [{mark}] {r['title']}")
        lines.append(f"`{r['nodeid'].split('::')[-1]}` · {r['duration_s']} sn")
        lines.append("")
        for s in r["steps"]:
            tick = "[x]" if s["ok"] else ("[!]" if s["ok"] is False else "[i]")
            det = ", ".join(f"{k}={v}" for k, v in s["detail"].items())
            lines.append(f"- {tick} {s['name']}" + (f" — {det}" if det else ""))
        if r["error"]:
            lines += ["", "```", r["error"].strip(), "```"]
        lines.append("")
    (REPORT_DIR / "e2e_results.md").write_text("\n".join(lines), encoding="utf-8")
