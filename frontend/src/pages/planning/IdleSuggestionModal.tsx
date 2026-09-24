import { useMemo, useState } from "react";
import { api, fmt, shortDate, weekLabel, type IdleSuggestion, type MaterialPolicy, type Placement, type PlanMode, type PlanRevision } from "../../api";
import { ErrorText, useAsync } from "../../components";

/**
 * Atıl kapasite penceresi: seçilen iş merkezi × hafta için öne çekilebilir işler.
 * Seçilen siparişler tek tıkla iş taşıma revizyon taslağına girer (canlı plan onaya kadar değişmez).
 */
export default function IdleSuggestionModal({
  wcId, wcCode, week, horizon, canEdit, onClose, onDraftCreated,
}: {
  wcId: number;
  wcCode: string;
  week: string;
  horizon: { start: string; weeks: number; wcIds: number[] | null; mode: PlanMode; materialPolicy: MaterialPolicy; placement: Placement; jitBufferDays: number };
  canEdit: boolean;
  onClose: () => void;
  onDraftCreated: (rev: PlanRevision) => void;
}) {
  const data = useAsync(() => api.get<IdleSuggestion>(`/api/plan/idle-suggestions?work_center_id=${wcId}&week_start=${week}`), [wcId, week]);
  const [selected, setSelected] = useState<number[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const rows = data.data?.rows ?? [];
  // Varsayılan seçim: uygun ve atıl saate sığanlar
  const chosen = useMemo(() => selected ?? rows.filter((r) => r.eligible && r.fits).map((r) => r.order_id), [selected, rows]);
  const chosenHours = rows.filter((r) => chosen.includes(r.order_id)).reduce((s, r) => s + r.hours, 0);
  const toggle = (id: number) => setSelected(chosen.includes(id) ? chosen.filter((x) => x !== id) : [...chosen, id]);

  const createDraft = async () => {
    if (!chosen.length) return;
    setBusy(true);
    setErr("");
    try {
      const rev = await api.post<PlanRevision>("/api/plan/revisions", {
        reason_codes: ["other"],
        note: `Atıl kapasite: ${wcCode} ${weekLabel(week)} haftasına ${chosen.length} iş öne çekme`,
        start_week: horizon.start,
        weeks: horizon.weeks,
        mode: horizon.mode,
        material_policy: horizon.materialPolicy,
        placement: horizon.placement,
        jit_buffer_days: horizon.jitBufferDays,
        work_center_ids: horizon.wcIds,
        replace_manual: false,
      });
      const changes = rows
        .filter((r) => chosen.includes(r.order_id))
        .map((r) => ({
          entity_type: "order",
          entity_id: r.order_id,
          extra_key: r.item_code,
          field: "job_move",
          new_value: JSON.stringify({ item_code: r.item_code, start_date: week, qty_mode: "remaining", quantity: null }),
        }));
      const out = await api.post<PlanRevision>(`/api/plan/revisions/${rev.id}/changes/bulk`, { changes });
      onDraftCreated(out);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="panel modal" style={{ width: "min(1100px, 96vw)" }} onClick={(e) => e.stopPropagation()}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h3 style={{ margin: 0 }}>
            Atıl kapasite — {wcCode} · {weekLabel(week)} <span className="muted">{shortDate(week)}</span>
          </h3>
          <button className="secondary" onClick={onClose}>Kapat</button>
        </div>
        <ErrorText err={data.err || err} />
        {data.data && (
          <>
            <div className="row" style={{ marginTop: 6 }}>
              <div className="kpi"><span className="v">{fmt(data.data.capacity_hours, 0)}</span><span className="l">Planlanabilir saat</span></div>
              <div className="kpi"><span className="v">{fmt(data.data.planned_hours, 0)}</span><span className="l">Planlı saat</span></div>
              <div className="kpi warn"><span className="v">{fmt(data.data.idle_hours, 0)}</span><span className="l">Atıl saat</span></div>
              <div className="kpi ok"><span className="v">{data.data.eligible_count}</span><span className="l">Öne çekilebilir iş</span></div>
              <div className="kpi"><span className="v">{fmt(chosenHours, 0)}</span><span className="l">Seçili saat</span></div>
            </div>
            <p className="muted" style={{ marginTop: 4 }}>
              Sonraki haftalarda bu iş merkezinde planlı işler; öncülü tamamlanmış/aynı haftada ve malzemesi hazır olanlar uygundur. Termin sırasıyla atıl saate sığanlar önceden seçilidir.
              Taslak, canlı planı değiştirmez; revizyon sekmesinde hesaplayıp onaylarsınız.
            </p>
            {data.data.notes.map((n, i) => <p key={i} className="muted">{n}</p>)}
            <div className="table-wrap" style={{ maxHeight: 420 }}>
              <table>
                <thead>
                  <tr>
                    <th />
                    <th>Sipariş</th><th>Müşteri</th><th>Stok</th><th>Operasyon</th><th>Planlı hafta</th><th>Termin</th>
                    <th className="num">Saat</th><th className="num">Miktar</th><th>Durum</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={`${r.order_id}-${r.operation_seq}`} style={{ opacity: r.eligible ? 1 : 0.55 }}>
                      <td><input type="checkbox" disabled={!r.eligible || !canEdit} checked={chosen.includes(r.order_id)} onChange={() => toggle(r.order_id)} /></td>
                      <td>{r.order_no}{r.position_no ? `/${r.position_no}` : ""}</td>
                      <td>{r.customer}</td>
                      <td>{r.item_code}</td>
                      <td>{r.operation_seq} {r.operation_name}{r.semi_finished_code ? <span className="muted"> · {r.semi_finished_code}</span> : null}</td>
                      <td>{weekLabel(r.from_week)} <span className="muted">{shortDate(r.from_week)}</span></td>
                      <td>{r.due_date}</td>
                      <td className="num">{fmt(r.hours, 1)}</td>
                      <td className="num">{fmt(r.qty, 0)}</td>
                      <td>
                        {r.eligible
                          ? (r.fits ? <span className="badge ok">Sığar</span> : <span className="badge warn">Atıl saati aşar</span>)
                          : <span className="badge muted" title={r.blocker}>Uygun değil · {r.blocker}</span>}
                      </td>
                    </tr>
                  ))}
                  {!rows.length && <tr><td colSpan={10} className="muted">Sonraki haftalarda bu iş merkezinde öne çekilebilecek planlı iş yok.</td></tr>}
                </tbody>
              </table>
            </div>
            {canEdit && (
              <div className="row" style={{ marginTop: 8 }}>
                <button onClick={() => void createDraft()} disabled={busy || !chosen.length}>
                  {chosen.length ? `${chosen.length} işi bu haftaya taşıyan revizyon taslağı oluştur` : "İş seçin"}
                </button>
                {chosenHours > data.data.idle_hours + 0.5 && <span className="badge warn">Seçim atıl saati {fmt(chosenHours - data.data.idle_hours, 0)} sa aşıyor; hesap sonucunda kaydırma görünür</span>}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
