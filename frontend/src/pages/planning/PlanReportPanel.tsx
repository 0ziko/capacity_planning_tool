import { useEffect, useState } from "react";
import { api, fmt, shortDate, weekLabel, type OvertimePendingCell, type PlanReport, type PlanReportListItem } from "../../api";

const STATUS_TR: Record<string, string> = { on_time: "Zamanında", late: "Geç", partial: "Kısmi", unplanned: "Plansız", no_ops: "Operasyon yok", finish_unknown: "Bitiş belirsiz", closed: "Kapalı" };
const KIND_TR: Record<string, string> = { auto: "Otomatik plan", revision: "Revizyon onayı" };
const LEVEL_TR: Record<string, string> = { critical: "Kritik", warn: "Uyarı", info: "Bilgi" };

function pct(v: number | null | undefined) { return v == null ? "—" : `%${Math.round(v * 100)}`; }
function delta(cur: number | null | undefined, prev: number | null | undefined, unit = " sa", invert = false) {
  if (cur == null || prev == null) return null;
  const d = cur - prev;
  if (Math.abs(d) < 0.05) return <span className="muted"> (±0)</span>;
  const good = invert ? d < 0 : d > 0;
  return <span style={{ color: good ? "var(--ok)" : "var(--bad)", fontSize: 12 }}> ({d > 0 ? "+" : ""}{fmt(d)}{unit})</span>;
}

