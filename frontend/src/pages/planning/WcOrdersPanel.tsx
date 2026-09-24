import { Fragment, useMemo, useState } from "react";
import { fmt, weekLabel, weekLong, type PlanBatchMember, type OrderSchedule, type PlanLine, type WorkCenter } from "../../api";
import { PlanStatusBadge } from "./OrderSchedulePanel";

interface Row {
  key: string; members: PlanBatchMember[]; order_id: number; order_no: string; position_no: string; customer: string; item_code: string; due_date: string;
  details: PlanLine[]; ops: number[]; weeks: string[]; hours: number; qty: number; sched?: OrderSchedule;
}

/** İş merkezi bazlı: seçilen iş merkezine planlanmış tüm siparişler (nihai ürün termini ile). */
export default function WcOrdersPanel({ lines, schedule, wcs, horizon }: { lines: PlanLine[] | null; schedule: OrderSchedule[] | null; wcs: WorkCenter[]; horizon: string }) {
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const codes = search.trim().toUpperCase().split(/[\s,;]+/).filter(Boolean);
  const grouped = useMemo(() => {
    const byWc = new Map<number, Map<string, Row>>();
    const qtyByOp = new Map<string, number>(); // wc|order|op -> planlanan miktar (haftalar toplamı)
    for (const l of lines ?? []) {
      if (codes.length && !codes.includes(l.item_code.toUpperCase())) continue;
      if (!byWc.has(l.work_center_id)) byWc.set(l.work_center_id, new Map());
      const m = byWc.get(l.work_center_id)!;
      const key = l.production_batch_id ? `batch:${l.production_batch_id}` : `order:${l.order_id}`;
      let r = m.get(key);
      if (!r) {
        r = { key, members: l.batch_members ?? [], order_id: l.order_id, order_no: l.order_no, position_no: l.position_no, customer: l.customer, item_code: l.item_code, due_date: l.due_date, details: [], ops: [], weeks: [], hours: 0, qty: 0 };
        m.set(key, r);
      }
      r.details.push(l);
      if (!r.ops.includes(l.operation_seq)) r.ops.push(l.operation_seq);
      if (!r.weeks.includes(l.week_start)) r.weeks.push(l.week_start);
      r.hours += l.planned_hours;
      const k = `${l.work_center_id}|${key}|${l.operation_id}`;
      qtyByOp.set(k, (qtyByOp.get(k) ?? 0) + l.planned_qty);
    }
    const schedMap = new Map((schedule ?? []).map((s) => [s.order_id, s]));
    const out = new Map<number, Row[]>();
    byWc.forEach((m, wcId) => {
      const rows = Array.from(m.values()).map((r) => ({
        ...r,
        ops: r.ops.sort((a, b) => a - b),
        weeks: r.weeks.sort(),
        // birden fazla operasyon aynı İM'de ise miktar toplanmaz; en yüksek operasyon miktarı gösterilir
        qty: Math.max(...r.details.map((line) => qtyByOp.get(`${wcId}|${r.key}|${line.operation_id}`) ?? 0), 0),
        sched: r.members.length ? undefined : schedMap.get(r.order_id),
      }));
      rows.sort((a, b) => a.due_date.localeCompare(b.due_date) || a.order_no.localeCompare(b.order_no));
      out.set(wcId, rows);
    });
    return out;
  }, [lines, schedule, search]);

  const wcList = wcs.filter((w) => grouped.has(w.id) || w.is_planned);
  const [sel, setSel] = useState<number | null>(null);
  const active = sel ?? wcList.find((w) => (grouped.get(w.id)?.length ?? 0) > 0)?.id ?? wcList[0]?.id ?? null;
  const rows = active ? grouped.get(active) ?? [] : [];
  const wc = wcs.find((w) => w.id === active);
  const totalHours = rows.reduce((s, r) => s + r.hours, 0);

  return (
    <>
      <div className="row" style={{ marginBottom: 10 }}><label>Bitmiş ürün kodu (tek / çoklu)<input value={search} onChange={e => setSearch(e.target.value)} placeholder="6010758, 6005510" /></label><span className="muted">Tam kodla arama · Boş bırakırsanız tümü. Detaylar haftalık plan miktarlarıdır; gerçekleşmiş stok girişi değildir.</span></div>
      <div className="wc-tabs">
        {wcList.map((w) => {
          const n = grouped.get(w.id)?.length ?? 0;
          return (
            <button key={w.id} className={`wc-tab ${active === w.id ? "active" : ""}`} onClick={() => setSel(w.id)} title={w.name}>
              <b>{w.code}</b> <span className="muted">{w.name}</span> <span className={`badge ${n ? "ok" : "muted"}`}>{n}</span>
            </button>
          );
        })}
        {wcList.length === 0 && <span className="muted">Planlanan iş merkezi yok.</span>}
      </div>
      {wc && (
        <>
          <div className="row" style={{ margin: "8px 0" }}>
            <span><b>{wc.code}</b> — {wc.name}: <b>{rows.length}</b> sipariş / parti, toplam <b>{fmt(totalHours)}</b> saat ({fmt(totalHours / (wc.capacity_unit_hours || 1))} birim) · ufuk: {horizon}</span>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Sipariş</th><th>Poz</th><th>Müşteri</th><th>Stok</th><th className="num">Sipariş / parti miktarı</th><th>Op. / yarımamul</th>
                  <th>Plan haftaları</th><th className="num">Bu İM'de saat</th><th className="num" title="Farklı operasyonların miktarları toplanmaz. Ayrı miktarlar detayda gösterilir.">En yüksek operasyon miktarı</th>
                  <th>Nihai ürün termini</th><th>Tahmini bitiş (tümü)</th><th>Durum</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <Fragment key={r.key}><tr>
                    <td><b>{r.order_no}</b></td><td>{r.position_no || <span className="muted">—</span>}</td><td>{r.customer}</td>
                    <td>{r.item_code} <span className="muted">{r.sched?.item_name}</span></td>
                    <td className="num">{fmt(r.members.length ? r.members.reduce((sum,m) => sum+m.quantity,0) : r.sched?.quantity, 0)}</td>
                    <td>{r.ops.join(", ")}<br />{Array.from(new Set(r.details.map(l => l.semi_finished_code).filter(Boolean))).join(", ") || "—"}<br /><button className="secondary" onClick={() => setExpanded(expanded === `${active}:${r.key}` ? null : `${active}:${r.key}`)}>Haftalık operasyon detayları</button></td>
                    <td title={r.weeks.join(", ")}>{r.weeks.length === 1 ? weekLong(r.weeks[0]) : `${weekLabel(r.weeks[0])} → ${weekLabel(r.weeks[r.weeks.length - 1])} (${r.weeks.length} hafta)`}</td>
                    <td className="num">{fmt(r.hours, 2)}</td><td className="num">{fmt(r.qty, 0)}</td>
                    <td style={{ fontWeight: 600 }}>{r.due_date}</td>
                    <td style={{ color: r.sched?.plan_status === "late" ? "var(--bad)" : undefined }}>{r.sched?.planned_end ?? "-"}</td>
                    <td>{r.members.length ? "Sipariş detayında" : r.sched ? <PlanStatusBadge s={r.sched.plan_status} /> : "-"}</td>
                  </tr>
                  {expanded === `${active}:${r.key}` && <tr><td colSpan={12}>
                    {r.members.length > 0 && <>
                      <p>Ortak üretim partisi: aşağıdaki miktarlar partiye bağlı sipariş paylarıdır; stok rezervasyonu değildir. Operasyon miktarı ve işçilik ortak satırda bir kez sayılır.</p>
                      <table><thead><tr><th>Sipariş / Poz</th><th>Müşteri</th><th>Partiye bağlı miktar</th><th>Termin</th><th>Plan durumu</th><th>Tahmini bitiş</th></tr></thead><tbody>{r.members.map(member => {
                        const sched = schedule?.find(s => s.order_id === member.order_id);
                        return <tr key={member.order_id}><td>{member.order_no} / {member.position_no || "—"}</td><td>{member.customer}</td><td>{fmt(member.quantity,2)}</td><td>{member.due_date}</td><td>{sched ? <PlanStatusBadge s={sched.plan_status} /> : "Açık sipariş raporunda yok"}</td><td>{sched?.planned_end ?? "—"}</td></tr>;
                      })}</tbody></table>
                    </>}
                    <table><thead><tr><th>Hafta</th><th>Operasyon</th><th>Yarımamul kodu</th><th>Yarımamul adı</th><th>Plan miktarı</th><th>İşçilik (saat)</th><th>Plan türü</th></tr></thead><tbody>
                      {[...r.details].sort((a,b) => a.week_start.localeCompare(b.week_start) || a.operation_seq-b.operation_seq).map(l => <tr key={l.id}>
                        <td>{weekLong(l.week_start)}</td><td>{l.operation_seq} · {l.operation_name || "Ad tanımlı değil"}</td><td>{l.semi_finished_code || "—"}</td><td>{l.semi_finished_name || "—"}</td><td>{fmt(l.planned_qty, 2)}</td><td>{fmt(l.planned_hours)}</td><td>{l.mode === "forecast" ? "Tahmin" : l.mode === "manual" ? "Manuel" : "Otomatik"}</td>
                      </tr>)}
                    </tbody></table>
                  </td></tr>}
                  </Fragment>
                ))}
                {rows.length === 0 && <tr><td colSpan={12} className="muted">Bu iş merkezine plan ufku içinde yerleştirilmiş sipariş yok.</td></tr>}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}
