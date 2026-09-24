"""Canlı ortam salt okunur duman testi (http://localhost:8000, PostgreSQL, gerçek veri).

HİÇBİR YAZMA YAPMAZ: yalnızca GET uç noktaları ve saf hesaplama yapan POST'lar
(ihtiyaç, terminleme, ön kontrol, karşılaştırma). Canlı sipariş/plan/stok değişmez.

Kullanım (backend dizininde):
    .\\.venv\\Scripts\\python.exe tests\\e2e\\live_smoke.py [--base http://localhost:8000]
Giriş bilgisi .env içindeki FIRST_ADMIN_USERNAME / FIRST_ADMIN_PASSWORD'dan okunur
(yalnızca ilk admin kullanıcısı için geçerlidir; şifre değiştirildiyse E2E_USER / E2E_PASS ortam değişkeni verin).
Çıktı: outputs/e2e/live_smoke.json ve live_smoke.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "outputs" / "e2e"


def read_env(key: str, default: str = "") -> str:
    env = ROOT / "backend" / ".env"
    if os.environ.get(key):
        return os.environ[key]
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(key + "="):
                return line.split("=", 1)[1].strip()
    return default


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


class Smoke:
    def __init__(self, base: str):
        self.c = httpx.Client(base_url=base, timeout=60)
        self.rows: list[dict] = []
        self.h: dict = {}

    def check(self, name: str, method: str, path: str, *, ok=lambda r: r.status_code == 200, note=lambda r: "", anon: bool = False, timeout: float = 60, **kw):
        t = time.perf_counter()
        try:
            r = self.c.request(method, path, headers={} if anon else self.h, timeout=timeout, **kw)
            passed = bool(ok(r))
            detail = note(r) if passed else r.text[:200]
        except Exception as exc:  # noqa: BLE001
            passed, detail, r = False, f"{type(exc).__name__}: {exc}", None
        ms = round((time.perf_counter() - t) * 1000)
        self.rows.append({"name": name, "path": f"{method} {path}", "ok": passed, "ms": ms,
                          "status": getattr(r, "status_code", None), "detail": str(detail)[:300]})
        print(("OK  " if passed else "FAIL"), f"{ms:>6} ms", name, "" if passed else f"-> {detail}")
        return r

    def run(self) -> int:
        j = lambda r: r.json()  # noqa: E731
        self.check("Sağlık (DB bağlantısı)", "GET", "/api/health", ok=lambda r: r.status_code == 200 and r.json()["db_ok"], note=lambda r: r.json()["database"])
        user = read_env("E2E_USER") or read_env("FIRST_ADMIN_USERNAME", "admin")
        pw = read_env("E2E_PASS") or read_env("FIRST_ADMIN_PASSWORD", "admin123")
        r = self.check("Giriş (ilk admin)", "POST", "/api/auth/login", data={"username": user, "password": pw})
        if r is None or r.status_code != 200:
            print("Giriş başarısız; kalan kontroller atlandı. E2E_USER / E2E_PASS ile deneyin.")
            return 2
        self.h = {"Authorization": f"Bearer {r.json()['access_token']}"}
        self.check("Tokensiz erişim reddi", "GET", "/api/workcenters", ok=lambda r: r.status_code == 401, anon=True)
        wk = monday(date.today())
        wcs = self.check("İş merkezleri", "GET", "/api/workcenters", note=lambda r: f"{len(j(r))} merkez, {sum(1 for w in j(r) if w['is_planned'])} planlanan")
        wc_list = wcs.json() if wcs is not None and wcs.status_code == 200 else []
        planned = [w["id"] for w in wc_list if w.get("is_planned")] or [w["id"] for w in wc_list[:5]]
        self.check("Makineler", "GET", "/api/machines", note=lambda r: f"{len(j(r))} makine")
        self.check("Haftalık iş gücü", "GET", "/api/wc-weeks", params={"start": wk.isoformat(), "weeks": 8}, note=lambda r: f"{len(j(r))} kayıt")
        self.check("Stok kartları", "GET", "/api/items", params={"limit": 50}, note=lambda r: f"{len(j(r)) if isinstance(j(r), list) else j(r).get('total', '?')} kayıt/sayfa")
        self.check("Ürün grupları", "GET", "/api/items/groups", note=lambda r: f"{len(j(r))} grup")
        self.check("Açık siparişler", "GET", "/api/orders", note=lambda r: f"{len(j(r))} sipariş")
        self.check("Sipariş analizi", "GET", "/api/orders/analysis")
        self.check("Kapasite (bu hafta)", "GET", "/api/capacity", params={"start": wk.isoformat()}, note=lambda r: f"{round(sum(c['capacity_hours'] for c in j(r)))} saat toplam")
        self.check("İş gücü ihtiyacı", "POST", "/api/requirements", json={}, note=lambda r: f"{round(j(r).get('total_hours', 0))} saat")
        self.check("Plan ön kontrol", "POST", "/api/plan/auto/preflight", json={"start_week": wk.isoformat(), "weeks": 8, "work_center_ids": planned},
                   note=lambda r: f"eksik kişi token: {bool(j(r).get('missing_headcount_token'))}")
        self.check("Plan yükü (8 hafta)", "GET", "/api/plan/load", params={"start": wk.isoformat(), "weeks": 8, "work_center_ids": planned},
                   note=lambda r: f"{round(sum(w['planned_hours'] for l in j(r) for w in l['weeks']))} saat planlı")
        self.check("Plan satırları", "GET", "/api/plan/lines", params={"start": wk.isoformat(), "work_center_ids": planned}, note=lambda r: f"{len(j(r))} satır")
        self.check("Haftalık çıktı", "GET", "/api/plan/weekly-output", params={"week_start": wk.isoformat()})
        self.check("Sipariş takvimi", "GET", "/api/plan/orders", note=lambda r: f"{len(j(r))} sipariş")
        self.check("Ciro görünümü", "GET", "/api/plan/revenue", params={"start": wk.isoformat(), "weeks": 8})
        self.check("Plan revizyonları", "GET", "/api/plan/revisions", note=lambda r: f"{len(j(r))} revizyon")
        self.check("Üretim partileri", "GET", "/api/plan/production-batches", note=lambda r: f"{len(j(r))} parti")
        self.check("Birleştirme önerileri", "GET", "/api/plan/merge-suggestions", note=lambda r: f"{len(j(r))} grup")
        self.check("Tahminler", "GET", "/api/plan/forecast")
        if planned:
            self.check("Gantt (ilk merkez, 4 hafta)", "GET", "/api/plan/gantt", params={"work_center_id": planned[0], "start": wk.isoformat(), "end": (wk + timedelta(days=27)).isoformat()},
                       note=lambda r: f"{len(j(r).get('bars', []))} çubuk")
            self.check("Segmentler (ilk merkez)", "GET", "/api/plan/segments", params={"work_center_id": planned[0], "start": wk.isoformat(), "end": (wk + timedelta(days=27)).isoformat()},
                       note=lambda r: f"{len(j(r))} segment")
            self.check("Yük detayı", "GET", "/api/plan/load/detail", params={"work_center_id": planned[0], "week_start": wk.isoformat()})
        self.check("Haftalık ilerleme", "GET", "/api/progress", params={"week": wk.isoformat(), "as_of": date.today().isoformat()})
        self.check("Sipariş ilerlemesi", "GET", "/api/progress/orders", params={"as_of": date.today().isoformat()})
        self.check("Duruş analizi (30 gün)", "GET", "/api/analysis/downtime", params={"start": (date.today() - timedelta(days=30)).isoformat(), "end": date.today().isoformat()})
        self.check("Çevrim süresi analizi", "GET", "/api/analysis/cycletime")
        self.check("Senaryo grupları", "GET", "/api/scenarios/groups", note=lambda r: f"{len(j(r))} grup")
        self.check("Senaryo kuralları", "GET", "/api/scenarios/rules")
        self.check("Stok özeti", "GET", "/api/stock/summary", note=lambda r: f"{len(j(r))} stok")
        self.check("Stok karşılama", "GET", "/api/stock/orders")
        self.check("Rezervasyonlar", "GET", "/api/stock/reservations")
        self.check("Sevkiyatlar", "GET", "/api/stock/shipments")
        self.check("MES kaynak durumu", "GET", "/api/mes/source-status", note=lambda r: f"{j(r)['production_source']}, MES kaydı: {j(r)['has_mes_records']}")
        self.check("MES ilerleme", "GET", "/api/mes/progress", params={"as_of": date.today().isoformat()})
        self.check("MES serbest stok", "GET", "/api/mes/free-stock")
        self.check("MES stok defteri", "GET", "/api/mes/inventory")
        self.check("Teslimat riski", "GET", "/api/mes/delivery-risk", params={"as_of": date.today().isoformat(), "horizon": 8})
        self.check("Veri tazeliği", "GET", "/api/data-freshness", note=lambda r: ", ".join(f"{c['key']}={c.get('status')}" for c in j(r).get("checkpoints", [])))
        self.check("Veri bütünlüğü", "GET", "/api/data-integrity", timeout=300)
        self.check("Kaynak modeli istatistiği", "GET", "/api/resource-model/stats")
        self.check("Import günlüğü", "GET", "/api/imports/log")
        self.check("Import türleri", "GET", "/api/imports/kinds")
        for path, name, params in (("/api/backup.xlsx", "Yedek Excel", {}), ("/api/plan/orders.xlsx", "Sipariş takvimi Excel", {}),
                                   ("/api/exports/wc-weeks.xlsx", "Haftalık iş gücü Excel", {"start": wk.isoformat(), "weeks": 8}),
                                   ("/api/stock/orders/export.xlsx", "Stok karşılama Excel", {})):
            self.check(name, "GET", path, params=params, timeout=300, ok=lambda r: r.status_code == 200 and r.content[:2] == b"PK", note=lambda r: f"{round(len(r.content) / 1024)} KB")
        item = next((o.get("item_code") for o in (self.c.get("/api/orders", headers=self.h).json() or []) if o.get("item_code")), None)
        if item:
            self.check(f"Terminleme ({item})", "POST", "/api/plan/leadtime", json={"item_code": item, "quantity": 10, "start": (wk + timedelta(weeks=8)).isoformat()},
                       ok=lambda r: r.status_code in (200, 400), note=lambda r: f"{len(j(r).get('steps', []))} adım" if r.status_code == 200 else "rota yok")
        self.check("Frontend (Vite) ayakta", "GET", "http://localhost:5173/", ok=lambda r: r.status_code == 200 and "<div id=\"root\"" in r.text, anon=True)
        self.write()
        failed = [r for r in self.rows if not r["ok"]]
        print(f"\n{len(self.rows) - len(failed)}/{len(self.rows)} kontrol geçti")
        return 1 if failed else 0

    def write(self):
        OUT.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        (OUT / "live_smoke.json").write_text(json.dumps({"generated": stamp, "checks": self.rows}, ensure_ascii=False, indent=2), encoding="utf-8")
        ok = sum(1 for r in self.rows if r["ok"])
        lines = [f"# Canlı ortam duman testi — {stamp}", "", f"{ok}/{len(self.rows)} kontrol geçti · salt okunur (canlı veri değiştirilmedi)", "",
                 "| Durum | Kontrol | Uç nokta | Süre | Not |", "|---|---|---|---|---|"]
        for r in self.rows:
            lines.append(f"| {'OK' if r['ok'] else 'FAIL'} | {r['name']} | `{r['path']}` | {r['ms']} ms | {r['detail'].replace('|', '/')} |")
        (OUT / "live_smoke.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    sys.exit(Smoke(ap.parse_args().base).run())
