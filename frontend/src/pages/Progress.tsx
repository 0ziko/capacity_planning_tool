import { useState } from "react";
import { api, fmt, qs } from "../api";
import { ErrorText, WcMultiSelect, useAsync, useWorkCenters } from "../components";
import { useAuth } from "../auth";
import "./Progress.css";
import DeliveryRisk from "./DeliveryRisk";

type Mapping = { status: string; reason: string; kind?: string; operation_name?: string; work_center_code?: string; candidates: string[]; inputs: Record<string, number>; standard_unit_hours: number };
type Detail = { detail_id: string; prod_date: string; material_code: string; machine_code: string; quantity: number; mapping: Mapping; action?: string; match_week?: string; match_products?: string[]; standard_hours?: number; classification?: string };
type Preview = { token: string; counts: Record<string, number>; rows: Detail[]; shortages: { material_code: string; missing_qty: number }[]; net_quantity: number; standard_hours: number };
type Operation = { key: string; material_code: string; operation_name: string; work_center_id: number; products: string[]; quantity: number; hours: number; actual_qty: number; early_qty: number; remaining_qty: number; remaining_hours: number; completion_pct: number | null; daily: number[] };
type Day = { day: string; hours: number; matched_hours: number; off_plan_hours: number; cumulative_hours: number; reported: boolean };
type Wc = { id: number; code: string; planned_hours: number; actual_hours: number; matched_hours: number; remaining_hours: number; elapsed_capacity_hours: number; output_vs_plan_pct: number | null; capacity_usage_pct: number | null };
type Allocation = { material_code: string; quantity: number; finished_item_code: string; finish_week: string; plan_line_id: number; operation_name: string };
type Report = { week: string; week_end: string; baseline: string; summary: Record<string, number | null>; daily: Day[]; operations: Operation[]; work_centers: Wc[]; off_plan: Detail[]; unresolved: Detail[]; pool: { material_code: string; quantity: number }[]; allocations: Allocation[]; notes: string[] };
const pct = (v: number | null | undefined) => v == null ? "—" : `%${fmt(v, 1)}`;
const dateLabel = (d: string) => new Date(`${d}T12:00:00`).toLocaleDateString("tr-TR", { day: "2-digit", month: "short" });
const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };

function DailyChart({ days, target }: { days: Day[]; target: number }) {
  const max = Math.max(target, ...days.map(d => d.cumulative_hours), 1);
  const y = (n: number) => 190 - n / max * 155;
  return <div className="mes-chart"><svg viewBox="0 0 710 235" role="img" aria-label="Haftalık hedef ve günlük kümülatif standart işçilik grafiği">
    {[0, .5, 1].map(n => <g key={n}><line x1="55" x2="670" y1={y(max * n)} y2={y(max * n)} stroke="#e5eaf0" /><text x="45" y={y(max * n) + 4} textAnchor="end">{fmt(max * n, 1)}</text></g>)}
    <line x1="55" x2="670" y1={y(target)} y2={y(target)} stroke="#77879a" strokeDasharray="6 5" />
    <polyline points={days.filter(d => d.reported).map((d, i) => `${65 + i * 95},${y(d.cumulative_hours)}`).join(" ")} fill="none" stroke="#167c80" strokeWidth="3" />
    {days.map((d, i) => <g key={d.day}><text x={65 + i * 95} y="219" textAnchor="middle">{dateLabel(d.day)}</text>{d.reported && <circle cx={65 + i * 95} cy={y(d.cumulative_hours)} r="5" fill="#167c80"><title>{dateLabel(d.day)}: {fmt(d.cumulative_hours)} standart saat</title></circle>}</g>)}
  </svg><div className="mes-legend"><span>● Kümülatif üretim</span><span>┄ Haftalık plan: {fmt(target)} sa.</span></div></div>;
}

