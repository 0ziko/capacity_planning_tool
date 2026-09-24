"""Yavaş uç noktaları canlı veritabanında SALT OKUNUR profiller; sonuç özetlerini SHA256 ile kaydeder.

Kullanım: .\\.venv\\Scripts\\python.exe scripts\\profile_slow_endpoints.py [etiket]
Çıktı: outputs/e2e/perf_<etiket>.txt (süre, sorgu sayısı, cProfile ilk 15) ve perf_<etiket>.json (özet + hash)
Hiçbir yazma yapmaz; her ölçüm sonunda oturum rollback edilir.
"""
from __future__ import annotations

import cProfile
import hashlib
import io
import json
import pstats
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import event  # noqa: E402

from app.db.session import SessionLocal, engine  # noqa: E402
from app.schemas import RequirementQuery  # noqa: E402
from app.services import data_integrity, requirements, stock  # noqa: E402
from app.services import orders as orders_svc  # noqa: E402
from app.services import planning  # noqa: E402
from app.services.excel import build_backup  # noqa: E402

label = sys.argv[1] if len(sys.argv) > 1 else "run"
OUT = ROOT.parent / "outputs" / "e2e"
OUT.mkdir(parents=True, exist_ok=True)
counter = {"n": 0}


@event.listens_for(engine, "before_cursor_execute")
def _count(conn, cursor, statement, parameters, context, executemany):
    counter["n"] += 1


def digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()[:16]


def measure(name, fn, summarize):
    db = SessionLocal()
    counter["n"] = 0
    pr = cProfile.Profile()
    t = time.perf_counter()
    pr.enable()
    try:
        result = fn(db)
    finally:
        pr.disable()
        db.rollback()
        db.close()
    secs = round(time.perf_counter() - t, 2)
    s = io.StringIO()
    pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(15)
    summary = summarize(result)
    print(f"{name}: {secs} sn, {counter['n']} sorgu, hash {digest(summary)}")
    return {"name": name, "seconds": secs, "queries": counter["n"], "hash": digest(summary), "summary": summary, "profile": s.getvalue()}


wk = date.today() - timedelta(days=date.today().weekday())
rows = [
    measure("data_integrity", lambda db: data_integrity.audit_data(db),
            lambda r: {"error_count": r["error_count"], "warn_count": r["warn_count"], "issues": r["issues"]}),
    measure("requirements", lambda db: requirements.requirement_lines(db, RequirementQuery()),
            lambda r: [l.model_dump(mode="json") for l in r]),
    measure("weekly_output", lambda db: planning.weekly_output(db, wk),
            lambda r: r.model_dump(mode="json")),
    measure("plan_orders(order_schedule)", lambda db: orders_svc.order_schedule(db, None),
            lambda r: [s.model_dump(mode="json") for s in r]),
    measure("stock_orders", lambda db: stock.order_rows(db, None, False, None),
            lambda r: [s.model_dump(mode="json") for s in r]),
    measure("backup_xlsx", lambda db: build_backup(db),
            lambda r: {"size": len(r)}),
]
(OUT / f"perf_{label}.json").write_text(json.dumps([{k: v for k, v in r.items() if k != "profile"} for r in rows], ensure_ascii=False, default=str), encoding="utf-8")
with open(OUT / f"perf_{label}.txt", "w", encoding="utf-8") as f:
    for r in rows:
        f.write(f"===== {r['name']}: {r['seconds']} sn, {r['queries']} sorgu, hash {r['hash']}\n{r['profile']}\n")
print("yazıldı:", OUT / f"perf_{label}.txt")
