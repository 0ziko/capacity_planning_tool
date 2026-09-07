import { Fragment, useState } from "react";
import { api, fmt, mondayOf, qs, weekLabel, type Progress, type WorkCenterLoad } from "../api";
import { Bar, ErrorText, StatusBadge, UtilBadge, useAsync, WeekPicker } from "../components";

export default function Dashboard() {
  const [week, setWeek] = useState(mondayOf(new Date()));
  const load = useAsync(() => api.get<WorkCenterLoad[]>(`/api/plan/load${qs({ start: week, weeks: 4 })}`), [week]);
  const prog = useAsync(() => api.get<Progress[]>(`/api/progress${qs({ week })}`), [week]);

  const totalCap = load.data?.reduce((s, w) => s + (w.weeks[0]?.capacity_hours ?? 0), 0) ?? 0;
  const totalPlan = load.data?.reduce((s, w) => s + (w.weeks[0]?.planned_hours ?? 0), 0) ?? 0;
  const planned = load.data?.filter((w) => w.weeks.some((x) => x.planned_hours > 0)).length ?? 0;
  const weekCols = load.data?.[0]?.weeks ?? [];

  return (
    <>
      <h1>Özet</h1>
      <div className="panel row">
        <WeekPicker value={week} onChange={setWeek} />
      </div>
      <div className="grid">
        <div className="panel kpi"><span className="v">{fmt(totalCap, 0)} saat</span><span className="l">Bu hafta toplam verimli kapasite</span></div>
        <div className="panel kpi"><span className="v">{fmt(totalPlan, 0)} saat</span><span className="l">Bu hafta planlanan iş gücü</span></div>
        <div className="panel kpi"><span className="v">{totalCap ? Math.round((totalPlan / totalCap) * 100) : 0}%</span><span className="l">Doluluk</span></div>
        <div className="panel kpi"><span className="v">{planned} / {load.data?.length ?? 0}</span><span className="l">Planı olan iş merkezi</span></div>
      </div>

      <h2>İş merkezi bazında 4 haftalık yük</h2>
      <ErrorText err={load.err} />
      <div className="table-wrap">
        <table className="dash-load">
          <thead>
            <tr>
              <th rowSpan={2}>İş Merkezi</th>
              {weekCols.map((w) => (
                <th key={w.week_start} colSpan={2} className="dash-week-h" title={`Hafta başlangıcı (Pzt): ${w.week_start}`}>
                  {weekLabel(w.week_start, true)}
                </th>
              ))}
            </tr>
            <tr>
              {weekCols.map((w) => (
                <Fragment key={`${w.week_start}-sub`}>
                  <th className="num muted dash-sub-h">Saat</th>
                  <th className="muted dash-sub-h">Doluluk</th>
                </Fragment>
              ))}
            </tr>
          </thead>
          <tbody>
            {load.data?.map((wc) => (
              <tr key={wc.work_center_id}>
                <td><b>{wc.work_center_code}</b></td>
                {wc.weeks.map((w) => (
                  <WeekCells key={w.week_start} w={w} />
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>Bu haftanın ilerlemesi</h2>
      <ErrorText err={prog.err} />
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>İş Merkezi</th><th className="num">Planlanan</th><th className="num">Beklenen (bugüne)</th><th className="num">Gerçekleşen</th><th className="num">Kalan saat</th><th className="num">Kalan gün</th><th>Durum</th></tr>
          </thead>
          <tbody>
            {prog.data?.map((p) => (
              <tr key={p.work_center_id}>
                <td><b>{p.work_center_code}</b></td>
                <td className="num">{fmt(p.planned_hours)}</td>
                <td className="num">{fmt(p.expected_hours_to_date)}</td>
                <td className="num">{fmt(p.actual_hours_to_date)}</td>
                <td className="num">{fmt(p.remaining_hours)}</td>
                <td className="num">{fmt(p.remaining_days)}</td>
                <td><StatusBadge s={p.status} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function WeekCells({ w }: { w: { capacity_hours: number; planned_hours: number; utilization: number; planned_units: number; capacity_units: number } }) {
  return (
    <>
      <td className="num dash-hours" title={`${fmt(w.planned_units)} / ${fmt(w.capacity_units)} birim`}>
        {fmt(w.planned_hours, 0)} / {fmt(w.capacity_hours, 0)} sa
      </td>
      <td className="dash-bar">
        <Bar ratio={w.utilization} /> <UtilBadge u={w.utilization} />
      </td>
    </>
  );
}
