import { useState } from "react";
import { api, fmt, qs, weekLong, type PeriodRevenue, type RevenueReport } from "../../api";
import { ErrorText, useAsync } from "../../components";

const MONTHS = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"];
export const monthLabel = (p: string) => { const [y, m] = p.split("-"); return `${MONTHS[Number(m) - 1]} ${y}`; };

/** Haftalık / aylık ciro tablosu (tek senaryo). */
export function RevenueTable({ rows, granularity, showCumulative = true }: { rows: PeriodRevenue[]; granularity: "week" | "month"; showCumulative?: boolean }) {
  const max = Math.max(1, ...rows.map((r) => Math.max(r.completed_revenue, r.earned_revenue)));
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>{granularity === "week" ? "Hafta" : "Ay"}</th>
            <th className="num" title="Son operasyonu bu dönemde biten siparişlerin tüm cirosu (teslim / fatura mantığı)">Tamamlanan ciro</th>
            <th className="num">Sipariş</th>
            <th className="num" title="Her plan satırı, sipariş cirosunun planlanan saat / gereken saat payını kendi dönemine yazar (oransal ilerleme)">Oransal ciro</th>
            <th style={{ minWidth: 160 }}></th>
            {showCumulative && <><th className="num">Kümülatif tamamlanan</th><th className="num">Kümülatif oransal</th></>}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.period}>
              <td title={r.period}><b>{granularity === "week" ? weekLong(r.period) : monthLabel(r.period)}</b></td>
              <td className="num" style={{ fontWeight: 600 }}>{r.completed_revenue ? fmt(r.completed_revenue, 0) : "—"}</td>
              <td className="num">{r.completed_orders || "—"}</td>
              <td className="num">{r.earned_revenue ? fmt(r.earned_revenue, 0) : "—"}</td>
              <td>
                <div className="rev-bars">
                  <div className="rev-bar c" style={{ width: `${(r.completed_revenue / max) * 100}%` }} title={`Tamamlanan ${fmt(r.completed_revenue, 0)}`} />
                  <div className="rev-bar e" style={{ width: `${(r.earned_revenue / max) * 100}%` }} title={`Oransal ${fmt(r.earned_revenue, 0)}`} />
                </div>
              </td>
              {showCumulative && <><td className="num">{fmt(r.cumulative_completed, 0)}</td><td className="num">{fmt(r.cumulative_earned, 0)}</td></>}
            </tr>
          ))}
          {rows.length === 0 && <tr><td colSpan={7} className="muted">Veri yok.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

export function RevenueKpis({ r }: { r: RevenueReport }) {
  const pct = (v: number) => (r.total_open_revenue > 0 ? ` (${fmt((v / r.total_open_revenue) * 100, 0)}%)` : "");
  return (
    <div className="row" style={{ marginBottom: 10 }}>
      <div className="panel kpi"><span className="v">{fmt(r.total_open_revenue, 0)}</span><span className="l">Açık sipariş cirosu (toplam)</span></div>
      <div className="panel kpi ok"><span className="v">{fmt(r.planned_revenue, 0)}</span><span className="l">Ufuk içinde tamamlanan{pct(r.planned_revenue)}</span></div>
      <div className="panel kpi warn"><span className="v">{fmt(r.partial_revenue, 0)}</span><span className="l">Kısmen planlanan (ufka sığmadı){pct(r.partial_revenue)}</span></div>
      <div className="panel kpi bad"><span className="v">{fmt(r.unplanned_revenue, 0)}</span><span className="l">Planlanmayan{pct(r.unplanned_revenue)}</span></div>
      {r.no_price_orders > 0 && <div className="panel kpi"><span className="v" style={{ color: "var(--warn)" }}>{r.no_price_orders}</span><span className="l">Birim fiyatı olmayan sipariş</span></div>}
    </div>
  );
}

/** Mevcut (kayıtlı) plana göre haftalık / aylık ciro. */
export default function RevenuePanel({ start, weeks, wcIds }: { start: string; weeks: number; wcIds: number[] }) {
  const [gran, setGran] = useState<"week" | "month">("week");
  const rep = useAsync(() => api.get<RevenueReport>(`/api/plan/revenue${qs({ start, weeks, work_center_ids: wcIds })}`), [start, weeks, wcIds.join(",")]);
  return (
    <>
      <div className="row" style={{ marginBottom: 10 }}>
        <div className="seg">
          <button className={gran === "week" ? "active" : ""} onClick={() => setGran("week")}>Haftalık</button>
          <button className={gran === "month" ? "active" : ""} onClick={() => setGran("month")}>Aylık</button>
        </div>
        <button className="secondary" onClick={rep.reload}>↻ Yenile</button>
        <span className="muted">Mevcut plan satırlarına göre. <b>Tamamlanan</b>: siparişin son operasyonu bittiği dönemde faturalanır varsayımı. <b>Oransal</b>: planlanan saat payına göre dönemlere dağıtılmış ciro.</span>
      </div>
      <ErrorText err={rep.err} />
      {rep.data && (
        <>
          <RevenueKpis r={rep.data} />
          <RevenueTable rows={gran === "week" ? rep.data.weeks : rep.data.months} granularity={gran} />
        </>
      )}
    </>
  );
}