export default function ProgressPage() {
  const { wcs } = useWorkCenters();
  const { can } = useAuth();
  const [asOf, setAsOf] = useState(today());
  const [wcIds, setWcIds] = useState<number[]>([]);
  const [horizon, setHorizon] = useState(4);
  const [refresh, setRefresh] = useState(0);
  const [search, setSearch] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [message, setMessage] = useState("");
  const [previewFilter, setPreviewFilter] = useState("all");
  const report = useAsync(() => api.get<Report>(`/api/mes/progress${qs({ as_of: asOf, work_center_ids: wcIds, horizon })}`), [asOf, wcIds.join(","), horizon, refresh]);
  const r = report.data, s = r?.summary;
  const query = search.trim().toLocaleLowerCase("tr");
  const ops = r?.operations.filter(o => `${o.material_code} ${o.operation_name} ${o.products.join(" ")}`.toLocaleLowerCase("tr").includes(query)) ?? [];
  async function inspect(selected: File) {
    setFile(selected); setPreview(null); setErr(""); setMessage(""); setBusy(true);
    try { setPreview(await api.upload<Preview>("/api/mes/preview", selected)); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  }
  async function confirm() {
    if (!file || !preview) return;
    setBusy(true); setErr("");
    try {
      const result = await api.upload<Preview>("/api/mes/import", file, { token: preview.token });
      setMessage(`${result.counts.new} yeni, ${result.counts.updated} güncellenen, ${result.counts.unchanged} değişmeyen kayıt. ${result.counts.unresolved} kayıt eşleştirme bekliyor.`);
      setPreview(null); setRefresh(v => v + 1);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  }
  return <div className="mes-page">
    <div className="mes-heading"><div><span className="mes-eyebrow">ÜRETİMİN NABZI</span><h1>Üretim İlerlemesi</h1><p className="muted">Haftanın planı, günlük üretim ve kalan işçilik — ürün ve operasyon bazında.</p></div>
      {can("poweruser") && <label className={`mes-upload ${busy ? "disabled" : ""}`}>{busy ? "İşleniyor…" : "↑ MES Excel yükle"}<input aria-label="MES Excel dosyası" type="file" accept=".xlsx,.xlsm" disabled={busy} onChange={e => { const f = e.target.files?.[0]; if (f) void inspect(f); e.target.value = ""; }} /></label>}
    </div>
    <div className="panel row mes-filters"><label>Rapor tarihi<input type="date" value={asOf} onChange={e => e.target.value && setAsOf(e.target.value)} /></label><WcMultiSelect wcs={wcs} value={wcIds} onChange={setWcIds} /><label>Yakın plan ufku<select value={horizon} onChange={e => setHorizon(Number(e.target.value))}>{[2, 4, 8, 12].map(n => <option key={n} value={n}>{n} hafta</option>)}</select></label><button onClick={() => setRefresh(v => v + 1)}>Yenile</button></div>
    <div className="mes-export-row"><span className="muted" role="status">{report.loading ? "Rapor hazırlanıyor…" : "Tüm saatler standart işçilik karşılığıdır."}</span><button disabled={report.loading || !r || busy} onClick={async () => { setBusy(true); setErr(""); try { await api.download(`/api/mes/progress.xlsx${qs({ as_of: asOf, work_center_ids: wcIds, horizon })}`, "MES_ilerleme.xlsx"); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); } }}>Excel'e aktar</button></div>
    <ErrorText err={report.err || err} />{message && <div className="mes-notice" role="status">{message}</div>}
    <DeliveryRisk asOf={asOf} horizon={horizon} wcIds={wcIds} refresh={refresh} />
    {r && <>
      <div className="mes-week"><b>{dateLabel(r.week)} – {dateLabel(r.week_end)}</b><span>{r.baseline === "frozen" ? "İlk MES importundaki plan korunuyor" : "Güncel plan · henüz MES referansı alınmadı"}</span><span>Rapor tarihi dahil</span></div>
      <div className="mes-cards">
        <article><span>Haftalık plan</span><strong>{fmt(s?.planned_hours ?? 0)} <small>sa.</small></strong><p>Planlanan işçilik yükü</p></article>
        <article className="mes-accent"><span>Üretimin standart karşılığı</span><strong>{fmt(s?.actual_hours ?? 0)} <small>sa.</small></strong><p>Haftalık plana oranı {pct(s?.output_vs_plan_pct)}</p></article>
        <article><span>Kalan standart işçilik</span><strong>{fmt(s?.remaining_hours ?? 0)} <small>sa.</small></strong><p>Kalan adet × birim süre · hazırlık hariç</p></article>
        <article><span>Kapasite kullanımı</span><strong>{pct(s?.capacity_usage_pct)}</strong><p>Üretim / tarih dahil kullanılabilir işçilik</p></article>
        <article className="mes-amber"><span>Bu hafta planı dışında</span><strong>{fmt(s?.off_plan_hours ?? 0)} <small>sa.</small></strong><p>{fmt(s?.early_hours ?? 0)} sa. yakın planla eşleşti</p></article>
      </div>
      {(s?.record_count ?? 0) === 0 && <div className="mes-notice">Bu hafta için MES kaydı bulunmuyor. Excel yükleyerek önizlemeyi inceleyebilir ve onayınızla içe aktarabilirsiniz. Eski genel üretim importları MES raporuna dahil değildir.</div>}
      {(s?.unresolved_count ?? 0) > 0 && <div className="mes-warning">{s?.unresolved_count} MES kaydı eşleştirme bekliyor; miktarı ve işçiliği hesaplara katılmadı. Ayrıntılar aşağıda.</div>}
      <div className="mes-charts"><section className="panel"><h2>Hafta nasıl ilerliyor?</h2><p className="muted">Kümülatif standart saat. Kesikli çizgi haftanın toplam hedefidir.</p><DailyChart days={r.daily} target={s?.planned_hours ?? 0} /></section>
        <section className="panel"><h2>Günlük üretim</h2><div className="mes-daybars">{r.daily.map(d => <div key={d.day}><span>{dateLabel(d.day)}</span><div className="mes-track"><i style={{ width: `${d.matched_hours / Math.max(...r.daily.map(x => x.hours), 1) * 100}%` }} /><i className="off" style={{ width: `${d.off_plan_hours / Math.max(...r.daily.map(x => x.hours), 1) * 100}%` }} /></div><b>{d.reported ? fmt(d.hours, 1) : "—"}</b></div>)}</div><p className="muted">Yeşil: haftalık planla eşleşen · Turuncu: plan dışı / erken üretim</p></section></div>
      <section className="panel"><h2>İş merkezleri</h2><div className="table-wrap"><table><thead><tr><th>İş merkezi</th><th>Plan (sa.)</th><th>Üretim (std. sa.)</th><th>Planla eşleşen (sa.)</th><th>Kalan (sa.)</th><th>Üretim / plan</th><th>Kapasite kullanımı</th></tr></thead><tbody>{r.work_centers.map(w => <tr key={w.id}><td><b>{w.code}</b></td><td>{fmt(w.planned_hours)}</td><td>{fmt(w.actual_hours)}</td><td>{fmt(w.matched_hours)}</td><td>{fmt(w.remaining_hours)}</td><td>{pct(w.output_vs_plan_pct)}</td><td title={`${fmt(w.actual_hours)} / ${fmt(w.elapsed_capacity_hours)} saat`}>{pct(w.capacity_usage_pct)}<div className="mes-meter"><i style={{ width: `${Math.min(w.capacity_usage_pct ?? 0, 100)}%` }} /></div></td></tr>)}{!r.work_centers.length && <tr><td colSpan={7}>Seçimde plan veya eşleşmiş üretim bulunmuyor.</td></tr>}</tbody></table></div></section>
      <section className="panel"><div className="mes-heading"><div><h2>Ürün ve operasyon takibi</h2><p className="muted">Ortak yarımamüller tek satırdır. Satırı açarak günlük adetleri ve aday bitmiş ürünleri görün.</p></div><input aria-label="Ürün veya operasyon ara" placeholder="Stok, bitmiş ürün, operasyon ara…" value={search} onChange={e => setSearch(e.target.value)} /></div>
        {ops.map(o => <details className="mes-operation" key={o.key}><summary><div><b>{o.material_code}</b><span>{o.operation_name} · {wcs.find(w => w.id === o.work_center_id)?.code}</span></div><div><small>Plan / Bu hafta / Önceden</small><b>{fmt(o.quantity)} / {fmt(o.actual_qty)} / {fmt(o.early_qty)}</b></div><div><small>Kalan adet · işçilik</small><b>{fmt(o.remaining_qty)} · {fmt(o.remaining_hours)} sa.</b></div><div><b>{pct(o.completion_pct)}</b><div className="mes-meter"><i style={{ width: `${Math.min(o.completion_pct ?? 0, 100)}%` }} /></div></div></summary><div className="mes-operation-detail"><p><b>BOM'a göre bitmiş ürün adayları:</b> {o.products.join(", ")}</p><div className="mes-days">{r.daily.map((d, i) => <div key={d.day}><small>{dateLabel(d.day)}</small><b>{d.reported ? fmt(o.daily[i]) : "—"}</b></div>)}</div></div></details>)}{!ops.length && <p className="muted">Bu seçimde planlı operasyon bulunamadı.</p>}
      </section>
      <section className="panel"><h2>Plan dışı ve erken üretim</h2><div className="table-wrap"><table><thead><tr><th>Tarih · Detay ID</th><th>Malzeme / operasyon</th><th>Net adet</th><th>Std. saat</th><th>Yakın plan eşleşmesi</th></tr></thead><tbody>{r.off_plan.map((d, i) => <tr key={`${d.detail_id}-${i}`}><td>{dateLabel(d.prod_date)}<br /><small>{d.detail_id}</small></td><td><b>{d.material_code}</b><br />{d.mapping.operation_name}</td><td>{fmt(d.quantity)}</td><td>{fmt(d.standard_hours ?? 0)}</td><td>{d.match_week ? <><span className="mes-pill">Erken üretim · {dateLabel(d.match_week)}</span><br /><small>{d.match_products?.join(", ")}</small></> : <span className="mes-pill amber">{horizon} haftalık ufukta plan yok / hedef aşıldı</span>}</td></tr>)}{!r.off_plan.length && <tr><td colSpan={5}>Plan dışı üretim bulunmuyor.</td></tr>}</tbody></table></div></section>
      <details className="panel"><summary><b>Ortak yarımamül havuzu ve veri kontrolleri</b> · {r.pool.filter(p => p.quantity < 0).length} eksik bakiye · {r.unresolved.length} eşleşmeyen kayıt</summary><p className="muted">Bakiyeler rapor tarihi dahil MES hareketlerinden hesaplanır. Negatifler, eksik açılış stoğunu veya eksik üretim kaydını gösterir.</p><div className="table-wrap"><table><thead><tr><th>Yarımamül</th><th>Net havuz bakiyesi</th></tr></thead><tbody>{r.pool.map(p => <tr key={p.material_code}><td>{p.material_code}</td><td style={{ color: p.quantity < 0 ? "#b45309" : undefined }}>{fmt(p.quantity)}</td></tr>)}</tbody></table></div>{r.unresolved.map(d => <p key={d.detail_id}><b>{d.material_code}</b> · {d.detail_id} · {d.mapping.reason}</p>)}</details>
      <section className="panel"><h2>Yarımamül hangi bitiş planını destekliyor?</h2><p className="muted">Önce bu hafta, sonra yakın haftadan uzağa. Bu dağıtım güncel bitiş planını esas alır; müşteri rezervasyonu değildir. Bitiş planı olmayan miktar ortak havuzda kalır.</p><div className="table-wrap"><table><thead><tr><th>Bitiş haftası</th><th>Bitmiş ürün</th><th>Yarımamül / aşama</th><th>Ayrılan adet</th></tr></thead><tbody>{r.allocations.filter(a => `${a.material_code} ${a.finished_item_code} ${a.operation_name}`.toLocaleLowerCase("tr").includes(query)).map((a, i) => <tr key={`${a.plan_line_id}-${i}`}><td>{dateLabel(a.finish_week)}</td><td><b>{a.finished_item_code}</b></td><td>{a.material_code}<br /><small>{a.operation_name}</small></td><td>{fmt(a.quantity)}</td></tr>)}{!r.allocations.length && <tr><td colSpan={4}>Bitiş planına dağıtılabilen yarımamül bulunmuyor.</td></tr>}</tbody></table></div></section>
      <div className="mes-footnote">{r.notes.map(n => <p key={n}>{n}</p>)}</div>
    </>}
    {preview && <div className="mes-overlay"><section className="mes-dialog" role="dialog" aria-modal="true" aria-labelledby="mes-preview-title"><div className="mes-heading"><div><h2 id="mes-preview-title">MES import önizlemesi</h2><p>{file?.name}</p></div><button disabled={busy} onClick={() => setPreview(null)}>Kapat</button></div>
      <div className="mes-notice">{preview.counts.new} yeni · {preview.counts.updated} güncellenecek · {preview.counts.unchanged} değişmeyen · {preview.counts.unresolved} eşleşme bekleyen kayıt<br />Dosya net adedi: {fmt(preview.net_quantity)} · Eşleşen standart işçilik: {fmt(preview.standard_hours)} sa.</div>
      {preview.counts.unresolved > 0 && <p className="mes-warning">Eşleşmeyen kayıtlar saklanır; üretim, stok ve işçilik hesabına katılmaz. BOM/rota veya makine tanımını düzelttikten sonra aynı dosyayı tekrar yükleyin.</p>}
      {preview.shortages.length > 0 && <details className="mes-warning"><summary>{preview.shortages.length} yarımamülde eksik bakiye — tüketim ayrıntılarını inceleyin</summary>{preview.shortages.map(x => <div key={x.material_code}>{x.material_code}: {fmt(x.missing_qty)} eksik</div>)}</details>}
      <label>Göster<select value={previewFilter} onChange={e => setPreviewFilter(e.target.value)}><option value="all">Tüm kayıtlar</option><option value="unresolved">Eşleşmeyenler</option><option value="changed">Yeni / güncellenecek</option></select></label>
      <div className="table-wrap mes-preview-table"><table><thead><tr><th>Tarih · Detay ID</th><th>Malzeme · Makine</th><th>Net adet</th><th>Operasyon / sonuç</th><th>Tüketilecek yarımamül</th></tr></thead><tbody>{preview.rows.filter(d => previewFilter === "all" || previewFilter === "unresolved" && d.mapping.status !== "mapped" || previewFilter === "changed" && d.action !== "unchanged").map(d => <tr key={d.detail_id}><td>{d.prod_date}<br /><small>{d.detail_id}</small></td><td><b>{d.material_code}</b><br />{d.machine_code}</td><td>{fmt(d.quantity)}</td><td>{d.mapping.status === "mapped" ? <>{d.mapping.operation_name}<br /><small>{d.mapping.work_center_code} · {d.action === "new" ? "Yeni" : d.action === "updated" ? "Güncelleme" : "Değişmedi"}</small></> : d.mapping.reason}</td><td>{Object.entries(d.mapping.inputs).map(([code, q]) => <div key={code}>{code} × {fmt(q * d.quantity)}</div>)}</td></tr>)}</tbody></table></div>
      <p className="muted">Onayla: tekil kayıtlar güncellenir, eşleşen bitmiş ürünler stoğa girer, BOM tüketimleri havuza yansır. Sipariş veya müşteri rezervasyonu yapılmaz.</p><ErrorText err={err} /><div className="mes-dialog-actions"><button disabled={busy} onClick={() => setPreview(null)}>Vazgeç</button><button className="primary" disabled={busy} onClick={() => void confirm()}>{busy ? "Kaydediliyor…" : "Onayla ve içe aktar"}</button></div>
    </section></div>}
  </div>;
}
