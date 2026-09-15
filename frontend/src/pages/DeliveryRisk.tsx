import { useState } from "react";
import { api, fmt, qs } from "../api";
import { ErrorText, useAsync } from "../components";
import "./DeliveryRisk.css";

type Step = { operation_id: number; operation: string; material_code: string; work_center: string; remaining_qty: number; remaining_hours: number; latest_finish: string | null; forecast_finish: string | null; delay_days: number | null; risk: boolean };
type Row = { order_id: number; order_no: string; position_no: string; customer: string; item_code: string; item_name: string; due_date: string; forecast_finish: string | null; status: string; delay_days: number | null; open_qty: number; reserved_qty: number; scenario_stock_qty: number; production_qty: number; at_risk_qty: number; hours: number; steps: Step[]; warnings: string[] };
type Report = { capacity_start: string; horizon_end: string; orders: Row[]; summary: Record<string, number>; assumptions: string[]; work_centers: { code: string; remaining_capacity: number; due_this_week_hours: number; gap_hours: number }[] };
const labels: Record<string, string> = { late: "Gecikme riski", tight: "Tampon ≤ 1 gün", unknown: "Veri eksik / belirsiz", covered: "Stokla karşılanıyor", on_time: "Zamanında öngörülüyor" };
const day = (d: string | null) => d ? new Date(`${d}T12:00:00`).toLocaleDateString("tr-TR") : "Hesaplanamıyor";
const norm = (s: string) => s.toLocaleLowerCase("tr-TR").trim();

