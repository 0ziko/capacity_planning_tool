import { useState } from "react";
import { addDays, api, fmt, mondayOf, qs, type ItemDetail, type Order, type OrderSchedule, type PlanLine, type PlanMode, type WorkCenterLoad } from "../api";
import { useAuth } from "../auth";
import { Bar, ErrorText, UtilBadge, WcMultiSelect, useAsync, useWorkCenters } from "../components";
import ComparePanel from "./planning/ComparePanel";
import MergePanel from "./planning/MergePanel";
import OrderProgressPanel from "./planning/OrderProgressPanel";
import OrderSchedulePanel from "./planning/OrderSchedulePanel";
import RevenuePanel from "./planning/RevenuePanel";
import WcOrdersPanel from "./planning/WcOrdersPanel";

interface AutoResult { created: number; message: string; mode: PlanMode; unplanned: { order_no: string; item_code: string; operation_seq: number; work_center_code: string; hours: number }[]; skipped: { order_no: string; item_code: string; revenue: number; hours: number }[] }
interface LeadTime { item_code: string; quantity: number; total_hours: number; start: string; end: string; steps: { operation_seq: number; operation_name: string; work_center_code: string; hours: number; start: string; end: string }[] }

type Tab = "load" | "orders" | "wc" | "revenue" | "compare" | "progress" | "merge" | "leadtime";
const TABS: { id: Tab; label: string; hint: string }[] = [
  { id: "load", label: "Haftalık yük & plan satırları", hint: "İş merkezi × hafta doluluk ve tüm plan satırları" },
  { id: "orders", label: "Sipariş bitiş tarihleri", hint: "Plan sonucuna göre her siparişin tahmini üretim bitişi" },
  { id: "wc", label: "İş merkezi bazlı siparişler", hint: "Seçilen iş merkezine planlanmış siparişler" },
  { id: "revenue", label: "Ciro", hint: "Mevcut plana göre haftalık / aylık ciro" },
  { id: "compare", label: "Plan karşılaştır", hint: "Termine göre ve maksimum ciro planlarını yan yana karşılaştır" },
  { id: "progress", label: "Sipariş ilerleme", hint: "Günlük üretim verisine göre iş emri ilerlemesi" },
  { id: "merge", label: "Birleştirme önerileri", hint: "Aynı stok kodlu siparişleri birleştir" },
  { id: "leadtime", label: "Yeni iş terminleme", hint: "Yeni bir iş için mevcut doluluğa göre bitiş" },
];

