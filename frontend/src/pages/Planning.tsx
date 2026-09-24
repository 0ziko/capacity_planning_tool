import { Fragment, useState, useEffect } from "react";
import { AUTO_PLAN_JOB_KEY, trackedAutoPlan, addDays, api, fmt, mondayOf, qs, shortDate, weekLabel, weekLong, type DailyScheduleResult, type AutoPlanRequest, type Placement, type SlipMode, type OvertimeProposal, type PlacementNote, type PullForwardPlan, type PlanRevision, type CoShipmentOptions, type CoShipmentException, type CoShipmentResult, type ForecastSummary, type ItemDetail, type LoadDetail, type LeadTime, type MaterialPolicy, type Order, type OrderSchedule, type PlanLine, type PlanMode, type PlanningGranularity, type WcWeek, type WorkCenterLoad } from "../api";
import { useAuth } from "../auth";
import { Bar, ErrorText, PlanStackBar, UtilBadge, WeekInput, WcMultiSelect, useAsync, useWorkCenters } from "../components";
import WcWeeksPanel from "./WcWeeksPanel";
import ComparePanel from "./planning/ComparePanel";
import MergePanel from "./planning/MergePanel";
import OrderProgressPanel from "./planning/OrderProgressPanel";
import OrderSchedulePanel from "./planning/OrderSchedulePanel";
import RevenuePanel from "./planning/RevenuePanel";
import WcOrdersPanel from "./planning/WcOrdersPanel";
import ProductionOutputPanel from "./planning/ProductionOutputPanel";
import PlanPreflightModal from "./planning/PlanPreflightModal";
import CoShipmentPanel, { defaultCoShipment } from "./planning/CoShipmentPanel";
import RevisionsPanel from "./planning/RevisionsPanel";
import IdleSuggestionModal from "./planning/IdleSuggestionModal";
import PlanReportPanel from "./planning/PlanReportPanel";

interface AutoResult {
  daily_schedule?: DailyScheduleResult;
  created: number;
  message: string;
  mode: PlanMode;
  unplanned: { order_no: string; item_code: string; operation_seq: number; work_center_code: string; hours: number; reason?: string; planning_granularity?: string }[];
  skipped: { order_no?: string; batch_no?: string; kind?: string; item_code: string; revenue: number; hours: number }[];
  co_shipment_results?: CoShipmentResult[];
  co_shipment_exceptions?: CoShipmentException[];
  material_unverified?: boolean;
  material_unverified_order_ids?: number[];
  placement?: Placement;
  placement_notes?: PlacementNote[];
  overtime_proposals?: OvertimeProposal[];
  slip_mode?: SlipMode;
  prep_hours?: number;
  report_id?: number;
  report_error?: string;
}

