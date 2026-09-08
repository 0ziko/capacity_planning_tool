import { useState } from "react";
import { api, fmt, qs, type OrderAnalysis } from "../../api";
import { ErrorText, useAsync } from "../../components";

const PERIODS: { id: string; label: string }[] = [
  { id: "1m", label: "1 Ay" },
  { id: "3m", label: "3 Ay" },
  { id: "6m", label: "6 Ay" },
  { id: "1y", label: "1 Yıl" },
];

const MARKET_LABEL: Record<string, string> = { domestic: "Yerli", export: "Yurtdışı" };

export default function RevenueAnalysis() {
  const [period, setPeriod] = useState("3m");
  const [market, setMarket] = useState("");
  const analysis = useAsync(
    () => api.get<OrderAnalysis>(`/api/orders/analysis${qs({ status: "open", period, market: market || undefined })}`),
    [period, market]
  );

  const pareto = analysis.data?.pareto ?? [];
  const total = analysis.data?.total_revenue ?? 0;
  const top = pareto.slice(0, 14);
  const rest = pareto.slice(14);
  const merged = rest.length
    ? [...top, { customer: `Diğer (${rest.length} müşteri)`, revenue: rest.reduce((s, r) => s + r.revenue, 0), pct: rest.reduce((s, r) => s + r.pct, 0), cum_pct: 100, order_count: rest.reduce((s, r) => s + r.order_count, 0) }]
    : top;
  let cum = 0;
  const chartData = merged.map((d) => {
    cum += d.revenue;
    return { ...d, cum_pct: total > 0 ? (cum / total) * 100 : 0 };
  });

  return (
    <>
      <div className="panel row">
        <span className="muted" style={{ alignSelf: "center" }}>Termin aralığı (plan/revize termin):</span>
        {PERIODS.map((p) => (
          <button key={p.id} className={period === p.id ? "" : "secondary"} onClick={() => setPeriod(p.id)}>{p.label}</button>
        ))}
        <label style={{ marginLeft: 8 }}>Pazar
          <select value={market} onChange={(e) => setMarket(e.target.value)}>
            <option value="">Tümü</option>
            <option value="domestic">Yerli</option>
            <option value="export">Yurtdışı</option>
          </select>
        </label>
        {analysis.data?.due_from && analysis.data?.due_to && (
          <span className="muted" style={{ alignSelf: "center" }}>{analysis.data.due_from} → {analysis.data.due_to}</span>
        )}
      </div>
      <ErrorText err={analysis.err} />
      {analysis.data && (
        <>
          <div className="row" style={{ marginBottom: 12 }}>
            <div className="kpi"><span className="v">{fmt(analysis.data.total_revenue, 0)}</span><span className="l">Toplam ciro</span></div>
            <div className="kpi"><span className="v">{fmt(analysis.data.domestic_revenue, 0)}</span><span className="l">Yerli</span></div>
            <div className="kpi"><span className="v">{fmt(analysis.data.export_revenue, 0)}</span><span className="l">Yurtdışı</span></div>
            <div className="kpi"><span className="v">{pareto.length}</span><span className="l">Müşteri</span></div>
          </div>
          <div className="panel">
            <h2 style={{ marginTop: 0 }}>Pareto — müşteri × ciro</h2>
            <p className="muted" style={{ marginTop: -6 }}>Sütunlar müşteri cirosu; kırmızı çizgi kümülatif pay (%). Açık siparişler, seçili termin penceresinde.</p>
            {chartData.length > 0 ? <ParetoChart data={chartData} total={total} /> : <p className="muted">Seçili dönemde sipariş yok.</p>}
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>#</th><th>Müşteri</th><th className="num">Sipariş</th><th className="num">Ciro</th><th className="num">Pay %</th><th className="num">Küm. %</th>
                </tr>
              </thead>
              <tbody>
                {pareto.map((r, i) => (
                  <tr key={r.customer}>
                    <td>{i + 1}</td>
                    <td>{r.customer}</td>
                    <td className="num">{r.order_count}</td>
                    <td className="num">{fmt(r.revenue, 0)}</td>
                    <td className="num">{fmt(r.pct, 1)}</td>
                    <td className="num"><b>{fmt(r.cum_pct, 1)}</b></td>
                  </tr>
                ))}
                {pareto.length === 0 && <tr><td colSpan={6} className="muted">Veri yok.</td></tr>}
                {pareto.length > 0 && (
                  <tr style={{ fontWeight: 700, background: "#f5f7fa" }}>
                    <td colSpan={2}>Alt toplam</td>
                    <td className="num">{pareto.reduce((s, r) => s + r.order_count, 0)}</td>
                    <td className="num">{fmt(analysis.data.total_revenue, 0)}</td>
                    <td className="num">100</td>
                    <td className="num">100</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <p className="muted">Yerli / yurtdışı kırılımı müşteri bazında planlama ekranından veya sipariş pazar alanından gelir. {MARKET_LABEL.domestic} + {MARKET_LABEL.export} = toplam.</p>
        </>
      )}
    </>
  );
}

function trunc(s: string, n: number) {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

function ParetoChart({ data, total }: { data: { customer: string; revenue: number; cum_pct: number }[]; total: number }) {
  const W = 900;
  const H = 320;
  const padL = 56;
  const padR = 48;
  const padT = 24;
  const padB = 72;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;
  const maxRev = Math.max(...data.map((d) => d.revenue), 1);
  const n = data.length;
  const barW = Math.min((innerW / Math.max(n, 1)) * 0.65, 48);
  const gap = innerW / Math.max(n, 1);

  const points = data.map((d, i) => {
    const x = padL + gap * i + gap / 2;
    const barH = (d.revenue / maxRev) * innerH;
    return { x, barH, yBar: padT + innerH - barH, cumPct: d.cum_pct, label: d.customer, revenue: d.revenue };
  });

  const linePath = points.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${padT + innerH - (p.cumPct / 100) * innerH}`).join(" ");

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", maxHeight: 360 }} role="img" aria-label="Müşteri ciro Pareto grafiği">
      {[0, 25, 50, 75, 100].map((pct) => {
        const y = padT + innerH - (pct / 100) * innerH;
        return (
          <g key={pct}>
            <line x1={padL} y1={y} x2={W - padR} y2={y} stroke="#e8ecf0" strokeWidth={1} />
            <text x={padR - 4} y={y + 4} textAnchor="end" fontSize={10} fill="#6b7a8c">{pct}%</text>
          </g>
        );
      })}
      {points.map((p, i) => (
        <g key={i}>
          <rect x={p.x - barW / 2} y={p.yBar} width={barW} height={p.barH} fill="var(--primary-2)" opacity={0.85} rx={2} />
          <text x={p.x} y={H - padB + 14} textAnchor="end" fontSize={9} fill="#1f2933" transform={`rotate(-35, ${p.x}, ${H - padB + 14})`}>{trunc(p.label, 18)}</text>
          <title>{`${p.label}: ${fmt(p.revenue, 0)} (${fmt(p.cumPct, 1)}% küm.)`}</title>
        </g>
      ))}
      <path d={linePath} fill="none" stroke="var(--bad)" strokeWidth={2} />
      {points.map((p, i) => (
        <circle key={`c${i}`} cx={p.x} cy={padT + innerH - (p.cumPct / 100) * innerH} r={3} fill="var(--bad)" />
      ))}
      <text x={padL} y={16} fontSize={11} fill="#6b7a8c">Ciro (sol eksen, sütun)</text>
      <text x={W - padR} y={16} textAnchor="end" fontSize={11} fill="var(--bad)">Kümülatif % (çizgi)</text>
    </svg>
  );
}