export default function Planning() {
  const { can } = useAuth();
  const { wcs } = useWorkCenters();
  const [tab, setTab] = useState<Tab>("load");
  const [start, setStart] = useState(mondayOf(new Date()));
  const [weeks, setWeeks] = useState(8);
  const [wcIds, setWcIds] = useState<number[]>([]);
  const [mode, setMode] = useState<PlanMode>("due_date");
  const [result, setResult] = useState<AutoResult | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [weekFilter, setWeekFilter] = useState("");
  const load = useAsync(() => api.get<WorkCenterLoad[]>(`/api/plan/load${qs({ start, weeks, work_center_ids: wcIds })}`), [start, weeks, wcIds.join(",")]);
  const lines = useAsync(() => api.get<PlanLine[]>(`/api/plan/lines${qs({ start, end: addDays(start, weeks * 7 - 1), work_center_ids: wcIds })}`), [start, weeks, wcIds.join(",")]);
  const schedule = useAsync(() => api.get<OrderSchedule[]>(`/api/plan/orders${qs({ work_center_ids: wcIds })}`), [wcIds.join(",")]);
  const refresh = () => { load.reload(); lines.reload(); schedule.reload(); };
  const mergeCount = useAsync(() => api.get<unknown[]>("/api/plan/merge-suggestions").then((g) => g.length), [schedule.data?.length]);

  const runAuto = async () => {
    if (!confirm(`${mode === "revenue" ? "MAKSİMUM CİRO" : "TERMİNE GÖRE"} planlanacak. Seçili iş merkezleri için mevcut OTOMATİK plan satırları silinip yeniden oluşturulacak. Manuel satırlar korunur. Devam?`)) return;
    setBusy(true); setErr("");
    try {
      setResult(await api.post<AutoResult>("/api/plan/auto", { start_week: start, weeks, work_center_ids: wcIds.length ? wcIds : null, replace_existing: true, mode }));
      refresh();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const clearPlan = async (mode?: string) => {
    if (!confirm(`${start} ve sonrası ${mode ?? "tüm"} plan satırları silinsin mi?`)) return;
    await api.del(`/api/plan/lines${qs({ start, mode, work_center_ids: wcIds })}`);
    refresh();
  };
  const moveLine = async (pl: PlanLine, week: string) => { await api.patch(`/api/plan/lines/${pl.id}${qs({ week_start: week })}`); refresh(); };
  const setHours = async (pl: PlanLine) => {
    const v = prompt("Planlanan saat", String(pl.planned_hours));
    if (v === null) return;
    await api.patch(`/api/plan/lines/${pl.id}${qs({ planned_hours: Number(v) })}`);
    refresh();
  };
  const weekList = load.data?.[0]?.weeks.map((w) => w.week_start) ?? [];
  const shownLines = (lines.data ?? []).filter((l) => !weekFilter || l.week_start === weekFilter);

  return (
    <>
      <h1>Planlama</h1>
      <div className="panel row">
        <label>Başlangıç haftası<input type="date" value={start} onChange={(e) => setStart(mondayOf(new Date(e.target.value)))} /></label>
        <label>Hafta sayısı<input type="number" min={1} max={52} value={weeks} onChange={(e) => setWeeks(Number(e.target.value) || 1)} /></label>
        <WcMultiSelect wcs={wcs} value={wcIds} onChange={setWcIds} onlyPlanned />
        {can("poweruser") && (
          <>
            <label title="Termine göre: siparişler termin sırasıyla yerleştirilir. Maksimum ciro: saat başına cirosu yüksek ve ufka tamamen sığan siparişler önceliklendirilir.">Planlama modu
              <select value={mode} onChange={(e) => setMode(e.target.value as PlanMode)}>
                <option value="due_date">📅 Termine göre</option>
                <option value="revenue">💰 Maksimum ciro</option>
              </select>
            </label>
            <button onClick={runAuto} disabled={busy}>▶ Otomatik planla</button>
            <button className="secondary" onClick={() => setTab("compare")} title="İki modu kaydetmeden hesaplayıp karşılaştır">⚖ Karşılaştır</button>
            <button className="danger" onClick={() => clearPlan("auto")}>Otomatik satırları sil</button>
            <button className="danger" onClick={() => clearPlan()}>Tümünü sil</button>
          </>
        )}
        <button className="secondary" onClick={() => api.download(`/api/plan/export.xlsx${qs({ start, weeks, work_center_ids: wcIds })}`)}>⬇ Excel</button>
      </div>
      <ErrorText err={err || load.err || lines.err} />
      {result && (
        <div className="panel">
          <div className="success">{result.message}
            <button className="secondary small" style={{ marginLeft: 8 }} onClick={() => setTab("orders")}>Sipariş bitiş tarihleri →</button>{" "}
            <button className="secondary small" onClick={() => setTab("revenue")}>Ciro →</button>
          </div>
          {result.skipped?.length > 0 && (
            <>
              <div className="error">Maksimum ciro planı {result.skipped.length} siparişi tamamen dışarıda bıraktı (ufka sığmadı, kalan kapasite de yetmedi):</div>
              <ul className="errors">{result.skipped.map((u, i) => <li key={i}>{u.order_no} / {u.item_code}: {fmt(u.hours)} saat · ciro {fmt(u.revenue, 0)}</li>)}</ul>
            </>
          )}
          {result.unplanned.length > 0 && (
            <>
              <div className="error">Ufuk içine sığmayan {result.unplanned.length} operasyon (hafta sayısını artırın veya kapasite ekleyin):</div>
              <ul className="errors">{result.unplanned.map((u, i) => <li key={i}>{u.order_no} / {u.item_code} op.{u.operation_seq} @ {u.work_center_code}: {fmt(u.hours)} saat</li>)}</ul>
            </>
          )}
        </div>
      )}

      <div className="tabs">
        {TABS.map((t) => (
          <button key={t.id} className={`tab ${tab === t.id ? "active" : ""}`} onClick={() => setTab(t.id)} title={t.hint}>
            {t.label}
            {t.id === "orders" && schedule.data && <span className={`badge ${schedule.data.some((s) => s.plan_status === "late") ? "bad" : "muted"}`}>{schedule.data.filter((s) => s.plan_status === "late").length} geç</span>}
            {t.id === "merge" && !!mergeCount.data && <span className="badge warn">{mergeCount.data}</span>}
          </button>
        ))}
      </div>

      {tab === "orders" && <OrderSchedulePanel rows={schedule.data} err={schedule.err} onReload={schedule.reload} />}
      {tab === "wc" && <WcOrdersPanel lines={lines.data} schedule={schedule.data} wcs={wcIds.length ? wcs.filter((w) => wcIds.includes(w.id)) : wcs.filter((w) => w.is_planned)} horizon={`${start} → ${addDays(start, weeks * 7 - 1)}`} />}
      {tab === "revenue" && <RevenuePanel start={start} weeks={weeks} wcIds={wcIds} />}
      {tab === "compare" && <ComparePanel start={start} weeks={weeks} wcIds={wcIds} onApplied={refresh} />}
      {tab === "progress" && <OrderProgressPanel wcIds={wcIds} />}
      {tab === "merge" && <MergePanel onChanged={() => { refresh(); mergeCount.reload(); }} />}
      {tab === "leadtime" && <LeadTimePanel />}
      {tab === "load" && (<>
      <h2>Haftalık yük (saat: planlanan / kapasite)</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>İş Merkezi</th>{weekList.map((w) => <th key={w} style={{ cursor: "pointer", textDecoration: weekFilter === w ? "underline" : undefined }} onClick={() => setWeekFilter(weekFilter === w ? "" : w)}>{w}</th>)}</tr></thead>
          <tbody>
            {load.data?.map((wc) => (
              <tr key={wc.work_center_id}>
                <td><b>{wc.work_center_code}</b></td>
                {wc.weeks.map((w) => (
                  <td key={w.week_start} title={`${fmt(w.planned_units)} / ${fmt(w.capacity_units)} birim`}>
                    <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                      <span style={{ minWidth: 90 }}>{fmt(w.planned_hours, 0)} / {fmt(w.capacity_hours, 0)}</span>
                      <Bar ratio={w.utilization} />
                      <UtilBadge u={w.utilization} />
                    </div>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>Plan satırları {weekFilter && <span className="muted">— {weekFilter} (filtreyi kaldırmak için başlığa tekrar tıklayın)</span>}</h2>
      {can("poweruser") && <ManualAdd wcs={wcs} weekList={weekList} onAdded={refresh} />}
      <div className="table-wrap">
        <table>
          <thead><tr><th>Hafta</th><th>İş Merkezi</th><th>Sipariş</th><th>Müşteri</th><th>Termin</th><th>Stok</th><th>Op.</th><th className="num">Saat</th><th className="num">Miktar</th><th>Mod</th><th></th></tr></thead>
          <tbody>
            {shownLines.map((l) => (
              <tr key={l.id}>
                <td>
                  {can("poweruser") ? (
                    <select value={l.week_start} onChange={(e) => moveLine(l, e.target.value)}>
                      {[...new Set([l.week_start, ...weekList])].sort().map((w) => <option key={w} value={w}>{w}</option>)}
                    </select>
                  ) : l.week_start}
                </td>
                <td><b>{l.work_center_code}</b></td><td>{l.order_no}</td><td>{l.customer}</td>
                <td style={{ color: l.due_date < l.week_start ? "var(--bad)" : undefined }} title={l.due_date < l.week_start ? "Termin, plan haftasından önce!" : ""}>{l.due_date}</td>
                <td>{l.item_code}</td><td>{l.operation_seq}</td>
                <td className="num">{fmt(l.planned_hours, 2)}</td><td className="num">{fmt(l.planned_qty, 0)}</td>
                <td><span className={`badge ${l.mode === "manual" ? "warn" : "muted"}`} title={l.strategy === "revenue" ? "Otomatik · maksimum ciro" : l.strategy === "due_date" ? "Otomatik · termine göre" : ""}>{l.mode === "manual" ? "manuel" : l.strategy === "revenue" ? "oto 💰" : "oto"}</span></td>
                <td>{can("poweruser") && (<><button className="secondary small" onClick={() => setHours(l)}>Saat</button> <button className="danger small" onClick={async () => { await api.del(`/api/plan/lines/${l.id}`); refresh(); }}>Sil</button></>)}</td>
              </tr>
            ))}
            {shownLines.length === 0 && <tr><td colSpan={11} className="muted">Plan satırı yok.</td></tr>}
          </tbody>
        </table>
      </div>
      </>)}
    </>
  );
}

function ManualAdd({ wcs, weekList, onAdded }: { wcs: { id: number; code: string }[]; weekList: string[]; onAdded: () => void }) {
  const [open, setOpen] = useState(false);
  const [orderId, setOrderId] = useState<number | "">("");
  const [opId, setOpId] = useState<number | "">("");
  const [week, setWeek] = useState(weekList[0] ?? mondayOf(new Date()));
  const [hours, setHours] = useState("");
  const [err, setErr] = useState("");
  const orders = useAsync(() => (open ? api.get<Order[]>("/api/orders?status=open") : Promise.resolve([])), [open]);
  const order = orders.data?.find((o) => o.id === orderId);
  const detail = useAsync(() => (order ? api.get<ItemDetail>(`/api/items/${order.item_id}`) : Promise.resolve(null)), [order?.item_id]);
  const submit = async () => {
    setErr("");
    try {
      await api.post("/api/plan/manual", { order_id: orderId, operation_id: opId, week_start: week, planned_hours: hours === "" ? null : Number(hours) });
      setOpen(false); onAdded();
    } catch (e) { setErr((e as Error).message); }
  };
  if (!open) return <button className="secondary small" style={{ marginBottom: 8 }} onClick={() => setOpen(true)}>+ Manuel plan satırı</button>;
  return (
    <div className="panel row">
      <label>Sipariş
        <select value={orderId} onChange={(e) => { setOrderId(Number(e.target.value)); setOpId(""); }}>
          <option value="">Seçin</option>
          {orders.data?.map((o) => <option key={o.id} value={o.id}>{o.order_no} · {o.item_code} · {fmt(o.quantity, 0)} · {o.due_date}</option>)}
        </select>
      </label>
      <label>Operasyon
        <select value={opId} onChange={(e) => setOpId(Number(e.target.value))} disabled={!detail.data}>
          <option value="">Seçin</option>
          {detail.data?.operations.map((op) => <option key={op.id} value={op.id}>{op.seq} {op.operation_name} @ {wcs.find((w) => w.id === op.work_center_id)?.code}</option>)}
        </select>
      </label>
      <label>Hafta<input type="date" value={week} onChange={(e) => setWeek(mondayOf(new Date(e.target.value)))} /></label>
      <label>Saat (boş = tamamı)<input type="number" step="0.5" value={hours} onChange={(e) => setHours(e.target.value)} /></label>
      <button onClick={submit} disabled={!orderId || !opId}>Ekle</button>
      <button className="secondary" onClick={() => setOpen(false)}>Vazgeç</button>
      <ErrorText err={err} />
    </div>
  );
}

function LeadTimePanel() {
  const [code, setCode] = useState("");
  const [qty, setQty] = useState(100);
  const [start, setStart] = useState(new Date().toISOString().slice(0, 10));
  const [res, setRes] = useState<LeadTime | null>(null);
  const [err, setErr] = useState("");
  const run = async () => {
    setErr("");
    try { setRes(await api.post<LeadTime>("/api/plan/leadtime", { item_code: code, quantity: qty, start })); } catch (e) { setErr((e as Error).message); }
  };
  return (
    <div className="panel">
      <h2 style={{ marginTop: 0 }}>Yeni iş terminleme (mevcut plan doluluğuna göre)</h2>
      <div className="row">
        <label>Stok kodu<input value={code} onChange={(e) => setCode(e.target.value)} /></label>
        <label>Miktar<input type="number" value={qty} onChange={(e) => setQty(Number(e.target.value))} /></label>
        <label>En erken başlangıç<input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
        <button onClick={run} disabled={!code}>Hesapla</button>
      </div>
      <ErrorText err={err} />
      {res && (
        <>
          <p><b>{res.item_code}</b> × {fmt(res.quantity, 0)} → toplam {fmt(res.total_hours)} saat · başlangıç <b>{res.start}</b> · bitiş <b>{res.end}</b></p>
          <table style={{ width: "auto" }}>
            <thead><tr><th>Op.</th><th>İş Merkezi</th><th className="num">Saat</th><th>Başlangıç</th><th>Bitiş</th></tr></thead>
            <tbody>{res.steps.map((s) => <tr key={s.operation_seq}><td>{s.operation_seq} {s.operation_name}</td><td>{s.work_center_code}</td><td className="num">{fmt(s.hours)}</td><td>{s.start}</td><td>{s.end}</td></tr>)}</tbody>
          </table>
        </>
      )}
    </div>
  );
}
