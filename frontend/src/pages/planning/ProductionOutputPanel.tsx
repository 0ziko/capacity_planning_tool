import { useState } from "react";
import { api, fmt, mondayOf, qs, shortDate, weekLong, type LoadDetailRow, type WeeklyOutput } from "../../api";
import { ErrorText, WeekInput } from "../../components";

function orderLabel(r: LoadDetailRow): string {
  if (r.batch_order_nos.length) return `${r.order_no} (${r.batch_order_nos.join(", ")})`;
  return r.position_no ? `${r.order_no} / ${r.position_no}` : r.order_no;
}

function dateLabel(iso: string | null): string {
  if (!iso) return "—";
  return shortDate(iso.slice(0, 10));
}

function wipSummary(rows: LoadDetailRow[]): { code: string; qty: number }[] {
  const by = new Map<string, number>();
  for (const r of rows) {
    const code = r.semi_finished_code?.trim() || r.item_code;
    by.set(code, (by.get(code) ?? 0) + r.planned_qty);
  }
  return Array.from(by.entries())
    .map(([code, qty]) => ({ code, qty }))
    .sort((a, b) => a.code.localeCompare(b.code, "tr"));
}

export default function ProductionOutputPanel() {
  const [week, setWeek] = useState(mondayOf(new Date()));
  const [data, setData] = useState<WeeklyOutput | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setBusy(true);
    setErr("");
    try {
      setData(await api.get<WeeklyOutput>(`/api/plan/weekly-output${qs({ week_start: week })}`));
    } catch (e) {
      setData(null);
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const exportXlsx = () => api.download(`/api/plan/weekly-output.xlsx${qs({ week_start: week })}`, `uretim_plani_${week}.xlsx`);
  const print = () => window.print();

  return (
    <div className="production-output">
      <div className="panel row no-print">
        <WeekInput value={week} onChange={setWeek} label="Çalışma haftası" />
        <button onClick={load} disabled={busy}>{busy ? "Yükleniyor…" : "Planı göster"}</button>
        {data && (
          <>
            <button className="secondary" onClick={print}>Yazdır</button>
            <button className="secondary" onClick={exportXlsx} disabled={!data.work_centers.length}>Excel indir</button>
          </>
        )}
      </div>

      <ErrorText err={err} />

      {data && (
        <div className="production-output-body">
          <div className="production-output-header">
            <h2 style={{ margin: 0 }}>Haftalık Üretim Planı</h2>
            <p className="muted" style={{ margin: "4px 0 0" }}>
              {weekLong(data.week_start)} — {shortDate(data.week_end)} · {data.work_centers.length} iş merkezi · {data.total_jobs} iş · {fmt(data.total_qty, 0)} adet
            </p>
          </div>

          {data.work_centers.length === 0 ? (
            <div className="panel"><p className="muted" style={{ margin: 0 }}>Bu hafta için planlanmış üretim yok.</p></div>
          ) : (
            data.work_centers.map((wc) => {
              const summary = wipSummary(wc.rows);
              return (
              <section key={wc.work_center_id} className="production-wc-block">
                <h3>{wc.work_center_code} <span className="muted">— {wc.rows.length} iş · {summary.length} yarımamül</span></h3>
                {summary.length > 0 && (
                  <table className="production-wip-summary" style={{ marginBottom: 10, maxWidth: 420 }}>
                    <thead>
                      <tr><th colSpan={2}>Bu hafta üretilecek yarımamüller (özet)</th></tr>
                      <tr><th>Yarımamül kodu</th><th className="num">Adet</th></tr>
                    </thead>
                    <tbody>
                      {summary.map((s) => (
                        <tr key={s.code}><td><b>{s.code}</b></td><td className="num">{fmt(s.qty, 0)}</td></tr>
                      ))}
                    </tbody>
                  </table>
                )}
                <table>
                  <thead>
                    <tr>
                      <th>Yarımamül</th>
                      <th className="num">Adet</th>
                      <th>Op.</th>
                      <th>Sipariş</th>
                      <th>Bitmiş ürün</th>
                      <th>Başlangıç</th>
                      <th>Bitiş</th>
                    </tr>
                  </thead>
                  <tbody>
                    {wc.rows.map((r) => (
                      <tr key={r.plan_line_id}>
                        <td><b>{r.semi_finished_code?.trim() || <span className="muted">—</span>}</b></td>
                        <td className="num">{fmt(r.planned_qty, 0)}</td>
                        <td title={r.operation_name}>{r.operation_seq}</td>
                        <td>{orderLabel(r)}</td>
                        <td>{r.item_code}</td>
                        <td>{dateLabel(r.planned_start)}</td>
                        <td>{dateLabel(r.planned_end)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            );})
          )}
        </div>
      )}

      {!data && !err && (
        <p className="muted no-print">Haftayı seçip «Planı göster» ile üretim ekibine verilecek listeyi oluşturun.</p>
      )}
    </div>
  );
}
