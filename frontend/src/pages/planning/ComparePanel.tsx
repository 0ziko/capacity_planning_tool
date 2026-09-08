import { useState } from "react";
import { api, fmt, weekLong, type CompareDiff, type CompareRow, type PlanCompare, type PlanMode, type PlanScenario } from "../../api";
import { useAuth } from "../../auth";
import { ErrorText } from "../../components";
import { PlanStatusBadge } from "./OrderSchedulePanel";
import { RevenueTable, monthLabel } from "./RevenuePanel";

const DIFF: Record<CompareDiff, [string, string, string]> = {
  rev_misses_due: ["bad", "Ciro planında termin kaçar", "Termine göre planda zamanında biten sipariş, maksimum ciro planında termin sonrasına kalıyor"],
  rev_drops: ["bad", "Ciro planı dışarıda bırakır", "Termine göre planda ufuk içinde tamamlanan sipariş, ciro planında ufka sığmıyor / tamamlanmıyor"],
  due_drops: ["warn", "Termin planı dışarıda bırakır", "Ciro planında ufuk içinde tamamlanan sipariş, termine göre planda tamamlanamıyor (ciro fırsatı kaçar)"],
  rev_earlier: ["ok", "Ciro planında daha erken", "Her iki planda da tamamlanır; ciro planı daha erken bitirir"],
  rev_later: ["muted", "Ciro planında daha geç", "Her iki planda da tamamlanır; ciro planı daha geç bitirir (termin içinde)"],
  same: ["muted", "Aynı", "İki planda aynı sonuç"],
  other: ["muted", "Farklı", ""],
};

function DiffBadge({ d }: { d: CompareDiff }) {
  const [cls, label, title] = DIFF[d];
  return <span className={`badge ${cls}`} title={title}>{label}</span>;
}

function ScenarioCard({ s, other, onApply, busy }: { s: PlanScenario; other: PlanScenario; onApply?: () => void; busy: boolean }) {
  const delta = (a: number, b: number, invert = false) => {
    const d = a - b;
    if (Math.abs(d) < 0.5) return null;
    const good = invert ? d < 0 : d > 0;
    return <span className={`delta ${good ? "good" : "bad"}`}>{d > 0 ? "+" : ""}{fmt(d, 0)}</span>;
  };
  return (
    <div className={`panel scenario ${s.mode}`}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h3 style={{ margin: 0 }}>{s.mode === "revenue" ? "💰" : "📅"} {s.label}</h3>
        {onApply && <button onClick={onApply} disabled={busy}>Bu planı uygula</button>}
      </div>
      <div className="scen-grid">
        <div><span className="l">Ufuk içinde ciro</span><span className="v">{fmt(s.planned_revenue, 0)} {delta(s.planned_revenue, other.planned_revenue)}</span></div>
        <div><span className="l">Termine uygun</span><span className="v ok">{s.on_time} {delta(s.on_time, other.on_time)}</span></div>
        <div><span className="l">Geç</span><span className="v bad">{s.late} {delta(s.late, other.late, true)}</span></div>
        <div><span className="l">Toplam gecikme (gün)</span><span className="v">{s.total_lateness_days} {delta(s.total_lateness_days, other.total_lateness_days, true)}</span></div>
        <div><span className="l">Kısmi / planlanmadı</span><span className="v warn">{s.partial} / {s.unplanned} {delta(s.partial + s.unplanned, other.partial + other.unplanned, true)}</span></div>
        <div><span className="l">Kapasite kullanımı</span><span className="v">{fmt(s.utilization_pct, 0)}%</span></div>
      </div>
    </div>
  );
}

