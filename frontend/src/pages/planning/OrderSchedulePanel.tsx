import { useState } from "react";
import { api, qs, fmt, weekLong, type OrderSchedule } from "../../api";

const STATUS: Record<OrderSchedule["plan_status"], [string, string, string]> = {
  finish_unknown: ["warn", "Bitiş tarihi belirsiz", "Seçili kapsamdaki ihtiyaç planlandı; son operasyon bitişi hesaplanamadığı için termin uygunluğu belirlenemiyor"],
  covered: ["ok", "Üretim ihtiyacı kalmadı", "Stok ve üretim dikkate alındığında seçili kapsamda kalan üretim ihtiyacı yok; sevk edildiği anlamına gelmez"],
  on_time: ["ok", "Termine uygun", "Planlanan bitiş termin tarihinden önce veya aynı gün"],
  late: ["bad", "Geç", "Planlanan bitiş termin tarihinden sonra"],
  partial: ["warn", "Kısmi plan", "İhtiyacın bir kısmı plan ufkuna sığmadı (hafta sayısını artırın / kapasite ekleyin)"],
  unplanned: ["muted", "Planlanmadı", "Bu sipariş için plan satırı yok — Otomatik planla'yı çalıştırın"],
  no_ops: ["muted", "Rota yok", "Seçili iş merkezlerinde bu stok için operasyon tanımlı değil"],
};

export function PlanStatusBadge({ s }: { s: OrderSchedule["plan_status"] }) {
  const [cls, label, title] = STATUS[s] ?? ["muted", s, ""];
  return <span className={`badge ${cls}`} title={title}>{label}</span>;
}