/** Otomatik plan / revizyon onayı sonrası üretilen plan raporu; son raporlar karşılaştırılabilir, Excel'e alınabilir. */
export default function PlanReportPanel({ focusId, refreshKey, canEdit, onOvertimeDecided }: { focusId?: number | null; refreshKey?: unknown; canEdit?: boolean; onOvertimeDecided?: () => void }) {
  const [list, setList] = useState<PlanReportListItem[]>([]);
  const [selId, setSelId] = useState<number | null>(null);
  const [cmpId, setCmpId] = useState<number | null>(null);
  const [report, setReport] = useState<PlanReport | null>(null);
  const [cmp, setCmp] = useState<PlanReport | null>(null);
  const [open, setOpen] = useState(true);
  const [err, setErr] = useState("");
  const [pending, setPending] = useState<OvertimePendingCell[]>([]);
  const [deciding, setDeciding] = useState(false);

  const loadPending = (r: PlanReport | null) => {
    if (!r) { setPending([]); return; }
    api.get<OvertimePendingCell[]>(`/api/wc-weeks/overtime-pending?start=${r.horizon.start_week}&weeks=${r.horizon.weeks}`).then(setPending).catch(() => setPending([]));
  };
  const decide = async (decision: "approve" | "reject", cells: { work_center_id: number; week_start: string }[]) => {
    if (!cells.length) return;
    setDeciding(true);
    try {
      await api.post("/api/wc-weeks/overtime-decision", { decision, cells });
      loadPending(report);
      onOvertimeDecided?.();
    } catch (e) { setErr((e as Error).message); } finally { setDeciding(false); }
  };

  useEffect(() => {
    api.get<PlanReportListItem[]>("/api/plan/reports?limit=15").then((rows) => {
      setList(rows);
      const target = focusId && rows.some((r) => r.id === focusId) ? focusId : rows[0]?.id ?? null;
      setSelId(target);
      const prev = rows.find((r) => r.id !== target);
      setCmpId(prev ? prev.id : null);
    }).catch((e) => setErr((e as Error).message));
  }, [focusId, refreshKey]);
  useEffect(() => {
    if (!selId) { setReport(null); return; }
    api.get<PlanReport>(`/api/plan/reports/${selId}`).then((r) => { setReport(r); loadPending(r); }).catch((e) => setErr((e as Error).message));
  }, [selId]);
  useEffect(() => {
    if (!cmpId) { setCmp(null); return; }
    api.get<PlanReport>(`/api/plan/reports/${cmpId}`).then(setCmp).catch(() => setCmp(null));
  }, [cmpId]);

  if (!list.length && !err) return null;
  const s = report?.summary; const ps = cmp?.summary; const o = report?.orders; const po = cmp?.orders;
  const label = (r: PlanReportListItem) => `#${r.id} · ${KIND_TR[r.kind] ?? r.kind}${r.revision_id ? ` (REV ${r.revision_id})` : ""} · ${r.created_at ? new Date(r.created_at).toLocaleString("tr-TR") : ""} · ${r.username}`;

  return (
    <div className="panel" data-testid="plan-report">
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <h3 style={{ margin: 0 }}>Plan raporu</h3>
        <select value={selId ?? ""} onChange={(e) => setSelId(Number(e.target.value) || null)}>
          {list.map((r) => <option key={r.id} value={r.id}>{label(r)}</option>)}
        </select>
        <span className="muted">Karşılaştır:</span>
        <select value={cmpId ?? ""} onChange={(e) => setCmpId(Number(e.target.value) || null)}>
          <option value="">—</option>
          {list.filter((r) => r.id !== selId).map((r) => <option key={r.id} value={r.id}>{label(r)}</option>)}
        </select>
        {report && <button type="button" className="secondary" onClick={() => void api.download(`/api/plan/reports/${report.id}.xlsx`, `plan-raporu-${report.id}.xlsx`).catch((e) => setErr((e as Error).message))}>Excel</button>}
        <button type="button" className="secondary" onClick={() => setOpen(!open)}>{open ? "Gizle" : "Göster"}</button>
      </div>
      {err && <div className="error">{err}</div>}
      {open && report && s && o && (
        <>
          <p className="muted" style={{ margin: "6px 0" }}>Ufuk {shortDate(report.horizon.start_week)} + {report.horizon.weeks} hafta · {report.horizon.work_center_count} iş merkezi · {s.line_count} plan satırı{cmp ? ` · karşılaştırma: #${cmp.id}` : ""}</p>
          <div style={{ display: "flex", gap: 18, flexWrap: "wrap", margin: "8px 0 12px" }}>
            <div className="kpi"><span className="v">{fmt(s.planned_hours)} sa{delta(s.planned_hours, ps?.planned_hours)}</span><span className="l">Planlanan</span></div>
            <div className="kpi"><span className="v">{fmt(s.unplanned_hours)} sa{delta(s.unplanned_hours, ps?.unplanned_hours, " sa", true)}</span><span className="l">Plansız ({s.unplanned_orders} sipariş)</span></div>
            <div className="kpi"><span className="v">{fmt(s.idle_hours)} sa{delta(s.idle_hours, ps?.idle_hours, " sa", true)}</span><span className="l">Atıl kapasite</span></div>
            <div className="kpi"><span className="v">{pct(s.utilization)}{ps ? delta(s.utilization * 100, ps.utilization * 100, " puan") : null}</span><span className="l">Doluluk</span></div>
            <div className="kpi"><span className="v">{o.target_met} / {o.open_orders}{delta(o.target_met, po?.target_met, "")}</span><span className="l">Hedefi tutan (termin − {o.delivery_buffer_days} gün)</span></div>
            <div className="kpi"><span className="v">{o.overdue_before_horizon}</span><span className="l">Termini ufuktan önce</span></div>
            <div className="kpi"><span className="v">{o.avg_slack_days == null ? "—" : `${fmt(o.avg_slack_days)} gün`}</span><span className="l">Ort. termine kalan</span></div>
            <div className="kpi"><span className="v">{report.transitions.count ? pct(report.transitions.no_wait / report.transitions.count) : "—"}</span><span className="l">Beklemesiz geçiş ({report.transitions.no_wait}/{report.transitions.count})</span></div>
            <div className="kpi"><span className="v" style={{ color: s.overtime_hours ? "var(--warn)" : undefined }}>{fmt(s.overtime_hours ?? 0)} sa{delta(s.overtime_hours ?? 0, ps ? ps.overtime_hours ?? 0 : null, " sa", true)}</span><span className="l">Fazla mesai ihtiyacı ({s.overtime_cells ?? 0} hücre · {s.overtime_orders ?? 0} sipariş)</span></div>
            <div className="kpi"><span className="v" style={{ color: s.slip_orders ? "var(--bad)" : undefined }}>{s.slip_orders ?? 0}{delta(s.slip_orders ?? 0, ps ? ps.slip_orders ?? 0 : null, "", true)}</span><span className="l">Hedefe sığmayan sipariş (+{s.slip_overdue_orders ?? 0} termini geçmiş)</span></div>
            <div className="kpi"><span className="v" style={{ color: (s.orphan_share ?? 0) > 0.05 ? "var(--warn)" : undefined }}>{fmt(s.orphan_hours ?? 0)} sa{delta(s.orphan_hours ?? 0, ps ? ps.orphan_hours ?? 0 : null, " sa", true)}</span><span className="l">Bitmiş ürüne dönüşmeyen ({pct(s.orphan_share ?? 0)})</span></div>
            <div className="kpi"><span className="v">{fmt(s.slip_line_hours ?? 0)} / {fmt(s.prep_line_hours ?? 0)} sa</span><span className="l">Hedef sonrası / hazırlık işi</span></div>
          </div>
          {report.findings.length > 0 && (
            <div className="table-wrap" style={{ marginBottom: 12 }}>
              <table>
                <thead><tr><th style={{ width: 90 }}>Önem</th><th style={{ width: 260 }}>Konu</th><th>Açıklama</th></tr></thead>
                <tbody>
                  {report.findings.map((f, i) => (
                    <tr key={i}>
                      <td><span className={`badge ${f.level === "critical" ? "bad" : f.level === "warn" ? "warn" : "info"}`}>{LEVEL_TR[f.level] ?? f.level}</span></td>
                      <td><b>{f.title ?? f.code}</b></td>
                      <td>{f.text}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 12, alignItems: "start" }}>
            <div className="table-wrap" style={{ maxHeight: 360 }}>
              <table>
                <thead><tr><th>İş merkezi</th><th>Kapasite</th><th>Plan</th><th>Atıl</th><th>Doluluk</th><th>Plansız</th><th>Durum</th></tr></thead>
                <tbody>
                  {report.work_centers.map((w) => {
                    const pw = cmp?.work_centers.find((x) => x.code === w.code);
                    return (
                      <tr key={w.code}>
                        <td><b>{w.code}</b> <span className="muted">{w.name}</span></td>
                        <td>{fmt(w.capacity_hours)}</td>
                        <td>{fmt(w.planned_hours)}{delta(w.planned_hours, pw?.planned_hours, "")}</td>
                        <td>{fmt(w.idle_hours)}</td>
                        <td>{pct(w.utilization)}</td>
                        <td title={Object.entries(w.unplanned_reasons).map(([k, v]) => `${k}: ${fmt(v)} sa`).join("\n")}>{fmt(w.unplanned_hours)}{delta(w.unplanned_hours, pw?.unplanned_hours, "", true)}</td>
                        <td>{w.zero_capacity ? <span className="badge bad">Kapasite 0</span> : w.bottleneck ? <span className="badge warn">Darboğaz</span> : w.utilization < 0.25 ? <span className="badge muted">Atıl</span> : <span className="badge ok">Uygun</span>}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div>
              <div className="table-wrap" style={{ marginBottom: 10 }}>
                <table>
                  <thead><tr><th>Plansız neden</th><th>Saat</th></tr></thead>
                  <tbody>
                    {report.unplanned_reasons.length === 0 && <tr><td colSpan={2} className="muted">Plansız iş yok</td></tr>}
                    {report.unplanned_reasons.map((r) => <tr key={r.reason}><td>{r.label}{r.help && <div className="muted">{r.help}</div>}</td><td>{fmt(r.hours)}</td></tr>)}
                  </tbody>
                </table>
              </div>
              <div className="table-wrap" style={{ marginBottom: 10 }}>
                <table>
                  <thead><tr><th>Sipariş durumu</th><th>Adet</th></tr></thead>
                  <tbody>{Object.entries(o.status).map(([k, v]) => <tr key={k}><td>{STATUS_TR[k] ?? k}</td><td>{v}{po ? delta(v, po.status[k] ?? 0, "") : null}</td></tr>)}</tbody>
                </table>
              </div>
              {report.top_unplanned_orders.length > 0 && (
                <div className="table-wrap" style={{ maxHeight: 200 }}>
                  <table>
                    <thead><tr><th>En çok plansız sipariş</th><th>Saat</th></tr></thead>
                    <tbody>{report.top_unplanned_orders.map((r) => <tr key={r.order_no}><td>{r.order_no}</td><td>{fmt(r.hours)}</td></tr>)}</tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
          {(report.machines?.length ?? 0) > 0 && (
            <div style={{ marginTop: 12 }}>
              <h4 style={{ margin: "0 0 6px" }}>İstasyon doluluğu (hat merkezleri)</h4>
              <div className="table-wrap" style={{ maxHeight: 260 }}>
                <table>
                  <thead><tr><th>Merkez</th><th>İstasyon</th><th className="num">Kapasite</th><th className="num">Plan</th><th className="num">Atıl</th><th className="num">Doluluk</th>{report.weekly.map((w) => <th key={w.week_start} className="num">{shortDate(w.week_start)}</th>)}</tr></thead>
                  <tbody>
                    {report.machines!.map((m) => (
                      <tr key={m.machine_code}>
                        <td>{m.work_center}</td><td><b>{m.machine_code}</b></td>
                        <td className="num">{fmt(m.capacity_hours)}</td><td className="num">{fmt(m.planned_hours)}</td><td className="num">{fmt(m.idle_hours)}</td>
                        <td className="num">{pct(m.utilization)}</td>
                        {m.weekly.map((w) => <td key={w.week_start} className="num" style={{ color: w.utilization >= 0.95 ? "var(--bad)" : w.utilization === 0 ? "var(--muted)" : undefined }}>{pct(w.utilization)}</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          {(report.overtime?.length ?? 0) > 0 && (
            <div style={{ marginTop: 12 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                <h4 style={{ margin: 0 }}>Fazla mesai ihtiyacı</h4>
                <span className="muted">{pending.length ? `${pending.length} hücre onay bekliyor` : "Onay bekleyen yok"}</span>
                {canEdit && pending.length > 0 && <button type="button" className="small" disabled={deciding} onClick={() => void decide("approve", pending.map((p) => ({ work_center_id: p.work_center_id, week_start: p.week_start })))}>Tümünü onayla</button>}
                {canEdit && pending.length > 0 && <button type="button" className="secondary small" disabled={deciding} onClick={() => void decide("reject", pending.map((p) => ({ work_center_id: p.work_center_id, week_start: p.week_start })))}>Tümünü reddet</button>}
              </div>
              <div className="table-wrap" style={{ maxHeight: 280, marginTop: 6 }}>
                <table>
                  <thead><tr><th>İş merkezi</th><th>Hafta</th><th className="num">Saat</th><th className="num">Hafta içi kişi × gün</th><th className="num">Hafta sonu kişi × gün</th><th className="num">Kişi başı hafta / yıl</th><th>Durum</th><th>Hesap</th></tr></thead>
                  <tbody>
                    {report.overtime!.map((r, i) => {
                      const isPending = pending.some((p) => p.work_center_id === r.work_center_id && p.week_start === r.week_start);
                      return (
                        <tr key={i}>
                          <td><b>{r.work_center_code}</b></td>
                          <td>{weekLabel(r.week_start)} <span className="muted">{shortDate(r.week_start)}</span></td>
                          <td className="num">{fmt(r.hours)}</td>
                          <td className="num">{r.weekday_persons ? `${r.weekday_persons} × ${r.weekday_days}` : "—"}</td>
                          <td className="num">{r.weekend_persons ? `${r.weekend_persons} × ${r.weekend_days}` : "—"}</td>
                          <td className="num" style={{ color: r.person_hours_ytd > r.legal_yearly_hours ? "var(--warn)" : undefined }} title="Fazla mesai yapan kişinin nominal saati; yıl içi birikim 270 sa yasal eşiğini aşınca turuncu">{fmt(r.person_hours)} / {fmt(r.person_hours_ytd, 0)}</td>
                          <td>{isPending ? <span className="badge warn">Onay bekliyor</span> : <span className="badge ok">Onaylı / çözüldü</span>}
                            {canEdit && isPending && <button type="button" className="small" style={{ marginLeft: 6 }} disabled={deciding} onClick={() => void decide("approve", [{ work_center_id: r.work_center_id, week_start: r.week_start }])}>Onayla</button>}
                            {canEdit && isPending && <button type="button" className="secondary small" style={{ marginLeft: 4 }} disabled={deciding} onClick={() => void decide("reject", [{ work_center_id: r.work_center_id, week_start: r.week_start }])}>Reddet</button>}
                          </td>
                          <td className="muted">{r.explanation}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p className="muted" style={{ margin: "4px 0 0" }}>Reddedilen fazla mesai bir sonraki planda yeniden hesaplanır; plan satırları o zamana kadar bu kapasiteye yaslanmış kalır.</p>
            </div>
          )}
          {(report.slips?.length ?? 0) > 0 && (
            <div style={{ marginTop: 12 }}>
              <h4 style={{ margin: "0 0 6px" }}>Hedef tarihe sığmayan siparişler ({report.slips!.length})</h4>
              <div className="table-wrap" style={{ maxHeight: 320 }}>
                <table>
                  <thead><tr><th>Sipariş</th><th>Müşteri</th><th>Ürün</th><th>Termin</th><th className="num">Kalan</th><th className="num">Hedefte</th><th className="num">FM ile</th><th className="num">Kayan</th><th>Darboğaz</th><th>Tahmini bitiş</th><th className="num">Gecikme (gün)</th><th>Durum</th></tr></thead>
                  <tbody>
                    {report.slips!.map((r, i) => (
                      <tr key={i} title={r.detail ?? undefined}>
                        <td><b>{r.order_no}</b></td>
                        <td>{r.customer}</td>
                        <td>{r.item_code}</td>
                        <td>{r.due_date ? shortDate(r.due_date) : "—"}</td>
                        <td className="num">{fmt(r.remaining_qty, 0)}</td>
                        <td className="num">{fmt(r.target_qty, 0)}</td>
                        <td className="num" title={r.overtime_saved_weeks ? `Fazla mesai gecikmeyi ${r.overtime_saved_weeks} hafta azalttı` : undefined}>{r.overtime_qty || r.overtime_extra_qty ? fmt((r.overtime_qty || 0) + (r.overtime_extra_qty || 0), 0) : "—"}{r.overtime_saved_weeks ? <span className="muted"> (−{r.overtime_saved_weeks} hf)</span> : null}</td>
                        <td className="num" style={{ color: r.slipped_qty ? "var(--bad)" : undefined }}>{fmt(r.slipped_qty, 0)}</td>
                        <td>{r.bottleneck || "—"}</td>
                        <td>{r.planned_end ? shortDate(r.planned_end) : r.est_finish_week ? shortDate(r.est_finish_week) : "—"}</td>
                        <td className="num" style={{ color: r.days_late > 0 ? "var(--bad)" : undefined }}>{r.days_late || "—"}</td>
                        <td>{r.overdue ? <span className="badge bad">Termini geçmiş</span> : r.slip_mode === "defer" ? <span className="badge warn">Plana yazılmadı</span> : <span className="badge info">Sonraya yerleşti</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          <div className="table-wrap" style={{ marginTop: 10 }}>
            <table>
              <thead><tr><th>Hafta</th>{report.weekly.map((w) => <th key={w.week_start}>{shortDate(w.week_start)}</th>)}</tr></thead>
              <tbody>
                <tr><td>Kapasite</td>{report.weekly.map((w) => <td key={w.week_start}>{fmt(w.capacity_hours)}</td>)}</tr>
                <tr><td>Plan</td>{report.weekly.map((w) => <td key={w.week_start}>{fmt(w.planned_hours)}</td>)}</tr>
                <tr><td>Atıl</td>{report.weekly.map((w) => <td key={w.week_start}>{fmt(w.idle_hours)}</td>)}</tr>
                <tr><td>Doluluk</td>{report.weekly.map((w) => <td key={w.week_start} style={{ color: w.utilization >= 0.95 ? "var(--bad)" : w.utilization < 0.5 ? "var(--muted)" : undefined }}>{pct(w.utilization)}</td>)}</tr>
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
