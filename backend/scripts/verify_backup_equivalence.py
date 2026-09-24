"""Yedek Excel'inin akış (write-only) modu ile klasik mod çıktısının içerik denkliğini canlı veride doğrular.

Salt okunurdur. Her iki üretim yolu aynı DB oturumundan okur; sayfa adları, satır/sütun sayıları,
tüm hücre değerleri, sütun genişlikleri ve başlık biçimi karşılaştırılır.
Kullanım: .\\.venv\\Scripts\\python.exe scripts\\verify_backup_equivalence.py
"""
from __future__ import annotations

import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import Workbook, load_workbook  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.services import excel  # noqa: E402


def build(write_only: bool) -> bytes:
    real = Workbook

    def factory(*args, **kwargs):
        kwargs["write_only"] = write_only
        return real(*args, **kwargs)

    excel.Workbook = factory  # type: ignore[assignment]
    db = SessionLocal()
    try:
        t = time.perf_counter()
        data = excel.build_backup(db)
        print(f"write_only={write_only}: {round(time.perf_counter() - t, 1)} sn, {round(len(data) / 1024)} KB")
        return data
    finally:
        db.rollback()
        db.close()
        excel.Workbook = real  # type: ignore[assignment]


old = load_workbook(io.BytesIO(build(False)), read_only=False)
new = load_workbook(io.BytesIO(build(True)), read_only=False)
problems: list[str] = []
if old.sheetnames != new.sheetnames:
    problems.append(f"sayfa adlari farkli: {old.sheetnames} != {new.sheetnames}")
for name in old.sheetnames:
    a, b = old[name], new[name]
    if (a.max_row, a.max_column) != (b.max_row, b.max_column):
        problems.append(f"{name}: boyut {a.max_row}x{a.max_column} != {b.max_row}x{b.max_column}")
        continue
    ra, rb = a.iter_rows(values_only=True), b.iter_rows(values_only=True)
    for i, (x, y) in enumerate(zip(ra, rb), start=1):
        if x != y:
            problems.append(f"{name}: satir {i} farkli: {x[:6]} != {y[:6]}")
            break
    for col in range(1, a.max_column + 1):
        from openpyxl.utils import get_column_letter

        L = get_column_letter(col)
        if round(a.column_dimensions[L].width or 0, 2) != round(b.column_dimensions[L].width or 0, 2):
            problems.append(f"{name}: sutun {L} genislik {a.column_dimensions[L].width} != {b.column_dimensions[L].width}")
            break
    ha, hb = a.cell(1, 1), b.cell(1, 1)
    if (ha.font.bold, ha.font.color.rgb if ha.font.color else None, ha.fill.fgColor.rgb) != (hb.font.bold, hb.font.color.rgb if hb.font.color else None, hb.fill.fgColor.rgb):
        problems.append(f"{name}: baslik bicimi farkli")
print("SONUC:", "BIREBIR AYNI" if not problems else f"{len(problems)} fark")
for p in problems[:20]:
    print(" -", p)
sys.exit(1 if problems else 0)