/** İki plan modunu (termin / maksimum ciro) kaydetmeden simüle eder ve karşılaştırır. */
export default function ComparePanel({ start, weeks, wcIds, onApplied }: { start: string; weeks: number; wcIds: number[]; onApplied: () => void }) {
  const { can } = useAuth();
  const [data, setData] = useState<PlanCompare | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState<"" | "diff" | CompareDiff>("diff");
  const [gran, setGran] = useState<"week" | "month">("week");
  const [msg, setMsg] = useState("");

  const run = async () => {
    setBusy(true); setErr(""); setMsg("");
    try { setData(await api.post<PlanCompare>("/api/plan/compare", { start_week: start, weeks, work_center_ids: wcIds.length ? wcIds : null })); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const apply = async (mode: PlanMode) => {
    const label = mode === "revenue" ? "MAKSİMUM CİRO" : "TERMİNE GÖRE";
    if (!confirm(`${label} planı uygulanacak: seçili iş merkezlerinde mevcut otomatik plan satırları silinip yeniden oluşturulur (manuel satırlar korunur). Devam?`)) return;
    setBusy(true); setErr("");
    try {
      const r = await api.post<{ message: string }>("/api/plan/auto", { start_week: start, weeks, work_center_ids: wcIds.length ? wcIds : null, replace_existing: true, mode });
      setMsg(r.message); onApplied();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };

  const rows: CompareRow[] = (data?.rows ?? []).filter((r) => (filter === "" ? true : filter === "diff" ? r.diff !== "same" : r.diff === filter));
  rows.sort((a, b) => a.due_date.localeCompare(b.due_date));
  const count = (d: CompareDiff) => (data?.rows ?? []).filter((r) => r.diff === d).length;

  // haftalik ciro yan yana
  const periods = data ? [...new Set([...(gran === "week" ? data.due.revenue.weeks : data.due.revenue.months).map((p) => p.period), ...(gran === "week" ? data.revenue.revenue.weeks : data.revenue.revenue.months).map((p) => p.period)])].sort() : [];
  const at = (s: PlanScenario, p: string) => (gran === "week" ? s.revenue.weeks : s.revenue.months).find((x) => x.period === p);

  return (
    <>
      <div className="row" style={{ marginBottom: 10 }}>
        <button onClick={run} disabled={busy}>⚖ İki planı hesapla ve karşılaştır</button>
        <span className="muted">Üstteki başlangıç haftası / hafta sayısı / iş merkezi seçimi kullanılır. Hesaplama kayıtlı planı <b>değiştirmez</b>; beğendiğiniz planı "Bu planı uygula" ile yazarsınız.</span>
      </div>
      <ErrorText err={err} />
      {msg && <div className="success">{msg}</div>}
      {!data && !busy && (
        <div className="panel muted">
          <p style={{ marginTop: 0 }}><b>Termine göre:</b> siparişler termin sırasıyla yerleştirilir; kapasite yetmezse geç kalan / ufka sığmayan siparişler görünür.</p>
          <p style={{ marginBottom: 0 }}><b>Maksimum ciro:</b> siparişler saat başına ciroya (ciro ÷ gereken saat) göre sıralanır; yalnızca ufuk içinde <i>tamamen</i> bitirilebilenler alınır (ciro teslimde gerçekleşir), kalan kapasite termin sırasıyla kısmen doldurulur. Bu planda bazı terminler kaçabilir.</p>
        </div>
      )}
      {busy && !data && <p className="muted">Hesaplanıyor…</p>}
      {data && (
        <>
          <div className="compare-grid">
            <ScenarioCard s={data.due} other={data.revenue} busy={busy} onApply={can("poweruser") ? () => apply("due_date") : undefined} />
            <ScenarioCard s={data.revenue} other={data.due} busy={busy} onApply={can("poweruser") ? () => apply("revenue") : undefined} />
          </div>

          <div className="panel">
            <h3 style={{ marginTop: 0 }}>Özet farklar</h3>
            <ul className="diff-list">
              <li>
                <DiffBadge d="rev_misses_due" /> <b>{data.rev_misses_due.length}</b> sipariş: ciro planı seçilirse termini kaçar
                {data.rev_misses_due.length > 0 && <span className="muted"> — {data.rev_misses_due.join(", ")}</span>}
              </li>
              <li>
                <DiffBadge d="rev_drops" /> <b>{data.rev_drops.length}</b> sipariş: ciro planı ufuk içinde bitiremez (termin planında biter)
                {data.rev_drops.length > 0 && <span className="muted"> — {data.rev_drops.join(", ")}</span>}
              </li>
              <li>
                <DiffBadge d="due_drops" /> <b>{data.due_drops.length}</b> sipariş: termin planı bunları ufka sığdıramaz, ciro planı bitirir (kaçan ciro fırsatı: <b>{fmt(data.rows.filter((r) => r.diff === "due_drops").reduce((s, r) => s + r.revenue, 0), 0)}</b>)
                {data.due_drops.length > 0 && <span className="muted"> — {data.due_drops.join(", ")}</span>}
              </li>
            </ul>
          </div>

          <h3>Dönemsel ciro karşılaştırması</h3>
          <div className="row" style={{ marginBottom: 8 }}>
            <div className="seg">
              <button className={gran === "week" ? "active" : ""} onClick={() => setGran("week")}>Haftalık</button>
              <button className={gran === "month" ? "active" : ""} onClick={() => setGran("month")}>Aylık</button>
            </div>
            <span className="muted">Tamamlanan (teslim) ciro; parantez içinde oransal ciro.</span>
          </div>
          <div className="table-wrap">
            <table style={{ width: "auto", minWidth: 640 }}>
              <thead><tr><th>{gran === "week" ? "Hafta" : "Ay"}</th><th className="num">📅 Termine göre</th><th className="num">💰 Maksimum ciro</th><th className="num">Fark</th><th className="num">Kümülatif fark</th></tr></thead>
              <tbody>
                {periods.map((p) => {
                  const a = at(data.due, p), b = at(data.revenue, p);
                  const d = (b?.completed_revenue ?? 0) - (a?.completed_revenue ?? 0);
                  const cd = (b?.cumulative_completed ?? 0) - (a?.cumulative_completed ?? 0);
                  return (
                    <tr key={p}>
                      <td title={p}><b>{gran === "week" ? weekLong(p) : monthLabel(p)}</b></td>
                      <td className="num">{fmt(a?.completed_revenue ?? 0, 0)} <span className="muted">({fmt(a?.earned_revenue ?? 0, 0)})</span></td>
                      <td className="num">{fmt(b?.completed_revenue ?? 0, 0)} <span className="muted">({fmt(b?.earned_revenue ?? 0, 0)})</span></td>
                      <td className="num" style={{ color: d > 0 ? "var(--ok)" : d < 0 ? "var(--bad)" : undefined }}>{d > 0 ? "+" : ""}{fmt(d, 0)}</td>
                      <td className="num" style={{ color: cd > 0 ? "var(--ok)" : cd < 0 ? "var(--bad)" : undefined }}>{cd > 0 ? "+" : ""}{fmt(cd, 0)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <h3>Sipariş bazlı karşılaştırma</h3>
          <div className="row" style={{ marginBottom: 8 }}>
            <button className={`kpi-btn ${filter === "diff" ? "active" : ""}`} onClick={() => setFilter("diff")}><span className="v">{data.rows.filter((r) => r.diff !== "same").length}</span><span className="l">Farklı olanlar</span></button>
            {(["rev_misses_due", "rev_drops", "due_drops", "rev_earlier", "rev_later"] as CompareDiff[]).map((d) => (
              <button key={d} className={`kpi-btn ${filter === d ? "active" : ""} ${DIFF[d][0]}`} onClick={() => setFilter(filter === d ? "diff" : d)} title={DIFF[d][2]}>
                <span className="v">{count(d)}</span><span className="l">{DIFF[d][1]}</span>
              </button>
            ))}
            <button className={`kpi-btn ${filter === "" ? "active" : ""}`} onClick={() => setFilter("")}><span className="v">{data.rows.length}</span><span className="l">Tümü</span></button>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Sipariş</th><th>Poz</th><th>Müşteri</th><th>Stok</th><th className="num">Miktar</th><th className="num">Ciro</th><th>Termin</th>
                  <th colSpan={2} className="grp due">📅 Termine göre</th>
                  <th colSpan={2} className="grp rev">💰 Maksimum ciro</th>
                  <th>Fark</th>
                </tr>
                <tr className="sub">
                  <th colSpan={7}></th>
                  <th>Bitiş</th><th>Durum</th>
                  <th>Bitiş</th><th>Durum</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.order_id}>
                    <td><b>{r.order_no}</b></td><td>{r.position_no || <span className="muted">—</span>}</td><td>{r.customer}</td><td>{r.item_code}</td>
                    <td className="num">{fmt(r.quantity, 0)}</td><td className="num">{r.revenue ? fmt(r.revenue, 0) : "—"}</td><td>{r.due_date}</td>
                    <td className="grp due">{r.due_end ?? "-"}{r.due_lateness !== null && r.due_lateness > 0 && <span className="muted"> (+{r.due_lateness}g)</span>}</td>
                    <td className="grp due"><PlanStatusBadge s={r.due_status} /></td>
                    <td className="grp rev">{r.rev_end ?? "-"}{r.rev_lateness !== null && r.rev_lateness > 0 && <span className="muted"> (+{r.rev_lateness}g)</span>}</td>
                    <td className="grp rev"><PlanStatusBadge s={r.rev_status} /></td>
                    <td><DiffBadge d={r.diff} /></td>
                  </tr>
                ))}
                {rows.length === 0 && <tr><td colSpan={12} className="muted">Bu filtrede sipariş yok{filter === "diff" && " — iki plan tüm siparişlerde aynı sonucu veriyor"}.</td></tr>}
              </tbody>
            </table>
          </div>

          <details style={{ marginTop: 12 }}>
            <summary className="muted">Senaryoların ayrı ciro tabloları</summary>
            <div className="compare-grid">
              <div><h4>📅 Termine göre</h4><RevenueTable rows={gran === "week" ? data.due.revenue.weeks : data.due.revenue.months} granularity={gran} showCumulative={false} /></div>
              <div><h4>💰 Maksimum ciro</h4><RevenueTable rows={gran === "week" ? data.revenue.revenue.weeks : data.revenue.revenue.months} granularity={gran} showCumulative={false} /></div>
            </div>
          </details>
        </>
      )}
    </>
  );
}