/** Plan sonucu: her siparişin tahmini üretim bitiş tarihi ve termine göre durumu. */
export default function OrderSchedulePanel({ rows, err, onReload, wcIds }: { wcIds: number[]; rows: OrderSchedule[] | null; err: string; onReload: () => void }) {
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");
  async function download() {
    setExporting(true); setExportError("");
    try { await api.download(`/api/plan/orders.xlsx${qs({ work_center_ids: wcIds, plan_status: filter || undefined, sort })}`); }
    catch (e) { setExportError(e instanceof Error ? e.message : String(e)); }
    finally { setExporting(false); }
  }
  const [filter, setFilter] = useState<"" | OrderSchedule["plan_status"]>("");
  const [sort, setSort] = useState<"due" | "end" | "late">("due");
  const list = (rows ?? []).filter((r) => !filter || r.plan_status === filter);
  list.sort((a, b) => {
    if (sort === "end") return (a.planned_end ?? "9999").localeCompare(b.planned_end ?? "9999");
    if (sort === "late") return (b.lateness_days ?? -9999) - (a.lateness_days ?? -9999);
    return a.due_date.localeCompare(b.due_date);
  });
  const count = (s: OrderSchedule["plan_status"]) => (rows ?? []).filter((r) => r.plan_status === s).length;
  const kpis: [OrderSchedule["plan_status"], string][] = [["finish_unknown", "Bitiş tarihi belirsiz"], ["on_time", "Termine uygun"], ["late", "Geç kalacak"], ["partial", "Kısmi"], ["unplanned", "Planlanmadı"], ["no_ops", "Rota yok"], ["covered", "Üretim ihtiyacı kalmadı"]];

  return (
    <>
      <div className="row" style={{ marginBottom: 10 }}>
        {kpis.map(([s, l]) => (
          <button key={s} className={`kpi-btn ${filter === s ? "active" : ""} ${STATUS[s][0]}`} onClick={() => setFilter(filter === s ? "" : s)}>
            <span className="v">{count(s)} <small style={{ fontSize: 13 }}>%{fmt(rows?.length ? count(s) / rows.length * 100 : 0, 1)}</small></span><span className="l">{l}</span>
          </button>
        ))}
        <span className="muted">Toplam {rows?.length ?? 0} sipariş pozisyonu · Gösterilen {list.length}</span>
        <label>Sırala
          <select value={sort} onChange={(e) => setSort(e.target.value as typeof sort)}>
            <option value="due">Termine göre</option>
            <option value="end">Tahmini bitişe göre</option>
            <option value="late">Gecikmeye göre</option>
          </select>
        </label>
        <button className="secondary" onClick={onReload}>↻ Yenile</button>
        <button className="secondary" disabled={exporting || rows === null || list.length === 0 || !!err} onClick={download}>{exporting ? "Hazırlanıyor…" : "↓ Excel (filtreli liste)"}</button>
        <span className="muted">Bitiş, mamulün son operasyon planından Gantt ile ortak yöntemle tahmin edilir. Kısmi planda bu tarih tüm siparişin tamamlanacağını göstermez.</span>
      </div>
      {exportError && <div className="error">{exportError}</div>}
      {err && <div className="error">{err}</div>}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Sipariş</th><th>Poz</th><th>Müşteri</th><th>Stok</th><th className="num">Miktar</th><th className="num">Ciro</th><th>Termin</th>
              <th className="num">Kalan ihtiyaç (saat)</th><th className="num">Planlanan (saat)</th><th className="num">Kapsam</th>
              <th>Plan başlangıcı</th><th>Tahmini bitiş</th><th>Son operasyon tamamlanma planı</th><th>Son İM</th><th className="num">Sapma (gün)</th><th className="num" title="Etkin termin − tahmini bitiş. Hedef: terminden en geç 2 gün önce (kırmızı: hedef kaçıyor)">Termine kalan (gün)</th><th>Durum</th>
            </tr>
          </thead>
          <tbody>
            {list.map((r) => (
              <tr key={r.order_id}>
                <td><b>{r.order_no}</b></td><td>{r.position_no || <span className="muted">—</span>}</td><td>{r.customer}</td>
                <td>{r.item_code} <span className="muted">{r.item_name}</span></td>
                <td className="num">{fmt(r.quantity, 0)}</td>
                <td className="num" title={r.unit_price ? `${fmt(r.unit_price, 2)} × ${fmt(r.quantity, 0)}` : "Birim fiyat yok"}>{r.revenue ? fmt(r.revenue, 0) : "—"}</td>
                <td>{r.due_date}</td>
                <td className="num">{fmt(r.required_hours)}</td><td className="num">{fmt(r.planned_hours)}</td>
                <td className="num">{fmt(r.coverage_pct, 0)}%</td>
                <td>{r.planned_start ?? "-"}</td>
                <td style={{ fontWeight: 600, color: r.plan_status === "late" ? "var(--bad)" : r.planned_end ? "var(--ok)" : undefined }}>{r.planned_end ?? "-"}</td>
                <td>{r.completion_weeks?.length ? <details><summary>{fmt(r.completion_weeks.reduce((n,w) => n+w.quantity,0), 2)} adet · {r.completion_weeks.length} hafta</summary>{r.completion_weeks.map(w => <div key={w.week_start}>{weekLong(w.week_start)} · {fmt(w.quantity,2)} adet · yaklaşık {w.planned_end}</div>)}<small>Son operasyon plan miktarıdır; gerçek stok girişi veya müşteri rezervasyonu değildir. Kısmi planda önceki operasyon eksikleri nedeniyle tamamlanma kesin değildir.</small></details> : "—"}</td>
                <td>{r.last_work_center_code || "-"}</td>
                <td className="num" style={{ color: (r.lateness_days ?? 0) > 0 ? "var(--bad)" : "var(--ok)" }}>{r.lateness_days === null ? "-" : r.lateness_days > 0 ? `+${r.lateness_days}` : r.lateness_days}</td>
                <td className="num" title={r.slack_days == null ? undefined : r.slack_days < 0 ? "Termin kaçıyor" : r.slack_days > 14 ? "Terminden çok önce biter: erken üretim / ara stok" : "Termine yakın"} style={{ color: r.slack_days == null ? undefined : r.buffer_ok === false ? "var(--bad)" : "var(--ok)" }}>
                  {r.slack_days == null ? "-" : `${r.slack_days > 0 ? "+" : ""}${r.slack_days}`}{r.target_date && r.buffer_ok === false ? <span className="muted"> (hedef {r.target_date})</span> : null}
                </td>
                <td><PlanStatusBadge s={r.plan_status} /><div className="muted">{r.material_note}</div></td>
              </tr>
            ))}
            {list.length === 0 && <tr><td colSpan={16} className="muted">{rows === null ? "Yükleniyor…" : "Gösterilecek sipariş yok."}</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}
