import { useState } from "react";
import { api, fmt, mondayOf, qs, type Progress } from "../api";
import { ErrorText, StatusBadge, WcMultiSelect, useAsync, useWorkCenters } from "../components";

interface DayRow { day: string; expected_cum: number; actual_day: number; actual_cum: number }

export default function ProgressPage() {
  const { wcs } = useWorkCenters();
  const [week, setWeek] = useState(mondayOf(new Date()));
  const [asOf, setAsOf] = useState(new Date().toISOString().slice(0, 10));
  const [wcIds, setWcIds] = useState<number[]>([]);
  const [sel, setSel] = useState<number | null>(null);
  const prog = useAsync(() => api.get<Progress[]>(`/api/progress${qs({ week, as_of: asOf, work_center_ids: wcIds })}`), [week, asOf, wcIds.join(",")]);
  const daily = useAsync(() => (sel ? api.get<DayRow[]>(`/api/progress/daily${qs({ week, work_center_id: sel })}`) : Promise.resolve(null)), [sel, week]);

  return (
    <>
      <h1>Günlük İlerleme</h1>
      <div className="panel row">
        <label>Hafta<input type="date" value={week} onChange={(e) => setWeek(mondayOf(new Date(e.target.value)))} /></label>
        <label>Bugün (as-of)<input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} /></label>
        <WcMultiSelect wcs={wcs} value={wcIds} onChange={setWcIds} />
        <span className="muted">Üretim verisi “bir önceki gün” import edildiği için as-of tarihinden önceki çalışma günleri geçmiş sayılır.</span>
      </div>
      <ErrorText err={prog.err} />
      <div className="table-wrap">
        <table>
          <thead><tr><th>İş Merkezi</th><th className="num">Planlanan</th><th className="num">Beklenen (bugüne)</th><th className="num">Gerçekleşen</th><th className="num">Fark</th><th className="num">Kalan saat</th><th className="num">Kalan gün</th><th>Gün</th><th>Durum</th></tr></thead>
          <tbody>
            {prog.data?.map((p) => (
              <tr key={p.work_center_id} onClick={() => setSel(p.work_center_id)} style={{ cursor: "pointer", background: sel === p.work_center_id ? "#e3f2fd" : undefined }}>
                <td><b>{p.work_center_code}</b></td>
                <td className="num">{fmt(p.planned_hours)}</td>
                <td className="num">{fmt(p.expected_hours_to_date)}</td>
                <td className="num">{fmt(p.actual_hours_to_date)}</td>
                <td className="num" style={{ color: p.actual_hours_to_date - p.expected_hours_to_date < 0 ? "var(--bad)" : "var(--ok)" }}>{fmt(p.actual_hours_to_date - p.expected_hours_to_date)}</td>
                <td className="num">{fmt(p.remaining_hours)}</td>
                <td className="num">{fmt(p.remaining_days)}</td>
                <td>{p.elapsed_days}/{p.working_days}</td>
                <td><StatusBadge s={p.status} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {sel && daily.data && (
        <div className="panel">
          <h2 style={{ marginTop: 0 }}>Gün gün kümülatif — {wcs.find((w) => w.id === sel)?.code}</h2>
          <table style={{ width: "auto" }}>
            <thead><tr><th>Gün</th><th className="num">Beklenen (küm.)</th><th className="num">Gerçekleşen (gün)</th><th className="num">Gerçekleşen (küm.)</th><th className="num">Fark</th></tr></thead>
            <tbody>{daily.data.map((d) => <tr key={d.day}><td>{d.day}</td><td className="num">{fmt(d.expected_cum)}</td><td className="num">{fmt(d.actual_day)}</td><td className="num">{fmt(d.actual_cum)}</td><td className="num" style={{ color: d.actual_cum - d.expected_cum < 0 ? "var(--bad)" : "var(--ok)" }}>{fmt(d.actual_cum - d.expected_cum)}</td></tr>)}</tbody>
          </table>
        </div>
      )}
    </>
  );
}
