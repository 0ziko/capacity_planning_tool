import { useEffect, useMemo, useState } from "react";
import {
  api,
  fmt,
  REVISION_REASONS,
  type JobMovePreview,
  type Order,
  type PlanMode,
  type PlanRevision,
  type PlanRevisionChange,
  type WorkCenter,
} from "../../api";
import { ErrorText, useAsync } from "../../components";

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
  orders,
  wcs,
  canEdit,
  onApplied,
}: {
  start: string;
  weeks: number;
  wcIds: number[];
  mode: PlanMode;
  orders: Order[];
  wcs: WorkCenter[];
  canEdit: boolean;
  onApplied: () => void;
}) {
  const list = useAsync(() => api.get<PlanRevision[]>("/api/plan/revisions"), []);
  const [selId, setSelId] = useState<number | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [reasons, setReasons] = useState<string[]>(["other"]);
  const [note, setNote] = useState("");
  const [replaceManual, setReplaceManual] = useState(false);
  const [orderId, setOrderId] = useState(0);
  const [newDue, setNewDue] = useState("");
  const [wcId, setWcId] = useState(0);
  const [week, setWeek] = useState(start);
  const [headcount, setHeadcount] = useState("");
  const [movePreview, setMovePreview] = useState<JobMovePreview | null>(null);
  const [moveQtyMode, setMoveQtyMode] = useState<"remaining" | "split">("remaining");
  const [moveQty, setMoveQty] = useState("");
  const [moveStart, setMoveStart] = useState(start);

  const selected = useMemo(() => (list.data ?? []).find((r) => r.id === selId) || null, [list.data, selId]);
  const editable = selected && (selected.status === "draft" || selected.status === "calculated");

  useEffect(() => {
    if (!orderId) {
      setMovePreview(null);
      return;
    }
    let cancelled = false;
    api
      .get<JobMovePreview>(`/api/plan/revisions/move-preview?order_id=${orderId}`)
      .then((p) => {
        if (!cancelled) {
          setMovePreview(p);
          setMoveQty(p.movable_qty ? String(p.movable_qty) : "");
        }
      })
      .catch(() => {
        if (!cancelled) setMovePreview(null);
      });
    return () => {
      cancelled = true;
    };
  }, [orderId]);

  const run = async (fn: () => Promise<PlanRevision>) => {
    setBusy(true);
    setErr("");
    try {
      const r = await fn();
      setSelId(r.id);
      await list.reload();
      if (r.status === "applied") onApplied();
    } catch (e) {
      setErr((e as Error).message);
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
        work_center_ids: wcIds.length ? wcIds : null,
        replace_manual: replaceManual,
      }),
    );

  const addDue = () => {
    if (!selected || !orderId || !newDue) return;
    run(() =>
      api.post<PlanRevision>(`/api/plan/revisions/${selected.id}/changes`, {
        entity_type: "order",
        entity_id: orderId,
        field: "revised_due_date",
        new_value: newDue,
      }),
    );
  };

  const addMove = () => {
    if (!selected || !orderId || !moveStart || !movePreview) return;
    const item = movePreview.item_code || orders.find((o) => o.id === orderId)?.item_code || "";
    run(() =>
      api.post<PlanRevision>(`/api/plan/revisions/${selected.id}/changes`, {
        entity_type: "order",
        entity_id: orderId,
        extra_key: item,
        field: "job_move",
        new_value: JSON.stringify({
          item_code: item,
          start_date: moveStart,
          qty_mode: moveQtyMode,
          quantity: moveQtyMode === "split" ? Number(moveQty) : null,
        }),
      }),
    );
  };

  const addLabor = () => {
    if (!selected || !wcId || !week || !headcount) return;
    run(() =>
      api.post<PlanRevision>(`/api/plan/revisions/${selected.id}/changes`, {
        entity_type: "wc_week",
        extra_key: `${wcId}|${week}`,
        field: "headcount",
        new_value: headcount,
      }),
    );
  };

  const cmp = selected?.compare;

  return (
    <div>
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
          <p className="muted">Ufuk: {start} · {weeks} hafta · {mode === "revenue" ? "maksimum ciro" : "termine göre"}</p>
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
                <td>{r.created_by}</td>
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
                <button className="secondary" onClick={() => void run(() => api.post(`/api/plan/revisions/${selected.id}/calculate`))} disabled={busy}>
                  Yeniden hesapla
                </button>
                {selected.status === "calculated" && (
                  <button onClick={() => void run(() => api.post(`/api/plan/revisions/${selected.id}/approve`))} disabled={busy}>
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
          {selected.apply_message && <div className="success">{selected.apply_message}</div>}

          {canEdit && editable && (
            <>
              <h4>Taslak girdiler</h4>
              <div className="row" style={{ flexWrap: "wrap" }}>
                <label>
                  Sipariş
                  <select value={orderId} onChange={(e) => setOrderId(Number(e.target.value))}>
                    <option value={0}>—</option>
                    {orders.map((o) => (
                      <option key={o.id} value={o.id}>
                        {o.order_no} {o.item_code} · {o.effective_due_date}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Yeni revize termin
                  <input type="date" value={newDue} onChange={(e) => setNewDue(e.target.value)} />
                </label>
                <button className="secondary" onClick={addDue} disabled={busy}>Vade ekle</button>
              </div>
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
                <button className="secondary" onClick={addLabor} disabled={busy}>İş gücü ekle</button>
              </div>
              <h4>İş taşıma (kalan zincir)</h4>
              <p className="muted" style={{ marginTop: -8 }}>
                Sipariş + mamul seçilir; tamamlanan operasyonlar kilitlenir. Yeni tarih yalnızca ilk kalan operasyonun
                başlangıcıdır. Sonrakiler senaryo kurallarına uyar. Miktar: tüm kalan veya kısmi (aynı siparişte iki aile).
              </p>
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
                {moveQtyMode === "split" && (
                  <label>
                    Taşınacak adet
                    <input type="number" min={0} step="any" value={moveQty} onChange={(e) => setMoveQty(e.target.value)} />
                  </label>
                )}
                <label>
                  Yeni başlangıç (hafta)
                  <input type="date" value={moveStart} onChange={(e) => setMoveStart(e.target.value)} />
                </label>
                <button className="secondary" onClick={addMove} disabled={busy || !orderId || !movePreview || movePreview.movable_qty <= 0}>
                  İş taşıma ekle
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
                <div className="kpi"><span className="v">{cmp.baseline.late} → {cmp.proposed.late}</span><span className="l">Geç sipariş</span></div>
                <div className="kpi"><span className="v">{cmp.baseline.on_time} → {cmp.proposed.on_time}</span><span className="l">Zamanında</span></div>
                <div className="kpi"><span className="v">{cmp.baseline.unplanned} → {cmp.proposed.unplanned}</span><span className="l">Plansız</span></div>
              </div>
              {!!cmp.insert_notes?.length && (
                <p>{cmp.insert_notes.join(" · ")}</p>
              )}
              {!!cmp.bumped_orders?.length && (
                <p>Kaydırılan (çakışan) işler: <b>{cmp.bumped_orders.join(", ")}</b></p>
              )}
              {!!cmp.unplanned?.length && (
                <p className="muted">
                  Ufka sığmayan: {cmp.unplanned.map((u) => `${u.order_no || "?"} ${u.work_center_code || ""} ${u.hours ?? ""}s`).join("; ")}
                </p>
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
