import { useState } from "react";
import { addDays, api, fmt, mondayOf, qs } from "../api";
import { ErrorText, WcMultiSelect, useAsync, useWorkCenters } from "../components";

interface DowntimeOut {
  totals: { work_center_code: string; expected_minutes: number; actual_minutes: number; excess_minutes: number }[];
  daily: { work_center_code: string; day: string; expected_minutes: number; actual_minutes: number; excess_minutes: number }[];
  reasons: { work_center_code: string; reason_code: string; reason_desc: string; minutes: number; count: number; share_pct: number; excess_attributed_minutes: number }[];
}
interface CtOut {
  rows: { item_code: string; product_group: string; work_center_code: string; operation_seq: number; defined_ct_sec: number; observed_median_ct_sec: number; observed_min_ct_sec: number; observed_max_ct_sec: number; samples: number; deviation_pct: number; suggested_ct_sec: number | null; status: string }[];
  groups: { product_group: string; work_center_code: string; items: number; samples: number; avg_deviation_pct: number }[];
}

export default function Analysis() {
  const { wcs } = useWorkCenters();
  const [start, setStart] = useState(mondayOf(new Date()));
  const [end, setEnd] = useState(addDays(mondayOf(new Date()), 6));
  const [wcIds, setWcIds] = useState<number[]>([]);
  const [minSamples, setMinSamples] = useState(10);
  const [threshold, setThreshold] = useState(10);
  const dt = useAsync(() => api.get<DowntimeOut>(`/api/analysis/downtime${qs({ start, end, work_center_ids: wcIds })}`), [start, end, wcIds.join(",")]);
  const ct = useAsync(() => api.get<CtOut>(`/api/analysis/cycletime${qs({ start, end, work_center_ids: wcIds, min_samples: minSamples, threshold_pct: threshold })}`), [start, end, wcIds.join(","), minSamples, threshold]);

  return (
    <>
      <h1>Duruş Analizi & Çevrim Süresi Önerileri</h1>
      <div className="panel row">
        <label>Başlangıç<input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
        <label>Bitiş<input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
        <WcMultiSelect wcs={wcs} value={wcIds} onChange={setWcIds} />
      </div>

      <h2>Duruşlar — beklenen (nominal − verimli) vs gerçekleşen <button className="secondary small" onClick={() => api.download(`/api/analysis/downtime.xlsx${qs({ start, end, work_center_ids: wcIds })}`)}>⬇ Excel raporu</button></h2>
      <ErrorText err={dt.err} />
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1.2fr", gap: 14 }}>
        <div className="table-wrap">
          <table>
            <thead><tr><th>İş Merkezi</th><th className="num">Beklenen (dk)</th><th className="num">Gerçekleşen (dk)</th><th className="num">Fazla (dk)</th></tr></thead>
            <tbody>{dt.data?.totals.map((t) => <tr key={t.work_center_code}><td><b>{t.work_center_code}</b></td><td className="num">{fmt(t.expected_minutes, 0)}</td><td className="num">{fmt(t.actual_minutes, 0)}</td><td className="num" style={{ color: t.excess_minutes > 0 ? "var(--bad)" : "var(--ok)" }}>{fmt(t.excess_minutes, 0)}</td></tr>)}</tbody>
          </table>
        </div>
        <div className="table-wrap">
          <table>
            <thead><tr><th>İş Merkezi</th><th>Sebep</th><th className="num">Süre (dk)</th><th className="num">Adet</th><th className="num">Pay</th><th className="num">Fazla duruş payı (dk)</th></tr></thead>
            <tbody>{dt.data?.reasons.map((r, i) => <tr key={i}><td>{r.work_center_code}</td><td><b>{r.reason_code}</b> {r.reason_desc}</td><td className="num">{fmt(r.minutes, 0)}</td><td className="num">{r.count}</td><td className="num">{fmt(r.share_pct)}%</td><td className="num">{fmt(r.excess_attributed_minutes, 0)}</td></tr>)}</tbody>
          </table>
        </div>
      </div>

      <h2>Çevrim süresi önerileri <button className="secondary small" onClick={() => api.download(`/api/analysis/cycletime.xlsx${qs({ start, end, work_center_ids: wcIds, min_samples: minSamples, threshold_pct: threshold })}`)}>⬇ Excel raporu</button></h2>
      <div className="panel row">
        <label>Min. örnek sayısı<input type="number" value={minSamples} onChange={(e) => setMinSamples(Number(e.target.value))} /></label>
        <label>Sapma eşiği (%)<input type="number" value={threshold} onChange={(e) => setThreshold(Number(e.target.value))} /></label>
        <span className="muted">Etkin çevrim süresi: fiili süre varsa fiili süre / miktar; yoksa günün (verimli kapasite − fazla duruş) süresinin üretimlere kazanılan saat oranıyla dağıtımı.</span>
      </div>
      <ErrorText err={ct.err} />
      <div className="table-wrap">
        <table>
          <thead><tr><th>Stok</th><th>Grup</th><th>İş Merkezi</th><th>Op.</th><th className="num">Tanımlı CT</th><th className="num">Gözlenen medyan</th><th className="num">Min–Max</th><th className="num">Örnek</th><th className="num">Sapma</th><th className="num">Öneri</th><th>Durum</th></tr></thead>
          <tbody>
            {ct.data?.rows.map((r, i) => (
              <tr key={i}>
                <td><b>{r.item_code}</b></td><td>{r.product_group}</td><td>{r.work_center_code}</td><td>{r.operation_seq}</td>
                <td className="num">{fmt(r.defined_ct_sec)} sn</td><td className="num">{fmt(r.observed_median_ct_sec)} sn</td><td className="num">{fmt(r.observed_min_ct_sec, 0)}–{fmt(r.observed_max_ct_sec, 0)}</td>
                <td className="num">{r.samples}</td>
                <td className="num" style={{ color: Math.abs(r.deviation_pct) > threshold ? "var(--bad)" : undefined }}>{r.deviation_pct > 0 ? "+" : ""}{fmt(r.deviation_pct)}%</td>
                <td className="num">{r.suggested_ct_sec !== null ? <b>{fmt(r.suggested_ct_sec)} sn</b> : "—"}</td>
                <td><span className={`badge ${r.status === "oneri var" ? "warn" : r.status === "uyumlu" ? "ok" : "muted"}`}>{r.status}</span></td>
              </tr>
            ))}
            {ct.data?.rows.length === 0 && <tr><td colSpan={11} className="muted">Seçilen aralıkta üretim verisi yok.</td></tr>}
          </tbody>
        </table>
      </div>
      {ct.data && ct.data.groups.length > 0 && (
        <>
          <h2>Ürün grubu / iş merkezi özeti</h2>
          <table style={{ width: "auto" }}>
            <thead><tr><th>Ürün Grubu</th><th>İş Merkezi</th><th className="num">Stok</th><th className="num">Örnek</th><th className="num">Ort. sapma</th></tr></thead>
            <tbody>{ct.data.groups.map((g, i) => <tr key={i}><td>{g.product_group}</td><td>{g.work_center_code}</td><td className="num">{g.items}</td><td className="num">{g.samples}</td><td className="num">{fmt(g.avg_deviation_pct)}%</td></tr>)}</tbody>
          </table>
        </>
      )}
    </>
  );
}
