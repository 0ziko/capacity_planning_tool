import { Fragment, useState } from "react";
import { addDays, api, fmt, mondayOf, qs, shortDate, weekLabel, weekLong, type AutoPlanRequest, type CoShipmentOptions, type CoShipmentException, type CoShipmentResult, type ForecastSummary, type ItemDetail, type LoadDetail, type LeadTime, type Order, type OrderSchedule, type PlanLine, type PlanMode, type WcWeek, type WorkCenterLoad } from "../api";
import { useAuth } from "../auth";
import { Bar, ErrorText, PlanStackBar, UtilBadge, WeekInput, WcMultiSelect, useAsync, useWorkCenters } from "../components";
import WcWeeksPanel from "./WcWeeksPanel";
import ComparePanel from "./planning/ComparePanel";
import MergePanel from "./planning/MergePanel";
import OrderProgressPanel from "./planning/OrderProgressPanel";
import OrderSchedulePanel from "./planning/OrderSchedulePanel";
import RevenuePanel from "./planning/RevenuePanel";
import GanttPanel from "./planning/GanttPanel";
import WcOrdersPanel from "./planning/WcOrdersPanel";
import ProductionOutputPanel from "./planning/ProductionOutputPanel";
import PlanPreflightModal from "./planning/PlanPreflightModal";
import CoShipmentPanel, { defaultCoShipment } from "./planning/CoShipmentPanel";
import RevisionsPanel from "./planning/RevisionsPanel";

interface AutoResult {
  created: number;
  message: string;
  mode: PlanMode;
  unplanned: { order_no: string; item_code: string; operation_seq: number; work_center_code: string; hours: number }[];
  skipped: { order_no: string; item_code: string; revenue: number; hours: number }[];
  co_shipment_results?: CoShipmentResult[];
  co_shipment_exceptions?: CoShipmentException[];
}

