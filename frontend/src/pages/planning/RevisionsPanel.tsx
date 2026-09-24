import { useEffect, useMemo, useState } from "react";
import {
  api,
  ApiError,
  fmt,
  trackedRevisionCalculate,
  REVISION_REASONS,
  type JobMovePreview,
  type Order,
  type PlanMode,
  type MaterialPolicy,
  type PlanRevision,
  type PlanRevisionChange,
  type PlanRevisionOrderDiff,
  type OvertimeSuggestion,
  type WorkCenter,
} from "../../api";
import { ErrorText, OrderMultiSelect, useAsync } from "../../components";
import PlanPreflightModal from "./PlanPreflightModal";
import {
  DIFF_FILTERS,
  applyToDrafts,
  buildDueDrafts,
  deltaLabel,
  diffsToCsv,
  draftsToChanges,
  filterDiffs,
  statusBadge,
  summarize,
  unmetChangeIds,
  type DiffFilter,
} from "./revisionDiff";

const STATUS: Record<string, [string, string]> = {
  draft: ["warn", "Taslak"],
  calculated: ["info", "Hesaplandı"],
  applied: ["ok", "Uygulandı"],
  rejected: ["bad", "Reddedildi"],
  cancelled: ["muted", "İptal"],
  superseded: ["muted", "Geçersiz"],
};

function StatusBadge({ status }: { status: string }) {
  const [cls, label] = STATUS[status] || ["muted", status];
  return <span className={`badge ${cls}`}>{label}</span>;
}

function reasonLabel(code: string) {
  return REVISION_REASONS.find((r) => r.id === code)?.label || code;
}

function changeLabel(c: PlanRevisionChange) {
  if (c.field === "job_move") {
    try {
      const v = JSON.parse(c.new_value || "{}") as { start_date?: string; qty_mode?: string; quantity?: number | null; item_code?: string };
      const qty = v.qty_mode === "split" ? `${v.quantity} adet` : "tüm kalan";
      return `İş taşıma · ${v.item_code || c.extra_key} · ${v.start_date || "?"} · ${qty}`;
    } catch {
      return c.new_value;
    }
  }
  return c.new_value || "—";
}

