import { useMemo, useState } from "react";
import { fmt, weekLabel, weekLong, type OrderSchedule, type PlanLine, type WorkCenter } from "../../api";
import { PlanStatusBadge } from "./OrderSchedulePanel";

interface Row {
  order_id: number; order_no: string; customer: string; item_code: string; due_date: string;
  ops: number[]; weeks: string[]; hours: number; qty: number; sched?: OrderSchedule;
}

/** İş merkezi bazlı: seçilen iş merkezine planlanmış tüm siparişler (nihai ürün termini ile). */
export default function WcOrdersPanel({ lines, schedule, wcs, horizon }: { lines: PlanLine[] | null; schedule: OrderSchedule[] | null; wcs: WorkCenter[]; horizon: string }) {
  const grouped = useMemo(() => {
    const byWc = new Map<number, Map<number, Row>>();
    const qtyByOp = new Map<string, number>(); // wc|order|op -> planlanan miktar (haftalar toplamı)
    for (const l of lines ?? []) {
      if (!byWc.has(l.work_center_id)) byWc.set(l.work_center_id, new Map());
      const m = byWc.get(l.work_center_id)!;
      let r = m.get(l.order_id);
      if (!r) {
        r = { order_id: l.order_id, order_no: l.order_no, customer: l.customer, item_code: l.item_code, due_date: l.due_date, ops: [], weeks: [], hours: 0, qty: 0 };
        m.set(l.order_id, r);
      }
      if (!r.ops.includes(l.operation_seq)) r.ops.push(l.operation_seq);
      if (!r.weeks.includes(l.week_start)) r.weeks.push(l.week_start);
      r.hours += l.planned_hours;
      const k = `${l.work_center_id}|${l.order_id}|${l.operation_seq}`;
      qtyByOp.set(k, (qtyByOp.get(k) ?? 0) + l.planned_qty);
    }
    const schedMap = new Map((schedule ?? []).map((s) => [s.order_id, s]));
    const out = new Map<number, Row[]>();
    byWc.forEach((m, wcId) => {
      const rows = Array.from(m.values()).map((r) => ({
        ...r,
        ops: r.ops.sort((a, b) => a - b),
        weeks: r.weeks.sort(),
        // birden fazla operasyon aynı İM'de ise miktar toplanmaz; en yüksek operasyon miktarı gösterilir
        qty: Math.max(...r.ops.map((op) => qtyByOp.get(`${wcId}|${r.order_id}|${op}`) ?? 0), 0),
        sched: schedMap.get(r.order_id),
      }));
      rows.sort((a, b) => a.due_date.localeCompare(b.due_date) || a.order_no.localeCompare(b.order_no));
      out.set(wcId, rows);
    });
    return out;
  }, [lines, schedule]);

  const wcList = wcs.filter((w) => grouped.has(w.id) || w.is_planned);
  const [sel, setSel] = useState<number | null>(null);
  const active = sel ?? wcList.find((w) => (grouped.get(w.id)?.length ?? 0) > 0)?.id ?? wcList[0]?.id ?? null;
  const rows = active ? grouped.get(active) ?? [] : [];
  const wc = wcs.find((w) => w.id === active);
  const totalHours = rows.reduce((s, r) => s + r.hours, 0);

  return (
    <>
      <div className="wc-tabs">
        {wcList.map((w) => {
          const n = grouped.get(w.id)?.length ?? 0;
          return (
            <button key={w.id} className={`wc-tab ${active === w.id ? "active" : ""}`} onClick={() => setSel(w.id)} title={w.name}>
              <b>{w.code}</b> <span className="muted">{w.name}</span> <span className={`badge ${n ? "ok" : "muted"}`}>{n}</span>
            </button>
          );
        })}
        {wcList.length === 0 && <span className="muted">Planlanan iş merkezi yok.</span>}
      </div>
      {wc && (
        <>
          <div className="row" style={{ margin: "8px 0" }}>
            <span><b>{wc.code}</b> — {wc.name}: <b>{rows.length}</b> sipariş, toplam <b>{fmt(totalHours)}</b> saat ({fmt(totalHours / (wc.capacity_unit_hours || 1))} birim) · ufuk: {horizon}</span>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Sipariş</th><th>Müşteri</th><th>Stok</th><th className="num">Sipariş miktarı</th><th>Op.</th>
                  <th>Plan haftaları</th><th className="num">Bu İM'de saat</th><th className="num">Bu İM'de miktar</th>
                  <th>Nihai ürün termini</th><th>Tahmini bitiş (tümü)</th><th>Durum</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.order_id}>
                    <td><b>{r.order_no}</b></td><td>{r.customer}</td>
                    <td>{r.item_code} <span className="muted">{r.sched?.item_name}</span></td>
                    <td className="num">{fmt(r.sched?.quantity, 0)}</td>
                    <td>{r.ops.join(", ")}</td>
                    <td title={r.weeks.join(", ")}>{r.weeks.length === 1 ? weekLong(r.weeks[0]) : `${weekLabel(r.weeks[0])} → ${weekLabel(r.weeks[r.weeks.length - 1])} (${r.weeks.length} hafta)`}</td>
                    <td className="num">{fmt(r.hours, 2)}</td><td className="num">{fmt(r.qty, 0)}</td>
                    <td style={{ fontWeight: 600 }}>{r.due_date}</td>
                    <td style={{ color: r.sched?.plan_status === "late" ? "var(--bad)" : undefined }}>{r.sched?.planned_end ?? "-"}</td>
                    <td>{r.sched ? <PlanStatusBadge s={r.sched.plan_status} /> : "-"}</td>
                  </tr>
                ))}
                {rows.length === 0 && <tr><td colSpan={11} className="muted">Bu iş merkezine plan ufku içinde yerleştirilmiş sipariş yok.</td></tr>}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}