export default function DeliveryRisk({ asOf, horizon, wcIds, refresh }: { asOf: string; horizon: number; wcIds: number[]; refresh: number }) {
  const report = useAsync(() => api.get<Report>(`/api/mes/delivery-risk${qs({ as_of: asOf, horizon, work_center_ids: wcIds })}`), [asOf, horizon, wcIds.join(","), refresh]);
  const [status, setStatus] = useState("attention");
  const [customer, setCustomer] = useState("");
  const [order, setOrder] = useState("");
  const [product, setProduct] = useState("");
  const [limit, setLimit] = useState(30);
  const r = report.data;
  const rows = (r?.orders ?? []).filter(o => (status === "all" || status === "attention" && ["late", "tight", "unknown"].includes(o.status) || o.status === status)
    && norm(o.customer).includes(norm(customer)) && norm(`${o.order_no} ${o.position_no}`).includes(norm(order))
    && norm(`${o.item_code} ${o.item_name} ${o.steps.map(s => s.material_code).join(" ")}`).includes(norm(product)))
    .sort((a, b) => a.due_date.localeCompare(b.due_date) || a.order_no.localeCompare(b.order_no));
  return <section className="risk-section" aria-label="Teslimat riski ve sipariş etkisi">
    <div className="mes-heading"><div><span className="mes-eyebrow">ÜRETİMDEN TESLİMATA</span><h2>Teslimat riski ve sipariş etkisi</h2><p className="muted">Hangi aşama yetişmeyebilir, hangi bitmiş ürünü ve sipariş pozunu etkiler?</p></div><span className="mes-pill">Salt okunur etki senaryosu</span></div>
    <p className="mes-notice">Üretim, müşteri veya siparişe bağlanmaz. Eşleştirmeler yalnızca olası teslimat etkisini gösterir; rezervasyon oluşturmaz.</p>
    {report.loading && <p role="status">Operasyon zincirleri ve ortak kapasite hesaplanıyor…</p>}<ErrorText err={report.err} />
    {!report.loading && r && <>
      {r.summary.unknown > 0 && <p className="mes-warning">Eksik tanımlar nedeniyle belirsiz işler var. Kapasite yükü yalnızca hesaplanabilen saatleri içerir; belirsiz pozlardaki tarihler teslimat taahhüdü olarak kullanılmamalıdır.</p>}
      <div className="risk-cards">{Object.entries(labels).map(([key, label]) => <button key={key} className={`risk-card ${key} ${status === key ? "selected" : ""}`} onClick={() => { setStatus(key); setLimit(30); }}><span>{label}</span><strong>{r.summary[key] ?? 0}</strong><small>sipariş pozu</small></button>)}</div>
      <details className="panel" open><summary><b>Bu haftanın kalan kapasitesi</b> · {day(r.capacity_start)} itibarıyla</summary><p className="muted">Bu hafta veya daha önce teslim edilmesi gereken siparişlerin tüm kalan operasyon yükü. İş merkezleri birbirinin açığını kapatmaz.</p><div className="risk-capacities">{r.work_centers.map(w => <article key={w.code}><b>{w.code}</b><p>{fmt(w.remaining_capacity, 1)} sa. kapasite / {fmt(w.due_this_week_hours, 1)} sa. yük</p><div className="risk-bar"><i style={{ width: `${Math.min(100, w.due_this_week_hours / Math.max(w.remaining_capacity, 1) * 100)}%`, background: w.gap_hours > 0 ? "#c46148" : "#288d83" }} /></div><small>{w.gap_hours > 0 ? `${fmt(w.gap_hours, 1)} saat açık` : "Toplam saat yeterli; operasyon sırası ayrıca değerlendirilir"}</small></article>)}</div>{!r.work_centers.length && <p>Hesaplanabilir operasyon bulunmuyor.</p>}</details>
      <div className="panel"><div className="risk-filters"><label>Durum<select value={status} onChange={e => { setStatus(e.target.value); setLimit(30); }}><option value="attention">Dikkat gerektirenler</option><option value="all">Tümü</option>{Object.entries(labels).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label><label>Müşteri<input value={customer} onChange={e => setCustomer(e.target.value)} placeholder="Müşteri ara" /></label><label>Sipariş / poz<input value={order} onChange={e => setOrder(e.target.value)} placeholder="Sipariş veya poz ara" /></label><label>Bitmiş ürün / yarımamül<input value={product} onChange={e => setProduct(e.target.value)} placeholder="Stok kodu veya ürün ara" /></label></div>
        <p className="muted">{rows.length} sipariş pozu · Termin ufku: {day(r.horizon_end)} · Filtreler ortak kapasite hesabını değiştirmez.</p>
        {rows.slice(0, limit).map(o => <details className={`risk-order ${o.status}`} key={o.order_id}><summary><div><span className={`risk-badge ${o.status}`}>{labels[o.status]}</span><b>{o.item_code} · {o.item_name}</b><small>{o.customer} · Sipariş {o.order_no} / Poz {o.position_no || "—"}</small></div><div><small>Termin → öngörülen bitiş</small><b>{day(o.due_date)} → {day(o.forecast_finish)}</b><small>{o.delay_days ? `${o.delay_days} gün gecikme öngörüsü` : "Operasyon ayrıntılarını açın"}</small></div><div><small>Risk altındaki / üretilecek</small><b>{fmt(o.at_risk_qty)} / {fmt(o.production_qty)} adet</b><small>{fmt(o.hours, 1)} sa. kalan işçilik</small></div></summary>
          <div className="risk-detail"><p><b>Etki zinciri:</b> {o.steps.filter(s => s.risk).map(s => s.material_code).filter((s, i, a) => a.indexOf(s) === i).join(", ") || (o.status === "unknown" ? "Eksik tanım" : "Belirgin operasyon gecikmesi yok")} → <b>{o.item_code}</b> → {o.customer} → <b>{o.order_no} / {o.position_no || "—"}</b></p><p className="muted">Sevk sonrası açık: {fmt(o.open_qty)} · Mevcut rezervasyonla karşılanan: {fmt(o.reserved_qty)} · Serbest stoktan senaryo payı: {fmt(o.scenario_stock_qty)} · Üretilecek: {fmt(o.production_qty)}</p>
            {o.warnings.map(w => <p className="mes-warning" key={w}>{w}</p>)}
            {!!o.steps.length && <div className="table-wrap"><table><thead><tr><th>Yarımamül / aşama</th><th>İş merkezi</th><th>Kalan adet / sa.</th><th>En geç bitiş</th><th>Öngörülen bitiş</th><th>Aşama riski</th></tr></thead><tbody>{o.steps.map((s, i) => <tr key={`${s.operation_id}-${i}`} className={s.risk ? "risk-step" : ""}><td><b>{s.material_code}</b><br />{s.operation}</td><td>{s.work_center}</td><td>{fmt(s.remaining_qty)} / {fmt(s.remaining_hours, 1)}</td><td>{day(s.latest_finish)}</td><td>{day(s.forecast_finish)}</td><td>{s.remaining_qty <= 0 ? "Tamamlanmış" : s.risk ? s.delay_days ? `${s.delay_days} gün geç` : "Tarih / kapasite yetersiz" : "Süresinde"}</td></tr>)}</tbody></table></div>}
          </div></details>)}
        {!rows.length && <p>Bu filtrelere uyan sipariş pozu yok.</p>}{rows.length > limit && <button onClick={() => setLimit(v => v + 30)}>30 poz daha göster</button>}
      </div>
      <details className="panel"><summary><b>Hesaplama varsayımları ve sınırlar</b></summary>{r.assumptions.map(a => <p className="muted" key={a}>{a}</p>)}</details>
    </>}
  </section>;
}