type Tab = "load" | "labor" | "gantt" | "orders" | "wc" | "revenue" | "compare" | "progress" | "merge" | "leadtime" | "output" | "revision";
const TABS: { id: Tab; label: string; hint: string }[] = [
  { id: "load", label: "Haftalık yük & plan satırları", hint: "İş merkezi × hafta doluluk ve tüm plan satırları" },
  { id: "output", label: "Haftalık üretim planı", hint: "Seçilen hafta için iş merkezi bazlı üretim listesi (üretim ekibi çıktısı)" },
  { id: "gantt", label: "Gantt", hint: "İş merkezi bazında sipariş / yarımamül zaman çizelgesi" },
  { id: "labor", label: "Haftalık iş gücü", hint: "İş merkezi × hafta kişi / verimli saat / gün — haftaya özel değişiklikler" },
  { id: "revision", label: "Plan revizyonu", hint: "Nedenli taslak, yeniden hesap, onayla ve devreye al" },
  { id: "orders", label: "Sipariş bitiş tarihleri", hint: "Plan sonucuna göre her siparişin tahmini üretim bitişi" },
  { id: "wc", label: "İş merkezi bazlı siparişler", hint: "Seçilen iş merkezine planlanmış siparişler" },
  { id: "revenue", label: "Ciro", hint: "Mevcut plana göre haftalık / aylık ciro" },
  { id: "compare", label: "Plan karşılaştır", hint: "Termine göre ve maksimum ciro planlarını yan yana karşılaştır" },
  { id: "progress", label: "Sipariş ilerleme", hint: "Günlük üretim verisine göre iş emri ilerlemesi" },
  { id: "merge", label: "Üretim partisi", hint: "Aynı stok kodlu siparişlerin üretimini birleştir (siparişler ayrı kalır)" },
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
  const [lineMode, setLineMode] = useState("");
  const [loadDetail, setLoadDetail] = useState<{ wcId: number; wcCode: string; week: string } | null>(null);
  const [preflightOpen, setPreflightOpen] = useState(false);
  const [coShipment, setCoShipment] = useState<CoShipmentOptions>(defaultCoShipment);
  const openOrders = useAsync(() => api.get<Order[]>("/api/orders?status=open"), []);
  const load = useAsync(() => api.get<WorkCenterLoad[]>(`/api/plan/load${qs({ start, weeks, work_center_ids: wcIds })}`), [start, weeks, wcIds.join(",")]);
  const lines = useAsync(() => api.get<PlanLine[]>(`/api/plan/lines${qs({ start, end: addDays(start, weeks * 7 - 1), work_center_ids: wcIds, mode: lineMode || undefined })}`), [start, weeks, wcIds.join(","), lineMode]);
  const schedule = useAsync(() => api.get<OrderSchedule[]>(`/api/plan/orders${qs({ work_center_ids: wcIds })}`), [wcIds.join(",")]);
  const refresh = () => { load.reload(); lines.reload(); schedule.reload(); };
  const mergeCount = useAsync(() => api.get<unknown[]>("/api/plan/merge-suggestions").then((g) => g.length), [schedule.data?.length]);

  const planReq: AutoPlanRequest = {
    start_week: start,
    weeks,
    work_center_ids: wcIds.length ? wcIds : null,
    replace_existing: true,
    mode,
    ...(coShipment.enabled && coShipment.selections.length
      ? { co_shipment: coShipment }
      : {}),
  };

  const runAuto = async (skipConfirm = false) => {
    if (!skipConfirm) {
      setPreflightOpen(true);
      return;
    }
    setBusy(true); setErr("");
    try {
      setResult(await api.post<AutoResult>("/api/plan/auto", planReq));
      refresh();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };

  const confirmAutoPlan = async () => {
    setPreflightOpen(false);
    setBusy(true); setErr("");
    try {
      setResult(await api.post<AutoResult>("/api/plan/auto", planReq));
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
      {tab !== "output" && (
        <>
          <div className="panel row">
            <WeekInput value={start} onChange={setStart} label="Başlangıç haftası" />
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
                <button onClick={() => runAuto()} disabled={busy} title="Onay kaydı olmadan canlı plana yazar">▶ Otomatik planla (revizyonsuz)</button>
                <button className="secondary" onClick={() => setTab("revision")}>Plan revizyonu</button>
                <button className="secondary" onClick={() => setTab("compare")} title="İki modu kaydetmeden hesaplayıp karşılaştır">⚖ Karşılaştır</button>
                <button className="danger" onClick={() => clearPlan("auto")}>Otomatik satırları sil</button>
                <button className="danger" onClick={() => clearPlan()}>Tümünü sil</button>
              </>
            )}
            <button className="secondary" onClick={() => api.download(`/api/plan/export.xlsx${qs({ start, weeks, work_center_ids: wcIds })}`)}>⬇ Excel</button>
          </div>
          {can("poweruser") && mode === "due_date" && (
            <CoShipmentPanel orders={openOrders.data ?? []} value={coShipment} onChange={setCoShipment} />
          )}
          {can("poweruser") && mode === "revenue" && coShipment.enabled && (
            <p className="muted">Birlikte sevk modu yalnızca “Termine göre” planlamada kullanılabilir.</p>
          )}
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
              {(result.co_shipment_results?.length ?? 0) > 0 && (
                <div style={{ marginTop: 12 }}>
                  <h3 style={{ margin: "0 0 8px" }}>Birlikte sevk sonucu</h3>
                  <div className="table-wrap">
                    <table>
                      <thead><tr><th>Sipariş</th><th>Pozlar</th><th>Termin</th><th>Hedef hazır</th><th>Planlanan hazır</th><th>Durum</th></tr></thead>
                      <tbody>
                        {result.co_shipment_results!.map((r) => (
                          <tr key={r.order_no}>
                            <td><b>{r.order_no}</b></td>
                            <td>{r.position_nos.join(", ")}</td>
                            <td>{new Date(r.due_date + "T12:00:00").toLocaleDateString("tr-TR")}</td>
                            <td>{new Date(r.target_ready_date + "T12:00:00").toLocaleDateString("tr-TR")}</td>
                            <td>{r.planned_ready_date ? new Date(r.planned_ready_date + "T12:00:00").toLocaleDateString("tr-TR") : "—"}</td>
                            <td>{r.on_target ? <span className="badge ok">Hedefte</span> : <span className="badge warn">Sapma</span>}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
              {(result.co_shipment_exceptions?.length ?? 0) > 0 && (
                <ul style={{ marginTop: 10, paddingLeft: 18 }}>
                  {result.co_shipment_exceptions!.map((e, i) => (
                    <li key={i} className="warn">
                      <b>{e.order_no}</b> ({e.position_nos.join(", ")}): {e.reason}
                      {e.suggestion && <span className="muted"> — {e.suggestion}</span>}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </>
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

      {tab === "labor" && <LaborPanel start={start} weeks={weeks} wcs={wcIds.length ? wcs.filter((w) => wcIds.includes(w.id)) : wcs.filter((w) => w.is_planned)} canEdit={can("poweruser")} onChanged={refresh} />}
      {tab === "revision" && (
        <RevisionsPanel
          start={start}
          weeks={weeks}
          wcIds={wcIds}
          mode={mode}
          orders={openOrders.data ?? []}
          wcs={wcIds.length ? wcs.filter((w) => wcIds.includes(w.id)) : wcs.filter((w) => w.is_planned)}
          canEdit={can("poweruser")}
          onApplied={refresh}
        />
      )}
      {tab === "gantt" && (
        <GanttPanel
          wcs={wcIds.length ? wcs.filter((w) => wcIds.includes(w.id)) : wcs.filter((w) => w.is_planned)}
          defaultStart={start}
          defaultEnd={addDays(start, weeks * 7 - 1)}
        />
      )}
      {tab === "orders" && <OrderSchedulePanel rows={schedule.data} err={schedule.err} onReload={schedule.reload} />}
      {tab === "wc" && <WcOrdersPanel lines={lines.data} schedule={schedule.data} wcs={wcIds.length ? wcs.filter((w) => wcIds.includes(w.id)) : wcs.filter((w) => w.is_planned)} horizon={`${start} → ${addDays(start, weeks * 7 - 1)}`} />}
      {tab === "revenue" && <RevenuePanel start={start} weeks={weeks} wcIds={wcIds} />}
      {tab === "compare" && <ComparePanel start={start} weeks={weeks} wcIds={wcIds} onApplied={refresh} />}
      {tab === "progress" && <OrderProgressPanel wcIds={wcIds} />}
      {tab === "merge" && <MergePanel planCtx={{ start_week: start, weeks, work_center_ids: wcIds, mode }} onChanged={() => { refresh(); mergeCount.reload(); }} onAutoPlan={() => runAuto(true)} />}
      {tab === "leadtime" && <LeadTimePanel onForecastAdded={refresh} onShowForecastLines={() => { setTab("load"); setLineMode("forecast"); }} />}
      {tab === "output" && <ProductionOutputPanel />}
      {tab === "load" && (<>
      <h2>Haftalık yük (saat: plan / gerçekleşen / kalan / kalan iş gün)</h2>
      <p className="muted" style={{ marginTop: -8 }}>Turuncu = kesin plan (oto/manuel); mor = tahmin payı. Yeşil = üretim beyanı (gerçekleşen). Plan doluluk %, iş merkezinde tanımlı <b>atıl kapasite</b> düşüldükten sonraki planlanabilir kapasiteye göre hesaplanır. %100 üzeri kapasite aşımı gösterir.</p>
      <div className="table-wrap">
        <table>
          <thead><tr><th>İş Merkezi</th>{weekList.map((w) => (
            <th key={w} style={{ cursor: "pointer", textDecoration: weekFilter === w ? "underline" : undefined }} onClick={() => setWeekFilter(weekFilter === w ? "" : w)} title={`Hafta başlangıcı (Pzt): ${w}`}>
              {weekLabel(w)} <span className="muted" style={{ fontWeight: 400, fontSize: 11 }}>{shortDate(w)}</span>
            </th>
          ))}</tr></thead>
          <tbody>
            {load.data?.map((wc) => (
              <tr key={wc.work_center_id}>
                <td><b>{wc.work_center_code}</b></td>
                {wc.weeks.map((w) => {
                  const cap = w.planning_capacity_hours || 1;
                  const firmPct = Math.round((w.firm_planned_hours / cap) * 100);
                  const fcPct = Math.round((w.forecast_hours / cap) * 100);
                  return (
                  <td
                    key={w.week_start}
                    style={{ cursor: w.planned_hours > 0 ? "pointer" : undefined }}
                    onClick={() => w.planned_hours > 0 && setLoadDetail({ wcId: wc.work_center_id, wcCode: wc.work_center_code, week: w.week_start })}
                    title={w.planned_hours > 0 || w.forecast_hours > 0 ? `Plan: ${fmt(w.planned_hours)} sa (kesin: ${fmt(w.firm_planned_hours)} · tahmin: ${fmt(w.forecast_hours)})\nSipariş: ${firmPct}% · Tahmin: ${fcPct}%` : undefined}
                  >
                    <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 155 }}>
                      <span style={{ fontSize: 12 }}>{fmt(w.planned_hours, 0)} / <b style={{ color: "var(--ok)" }}>{fmt(w.actual_hours, 0)}</b> / {fmt(w.remaining_hours, 0)} / <span title="Kalan iş gün">{fmt(w.remaining_days, 2)}</span></span>
                      <span style={{ fontSize: 11 }} className="muted">Atıl: {w.idle_hours > 0.5 ? <b>{fmt(w.idle_hours, 0)} sa</b> : "yok"}</span>
                      <PlanStackBar utilization={w.utilization} forecastHours={w.forecast_hours} planningCapacity={w.planning_capacity_hours} />
                      <Bar ratio={w.actual_utilization} cls="actual" />
                      <div style={{ display: "flex", gap: 6, fontSize: 11 }}>
                        <UtilBadge u={w.utilization} />
                        <span className="muted">plan</span>
                        {w.forecast_hours > 0 && <span className="badge" style={{ background: "#9333ea", color: "#fff", fontSize: 10 }} title={`Tahmin: ${fmt(w.forecast_hours)} sa`}>{Math.round((w.forecast_hours / (w.planning_capacity_hours || 1)) * 100)}% tah.</span>}
                        <UtilBadge u={w.actual_utilization} />
                        <span className="muted">gerç.</span>
                      </div>
                    </div>
                  </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>Plan satırları {weekFilter && <span className="muted">— {weekLong(weekFilter)} (filtreyi kaldırmak için başlığa tekrar tıklayın)</span>}</h2>
      <div className="row" style={{ marginBottom: 8 }}>
        <label>Mod filtresi
          <select value={lineMode} onChange={(e) => setLineMode(e.target.value)}>
            <option value="">Tümü</option>
            <option value="auto">Otomatik</option>
            <option value="manual">Manuel</option>
            <option value="forecast">Tahmin</option>
          </select>
        </label>
        {lineMode === "forecast" && <span className="muted">Terminleme ekranından eklenen tahmin satırları. Hafta aralığını genişletin veya iş merkezi filtresini kaldırın.</span>}
      </div>
      {can("poweruser") && <ManualAdd wcs={wcs} weekList={weekList} onAdded={refresh} />}
      <div className="table-wrap">
        <table>
          <thead><tr><th>Hafta</th><th>İş Merkezi</th><th>Sipariş</th><th>Poz</th><th>Müşteri</th><th>Termin</th><th>Stok</th><th>Op.</th><th className="num">Saat</th><th className="num">Miktar</th><th>Mod</th><th></th></tr></thead>
          <tbody>
            {shownLines.map((l) => (
              <tr key={l.id}>
                <td>
                  {can("poweruser") ? (
                    <select value={l.week_start} onChange={(e) => moveLine(l, e.target.value)} title={`Hafta başlangıcı: ${l.week_start}`}>
                      {[...new Set([l.week_start, ...weekList])].sort().map((w) => <option key={w} value={w}>{weekLong(w)}</option>)}
                    </select>
                  ) : <span title={l.week_start}>{weekLong(l.week_start)}</span>}
                </td>
                <td><b>{l.work_center_code}</b></td>
                <td>
                  {l.batch_no ? (
                    <>
                      <b title="Üretim partisi">{l.batch_no}</b>
                      <div className="muted" style={{ fontSize: 11 }}>{l.batch_order_nos.join(", ")}</div>
                    </>
                  ) : l.order_no}
                </td>
                <td>{l.batch_no ? <span className="muted">—</span> : (l.position_no || <span className="muted">—</span>)}</td>
                <td>{l.batch_no ? <span className="muted">parti</span> : l.customer}</td>
                <td style={{ color: l.due_date < l.week_start ? "var(--bad)" : undefined }} title={l.due_date < l.week_start ? "Termin, plan haftasından önce!" : ""}>{l.due_date}</td>
                <td>{l.item_code}</td><td>{l.operation_seq}</td>
                <td className="num">{fmt(l.planned_hours, 2)}</td><td className="num">{fmt(l.planned_qty, 0)}</td>
                <td><span className={`badge ${l.mode === "manual" ? "warn" : l.mode === "forecast" ? "ok" : "muted"}`} title={l.strategy === "revenue" ? "Otomatik · maksimum ciro" : l.strategy === "due_date" ? "Otomatik · termine göre" : l.mode === "forecast" ? "Terminleme tahmini" : ""}>{l.mode === "manual" ? "manuel" : l.mode === "forecast" ? "tahmin" : l.strategy === "revenue" ? "oto 💰" : "oto"}</span></td>
                <td>{can("poweruser") && (<><button className="secondary small" onClick={() => setHours(l)}>Saat</button> <button className="danger small" onClick={async () => { await api.del(`/api/plan/lines/${l.id}`); refresh(); }}>Sil</button></>)}</td>
              </tr>
            ))}
            {shownLines.length === 0 && <tr><td colSpan={12} className="muted">Plan satırı yok.</td></tr>}
          </tbody>
        </table>
      </div>
      {loadDetail && <LoadDetailModal wcId={loadDetail.wcId} wcCode={loadDetail.wcCode} week={loadDetail.week} onClose={() => setLoadDetail(null)} />}
      </>)}
      {preflightOpen && <PlanPreflightModal req={planReq} onClose={() => setPreflightOpen(false)} onConfirm={confirmAutoPlan} />}
    </>
  );
}

/** İş merkezi × hafta iş gücü matrisi (kişi · saat/kişi · gün → kapasite); satıra tıklayınca haftalık düzenleme paneli açılır. */
function LaborPanel({ start, weeks, wcs, canEdit, onChanged }: { start: string; weeks: number; wcs: { id: number; code: string; name: string }[]; canEdit: boolean; onChanged: () => void }) {
  const ids = wcs.map((w) => w.id);
  const rows = useAsync(() => (ids.length ? api.get<WcWeek[]>(`/api/wc-weeks${qs({ start, weeks, work_center_ids: ids })}`) : Promise.resolve([] as WcWeek[])), [start, weeks, ids.join(",")]);
  const [open, setOpen] = useState<number | null>(null);
  const byWc = new Map<number, WcWeek[]>();
  for (const r of rows.data ?? []) byWc.set(r.work_center_id, [...(byWc.get(r.work_center_id) ?? []), r]);
  const firstRows: WcWeek[] = byWc.size ? (byWc.values().next().value as WcWeek[]) : [];
  const weekList: string[] = firstRows.map((r) => r.week_start);
  const overrides = (rows.data ?? []).filter((r) => r.has_override).length;
  return (
    <>
      <h2>Haftalık iş gücü <span className="muted" style={{ fontWeight: 400 }}>— hücre: kişi × verimli saat × gün = kapasite (saat); turuncu = haftaya özel istisna</span></h2>
      <p className="muted" style={{ marginTop: -6 }}>
        İş gücü hafta hafta değişebilir (izin, bayram, fazla mesai, ek personel). Satırdaki <b>Düzenle</b> ile o iş merkezinin haftalarını tek tek değiştirin; plan, terminleme ve analiz yeni değerlere uyum sağlar.
        {overrides > 0 && <> Şu an <b>{overrides}</b> hafta istisnası tanımlı.</>}
      </p>
      <ErrorText err={rows.err} />
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>İş Merkezi</th>
              {weekList.map((w) => <th key={w} title={`Hafta başlangıcı: ${w}`}>{weekLabel(w)} <span className="muted" style={{ fontWeight: 400, fontSize: 11 }}>{shortDate(w)}</span></th>)}
              <th></th>
            </tr>
          </thead>
          <tbody>
            {wcs.map((w) => {
              const cells = byWc.get(w.id) ?? [];
              return (
                <Fragment key={w.id}>
                  <tr>
                    <td><b>{w.code}</b> <span className="muted">{w.name}</span></td>
                    {cells.map((c) => (
                      <td key={c.week_start} style={{ background: c.has_override ? "#fff7ed" : undefined }} title={c.has_override ? `İstisna: ${c.note || "-"}` : "Varsayılan"}>
                        <div style={{ whiteSpace: "nowrap" }}>
                          <b>{c.headcount}</b> kişi · {fmt(c.efficient_hours_per_person, 2)} sa · {c.working_days} gün
                        </div>
                        <div className="muted">= {fmt(c.capacity_hours, 0)} saat</div>
                      </td>
                    ))}
                    {cells.length === 0 && <td colSpan={Math.max(weekList.length, 1)} className="muted">…</td>}
                    <td><button className={`secondary small${open === w.id ? " active" : ""}`} onClick={() => setOpen(open === w.id ? null : w.id)}>{open === w.id ? "Kapat" : canEdit ? "Düzenle" : "Detay"}</button></td>
                  </tr>
                  {open === w.id && (
                    <tr>
                      <td colSpan={weekList.length + 2} style={{ background: "#f8fafc" }}>
                        <WcWeeksPanel wcId={w.id} wcCode={w.code} start={start} weeks={weeks} canEdit={canEdit} onChanged={() => { rows.reload(); onChanged(); }} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
            {wcs.length === 0 && <tr><td className="muted">Planlanan iş merkezi yok.</td></tr>}
          </tbody>
        </table>
      </div>
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
          {orders.data?.map((o) => <option key={o.id} value={o.id}>{o.order_no}{o.position_no ? ` / poz ${o.position_no}` : ""} · {o.item_code} · {fmt(o.quantity, 0)} · {o.due_date}</option>)}
        </select>
      </label>
      <label>Operasyon
        <select value={opId} onChange={(e) => setOpId(Number(e.target.value))} disabled={!detail.data}>
          <option value="">Seçin</option>
          {detail.data?.operations.map((op) => <option key={op.id} value={op.id}>{op.seq} {op.operation_name} @ {wcs.find((w) => w.id === op.work_center_id)?.code}</option>)}
        </select>
      </label>
      <label>Hafta <span className="muted">({weekLabel(week)})</span><WeekInput value={week} onChange={setWeek} label="" /></label>
      <label>Saat (boş = tamamı)<input type="number" step="0.5" value={hours} onChange={(e) => setHours(e.target.value)} /></label>
      <button onClick={submit} disabled={!orderId || !opId}>Ekle</button>
      <button className="secondary" onClick={() => setOpen(false)}>Vazgeç</button>
      <ErrorText err={err} />
    </div>
  );
}

function LoadDetailModal({ wcId, wcCode, week, onClose }: { wcId: number; wcCode: string; week: string; onClose: () => void }) {
  const detail = useAsync(() => api.get<LoadDetail>(`/api/plan/load/detail${qs({ work_center_id: wcId, week_start: week })}`), [wcId, week]);
  const rows = [...(detail.data?.rows ?? [])].sort((a, b) => (a.planned_start ?? "").localeCompare(b.planned_start ?? "") || a.order_no.localeCompare(b.order_no));
  const totalHours = detail.data?.total_hours ?? rows.reduce((s, r) => s + r.planned_hours, 0);
  const totalQty = detail.data?.total_qty ?? rows.reduce((s, r) => s + r.planned_qty, 0);
  const paretoRows = detail.data?.pareto ?? [];
  const exportXlsx = () => api.download(`/api/plan/load/detail.xlsx${qs({ work_center_id: wcId, week_start: week })}`, `is_listesi_${wcCode}_${week}.xlsx`);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal panel" style={{ maxWidth: 1100, width: "96vw" }} onClick={(e) => e.stopPropagation()}>
        <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
          <h2 style={{ margin: 0 }}>{wcCode} · {weekLong(week)} yük detayı</h2>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="secondary" onClick={exportXlsx} disabled={!detail.data?.rows.length}>⬇ Excel (iş listesi)</button>
            <button className="secondary" onClick={onClose}>Kapat</button>
          </div>
        </div>
        <ErrorText err={detail.err} />
        {detail.data && (
          <>
            <p className="muted">Toplam planlanan: <b>{fmt(totalHours)} saat</b> · <b>{fmt(totalQty, 0)}</b> adet · {rows.length} satır</p>
            <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr", gap: 16 }}>
              <div className="table-wrap" style={{ maxHeight: 420 }}>
                <table>
                  <thead>
                    <tr>
                      <th>Başlangıç</th><th>Bitiş</th><th>Yarımamül</th><th>Bitmiş ürün</th><th>Sipariş</th><th>Müşteri</th>
                      <th>Op.</th><th className="num">Miktar</th><th className="num">Saat</th><th>Mod</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.plan_line_id}>
                        <td><b>{r.planned_start ? shortDate(r.planned_start) : "—"}</b></td>
                        <td>{r.planned_end ? shortDate(r.planned_end) : "—"}</td>
                        <td><code title={r.operation_name}>{r.semi_finished_code || <span className="muted">—</span>}</code></td>
                        <td><b>{r.item_code}</b></td>
                        <td>
                          {r.batch_no ? <><b>{r.batch_no}</b> <span className="muted">({r.batch_order_nos.join(", ")})</span></> : <>{r.order_no}{r.position_no ? ` / ${r.position_no}` : ""}</>}
                        </td>
                        <td className="muted">{r.customer}</td>
                        <td title={r.operation_name}>{r.operation_seq}</td>
                        <td className="num">{fmt(r.planned_qty, 0)}</td>
                        <td className="num">{fmt(r.planned_hours, 2)}</td>
                        <td>{r.mode === "forecast" ? <span className="badge ok">tahmin</span> : r.mode}</td>
                      </tr>
                    ))}
                    {rows.length === 0 && <tr><td colSpan={10} className="muted">Plan satırı yok.</td></tr>}
                    {rows.length > 0 && (
                      <tr style={{ fontWeight: 700, background: "#f5f7fa" }}>
                        <td colSpan={7}>Alt toplam</td>
                        <td className="num">{fmt(totalQty, 0)}</td>
                        <td className="num">{fmt(totalHours, 2)}</td>
                        <td></td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
              <div>
                <h3 style={{ marginTop: 0 }}>Pareto — bitmiş ürün bazlı saat</h3>
                {paretoRows.map((p) => (
                  <div key={p.item_code} style={{ marginBottom: 8 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
                      <span><b>{p.item_code}</b></span>
                      <span>{fmt(p.hours, 1)} sa · {fmt(p.pct, 0)}% · küm. {fmt(p.cum_pct, 0)}%</span>
                    </div>
                    <div style={{ background: "#e0e0e0", height: 8, borderRadius: 4, overflow: "hidden" }}>
                      <div style={{ width: `${Math.min(p.pct, 100)}%`, background: "var(--primary)", height: "100%" }} />
                    </div>
                  </div>
                ))}
                {paretoRows.length === 0 && <p className="muted">Veri yok.</p>}
                {paretoRows.length > 0 && (
                  <div className="panel" style={{ marginTop: 12, padding: "8px 12px", background: "#f5f7fa" }}>
                    <b>Alt toplam:</b> {fmt(totalHours, 1)} saat · {paretoRows.length} ürün kodu
                  </div>
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function LeadTimePanel({ onForecastAdded, onShowForecastLines }: { onForecastAdded: () => void; onShowForecastLines: () => void }) {
  const { can } = useAuth();
  const [code, setCode] = useState("");
  const [qty, setQty] = useState(100);
  const [start, setStart] = useState(new Date().toISOString().slice(0, 10));
  const [label, setLabel] = useState("");
  const [res, setRes] = useState<LeadTime | null>(null);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const forecasts = useAsync(() => api.get<ForecastSummary[]>("/api/plan/forecast"), []);
  const reload = () => { forecasts.reload(); onForecastAdded(); };
  const run = async () => {
    setErr(""); setMsg("");
    try { setRes(await api.post<LeadTime>("/api/plan/leadtime", { item_code: code, quantity: qty, start })); } catch (e) { setErr((e as Error).message); }
  };
  const addForecast = async () => {
    if (!res || !confirm("Hesaplanan termin planda TAHMİN olarak kaydedilsin mi? Aynı gün içindeki sonraki terminlemeler bu yükü dikkate alır.")) return;
    setBusy(true); setErr("");
    try {
      const out = await api.post<{ created: number; message: string }>("/api/plan/leadtime/forecast", { item_code: res.item_code, quantity: res.quantity, label: label.trim(), steps: res.steps });
      setMsg(out.message);
      reload();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const clearForecast = async () => {
    if (!confirm("Tüm tahmin plan satırları silinsin mi?")) return;
    await api.del("/api/plan/forecast");
    setMsg("Tahmin satırları silindi.");
    reload();
  };
  const removeForecast = async (f: ForecastSummary) => {
    if (!confirm(`${f.order_no} tahmini silinsin mi?`)) return;
    await api.del(`/api/plan/forecast/${f.order_id}`);
    setMsg(`${f.order_no} silindi.`);
    reload();
  };
  return (
    <div className="panel">
      <h2 style={{ marginTop: 0 }}>Yeni iş terminleme (mevcut plan doluluğuna göre)</h2>
      <p className="muted" style={{ marginTop: -6 }}>Aynı gün birden fazla termin hesapladığınızda, önceki sonuçları <b>plana tahmin olarak ekleyin</b>; sonraki hesaplamalar doluluğu doğru yansıtır. Tahminler <b>Haftalık yük</b> sekmesindeki plan satırlarında (mod: tahmin) ve aşağıdaki listede görünür.</p>
      <div className="row">
        <label>Stok kodu<input value={code} onChange={(e) => setCode(e.target.value)} /></label>
        <label>Miktar<input type="number" value={qty} onChange={(e) => setQty(Number(e.target.value))} /></label>
        <label>En erken başlangıç<input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
        <button onClick={run} disabled={!code}>Hesapla</button>
        {can("poweruser") && <button className="secondary" onClick={clearForecast}>Tahminleri temizle</button>}
        <button className="secondary" onClick={onShowForecastLines}>Plan satırlarında göster →</button>
      </div>
      <ErrorText err={err || forecasts.err} />
      {msg && <div className="success">{msg}</div>}

      <h3>Plana eklenmiş tahminler ({forecasts.data?.length ?? 0})</h3>
      {(forecasts.data?.length ?? 0) > 0 ? (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Etiket</th><th>Stok</th><th className="num">Miktar</th><th className="num">Saat</th><th>Plan haftası</th><th>Termin</th><th className="num">Satır</th><th></th></tr></thead>
            <tbody>
              {forecasts.data!.map((f) => (
                <tr key={f.order_id}>
                  <td><b>{f.order_no}</b></td>
                  <td>{f.item_code} <span className="muted">{f.item_name}</span></td>
                  <td className="num">{fmt(f.quantity, 0)}</td>
                  <td className="num">{fmt(f.total_hours)}</td>
                  <td>{f.week_from ? `${weekLong(f.week_from)}${f.week_to && f.week_to !== f.week_from ? ` → ${weekLong(f.week_to)}` : ""}` : "—"}</td>
                  <td>{f.due_date}</td>
                  <td className="num">{f.line_count}</td>
                  <td>{can("poweruser") && <button className="danger small" onClick={() => removeForecast(f)}>Sil</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="muted">Henüz planda tahmin yok. Hesaplayıp &quot;Plana tahmin olarak ekle&quot; ile kaydedin.</p>}

      {res && (
        <>
          <h3>Son hesaplama</h3>
          <p><b>{res.item_code}</b> × {fmt(res.quantity, 0)} → toplam {fmt(res.total_hours)} saat · başlangıç <b>{res.start}</b> · bitiş <b>{res.end}</b></p>
          {can("poweruser") && (
            <div className="row" style={{ marginBottom: 10 }}>
              <label>Tahmin etiketi<input value={label} onChange={(e) => setLabel(e.target.value)} placeholder={`TAH-${res.item_code}`} style={{ minWidth: 200 }} /></label>
              <button onClick={addForecast} disabled={busy}>Plana tahmin olarak ekle</button>
            </div>
          )}
          <table style={{ width: "auto" }}>
            <thead><tr><th>Op.</th><th>İş Merkezi</th><th className="num">Saat</th><th>Başlangıç</th><th>Bitiş</th><th title="Senaryo matrisi: bu operasyon öncekine göre ne zaman başlar">Başlangıç kuralı</th></tr></thead>
            <tbody>{res.steps.map((s) => <tr key={s.operation_seq}><td>{s.operation_seq} {s.operation_name}</td><td>{s.work_center_code}</td><td className="num">{fmt(s.hours)}</td><td>{s.start}</td><td>{s.end}</td><td className="muted">{s.start_rule || (s === res.steps[0] ? "ilk operasyon" : "")}</td></tr>)}</tbody>
          </table>
          <p className="muted" style={{ marginBottom: 0 }}>Operasyon geçiş kuralları (iç içe başlama, bekleme) <a href="/scenarios">Senaryo Matrisi</a> sayfasından tanımlanır.</p>
        </>
      )}
    </div>
  );
}
