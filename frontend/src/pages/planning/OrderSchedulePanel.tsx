import { useState } from "react";
import { fmt, type OrderSchedule } from "../../api";

const STATUS: Record<OrderSchedule["plan_status"], [string, string, string]> = {
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
export default function OrderSchedulePanel({ rows, err, onReload }: { rows: OrderSchedule[] | null; err: string; onReload: () => void }) {
  const [filter, setFilter] = useState<"" | OrderSchedule["plan_status"]>("");
  const [sort, setSort] = useState<"due" | "end" | "late">("due");
  const list = (rows ?? []).filter((r) => !filter || r.plan_status === filter);
  list.sort((a, b) => {
    if (sort === "end") return (a.planned_end ?? "9999").localeCompare(b.planned_end ?? "9999");
    if (sort === "late") return (b.lateness_days ?? -9999) - (a.lateness_days ?? -9999);
    return a.due_date.localeCompare(b.due_date);
  });
  const count = (s: OrderSchedule["plan_status"]) => (rows ?? []).filter((r) => r.plan_status === s).length;
  const kpis: [OrderSchedule["plan_status"], string][] = [["on_time", "Termine uygun"], ["late", "Geç kalacak"], ["partial", "Kısmi"], ["unplanned", "Planlanmadı"]];

  return (
    <>
      <div className="row" style={{ marginBottom: 10 }}>
        {kpis.map(([s, l]) => (
          <button key={s} className={`kpi-btn ${filter === s ? "active" : ""} ${STATUS[s][0]}`} onClick={() => setFilter(filter === s ? "" : s)}>
            <span className="v">{count(s)}</span><span className="l">{l}</span>
          </button>
        ))}
        <label>Sırala
          <select value={sort} onChange={(e) => setSort(e.target.value as typeof sort)}>
            <option value="due">Termine göre</option>
            <option value="end">Tahmini bitişe göre</option>
            <option value="late">Gecikmeye göre</option>
          </select>
        </label>
        <button className="secondary" onClick={onReload}>↻ Yenile</button>
        <span className="muted">Bitiş tarihi, plan satırlarının yerleştiği son haftada iş merkezinin doluluk sırasına göre gün hassasiyetinde tahmin edilir.</span>
      </div>
      {err && <div className="error">{err}</div>}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Sipariş</th><th>Müşteri</th><th>Stok</th><th className="num">Miktar</th><th className="num">Ciro</th><th>Termin</th>
              <th className="num">İhtiyaç (saat)</th><th className="num">Planlanan (saat)</th><th className="num">Kapsam</th>
              <th>Plan başlangıcı</th><th>Tahmini bitiş</th><th>Son İM</th><th className="num">Sapma (gün)</th><th>Durum</th>
            </tr>
          </thead>
          <tbody>
            {list.map((r) => (
              <tr key={r.order_id}>
                <td><b>{r.order_no}</b></td><td>{r.customer}</td>
                <td>{r.item_code} <span className="muted">{r.item_name}</span></td>
                <td className="num">{fmt(r.quantity, 0)}</td>
                <td className="num" title={r.unit_price ? `${fmt(r.unit_price, 2)} × ${fmt(r.quantity, 0)}` : "Birim fiyat yok"}>{r.revenue ? fmt(r.revenue, 0) : "—"}</td>
                <td>{r.due_date}</td>
                <td className="num">{fmt(r.required_hours)}</td><td className="num">{fmt(r.planned_hours)}</td>
                <td className="num">{fmt(r.coverage_pct, 0)}%</td>
                <td>{r.planned_start ?? "-"}</td>
                <td style={{ fontWeight: 600, color: r.plan_status === "late" ? "var(--bad)" : r.planned_end ? "var(--ok)" : undefined }}>{r.planned_end ?? "-"}</td>
                <td>{r.last_work_center_code || "-"}</td>
                <td className="num" style={{ color: (r.lateness_days ?? 0) > 0 ? "var(--bad)" : "var(--ok)" }}>{r.lateness_days === null ? "-" : r.lateness_days > 0 ? `+${r.lateness_days}` : r.lateness_days}</td>
                <td><PlanStatusBadge s={r.plan_status} /></td>
              </tr>
            ))}
            {list.length === 0 && <tr><td colSpan={14} className="muted">{rows === null ? "Yükleniyor…" : "Gösterilecek sipariş yok."}</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}