export default function RevisionsPanel({
  start,
  weeks,
  wcIds,
  mode,
  materialPolicy,
  orders,
  wcs,
  canEdit,
  onApplied,
}: {
  start: string;
  weeks: number;
  wcIds: number[];
  mode: PlanMode;
  materialPolicy: MaterialPolicy;
  orders: Order[];
  wcs: WorkCenter[];
  canEdit: boolean;
  onApplied: () => void;
}) {
  const list = useAsync(() => api.get<PlanRevision[]>("/api/plan/revisions"), []);
  const [selId, setSelId] = useState<number | null>(null);
  const [err, setErr] = useState("");
  const [conflict, setConflict] = useState("");
  const [busy, setBusy] = useState(false);
  const [reasons, setReasons] = useState<string[]>(["other"]);
  const [note, setNote] = useState("");
  const [replaceManual, setReplaceManual] = useState(false);
  const [slipMode, setSlipMode] = useState<"chain" | "defer">("chain");
  const [useOvertime, setUseOvertime] = useState(true);
  const [prepFill, setPrepFill] = useState(true);
  const [orderIds, setOrderIds] = useState<number[]>([]);
  const [dueByOrder, setDueByOrder] = useState<Record<number, string>>({});
  const [applyDate, setApplyDate] = useState("");
  const [diffFilter, setDiffFilter] = useState<DiffFilter>("requested");
  const [diffQuery, setDiffQuery] = useState("");
  const [wcId, setWcId] = useState(0);
  const [week, setWeek] = useState(start);
  const [headcount, setHeadcount] = useState("");
  const [movePreviews, setMovePreviews] = useState<Record<number, JobMovePreview>>({});
  const [moveQtyMode, setMoveQtyMode] = useState<"remaining" | "split">("remaining");
  const [moveQty, setMoveQty] = useState("");
  const [moveStart, setMoveStart] = useState(start);
  const [approvalOpen, setApprovalOpen] = useState(false);
  const [calcPhase, setCalcPhase] = useState("");
  const [otHeadcount, setOtHeadcount] = useState("");
  const [otDays, setOtDays] = useState("");
  const [otSuggest, setOtSuggest] = useState<OvertimeSuggestion | null>(null);
  const [otErr, setOtErr] = useState("");
  const [placement] = useState<"asap" | "jit" | "flow">("flow");
  const [jitBufferDays] = useState(2);

  const selected = useMemo(() => (list.data ?? []).find((r) => r.id === selId) || null, [list.data, selId]);
  const editable = selected && (selected.status === "draft" || selected.status === "calculated");
  const singleOrderId = orderIds.length === 1 ? orderIds[0] : 0;
  const movePreview = singleOrderId ? movePreviews[singleOrderId] ?? null : null;

  useEffect(() => {
    if (!orderIds.length) {
      setMovePreviews({});
      return;
    }
    let cancelled = false;
    Promise.all(
      orderIds.map((id) =>
        api
          .get<JobMovePreview>(`/api/plan/revisions/move-preview?order_id=${id}`)
          .then((p) => ({ id, p }))
          .catch(() => null),
      ),
    ).then((rows) => {
      if (cancelled) return;
      const next: Record<number, JobMovePreview> = {};
      for (const row of rows) {
        if (row) next[row.id] = row.p;
      }
      setMovePreviews(next);
      const one = orderIds.length === 1 ? next[orderIds[0]] : undefined;
      if (one?.movable_qty) setMoveQty(String(one.movable_qty));
    });
    return () => {
      cancelled = true;
    };
  }, [orderIds]);

  const run = async (fn: () => Promise<PlanRevision>) => {
    setBusy(true);
    setErr("");
    setConflict("");
    try {
      const r = await fn();
      setSelId(r.id);
      await list.reload();
      if (r.status === "applied") onApplied();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setConflict(e.message);
      } else {
        setErr((e as Error).message);
      }
    } finally {
      setBusy(false);
    }
  };

  const create = () =>
    run(() =>
      api.post<PlanRevision>("/api/plan/revisions", {
        reason_codes: reasons,
        note,
        start_week: start,
        weeks,
        mode,
        material_policy: materialPolicy,
        placement,
        jit_buffer_days: jitBufferDays,
        slip_mode: slipMode,
        use_overtime: useOvertime,
        prep_fill: prepFill,
        work_center_ids: wcIds.length ? wcIds : null,
        replace_manual: replaceManual,
      }),
    );

  const drafts = useMemo(() => buildDueDrafts(orders, orderIds, dueByOrder), [orders, orderIds, dueByOrder]);
  const dueChanges = useMemo(() => draftsToChanges(drafts), [drafts]);
  const setDraftDue = (id: number, value: string) => setDueByOrder((m) => ({ ...m, [id]: value }));
  const applyDrafts = (mode: "same" | "shift", value: string | number) => {
    const next = applyToDrafts(drafts, mode, value);
    setDueByOrder((m) => ({ ...m, ...Object.fromEntries(next.map((d) => [d.order_id, d.new_due])) }));
  };

  const addDue = () => {
    if (!selected || !dueChanges.length) return;
    run(() => api.post<PlanRevision>(`/api/plan/revisions/${selected.id}/changes/bulk`, { changes: dueChanges })).then(() => {
      setDueByOrder({});
      setOrderIds([]);
    });
  };

  const cmp = selected?.compare;
  const diffRows: PlanRevisionOrderDiff[] = cmp?.order_diffs ?? [];
  const diffSummary = cmp?.diff_summary ?? summarize(diffRows);
  const visibleDiffs = useMemo(() => filterDiffs(diffRows, diffFilter, diffQuery), [diffRows, diffFilter, diffQuery]);
  const unmetIds = useMemo(() => unmetChangeIds(diffRows), [diffRows]);

  const removeUnmetAndRecalculate = () => {
    if (!selected || !unmetIds.length) return;
    run(async () => {
      await api.post<PlanRevision>(`/api/plan/revisions/${selected.id}/changes/remove`, { change_ids: unmetIds });
      return trackedRevisionCalculate(selected.id, setCalcPhase);
    });
  };

  const downloadCsv = () => {
    if (!selected) return;
    const blob = new Blob([diffsToCsv(visibleDiffs)], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${selected.revision_no}-${diffFilter}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const addMove = () => {
    if (!selected || !orderIds.length || !moveStart) return;
    const eligible = orderIds.filter((id) => (movePreviews[id]?.movable_qty ?? 0) > 0);
    if (!eligible.length) return;
    if (moveQtyMode === "split" && orderIds.length > 1) return;
    const changes = eligible.map((entity_id) => {
      const preview = movePreviews[entity_id];
      const item = preview?.item_code || orders.find((o) => o.id === entity_id)?.item_code || "";
      return {
        entity_type: "order",
        entity_id,
        extra_key: item,
        field: "job_move",
        new_value: JSON.stringify({
          item_code: item,
          start_date: moveStart,
          qty_mode: moveQtyMode,
          quantity: moveQtyMode === "split" ? Number(moveQty) : null,
        }),
      };
    });
    run(() => api.post<PlanRevision>(`/api/plan/revisions/${selected.id}/changes/bulk`, { changes }));
  };

  const addLabor = () => {
    if (!selected || !wcId || !week || (!headcount && !otHeadcount)) return;
    const changes: { entity_type: string; extra_key: string; field: string; new_value: string }[] = [];
    if (headcount) changes.push({ entity_type: "wc_week", extra_key: `${wcId}|${week}`, field: "headcount", new_value: headcount });
    if (otHeadcount) {
      changes.push({ entity_type: "wc_week", extra_key: `${wcId}|${week}`, field: "overtime_headcount", new_value: otHeadcount });
      if (otDays) changes.push({ entity_type: "wc_week", extra_key: `${wcId}|${week}`, field: "overtime_days", new_value: otDays });
      changes.push({ entity_type: "wc_week", extra_key: `${wcId}|${week}`, field: "overtime_hours_per_person", new_value: "2.5" });
    }
    run(() => api.post<PlanRevision>(`/api/plan/revisions/${selected.id}/changes/bulk`, { changes })).then(() => {
      setOtHeadcount("");
      setOtDays("");
    });
  };

  // Hesaplanmış revizyon için fazla mesai önerisi (salt hesap; planlamacı taslağa eklerse etkinleşir)
  useEffect(() => {
    setOtSuggest(null);
    setOtErr("");
    if (!selected || selected.status !== "calculated") return;
    let cancelled = false;
    api
      .get<OvertimeSuggestion>(`/api/plan/revisions/${selected.id}/overtime-suggestions`)
      .then((s) => { if (!cancelled) setOtSuggest(s); })
      .catch((e) => { if (!cancelled) setOtErr((e as Error).message); });
    return () => { cancelled = true; };
  }, [selected?.id, selected?.status, selected?.calculated_at]);

  const applyOvertimeSuggestions = (rows: OvertimeSuggestion["suggestions"]) => {
    if (!selected || !rows.length) return;
    const changes = rows.flatMap((r) => [
      { entity_type: "wc_week", extra_key: `${r.work_center_id}|${r.week_start}`, field: "overtime_headcount", new_value: String(r.suggested_overtime_headcount) },
      { entity_type: "wc_week", extra_key: `${r.work_center_id}|${r.week_start}`, field: "overtime_days", new_value: String(r.overtime_days) },
      { entity_type: "wc_week", extra_key: `${r.work_center_id}|${r.week_start}`, field: "overtime_hours_per_person", new_value: String(r.overtime_hours_per_person) },
    ]);
    run(async () => {
      await api.post<PlanRevision>(`/api/plan/revisions/${selected.id}/changes/bulk`, { changes });
      return trackedRevisionCalculate(selected.id, setCalcPhase);
    });
  };

  return (
    <div>
      {approvalOpen && selected && <PlanPreflightModal revision
        req={{ start_week: selected.start_week, weeks: selected.weeks, work_center_ids: selected.work_center_ids, material_policy: selected.material_policy, mode: selected.mode as PlanMode }}
        preflightUrl={`/api/plan/revisions/${selected.id}/preflight`}
        onClose={() => setApprovalOpen(false)}
        onConfirm={(ack) => { setApprovalOpen(false); void run(() => api.post(`/api/plan/revisions/${selected.id}/approve${ack ? `?missing_headcount_ack=${encodeURIComponent(ack)}` : ""}`)); }} />}
      <h2>Plan revizyonu</h2>
      <p className="muted" style={{ marginTop: -8 }}>
        Nedeni kaydedin; vade, haftalık iş gücü veya <b>iş taşıma</b> (kalan operasyon + yeni başlangıç) ekleyin.
        Canlı plan ancak <b>Onayla ve devreye al</b> ile değişir. Hedef haftada yer varsa işiniz öncelikli yerleşir;
        kaydırılan yalnızca tükettiğiniz iş merkezlerindeki çakışan işlerdir.
      </p>
      <ErrorText err={err || list.err} />

      {canEdit && (
        <div className="panel">
          <h3 style={{ marginTop: 0 }}>Yeni revizyon</h3>
          <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
            {REVISION_REASONS.map((r) => (
              <label key={r.id} style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <input
                  type="checkbox"
                  checked={reasons.includes(r.id)}
                  onChange={(e) =>
                    setReasons((prev) => (e.target.checked ? [...prev, r.id] : prev.filter((x) => x !== r.id)))
                  }
                />
                {r.label}
              </label>
            ))}
          </div>
          <label style={{ display: "block", marginTop: 8 }}>
            Açıklama
            <input value={note} onChange={(e) => setNote(e.target.value)} style={{ width: "100%" }} />
          </label>
          <label style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8 }}>
            <input type="checkbox" checked={replaceManual} onChange={(e) => setReplaceManual(e.target.checked)} />
            Manuel plan satırlarını da yeniden hesapla
          </label>
          <div style={{ display: "flex", gap: 16, alignItems: "center", marginTop: 8, flexWrap: "wrap" }}>
            <label title="Hedef tarihe sığmayan adet: zincir etkisi = sonraya dengeli yerleşir; komple kaydır = plana yazılmaz, raporlanır">Kayan adet{" "}
              <select value={slipMode} onChange={(e) => setSlipMode(e.target.value as "chain" | "defer")}>
                <option value="chain">Zincir etkisi</option>
                <option value="defer">Komple kaydır</option>
              </select>
            </label>
            <label style={{ display: "flex", gap: 6, alignItems: "center" }} title="Termini kurtarmak için sınırlar içinde fazla mesai önerilsin (onaylanınca haftalık iş gücüne yazılır)">
              <input type="checkbox" checked={useOvertime} onChange={(e) => setUseOvertime(e.target.checked)} />
              Termin için fazla mesai öner
            </label>
            <label style={{ display: "flex", gap: 6, alignItems: "center" }} title="Kalan normal kapasiteye hazırlık yarımamülü (son operasyon hariç, fazla mesai yok)">
              <input type="checkbox" checked={prepFill} onChange={(e) => setPrepFill(e.target.checked)} />
              Atıl kapasiteye hazırlık yarımamülü
            </label>
          </div>
          <p className="muted">Malzeme: {materialPolicy === "strict" ? "Katı" : "Koşullu"} · Ufuk: {start} · {weeks} hafta · {mode === "revenue" ? "ciro öncelikli (sezgisel)" : "termine göre"}</p>
          <button onClick={() => void create()} disabled={busy || reasons.length === 0}>
            Taslak oluştur
          </button>
        </div>
      )}

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>No</th>
              <th>Durum</th>
              <th>Neden</th>
              <th>Oluşturan</th>
              <th>Onay</th>
            </tr>
          </thead>
          <tbody>
            {(list.data ?? []).map((r) => (
              <tr key={r.id} onClick={() => setSelId(r.id)} style={{ cursor: "pointer", background: selId === r.id ? "#eef2f6" : undefined }}>
                <td><code>{r.revision_no}</code></td>
                <td><StatusBadge status={r.status} /></td>
                <td>{r.reason_codes.map(reasonLabel).join(", ")}</td>
                <td>{r.created_by}<br /><span className="muted">Malzeme: {r.material_policy === "strict" ? "Katı" : "Koşullu"}</span></td>
                <td>{r.approved_by || "—"}</td>
              </tr>
            ))}
            {!list.data?.length && (
              <tr><td colSpan={5} className="muted">Henüz revizyon yok</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {selected && (
        <div className="panel" style={{ marginTop: 12 }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h3 style={{ margin: 0 }}>
              {selected.revision_no} <StatusBadge status={selected.status} />
            </h3>
            {canEdit && editable && (
              <div className="row">
                <button className="secondary" onClick={() => void run(() => trackedRevisionCalculate(selected.id, setCalcPhase))} disabled={busy}>
                  Yeniden hesapla
                </button>
                {selected.status === "calculated" && (
                  <button onClick={() => setApprovalOpen(true)} disabled={busy}>
                    Onayla ve devreye al
                  </button>
                )}
                <button className="danger" onClick={() => void run(() => api.post(`/api/plan/revisions/${selected.id}/reject?note=`))} disabled={busy}>
                  Reddet
                </button>
                <button className="secondary" onClick={() => void run(() => api.post(`/api/plan/revisions/${selected.id}/cancel`))} disabled={busy}>
                  İptal
                </button>
              </div>
            )}
          </div>
          <p>{selected.note || <span className="muted">Açıklama yok</span>}</p>
          {calcPhase && (
            <div className="panel" style={{ padding: "8px 12px", marginBottom: 10, background: "#e3f2fd", borderColor: "#90caf9" }}>
              ⏳ Yeniden hesap arka planda sürüyor — {calcPhase}. Sayfayı kapatabilirsiniz; sonuç revizyon kaydına yazılır.
            </div>
          )}
          {conflict && (
            <div className="panel" style={{ padding: "8px 12px", marginBottom: 10, background: "#fff3e0", borderColor: "#ffb74d" }}>
              <b>Onay uygulanamadı</b>
              <p style={{ margin: "6px 0 8px" }}>{conflict}</p>
              {selected.status === "calculated" && (
                <button className="secondary" onClick={() => void run(() => trackedRevisionCalculate(selected.id, setCalcPhase))} disabled={busy}>
                  Yeniden hesapla
                </button>
              )}
            </div>
          )}
          {selected.apply_message && !conflict && <div className="success">{selected.apply_message}</div>}
          {selected.input_fingerprint && selected.status === "calculated" && (
            <p className="muted" style={{ fontSize: 11 }}>Veri sürümü: <code>{selected.input_fingerprint.slice(0, 12)}…</code> — onayda güncel veri ile eşleşmeli.</p>
          )}

          {canEdit && editable && (
            <>
              <h4>Taslak girdiler</h4>
              <div className="row" style={{ flexWrap: "wrap", alignItems: "flex-end", gap: 12 }}>
                <OrderMultiSelect orders={orders} value={orderIds} onChange={setOrderIds} label="Siparişler (müşteri / sipariş / stok ile arayın)" />
              </div>
              {drafts.length > 0 && (
                <>
                  <h4 style={{ marginBottom: 4 }}>Termin talepleri</h4>
                  <p className="muted" style={{ marginTop: 0 }}>
                    Her siparişe kendi yeni termini girilir. Kısayollar mevcut (etkin) termine göre uygular; boş bırakılan satır taslağa girmez.
                  </p>
                  <div className="row" style={{ flexWrap: "wrap", alignItems: "flex-end", gap: 8, marginBottom: 6 }}>
                    <label>
                      Tümüne aynı termin
                      <input type="date" value={applyDate} onChange={(e) => setApplyDate(e.target.value)} />
                    </label>
                    <button className="secondary" onClick={() => applyDate && applyDrafts("same", applyDate)} disabled={busy || !applyDate}>Uygula</button>
                    <span className="muted">veya termine göre:</span>
                    {[-7, -14, -21, 7, 14].map((d) => (
                      <button key={d} className="secondary small" onClick={() => applyDrafts("shift", d)} disabled={busy}>
                        {d > 0 ? `+${d}` : d} gün
                      </button>
                    ))}
                    <button className="secondary small" onClick={() => setDueByOrder({})} disabled={busy}>Temizle</button>
                  </div>
                  <div className="table-wrap rev-due" style={{ maxHeight: 320 }}>
                    <table>
                      <thead>
                        <tr>
                          <th>Sipariş</th>
                          <th>Müşteri</th>
                          <th>Stok</th>
                          <th>Etkin termin</th>
                          <th>Plan bitişi</th>
                          <th>Yeni termin</th>
                          <th>Fark</th>
                        </tr>
                      </thead>
                      <tbody>
                        {drafts.map((d) => {
                          const diff = d.new_due && d.new_due !== d.current_due ? Math.round((Date.parse(d.new_due) - Date.parse(d.current_due)) / 86400000) : null;
                          return (
                            <tr key={d.order_id} className={diff !== null ? "changed" : ""}>
                              <td>{d.order_no}{d.position_no ? `/${d.position_no}` : ""}</td>
                              <td>{d.customer}</td>
                              <td>{d.item_code}</td>
                              <td>{d.current_due}</td>
                              <td>{d.planned_end || <span className="muted">—</span>}</td>
                              <td><input type="date" value={d.new_due} onChange={(e) => setDraftDue(d.order_id, e.target.value)} /></td>
                              <td className={diff === null ? "muted" : diff < 0 ? "delta-minus" : "delta-plus"}>{diff === null ? "—" : deltaLabel(diff)}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  <div className="row" style={{ marginTop: 6 }}>
                    <button onClick={addDue} disabled={busy || !dueChanges.length}>
                      {dueChanges.length ? `${dueChanges.length} siparişe revize termin ekle` : "Yeni termin girin"}
                    </button>
                  </div>
                </>
              )}
              <div className="row" style={{ flexWrap: "wrap", marginTop: 8 }}>
                <label>
                  İş merkezi
                  <select value={wcId} onChange={(e) => setWcId(Number(e.target.value))}>
                    <option value={0}>—</option>
                    {wcs.map((w) => (
                      <option key={w.id} value={w.id}>{w.code}</option>
                    ))}
                  </select>
                </label>
                <label>
                  Hafta (Pzt)
                  <input type="date" value={week} onChange={(e) => setWeek(e.target.value)} />
                </label>
                <label>
                  Kişi sayısı
                  <input type="number" min={0} value={headcount} onChange={(e) => setHeadcount(e.target.value)} />
                </label>
                <label title="18:00-21:00 penceresinde fazla mesai yapacak kişi; haftanın kişi sayısını aşamaz">
                  Fazla mesai kişi
                  <input type="number" min={0} value={otHeadcount} onChange={(e) => setOtHeadcount(e.target.value)} />
                </label>
                <label title="Fazla mesai gün sayısı (boş = tüm çalışma günleri); kişi başı 2,5 saat sabittir">
                  FM gün
                  <input type="number" min={0} max={7} value={otDays} onChange={(e) => setOtDays(e.target.value)} disabled={!otHeadcount} />
                </label>
                <button className="secondary" onClick={addLabor} disabled={busy || !wcId || !week || (!headcount && !otHeadcount)}>İş gücü ekle</button>
              </div>
              <h4>İş taşıma (kalan zincir)</h4>
              <p className="muted" style={{ marginTop: -8 }}>
                Yukarıdaki sipariş seçiminden bir veya birden fazla sipariş seçin. Tamamlanan operasyonlar kilitlenir.
                Yeni tarih yalnızca ilk kalan operasyonun başlangıcıdır. Toplu iş taşımada miktar &quot;tüm kalan&quot; kullanılır;
                kısmi bölme yalnızca tek sipariş seçiliyken kullanılabilir.
              </p>
              {orderIds.length > 1 && (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Sipariş</th>
                        <th>Stok</th>
                        <th>Kalan</th>
                        <th>Mevcut başlangıç</th>
                        <th>Etkilenen İM</th>
                      </tr>
                    </thead>
                    <tbody>
                      {orderIds.map((id) => {
                        const o = orders.find((x) => x.id === id);
                        const p = movePreviews[id];
                        return (
                          <tr key={id} style={{ opacity: p && p.movable_qty <= 0 ? 0.5 : 1 }}>
                            <td>{o?.order_no}{o?.position_no ? `/${o.position_no}` : ""}</td>
                            <td>{p?.item_code || o?.item_code || "—"}</td>
                            <td>{p ? fmt(p.movable_qty) : "…"}</td>
                            <td>{p?.current_start || "—"}</td>
                            <td>{p?.consumed_work_centers?.join(", ") || "—"}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
              {movePreview && (
                <>
                  <p>
                    Kalan miktar: <b>{fmt(movePreview.movable_qty)}</b>
                    {movePreview.current_start ? ` · mevcut plan başlangıcı ${movePreview.current_start}` : ""}
                    {movePreview.consumed_work_centers.length
                      ? ` · etkilenen İM: ${movePreview.consumed_work_centers.join(", ")}`
                      : ""}
                  </p>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Op</th>
                          <th>İş merkezi</th>
                          <th>Üretilen</th>
                          <th>Kalan</th>
                          <th>Durum</th>
                        </tr>
                      </thead>
                      <tbody>
                        {movePreview.ops.map((op) => (
                          <tr key={op.operation_id} style={{ opacity: op.locked ? 0.55 : 1 }}>
                            <td>{op.operation_seq} {op.operation_name}{op.semi_finished_code ? ` · ${op.semi_finished_code}` : ""}</td>
                            <td>{op.work_center_code}</td>
                            <td>{fmt(op.produced_qty)}</td>
                            <td>{fmt(op.remaining_qty)}</td>
                            <td>{op.locked ? "Kilitli (tamam)" : op.status === "current" ? "İlk kalan" : "Taşınacak"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
              <div className="row" style={{ flexWrap: "wrap", marginTop: 8 }}>
                <label>
                  Miktar
                  <select value={moveQtyMode} onChange={(e) => setMoveQtyMode(e.target.value as "remaining" | "split")}>
                    <option value="remaining">Tüm kalan</option>
                    <option value="split">Kısmi (böl)</option>
                  </select>
                </label>
                {moveQtyMode === "split" && orderIds.length === 1 && (
                  <label>
                    Taşınacak adet
                    <input type="number" min={0} step="any" value={moveQty} onChange={(e) => setMoveQty(e.target.value)} />
                  </label>
                )}
                {moveQtyMode === "split" && orderIds.length > 1 && (
                  <span className="muted">Kısmi bölme için tek sipariş seçin</span>
                )}
                <label>
                  Yeni başlangıç (hafta)
                  <input type="date" value={moveStart} onChange={(e) => setMoveStart(e.target.value)} />
                </label>
                <button
                  className="secondary"
                  onClick={addMove}
                  disabled={
                    busy ||
                    !orderIds.length ||
                    (orderIds.length === 1 && (!movePreview || movePreview.movable_qty <= 0)) ||
                    (orderIds.length > 1 && !orderIds.some((id) => (movePreviews[id]?.movable_qty ?? 0) > 0)) ||
                    (moveQtyMode === "split" && orderIds.length > 1)
                  }
                >
                  {orderIds.length > 1
                    ? `${orderIds.filter((id) => (movePreviews[id]?.movable_qty ?? 0) > 0).length} siparişe iş taşıma ekle`
                    : "İş taşıma ekle"}
                </button>
              </div>
            </>
          )}

          <div className="table-wrap" style={{ marginTop: 10 }}>
            <table>
              <thead><tr><th>Tür</th><th>Alan</th><th>Eski</th><th>Yeni</th><th /></tr></thead>
              <tbody>
                {selected.changes.map((c) => (
                  <tr key={c.id}>
                    <td>{c.entity_type}{c.extra_key ? ` ${c.extra_key}` : ` #${c.entity_id}`}</td>
                    <td>{c.field}</td>
                    <td>{c.old_value || "—"}</td>
                    <td>{changeLabel(c)}</td>
                    <td>
                      {canEdit && editable && (
                        <button className="danger small" onClick={() => void run(() => api.del(`/api/plan/revisions/${selected.id}/changes/${c.id}`))}>Sil</button>
                      )}
                    </td>
                  </tr>
                ))}
                {!selected.changes.length && <tr><td colSpan={5} className="muted">Girdi yok — yine de mevcut kapasiteyle yeniden hesaplayabilirsiniz</td></tr>}
              </tbody>
            </table>
          </div>

          {cmp && (
            <>
              <h4>Canlı plan vs önerilen</h4>
              <div className="row">
                <div className="kpi"><span className="v">{fmt(cmp.baseline.planned_hours)}</span><span className="l">Canlı saat</span></div>
                <div className="kpi"><span className="v">{fmt(cmp.proposed.planned_hours)}</span><span className="l">Önerilen saat</span></div>
                <div className={`kpi ${cmp.proposed.late > cmp.baseline.late ? "bad" : cmp.proposed.late < cmp.baseline.late ? "ok" : ""}`}><span className="v">{cmp.baseline.late} → {cmp.proposed.late}</span><span className="l">Geç sipariş</span></div>
                <div className="kpi"><span className="v">{cmp.baseline.on_time} → {cmp.proposed.on_time}</span><span className="l">Zamanında</span></div>
                <div className="kpi"><span className="v">{cmp.baseline.unplanned} → {cmp.proposed.unplanned}</span><span className="l">Plansız</span></div>
              </div>
              {!!cmp.insert_notes?.length && (
                <p>{cmp.insert_notes.join(" · ")}</p>
              )}
              {!!cmp.unplanned?.length && (
                <p className="muted">
                  Ufka sığmayan: {cmp.unplanned.map((u) => `${u.order_no || "?"} ${u.work_center_code || ""} ${u.hours ?? ""}s`).join("; ")}
                </p>
              )}

              {(otSuggest || otErr) && (
                <div className="panel" style={{ marginTop: 10, background: otSuggest && otSuggest.suggestions.length ? "#fffbeb" : undefined, borderColor: otSuggest && otSuggest.suggestions.length ? "#fcd34d" : undefined }}>
                  <h4 style={{ marginTop: 0, marginBottom: 4 }}>Fazla mesai önerisi <span className="muted" style={{ fontWeight: 400 }}>({otSuggest?.window ?? "18:00-21:00"}, kişi başı en fazla {fmt(otSuggest?.max_hours_per_person ?? 2.5, 1)} sa; haftanın kişi sayısı ve çalışma günü aşılmaz)</span></h4>
                  {otErr && <ErrorText err={otErr} />}
                  {otSuggest && !otSuggest.suggestions.length && !otSuggest.notes.length && (
                    <p className="muted" style={{ margin: 0 }}>Önerilen planda kapasite yetersizliğinden plansız kalan iş yok; fazla mesai gerekmiyor.</p>
                  )}
                  {otSuggest && otSuggest.suggestions.length > 0 && (
                    <>
                      <p style={{ margin: "4px 0 8px" }}>
                        Kapasite açığı <b>{fmt(otSuggest.total_shortfall_hours, 1)} sa</b>; fazla mesai ile <b>{fmt(otSuggest.covered_hours, 1)} sa</b> kapatılabilir
                        {otSuggest.uncovered_hours > 0 && <>, <b className="badge bad">{fmt(otSuggest.uncovered_hours, 1)} sa açık kalır</b></>}.
                        Öneriyi taslağa eklemek canlıyı değiştirmez; yeniden hesap sonrası <b>onayla</b> derseniz haftalık iş gücüne yazılır.
                      </p>
                      <div className="table-wrap" style={{ maxHeight: 260 }}>
                        <table>
                          <thead>
                            <tr><th>İş merkezi</th><th>Hafta</th><th className="num">Doluluk</th><th className="num">Kişi</th><th className="num">FM kişi</th><th className="num">FM gün</th><th className="num">Ek kapasite</th><th className="num">Açık (önce → sonra)</th><th>Etkilenen siparişler</th><th /></tr>
                          </thead>
                          <tbody>
                            {otSuggest.suggestions.map((r) => (
                              <tr key={`${r.work_center_id}-${r.week_start}`}>
                                <td><b>{r.work_center_code}</b></td>
                                <td>{r.week_start}</td>
                                <td className="num">{Math.round(r.utilization * 100)}%</td>
                                <td className="num">{r.headcount}</td>
                                <td className="num"><b>{r.suggested_overtime_headcount}</b>{r.current_overtime_headcount ? <span className="muted"> (şu an {r.current_overtime_headcount})</span> : null}</td>
                                <td className="num">{r.overtime_days}</td>
                                <td className="num" title={r.explanation}>+{fmt(r.added_capacity_hours, 1)} sa</td>
                                <td className="num">{fmt(r.shortfall_hours_before, 1)} → {fmt(r.shortfall_hours_after, 1)}</td>
                                <td className="muted">{r.order_nos.slice(0, 4).join(", ")}{r.order_nos.length > 4 ? ` +${r.order_nos.length - 4}` : ""}</td>
                                <td>{canEdit && editable && <button className="secondary small" onClick={() => applyOvertimeSuggestions([r])} disabled={busy}>Taslağa ekle</button>}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                      {canEdit && editable && (
                        <div className="row" style={{ marginTop: 6 }}>
                          <button onClick={() => applyOvertimeSuggestions(otSuggest.suggestions)} disabled={busy}>Tüm önerileri taslağa ekle ve yeniden hesapla</button>
                        </div>
                      )}
                    </>
                  )}
                  {otSuggest && otSuggest.notes.length > 0 && (
                    <ul className="muted" style={{ margin: "6px 0 0", paddingLeft: 18 }}>{otSuggest.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
                  )}
                </div>
              )}

              <h4 style={{ marginBottom: 4 }}>Sipariş bazında önce / sonra</h4>
              <p className="muted" style={{ marginTop: 0 }}>
                Talepler yeni termine yetişiyor mu, hangi siparişler bu uğurda ötelendi? Kartlara tıklayarak süzün; tablo canlı plan ile önerilen planın farkıdır.
              </p>
              <div className="rev-cards">
                {(
                  [
                    ["requested", "Talep", diffSummary.requested, "info"],
                    ["requested", "Karşılanan", diffSummary.met, "ok"],
                    ["unmet", "Karşılanamayan", diffSummary.unmet, diffSummary.unmet ? "bad" : "muted"],
                    ["pushed", "Ötelenen", diffSummary.pushed, diffSummary.pushed ? "warn" : "muted"],
                    ["newly_late", "Yeni geç", diffSummary.newly_late, diffSummary.newly_late ? "bad" : "muted"],
                    ["pulled", "Öne alınan", diffSummary.pulled_forward, "ok"],
                    ["bumped", "Kaydırılan", diffSummary.bumped, diffSummary.bumped ? "warn" : "muted"],
                    ["all", "Değişmeyen", diffSummary.unchanged, "muted"],
                  ] as [DiffFilter, string, number, string][]
                ).map(([f, label, value, cls], i) => (
                  <button key={`${f}-${i}`} type="button" className={`rev-card ${cls} ${diffFilter === f ? "active" : ""}`} onClick={() => setDiffFilter(f)} title={DIFF_FILTERS.find((x) => x.id === f)?.hint}>
                    <span className="v">{value}</span>
                    <span className="l">{label}</span>
                  </button>
                ))}
              </div>
              <div className="row" style={{ alignItems: "center", gap: 8, marginBottom: 6 }}>
                <div className="seg">
                  {DIFF_FILTERS.map((f) => (
                    <button key={f.id} type="button" className={diffFilter === f.id ? "active" : ""} style={diffFilter === f.id ? { background: "#e3f2fd", fontWeight: 600 } : undefined} onClick={() => setDiffFilter(f.id)} title={f.hint}>
                      {f.label}
                    </button>
                  ))}
                </div>
                <input placeholder="Sipariş / müşteri / stok ara" value={diffQuery} onChange={(e) => setDiffQuery(e.target.value)} style={{ minWidth: 220 }} />
                <span className="muted">{visibleDiffs.length} / {diffRows.length} sipariş</span>
                <button className="secondary small" onClick={downloadCsv} disabled={!visibleDiffs.length}>CSV indir</button>
                {canEdit && editable && unmetIds.length > 0 && (
                  <button className="danger small" onClick={removeUnmetAndRecalculate} disabled={busy} title="Yeni termine yetişmeyen talepleri taslaktan çıkarır ve yeniden hesaplar">
                    {unmetIds.length} karşılanamayan talebi geri çek ve yeniden hesapla
                  </button>
                )}
              </div>
              {!diffRows.length ? (
                <p className="muted">Sipariş bazlı karşılaştırma için revizyonu yeniden hesaplayın.</p>
              ) : (
                <div className="table-wrap rev-diff" style={{ maxHeight: 460 }}>
                  <table>
                    <thead>
                      <tr>
                        <th>Sipariş</th>
                        <th>Müşteri</th>
                        <th>Stok</th>
                        <th className="num">Miktar</th>
                        <th>Termin</th>
                        <th>Bitiş (canlı → öneri)</th>
                        <th className="num">Kayma</th>
                        <th>Durum</th>
                        <th>Etiket</th>
                      </tr>
                    </thead>
                    <tbody>
                      {visibleDiffs.map((r) => {
                        const [clsB, labB] = statusBadge(r.status_before);
                        const [clsA, labA] = statusBadge(r.status_after);
                        const dueChanged = r.due_before !== r.due_after;
                        return (
                          <tr key={r.order_id} className={r.requested ? (r.met === false ? "unmet" : "requested") : r.newly_late ? "newly-late" : ""}>
                            <td>{r.order_no}{r.position_no ? `/${r.position_no}` : ""}</td>
                            <td>{r.customer}</td>
                            <td>{r.item_code}</td>
                            <td className="num">{fmt(r.quantity, 0)}</td>
                            <td>
                              {dueChanged ? (<>{r.due_before || "—"}<span className="arrow">→</span><b>{r.due_after || "—"}</b></>) : (r.due_after || r.due_before || "—")}
                            </td>
                            <td>
                              {r.end_before || "—"}<span className="arrow">→</span>{r.end_after ? <b>{r.end_after}</b> : <span className="badge bad">yok</span>}
                            </td>
                            <td className={`num ${r.delta_days ? (r.delta_days > 0 ? "delta-plus" : "delta-minus") : ""}`}>{deltaLabel(r.delta_days)}</td>
                            <td>
                              <span className={`badge ${clsB}`}>{labB}</span>
                              <span className="arrow">→</span>
                              <span className={`badge ${clsA}`}>{labA}</span>
                              {r.status_after === "late" && r.lateness_after ? <span className="muted"> {r.lateness_after} gün</span> : null}
                            </td>
                            <td>
                              <span className="rev-tags">
                                {r.requested && r.met === true && <span className="badge ok">Talep karşılandı</span>}
                                {r.requested && r.met === false && <span className="badge bad">Talep karşılanamadı</span>}
                                {r.change_kind === "job_move" && <span className="badge info">İş taşıma</span>}
                                {!r.requested && r.pushed && <span className="badge warn">Ötelendi</span>}
                                {r.newly_late && <span className="badge bad">Yeni geç</span>}
                                {r.pulled_forward && !r.requested && <span className="badge ok">Öne geldi</span>}
                                {r.bumped && <span className="badge warn">Kaydırıldı</span>}
                              </span>
                            </td>
                          </tr>
                        );
                      })}
                      {!visibleDiffs.length && <tr><td colSpan={9} className="muted">Bu filtrede sipariş yok</td></tr>}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}

          <h4>Olay kaydı</h4>
          <ul className="muted">
            {selected.events.map((e) => (
              <li key={e.id}>
                {e.created_at?.slice(0, 19).replace("T", " ")} · {e.username} · {e.action}
                {e.detail ? ` — ${e.detail}` : ""}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
