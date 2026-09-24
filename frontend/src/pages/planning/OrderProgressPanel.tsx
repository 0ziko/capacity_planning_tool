import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmt, qs, type OrderProgress } from "../../api";
import { Bar, ErrorText } from "../../components";

const STATUS: Record<OrderProgress["status"], [string, string]> = {
  not_started: ["muted", "Başlamadı"],
  in_progress: ["warn", "Devam ediyor"],
  completed: ["ok", "Tamamlandı"],
};

/** Günlük üretim verisine göre sipariş / iş emri ilerlemesi; hesaplama butonla tetiklenir. */
export default function OrderProgressPanel({ wcIds }: { wcIds: number[] }) {
  const [asOf, setAsOf] = useState(new Date().toISOString().slice(0, 10));
  const [rows, setRows] = useState<OrderProgress[] | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const [filter, setFilter] = useState<"" | OrderProgress["status"]>("");
  const [ranAt, setRanAt] = useState("");

  const run = async () => {
    setBusy(true); setErr("");
    try {
      setRows(await api.get<OrderProgress[]>(`/api/progress/orders${qs({ as_of: asOf, work_center_ids: wcIds })}`));
      setRanAt(new Date().toLocaleTimeString("tr-TR"));
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const mes = rows?.some(r => r.production_source === "mes") ?? false;
  const list = (rows ?? []).filter((r) => !filter || r.status === filter);
  const count = (s: OrderProgress["status"]) => (rows ?? []).filter((r) => r.status === s).length;
  const tot = (rows ?? []).reduce((a, r) => ({ req: a.req + r.required_hours, earned: a.earned + r.earned_hours }), { req: 0, earned: 0 });

  return (
    <>
      <div className="row" style={{ marginBottom: 10 }}>
        <label>Rapor tarihi (dahil)<input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} /></label>
        <button onClick={run} disabled={busy}>▶ İlerlemeyi hesapla</button>
        <button className="secondary" onClick={() => api.download(`/api/progress/orders.xlsx${qs({ as_of: asOf, work_center_ids: wcIds })}`)} disabled={!rows}>⬇ Excel</button>
        <Link to="/imports" className="secondary" style={{ padding: "6px 10px", border: "1px solid var(--border)", borderRadius: 6, textDecoration: "none" }}>Günlük üretim yükle →</Link>
        {ranAt && <span className="muted">Son hesaplama: {ranAt}</span>}
      </div>
      <p className="muted" style={{ marginTop: 0 }}>
        {mes ? <>MES üretimi güncel planla eşleşir; bu eşleşme müşteri rezervasyonu değildir. Müşteri karşılaması <Link to="/stock">Stok &amp; Rezervasyon</Link> içindeki güncel rezervasyon ve sevkiyattan gelir. Rapor tarihi MES kayıtlarını sınırlar; rezervasyon ve sevkiyatın bugünkü durumu gösterilir. Parti üyelerinin planlama payı, stok başka müşteriye ayrılsa da korunur.</> : <>Eski üretim kaynağı etkin. Üretim sipariş no, stok kodu ve operasyonla eşleştirilir; sipariş numarası boşsa termin sırasıyla dağıtılır. İlerleme dar boğaz operasyonun miktar oranıdır.</>}
      </p>
      <ErrorText err={err} />
      {rows && (
        <>
          <div className="row" style={{ marginBottom: 10 }}>
            {(Object.keys(STATUS) as OrderProgress["status"][]).map((s) => (
              <button key={s} className={`kpi-btn ${filter === s ? "active" : ""} ${STATUS[s][0]}`} onClick={() => setFilter(filter === s ? "" : s)}>
                <span className="v">{count(s)}</span><span className="l">{STATUS[s][1]}</span>
              </button>
            ))}
            <div className="kpi-btn"><span className="v">{fmt(tot.earned, 0)} / {fmt(tot.req, 0)}</span><span className="l">{mes ? "planla eşleşen / ihtiyaç saat" : "kazanılan / ihtiyaç saat"} ({tot.req ? fmt((tot.earned / tot.req) * 100, 0) : 0}%)</span></div>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th></th><th>Sipariş</th><th>Poz</th><th>Müşteri</th><th>Stok</th><th className="num">Miktar</th><th>Termin</th>
                  <th className="num">İhtiyaç (saat)</th><th className="num">{mes ? "Planla eşleşen (saat)" : "Kazanılan (saat)"}</th><th className="num">{mes ? "Son op. plan eşleşmesi" : "Çıkan miktar"}</th>
                  <th style={{ minWidth: 160 }}>{mes ? "Müşteri karşılaması" : "İlerleme"}</th><th>İlk / son üretim</th><th>Durum</th>
                  {mes && <><th>Planlama payı</th><th>Rezerve</th><th>Sevk</th><th>Karşılanmayan</th><th>Kalan işçilik (saat)</th></>}
                </tr>
              </thead>
              <tbody>
                {list.map((r) => (
                  <Fragment key={r.order_id}>
                    <tr key={r.order_id} onClick={() => setOpen(open === r.order_id ? null : r.order_id)} style={{ cursor: "pointer", background: open === r.order_id ? "#e3f2fd" : undefined }}>
                      <td className="muted">{open === r.order_id ? "▾" : "▸"}</td>
                      <td><b>{r.order_no}</b></td><td>{r.position_no || <span className="muted">—</span>}</td><td>{r.customer}</td><td>{r.item_code}</td>
                      <td className="num">{fmt(r.quantity, 0)}</td><td>{r.due_date}</td>
                      <td className="num">{fmt(r.required_hours)}</td><td className="num">{fmt(r.earned_hours)}</td>
                      <td className="num">{fmt(r.produced_qty, 0)}</td>
                      <td><div style={{ display: "flex", gap: 6, alignItems: "center" }}><Bar ratio={r.pct / 100} /><span style={{ minWidth: 40 }}>{fmt(r.pct, 0)}%</span></div></td>
                      <td className="muted">{r.first_prod_date ? `${r.first_prod_date} / ${r.last_prod_date}` : "-"}</td>
                      <td><span className={`badge ${STATUS[r.status][0]}`}>{STATUS[r.status][1]}</span></td>
                      {mes && <><td>{fmt(r.planned_share_qty)}</td><td>{fmt(r.reserved_qty)}</td><td>{fmt(r.shipped_qty)}</td><td>{fmt(r.unfulfilled_qty)}</td><td>{fmt(r.remaining_hours)}</td></>}
                    </tr>
                    {open === r.order_id && (
                      <tr key={`${r.order_id}-ops`}>
                        <td></td>
                        <td colSpan={mes ? 17 : 12} style={{ background: "#f8fafc" }}>
                          <table style={{ width: "auto", margin: "4px 0" }}>
                            <thead><tr><th>Op.</th><th>İş Merkezi</th><th className="num">İhtiyaç (saat)</th><th className="num">Planlanan (saat)</th><th className="num">{mes ? "Planla eşleşen / ihtiyaç" : "Üretilen"}</th><th className="num">{mes ? "Planla eşleşen (saat)" : "Kazanılan (saat)"}</th><th style={{ minWidth: 160 }}>İlerleme</th></tr></thead>
                            <tbody>
                              {r.ops.map((o) => (
                                <tr key={o.operation_id ?? o.operation_seq}>
                                  <td>{o.item_code && <span className="muted">{o.item_code} · </span>}{o.operation_seq} {o.operation_name}</td><td><b>{o.work_center_code}</b></td>
                                  <td className="num">{fmt(o.required_hours)}</td><td className="num">{fmt(o.planned_hours)}</td>
                                  <td className="num">{fmt(o.produced_qty, 0)} / {fmt(o.required_qty ?? r.quantity, 0)}</td><td className="num">{fmt(o.earned_hours)}</td>
                                  <td><div style={{ display: "flex", gap: 6, alignItems: "center" }}><Bar ratio={o.pct / 100} /><span style={{ minWidth: 40 }}>{fmt(o.pct, 0)}%</span></div></td>
                                </tr>
                              ))}
                              {r.ops.length === 0 && <tr><td colSpan={7} className="muted">Seçili iş merkezlerinde operasyon yok.</td></tr>}
                            </tbody>
                          </table>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
                {list.length === 0 && <tr><td colSpan={mes ? 17 : 12} className="muted">Gösterilecek sipariş yok.</td></tr>}
              </tbody>
            </table>
          </div>
        </>
      )}
      {!rows && !busy && <div className="muted">Raporu görmek için <b>İlerlemeyi hesapla</b> düğmesine basın.</div>}
    </>
  );
}