type Tab = "load" | "labor" | "orders" | "wc" | "revenue" | "compare" | "progress" | "merge" | "leadtime" | "output" | "revision";
const TABS: { id: Tab; label: string; hint: string }[] = [
  { id: "load", label: "Haftalık yük & plan satırları", hint: "İş merkezi × hafta doluluk ve tüm plan satırları" },
  { id: "output", label: "Haftalık üretim planı", hint: "Seçilen hafta için iş merkezi bazlı üretim listesi (üretim ekibi çıktısı)" },
  { id: "labor", label: "Haftalık iş gücü", hint: "İş merkezi × hafta kişi / verimli saat / gün — haftaya özel değişiklikler" },
  { id: "revision", label: "Plan revizyonu", hint: "Nedenli taslak, yeniden hesap, onayla ve devreye al" },
  { id: "orders", label: "Sipariş bitiş tarihleri", hint: "Plan sonucuna göre her siparişin tahmini üretim bitişi" },
  { id: "wc", label: "İş merkezi bazlı siparişler", hint: "Seçilen iş merkezine planlanmış siparişler" },
  { id: "revenue", label: "Ciro", hint: "Mevcut plana göre haftalık / aylık ciro" },
  { id: "compare", label: "Plan karşılaştır", hint: "Termine göre ve ciro öncelikli planları yan yana karşılaştır" },
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
  const [planningGranularity, setPlanningGranularity] = useState<PlanningGranularity>("weekly");
  const [placement] = useState<Placement>("flow");
  const [jitBufferDays] = useState(2);
  const [idleSel, setIdleSel] = useState<{ wcId: number; wcCode: string; week: string } | null>(null);
  const [pullPlan, setPullPlan] = useState<PullForwardPlan | null>(null);
  const [pullBusy, setPullBusy] = useState(false);
  const [pullMsg, setPullMsg] = useState("");
  const previewPullForward = async () => {
    setPullBusy(true); setPullMsg("");
    try { setPullPlan(await api.postLong<PullForwardPlan>("/api/plan/pull-forward/preview", planReq)); }
    catch (e) { setPullMsg((e as Error).message); }
    finally { setPullBusy(false); }
  };
  const createPullForwardDraft = async () => {
    setPullBusy(true); setPullMsg("");
    try {
      const rev = await api.postLong<PlanRevision>("/api/plan/pull-forward/draft", planReq);
      setPullPlan(null);
      setPullMsg(`${rev.revision_no} taslağı oluşturuldu (${rev.changes.length} iş taşıma). Revizyon sekmesinde hesaplayıp onaylayın.`);
      setTab("revision");
    } catch (e) { setPullMsg((e as Error).message); }
    finally { setPullBusy(false); }
  };
  const [materialPolicy, setMaterialPolicy] = useState<MaterialPolicy>("conditional");
  const [slipMode, setSlipMode] = useState<SlipMode>("chain");
  const [useOvertime, setUseOvertime] = useState(true);
  const [prepFill, setPrepFill] = useState(true);
  const [result, setResult] = useState<AutoResult | null>(null);
  const [reportKey, setReportKey] = useState(0);
  const [err, setErr] = useState("");
  const [jobPhase, setJobPhase] = useState("");
  const [busy, setBusy] = useState(false);
  const [weekFilter, setWeekFilter] = useState("");
  const [lineMode, setLineMode] = useState("");
  const [loadDetail, setLoadDetail] = useState<{ wcId: number; wcCode: string; week: string } | null>(null);
  const [preflightOpen, setPreflightOpen] = useState(false);
  const [coShipment, setCoShipment] = useState<CoShipmentOptions>(defaultCoShipment);
  const openOrders = useAsync(() => api.get<Order[]>("/api/orders?status=open"), [], coShipment.enabled || tab === "revision");
  const load = useAsync(() => api.get<WorkCenterLoad[]>(`/api/plan/load${qs({ start, weeks, work_center_ids: wcIds })}`), [start, weeks, wcIds.join(",")]);
  const lines = useAsync(() => api.get<PlanLine[]>(`/api/plan/lines${qs({ start, end: addDays(start, weeks * 7 - 1), work_center_ids: wcIds, mode: lineMode || undefined })}`), [start, weeks, wcIds.join(","), lineMode]);
  const schedule = useAsync(() => api.get<OrderSchedule[]>(`/api/plan/orders${qs({ work_center_ids: wcIds })}`), [wcIds.join(",")], tab === "orders" || tab === "wc");
  const refresh = () => { load.reload(); lines.reload(); schedule.reload(); };
  const mergeCount = useAsync(() => api.get<unknown[]>("/api/plan/merge-suggestions").then((g) => g.length), [schedule.data?.length]);

  const planReq: AutoPlanRequest = {
    start_week: start,
    weeks,
    work_center_ids: wcIds.length ? wcIds : null,
    replace_existing: true,
    mode,
    planning_granularity: planningGranularity,
    material_policy: materialPolicy,
    placement,
    jit_buffer_days: jitBufferDays,
    slip_mode: slipMode,
    use_overtime: useOvertime,
    prep_fill: prepFill,
    ...(coShipment.enabled && coShipment.selections.length
      ? { co_shipment: coShipment }
      : {}),
  };

  useEffect(() => {
    if (!sessionStorage.getItem(AUTO_PLAN_JOB_KEY)) return;
    setBusy(true);
    trackedAutoPlan<AutoResult>(undefined, setJobPhase).then(r => { setResult(r); refresh(); })
      .catch(e => setErr((e as Error).message)).finally(() => setBusy(false));
  }, []);

  const runAuto = async (skipConfirm = false) => {
    if (!skipConfirm) {
      setPreflightOpen(true);
      return;
    }
    setBusy(true); setErr("");
    try {
      setResult(await trackedAutoPlan<AutoResult>(planReq, setJobPhase));
      refresh();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };

  const confirmAutoPlan = async (missingHeadcountAck: string | null) => {
    setPreflightOpen(false);
    setBusy(true); setErr("");
    try {
      setResult(await trackedAutoPlan<AutoResult>({ ...planReq, missing_headcount_ack: missingHeadcountAck }, setJobPhase));
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
                <label title="Termine göre: siparişler termin sırasıyla yerleştirilir. Ciro öncelikli: kalan satış değeri / kalan saat oranına göre sıralanır (sezgisel; optimum garantisi yok).">Planlama modu
                  <select value={mode} onChange={(e) => setMode(e.target.value as PlanMode)}>
                    <option value="due_date">📅 Termine göre</option>
                    <option value="revenue">💰 Ciro öncelikli (sezgisel)</option>
                  </select>
                </label>
                <label title="Malzeme bilinmiyorsa: koşullu planda işaretlenir; strict planda planlanmaz.">Malzeme politikası
                  <select value={materialPolicy} onChange={(e) => setMaterialPolicy(e.target.value as MaterialPolicy)}>
                    <option value="conditional">Koşullu (unknown işaretle)</option>
                    <option value="strict">Strict (unknown engelle)</option>
                  </select>
                </label>
                <span className="muted" title="Dengeli yerleşim: her siparişte tüm parça ve operasyonlar aynı adette ilerler (yetim parça üretilmez); ardışık operasyonlar bekletilmez; hedef bitiş = etkin termin − 2 gün.">Yerleştirme: dengeli akış · hedef termin − 2 gün</span>
                <label title="Hedef tarihe (termin − 2 gün) sığmayan adet ne olsun? Zincir etkisi: sonraki haftalara dengeli yerleşir ve sonraki siparişlerden kapasite alır (etki görünür). Komple kaydır: plana yazılmaz; tahmini bitiş ve darboğaz raporlanır, kapasite sonraki siparişlere kalır.">Kayan adet
                  <select value={slipMode} onChange={(e) => setSlipMode(e.target.value as SlipMode)}>
                    <option value="chain">Zincir etkisi (sonraya yerleştir)</option>
                    <option value="defer">Komple kaydır (plana yazma)</option>
                  </select>
                </label>
                <label title="Hedef tarihi kurtarmak için gereken saat, sınırlar içinde (hafta içi 18:00-21:00 kişi başı 2,5 sa; Cmt/Paz 08:00-18:00 8,5 sa; haftanın kişi sayısı) fazla mesai olarak önerilir ve haftalık iş gücüne 'onay bekliyor' yazılır. Hazırlık işi için fazla mesai kullanılmaz.">
                  <input type="checkbox" checked={useOvertime} onChange={(e) => setUseOvertime(e.target.checked)} />
                  Termin için fazla mesai öner
                </label>
                <label title="Termin işleri yerleştikten sonra kalan normal kapasiteye, ufukta bitmiş ürüne dönüşemeyen siparişlerin yarımamülleri (son operasyon hariç) 'hazırlık' etiketiyle yazılır. Fazla mesai kullanılmaz, terminleri etkilemez.">
                  <input type="checkbox" checked={prepFill} onChange={(e) => setPrepFill(e.target.checked)} />
                  Atıl kapasiteye hazırlık yarımamülü
                </label>
                <label title="Pilot: yalnızca tam kaynak tanımlı operasyonlar için makine aralığı segmentleri üretilir.">
                  <input type="checkbox" checked={planningGranularity === "daily_detailed"} onChange={(e) => setPlanningGranularity(e.target.checked ? "daily_detailed" : "weekly")} />
                  Günlük ayrıntılı çizelge (pilot)
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
            <CoShipmentPanel orders={openOrders.data ?? []} value={coShipment} onChange={setCoShipment}
              loading={openOrders.loading} error={openOrders.err} onReload={openOrders.reload} />
          )}
          {can("poweruser") && mode === "revenue" && coShipment.enabled && (
            <p className="muted">Birlikte sevk modu yalnızca “Termine göre” planlamada kullanılabilir.</p>
          )}
          {busy && (
            <div className="progress-pop" role="status" aria-live="polite">
              <span className="progress-dot" />
              <div>
                <div style={{ fontWeight: 600 }}>Otomatik plan hesaplanıyor</div>
                <div className="progress-phase">{jobPhase || "Başlatılıyor…"}</div>
                <div className="muted">Bittiğinde sonuç ve rapor bu sayfada otomatik açılır. Sayfadan ayrılabilirsiniz.</div>
              </div>
            </div>
          )}
          <ErrorText err={err || load.err || lines.err} />
          {result && (
            <div className="panel">
              <div className="success">{result.message}
                <button className="secondary small" style={{ marginLeft: 8 }} onClick={() => setTab("orders")}>Sipariş bitiş tarihleri →</button>{" "}
                <button className="secondary small" onClick={() => setTab("revenue")}>Ciro →</button>
              </div>
              {result.daily_schedule && <DailyResult result={result.daily_schedule} />}
              {result.material_unverified && (
                <p className="muted" style={{ marginTop: 8 }}>Malzeme doğrulanmadı: plan koşulludur ({result.material_unverified_order_ids?.length ?? 0} sipariş).</p>
              )}
              {result.skipped?.length > 0 && (
                <>
                  <div className="error">Ciro öncelikli plan {result.skipped.length} adayı tamamen dışarıda bıraktı (ufka sığmadı, kalan kapasite de yetmedi):</div>
                  <ul className="errors">{result.skipped.map((u, i) => <li key={i}>{u.batch_no ? `Parti ${u.batch_no}` : u.order_no} / {u.item_code}: {fmt(u.hours)} saat · ciro {fmt(u.revenue, 0)}</li>)}</ul>
                </>
              )}
              {(result.prep_hours ?? 0) > 0 && (
                <p className="muted" style={{ margin: "6px 0" }}>Hazırlık: {fmt(result.prep_hours)} sa yarımamül atıl kapasitede üretildi ({result.placement_notes?.filter((n) => n.kind === "prep").length ?? 0} sipariş); plan satırlarında “hazırlık” rozeti.</p>
              )}
              {(result.overtime_proposals?.length ?? 0) > 0 && (
                <div style={{ marginTop: 10 }}>
                  <b>Fazla mesai ihtiyacı (onay bekliyor · haftalık iş gücünde onaylayın):</b>
                  <div className="table-wrap" style={{ maxHeight: 260, marginTop: 4 }}>
                    <table>
                      <thead><tr><th>İş merkezi</th><th>Hafta</th><th className="num">Saat</th><th className="num">Hafta içi kişi × gün</th><th className="num">Hafta sonu kişi × gün</th><th className="num">Kişi başı saat</th><th>Hesap</th></tr></thead>
                      <tbody>
                        {result.overtime_proposals!.map((p, i) => (
                          <tr key={i}>
                            <td><b>{p.work_center_code}</b></td>
                            <td>{weekLabel(p.week_start)} <span className="muted">{shortDate(p.week_start)}</span></td>
                            <td className="num">{fmt(p.hours)}</td>
                            <td className="num">{p.weekday_persons ? `${p.weekday_persons} × ${p.weekday_days}` : "—"}</td>
                            <td className="num">{p.weekend_persons ? `${p.weekend_persons} × ${p.weekend_days}` : "—"}</td>
                            <td className="num">{fmt(p.person_hours)}</td>
                            <td className="muted">{p.explanation}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
              {(result.placement_notes?.some((n) => n.kind === "slip") ?? false) && (
                <div style={{ marginTop: 10 }}>
                  <b>Hedef tarihe sığmayan siparişler ({result.placement_notes!.filter((n) => n.kind === "slip").length}):</b>
                  <div className="table-wrap" style={{ maxHeight: 300, marginTop: 4 }}>
                    <table>
                      <thead><tr><th>Sipariş</th><th className="num">Kalan adet</th><th className="num">Hedefte</th><th className="num">Fazla mesaiyle</th><th className="num">Kayan</th><th>Darboğaz</th><th>Tahmini bitiş</th><th>Durum</th></tr></thead>
                      <tbody>
                        {result.placement_notes!.filter((n) => n.kind === "slip").map((n, i) => (
                          <tr key={i}>
                            <td><b>{n.label}</b></td>
                            <td className="num">{fmt(n.remaining_qty, 0)}</td>
                            <td className="num">{fmt(n.target_qty, 0)}</td>
                            <td className="num">{n.overtime_qty ? fmt(n.overtime_qty, 0) : "—"}</td>
                            <td className="num" style={{ color: n.qty ? "var(--bad)" : undefined }}>{fmt(n.qty, 0)}</td>
                            <td>{n.bottleneck || "—"}</td>
                            <td>{n.est_finish_week ? shortDate(n.est_finish_week) : "—"}</td>
                            <td>{n.overdue ? <span className="badge bad">Termini geçmiş</span> : n.slip_mode === "defer" ? <span className="badge warn">Plana yazılmadı</span> : <span className="badge info">Sonraya yerleşti</span>}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
              {(result.placement_notes?.filter((n) => n.kind !== "slip" && n.kind !== "prep").length ?? 0) > 0 && (
                <div style={{ marginTop: 8 }}>
                  <b>{result.placement === "jit" ? "Termine yakın yerleştirme" : "Ara stok sınırı"} notları:</b>
                  <ul className={result.placement_notes!.some((n) => n.kind === "wip_cap_violation") ? "errors" : "muted"} style={{ margin: "4px 0", paddingLeft: 18, fontSize: 12 }}>
                    {result.placement_notes!.filter((n) => n.kind !== "slip" && n.kind !== "prep").slice(0, 40).map((n, i) => <li key={i} style={{ color: n.kind === "wip_cap_violation" ? "var(--bad)" : undefined }}>{n.label}: {n.detail}</li>)}
                    {result.placement_notes!.filter((n) => n.kind !== "slip" && n.kind !== "prep").length > 40 && <li>… ve {result.placement_notes!.filter((n) => n.kind !== "slip" && n.kind !== "prep").length - 40} not daha</li>}
                  </ul>
                </div>
              )}
              {result.unplanned.length > 0 && (
                <>
                  <div className="error">Yerleştirilemeyen {result.unplanned.length} operasyon (nedenlerini aşağıda inceleyin):</div>
                  <ul className="errors">{result.unplanned.map((u, i) => <li key={i}>{u.order_no} / {u.item_code} op.{u.operation_seq} @ {u.work_center_code}: {u.reason === "operasyon_suresi_eksik" ? "Süre hesaplanamadı" : u.hours > 0 && u.hours < 0.01 ? `${fmt(u.hours * 3600, 2)} saniye` : `${fmt(u.hours)} saat`}{u.reason ? ` · ${{kapasite_yetersiz: "İzin verilen haftalarda yeterli kapasite bulunamadı", termin_kaydi: "Hedef tarihe sığmadı; komple kaydır ayarıyla plana yazılmadı", oncul_eksik: "Önceki operasyon, Senaryo Matrisi kuralının gerektirdiği miktara ulaşamadı", yarimamul_eksik: "Mamulü besleyen yarımamul miktarı yetersiz", operasyon_suresi_eksik: "Operasyon süresi sıfır veya negatif; rota süresini düzeltin", bekleme_ufuk_disinda: "Operasyonlar arası bekleme süresi plan ufkunu aşıyor", rota_dongusu: "Rotada döngü var", malzeme_unknown_strict: "Malzeme durumu bilinmiyor; katı modda planlanamaz", material_date_missing: "Beklenen malzemenin hazır olacağı tarih girilmemiş"}[u.reason] ?? u.reason}` : ""}{u.planning_granularity === "weekly_approx" ? " (haftalık yaklaşık)" : ""}</li>)}</ul>
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
          <PlanReportPanel focusId={result?.report_id ?? null} refreshKey={reportKey} canEdit={can("poweruser")} onOvertimeDecided={refresh} />
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
          materialPolicy={materialPolicy}
          orders={openOrders.data ?? []}
          wcs={wcIds.length ? wcs.filter((w) => wcIds.includes(w.id)) : wcs.filter((w) => w.is_planned)}
          canEdit={can("poweruser")}
          onApplied={() => { refresh(); setReportKey((k) => k + 1); }}
        />
      )}
      {tab === "orders" && <OrderSchedulePanel wcIds={wcIds} rows={schedule.data} err={schedule.err} onReload={schedule.reload} />}
      {tab === "wc" && <WcOrdersPanel lines={lines.data} schedule={schedule.data} wcs={wcIds.length ? wcs.filter((w) => wcIds.includes(w.id)) : wcs.filter((w) => w.is_planned)} horizon={`${start} → ${addDays(start, weeks * 7 - 1)}`} />}
      {tab === "revenue" && <RevenuePanel start={start} weeks={weeks} wcIds={wcIds} />}
      {tab === "compare" && <ComparePanel materialPolicy={materialPolicy} start={start} weeks={weeks} wcIds={wcIds} onApplied={refresh} />}
      {tab === "progress" && <OrderProgressPanel wcIds={wcIds} />}
      {tab === "merge" && <MergePanel planCtx={{ start_week: start, weeks, work_center_ids: wcIds, mode }} onChanged={() => { refresh(); mergeCount.reload(); }} onAutoPlan={() => runAuto()} />}
      {tab === "leadtime" && <LeadTimePanel onForecastAdded={refresh} onShowForecastLines={() => { setTab("load"); setLineMode("forecast"); }} />}
      {tab === "output" && <ProductionOutputPanel />}
      {tab === "load" && (<>
      <h2>Haftalık yük (saat: plan / gerçekleşen / kalan / kalan iş gün)</h2>
      {can("poweruser") && (
        <div className="panel" style={{ padding: "8px 12px", marginBottom: 10 }}>
          <div className="row" style={{ alignItems: "center" }}>
            <b>Atıl kapasite (tüm ufuk)</b>
            <span className="muted">Hücre hücre seçmek yerine: ufuktaki tüm atıl haftalar taranır, öncülü/malzemesi hazır işler sipariş başına bir kez en erken sığdığı haftaya çekilir; tek revizyon taslağı oluşur (hesapla → önce/sonra → onayla). Kalıcı çözüm için yerleştirmeyi <b>Akış</b> moduna alın.</span>
            <button className="secondary" onClick={() => void previewPullForward()} disabled={pullBusy}>Öne çekilebilir işleri hesapla</button>
            {pullPlan && (
              <>
                <span>
                  <b>{pullPlan.idle_cells}</b> atıl hücre · <b>{fmt(pullPlan.idle_hours_total, 0)}</b> sa atıl · <b>{pullPlan.moves.length}</b> iş / <b>{fmt(pullPlan.moved_hours, 0)}</b> sa öne çekilebilir
                </span>
                <button onClick={() => void createPullForwardDraft()} disabled={pullBusy || !pullPlan.moves.length}>{pullPlan.moves.length ? `${pullPlan.moves.length} işi öne çeken revizyon taslağı oluştur` : "Öne çekilebilir iş yok"}</button>
              </>
            )}
            {pullMsg && <span className="muted">{pullMsg}</span>}
          </div>
          {pullPlan && pullPlan.moves.length > 0 && (
            <div className="table-wrap" style={{ maxHeight: 220, marginTop: 6 }}>
              <table>
                <thead><tr><th>Sipariş</th><th>Müşteri</th><th>Stok</th><th>İş merkezi</th><th>Planlı hafta → hedef</th><th>Termin</th><th className="num">Saat</th></tr></thead>
                <tbody>
                  {pullPlan.moves.map((m) => (
                    <tr key={`${m.order_id}-${m.operation_seq}`}>
                      <td>{m.order_no}{m.position_no ? `/${m.position_no}` : ""}</td><td>{m.customer}</td><td>{m.item_code}</td><td>{m.work_center_code}</td>
                      <td>{weekLabel(m.from_week)} → <b>{weekLabel(m.to_week)}</b></td><td>{m.due_date}</td><td className="num">{fmt(m.hours, 1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
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
              <Fragment key={wc.work_center_id}>
              <tr>
                <td><b>{wc.work_center_code}</b>{(wc.machines?.length ?? 0) > 0 && <div className="muted" style={{ fontSize: 11 }}>{wc.machines!.length} istasyon</div>}</td>
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
                      <span style={{ fontSize: 12 }} title="Plan / standart saat karşılığı çıktı / plan uyumu kalan">{fmt(w.planned_hours, 0)} / <b style={{ color: "var(--ok)" }}>{fmt(w.standard_hour_equivalent_output ?? w.actual_hours, 0)}</b> / {fmt(w.plan_adherence_remaining_hours ?? w.remaining_hours, 0)} / <span title="Plan uyumu kalan iş gün">{fmt(w.remaining_days, 2)}</span></span>
                      <span style={{ fontSize: 11 }} className="muted">Atıl: {w.idle_hours > 0.5 ? <button type="button" className="secondary small" style={{ padding: "0 6px", fontWeight: 700 }} title="Bu haftaya öne çekilebilecek işleri göster (iş taşıma taslağı)" onClick={(e) => { e.stopPropagation(); setIdleSel({ wcId: wc.work_center_id, wcCode: wc.work_center_code, week: w.week_start }); }}>{fmt(w.idle_hours, 0)} sa ▸</button> : "yok"}{(w.overtime_hours ?? 0) > 0 && <span title="Kapasitenin fazla mesaiden (18:00-21:00) gelen kısmı"> · FM +{fmt(w.overtime_hours, 0)} sa</span>}</span>
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
              {wc.machines?.map((m) => (
                <tr key={`m-${m.machine_id}`} className="machine-row">
                  <td style={{ paddingLeft: 18 }}><span className="muted">└</span> {m.machine_code}{m.machine_name && m.machine_name !== m.machine_code ? <span className="muted" style={{ fontSize: 11 }}> {m.machine_name.replace(/^.*- /, "")}</span> : null}</td>
                  {m.weeks.map((w) => (
                    <td key={w.week_start} title={`${m.machine_code}: plan ${fmt(w.planned_hours)} sa / kapasite ${fmt(w.capacity_hours)} sa`}>
                      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                        <span style={{ fontSize: 11 }} className="muted">{fmt(w.planned_hours, 1)} / {fmt(w.capacity_hours, 0)} sa</span>
                        <Bar ratio={w.utilization} />
                        <span style={{ fontSize: 11 }}><UtilBadge u={w.utilization} /> <span className="muted">istasyon</span></span>
                      </div>
                    </td>
                  ))}
                </tr>
              ))}
              </Fragment>
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
                <td><b>{l.work_center_code}</b>{l.machine_code && <div className="muted">{l.machine_code}</div>}</td>
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
                <td>{l.item_code}<div className="muted" title={l.material_note}>{l.material_unverified !== false ? l.material_note : ""}</div></td><td>{l.operation_seq}</td>
                <td className="num">{fmt(l.planned_hours, 2)}</td><td className="num">{fmt(l.planned_qty, 0)}</td>
                <td><span className={`badge ${l.mode === "manual" ? "warn" : l.mode === "forecast" ? "ok" : "muted"}`} title={l.strategy === "revenue" ? "Otomatik · ciro öncelikli" : l.strategy === "due_date" ? "Otomatik · termine göre" : l.mode === "forecast" ? "Terminleme tahmini" : ""}>{l.mode === "manual" ? "manuel" : l.mode === "forecast" ? "tahmin" : l.strategy === "revenue" ? "oto 💰" : "oto"}</span>{l.tag === "overtime" ? <span className="badge warn" style={{ marginLeft: 4 }} title="Fazla mesai kapasitesiyle yerleşti (onay bekliyor)">FM</span> : l.tag === "slip" ? <span className="badge bad" style={{ marginLeft: 4 }} title="Hedef tarihten (termin − 2 gün) sonra">kayma</span> : l.tag === "prep" ? <span className="badge muted" style={{ marginLeft: 4 }} title="Hazırlık: atıl kapasitede ileriki sipariş yarımamülü">hazırlık</span> : null}</td>
                <td>{can("poweruser") && (<><button className="secondary small" onClick={() => setHours(l)}>Saat</button> <button className="danger small" onClick={async () => { await api.del(`/api/plan/lines/${l.id}`); refresh(); }}>Sil</button></>)}</td>
              </tr>
            ))}
            {shownLines.length === 0 && <tr><td colSpan={12} className="muted">Plan satırı yok.</td></tr>}
          </tbody>
        </table>
      </div>
      {loadDetail && <LoadDetailModal wcId={loadDetail.wcId} wcCode={loadDetail.wcCode} week={loadDetail.week} onClose={() => setLoadDetail(null)} />}
      {idleSel && (
        <IdleSuggestionModal
          wcId={idleSel.wcId}
          wcCode={idleSel.wcCode}
          week={idleSel.week}
          horizon={{ start, weeks, wcIds: wcIds.length ? wcIds : null, mode, materialPolicy, placement, jitBufferDays }}
          canEdit={can("poweruser")}
          onClose={() => setIdleSel(null)}
          onDraftCreated={() => { setIdleSel(null); setTab("revision"); }}
        />
      )}
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
                          {c.planning_mode === "line" ? <>{fmt(c.capacity_hours,2)} hat-sa · {fmt(c.required_labor_hours,2)} kişi-sa</> : <><b>{c.headcount}</b> kişi · {fmt(c.efficient_hours_per_person, 2)} sa · {c.working_days} gün</>}
                        </div>
                          <div className="muted">= {fmt(c.capacity_hours, 0)} {c.planning_mode === "line" ? "hat-saat" : "kişi-saat"}{(c.overtime_capacity_hours ?? 0) > 0 && <span title={`Fazla mesai: ${c.overtime_headcount} kişi × ${fmt(c.overtime_hours_per_person, 1)} sa × ${c.overtime_days} gün (verim ${fmt(c.overtime_efficiency_ratio, 2)})`}> · FM {c.overtime_headcount} kişi +{fmt(c.overtime_capacity_hours, 0)}</span>}</div>
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
  const [materialStatus, setMaterialStatus] = useState("unknown");
  const [materialDate, setMaterialDate] = useState("");
  const [materialPolicy, setMaterialPolicy] = useState("conditional");
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
  useEffect(() => { setRes(null); setErr(""); }, [code, qty, start, materialStatus, materialDate, materialPolicy]);
  const reload = () => { forecasts.reload(); onForecastAdded(); };
  const run = async () => {
    setErr(""); setMsg("");
    setRes(null); setBusy(true);
    try { setRes(await api.post<LeadTime>("/api/plan/leadtime", { item_code: code, quantity: qty, start, material_status: materialStatus, material_ready_date: materialDate || null, material_policy: materialPolicy })); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const addForecast = async () => {
    if (!res || res.status !== "complete") return;
    if (!confirm("Hesaplanan termin planda TAHMİN olarak kaydedilsin mi? Aynı gün içindeki sonraki terminlemeler bu yükü dikkate alır.")) return;
    setBusy(true); setErr("");
    try {
      const out = await api.post<{ created: number; message: string }>("/api/plan/leadtime/forecast", {
        item_code: res.item_code, quantity: res.quantity, label: label.trim(), steps: res.steps, status: res.status,
        material_status: res.material_status, material_ready_date: res.material_ready_date, material_policy: res.material_policy,
      });
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
      <p className="muted" style={{ marginTop: -6 }}>
        Kapasiteye göre <b>yaklaşık termin</b> (kaynak rezervasyonu yok; kesin teslim tarihi değildir).
        Aynı gün birden fazla termin hesapladığınızda, başarılı sonuçları <b>plana tahmin olarak ekleyin</b>; sonraki hesaplamalar doluluğu doğru yansıtır.
      </p>
      <div className="row">
        <label>Stok kodu<input disabled={busy} value={code} onChange={(e) => setCode(e.target.value)} /></label>
        <label>Miktar<input disabled={busy} type="number" value={qty} onChange={(e) => setQty(Number(e.target.value))} /></label>
        <label>En erken başlangıç<input disabled={busy} type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
        <label>Malzeme durumu<select disabled={busy} value={materialStatus} onChange={e => { setMaterialStatus(e.target.value); setMaterialDate(""); }}><option value="unknown">Bilinmiyor</option><option value="ready">Hazır</option><option value="expected">Bekleniyor</option></select></label>
        {materialStatus === "expected" && <label>Malzeme hazır olma tarihi<input disabled={busy} type="date" value={materialDate} onChange={e => setMaterialDate(e.target.value)} /></label>}
        <label>Malzeme politikası<select disabled={busy} value={materialPolicy} onChange={e => setMaterialPolicy(e.target.value)}><option value="conditional">Koşullu</option><option value="strict">Katı (bilinmeyeni engelle)</option></select></label>
        <button onClick={run} disabled={busy || !code || (materialStatus === "expected" && !materialDate)}>Hesapla</button>
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
                  <td><b>{f.order_no}</b><div className="muted">{f.material_status === "unknown" ? "Malzeme doğrulanmadı — koşullu tahmin" : f.material_status === "expected" ? `Malzeme bekleniyor: ${f.material_ready_date || "tarih eksik"}` : "Malzeme hazır"}</div></td>
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
          {res.material_note && <p role="status" style={{background: "#fff3e0", padding: 12}}>{res.material_note}</p>}
          {res.status !== "complete" && (
            <div className="panel" style={{ padding: "8px 12px", marginBottom: 10, background: "#fff3e0", borderColor: "#ffb74d" }}>
              <b>{res.status === "infeasible" ? "Termin hesaplanamadı" : "Kısmi termin"}</b>
              {res.failure_reason && <p style={{ margin: "6px 0 0" }}>{res.failure_reason}</p>}
              {res.remaining_hours > 0 && <p style={{ margin: "4px 0 0" }}>Yerleştirilemeyen: <b>{fmt(res.remaining_hours)}</b> saat</p>}
            </div>
          )}
          <p className="muted" style={{ fontSize: 12 }}>{res.planning_note}</p>
          <p>
            <b>{res.item_code}</b> × {fmt(res.quantity, 0)} → toplam {fmt(res.total_hours)} saat
            {res.start && <> · başlangıç <b>{res.start}</b></>}
            {res.end ? <> · bitiş <b>{res.end}</b></> : res.status !== "complete" ? <> · bitiş <b>—</b></> : null}
          </p>
          {can("poweruser") && (
            <div className="row" style={{ marginBottom: 10 }}>
              <label>Tahmin etiketi<input value={label} onChange={(e) => setLabel(e.target.value)} placeholder={`TAH-${res.item_code}`} style={{ minWidth: 200 }} /></label>
              <button onClick={addForecast} disabled={busy || res.status !== "complete"} title={res.status !== "complete" ? "Yalnızca tam başarılı termin plana eklenebilir" : undefined}>
                Plana tahmin olarak ekle
              </button>
            </div>
          )}
          <table style={{ width: "auto" }}>
            <thead><tr><th>Op.</th><th>İş Merkezi</th><th className="num">Saat</th><th className="num">Yerleşen</th><th className="num">Kalan</th><th>Başlangıç</th><th>Bitiş</th><th title="Senaryo matrisi: bu operasyon öncekine göre ne zaman başlar">Başlangıç kuralı</th></tr></thead>
            <tbody>{res.steps.map((s) => (
              <tr key={s.operation_seq} className={s.status !== "scheduled" ? "warn-row" : undefined}>
                <td>{s.operation_seq} {s.operation_name}</td>
                <td>{s.work_center_code}</td>
                <td className="num">{fmt(s.hours)}</td>
                <td className="num">{fmt(s.scheduled_hours ?? s.hours)}</td>
                <td className="num">{s.remaining_hours > 0 ? fmt(s.remaining_hours) : "—"}</td>
                <td>{s.start ?? "—"}</td>
                <td>{s.end ?? "—"}</td>
                <td className="muted">{s.start_rule || (s === res.steps[0] ? "ilk operasyon" : "")}</td>
              </tr>
            ))}</tbody>
          </table>
          <p className="muted" style={{ marginBottom: 0 }}>Operasyon geçiş kuralları (iç içe başlama, bekleme) <a href="/scenarios">Senaryo Matrisi</a> sayfasından tanımlanır.</p>
        </>
      )}
    </div>
  );
}


function DailyResult({ result }: { result: DailyScheduleResult }) {
  const reasons: Record<string, string> = {
    missing_resource_definition: "Operasyon kaynak tanımı eksik",
    missing_wip_routing: "Bağlı yarımamul kartı veya rotası eksik",
    predecessor_incomplete: "Önceki operasyon / yarımamul üretimi yetersiz",
    insufficient_capacity: "Makine veya iş gücü kapasitesi yetersiz",
    setup_unknown: "Hazırlık süresi tanımlı değil",
    no_eligible_machine: "Uygun aktif makine yok",
    no_machine_hours: "Makine süresi tanımlı değil",
    material_unknown: "Malzeme durumu doğrulanmadı",
    material_date_missing: "Beklenen malzemenin hazır olacağı tarih girilmemiş",
  };
  const issues = [...result.skipped, ...result.remaining_qty];
  return <section>
    <h3>Günlük çizelge (pilot)</h3>
    <p>{result.segments_created} hazırlık / üretim aralığı oluşturuldu.</p>
    {issues.length > 0 ? <details open>
      <summary className="error">{issues.length} operasyon günlük çizelgede eksik veya yerleşemedi. Çizelge tamamlanmadı.</summary>
      <ul className="errors" style={{ maxHeight: 240, overflowY: "auto" }}>
        {issues.map((issue, index) => <li key={index}>
          {issue.order_no} / {issue.item_code || "—"} op.{issue.operation_seq}: {reasons[issue.reason || "insufficient_capacity"] || issue.reason}
          {"remaining_qty" in issue && <> · kalan {fmt(issue.remaining_qty)} adet</>}
          {"wip_codes" in issue && issue.wip_codes?.length ? <> · {issue.wip_codes.join(", ")}</> : null}
        </li>)}
      </ul>
    </details> : <p>Günlük çizelgeye aktarılan operasyonlarda kalan miktar veya eksik kaynak tanımı bildirilmedi.</p>}
  </section>;
}
