import { useState } from "react";
import { api, fmt, qs, shortDate, type GanttData, type WorkCenter } from "../../api";
import { ErrorText, useAsync } from "../../components";

const STATUS: Record<string, string> = { planned: "Planlandı", in_progress: "Devam", completed: "Tamam" };

export default function GanttPanel({
  wcs,
  defaultStart,
  defaultEnd,
}: {
  wcs: WorkCenter[];
  defaultStart: string;
  defaultEnd: string;
}) {
  const planned = wcs.filter((w) => w.is_planned);
  const [wcId, setWcId] = useState(planned[0]?.id ?? 0);
  const [start, setStart] = useState(defaultStart);
  const [end, setEnd] = useState(defaultEnd);
  const [asOf, setAsOf] = useState(new Date().toISOString().slice(0, 10));

  const data = useAsync(
    () =>
      wcId
        ? api.get<GanttData>(`/api/plan/gantt${qs({ work_center_id: wcId, start, end, as_of: asOf })}`)
        : Promise.resolve(null),
    [wcId, start, end, asOf],
  );

  const g = data.data;
  const days = g?.timeline_days ?? [];
  const dayIndex = (iso: string) => days.indexOf(iso);

  const pos = (from: string, to: string) => {
    if (!days.length) return { left: 0, width: 0 };
    const si = Math.max(dayIndex(from), 0);
    const ei = dayIndex(to);
    const e = ei >= 0 ? ei : days.length - 1;
    const span = days.length;
    const left = (si / span) * 100;
    const width = ((e - si + 1) / span) * 100;
    return { left, width: Math.max(width, 2) };
  };

  return (
    <>
      <p className="muted" style={{ marginTop: -6 }}>
        Seçilen iş merkezinde planlanan operasyon / yarımamül çubukları. Yeşil bant = üretim beyanı ile tamamlanan kısım; kalan miktar etikette gösterilir.
        Günlük ilerleme import edildikçe Gantt otomatik güncellenir.
      </p>
      <div className="panel row" style={{ alignItems: "flex-end" }}>
        <label>İş merkezi
          <select value={wcId} onChange={(e) => setWcId(Number(e.target.value))}>
            {planned.map((w) => <option key={w.id} value={w.id}>{w.code}</option>)}
          </select>
        </label>
        <label>Başlangıç<input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
        <label>Bitiş<input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
        <label>Üretim verisi (as-of)<input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} /></label>
        <button className="secondary" onClick={() => data.reload()}>Yenile</button>
      </div>
      <ErrorText err={data.err} />
      {g && (
        <>
          <div className="panel gantt-wrap">
            <div className="gantt-header">
              <div className="gantt-label-col">Sipariş / Yarımamül</div>
              <div className="gantt-timeline">
                {days.map((d) => (
                  <div key={d} className="gantt-day" title={d}>{shortDate(d)}</div>
                ))}
              </div>
            </div>
            {g.bars.length === 0 && <p className="muted" style={{ padding: 12 }}>Bu aralıkta plan satırı yok.</p>}
            {g.bars.map((b) => {
              const { left, width } = pos(b.planned_start, b.planned_end);
              const pct = b.planned_qty > 0 ? Math.min(b.produced_qty / b.planned_qty, 1) : 0;
              return (
                <div key={b.plan_line_id} className="gantt-row">
                  <div className="gantt-label-col" title={`${b.operation_name} · termin ${b.due_date}`}>
                    <b>{b.order_no}</b>
                    <div className="muted" style={{ fontSize: 11 }}>{b.semi_finished_code || b.operation_name}</div>
                    <div style={{ fontSize: 11 }}>{fmt(b.produced_qty, 0)}/{fmt(b.planned_qty, 0)} ad · kalan {fmt(b.remaining_qty, 0)}</div>
                  </div>
                  <div className="gantt-track">
                    <div
                      className={`gantt-bar ${b.status}`}
                      style={{ left: `${left}%`, width: `${width}%` }}
                      title={`Plan: ${b.planned_start} → ${b.planned_end} · ${STATUS[b.status] ?? b.status}`}
                    >
                      <span className="gantt-done" style={{ width: `${pct * 100}%` }} />
                      <span className="gantt-bar-text">{b.semi_finished_code || `Op ${b.operation_seq}`}</span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Sipariş</th><th>Yarımamül</th><th>Operasyon</th><th>Başlangıç</th><th>Bitiş</th>
                  <th className="num">Plan adet</th><th className="num">Üretilen</th><th className="num">Kalan</th><th>Durum</th>
                </tr>
              </thead>
              <tbody>
                {g.bars.map((b) => (
                  <tr key={b.plan_line_id}>
                    <td><b>{b.order_no}</b><div className="muted">{b.item_code}</div></td>
                    <td><code>{b.semi_finished_code || "—"}</code></td>
                    <td>{b.operation_name}</td>
                    <td>{b.planned_start}</td><td>{b.planned_end}</td>
                    <td className="num">{fmt(b.planned_qty, 0)}</td>
                    <td className="num">{fmt(b.produced_qty, 0)}</td>
                    <td className="num" style={{ color: b.remaining_qty > 0 ? "var(--warn)" : "var(--ok)" }}>{fmt(b.remaining_qty, 0)}</td>
                    <td><span className={`badge ${b.status === "completed" ? "ok" : b.status === "in_progress" ? "warn" : "muted"}`}>{STATUS[b.status] ?? b.status}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}
