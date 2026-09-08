import { Fragment, useMemo, useState } from "react";
import { api, fmt, qs, weekLong, type MergeGroup, type MergeImpact, type PlanMode, type ProductionBatch } from "../../api";
import { useAuth } from "../../auth";
import { ErrorText, useAsync } from "../../components";

type PlanCtx = { start_week: string; weeks: number; work_center_ids: number[]; mode: PlanMode };

type MergeFilters = {
  item_code: string;
  item_name: string;
  order_count_min: string;
  order_count_max: string;
  total_qty_min: string;
  total_qty_max: string;
  due_from: string;
  due_to: string;
  spread_max: string;
  customer: string;
  status: "" | "recommended" | "optional" | "progress" | "waiting";
};

const EMPTY_FILTERS: MergeFilters = {
  item_code: "",
  item_name: "",
  order_count_min: "",
  order_count_max: "",
  total_qty_min: "",
  total_qty_max: "",
  due_from: "",
  due_to: "",
  spread_max: "",
  customer: "",
  status: "",
};

function matchNum(v: number, min: string, max: string) {
  if (min && v < Number(min)) return false;
  if (max && v > Number(max)) return false;
  return true;
}

function filterGroups(rows: MergeGroup[], f: MergeFilters): MergeGroup[] {
  return rows.filter((g) => {
    if (f.item_code && !`${g.item_code}`.toLocaleLowerCase("tr").includes(f.item_code.toLocaleLowerCase("tr"))) return false;
    if (f.item_name && !`${g.item_name}`.toLocaleLowerCase("tr").includes(f.item_name.toLocaleLowerCase("tr"))) return false;
    if (!matchNum(g.order_count, f.order_count_min, f.order_count_max)) return false;
    if (!matchNum(g.total_qty, f.total_qty_min, f.total_qty_max)) return false;
    if (f.due_from && g.earliest_due < f.due_from) return false;
    if (f.due_to && g.latest_due > f.due_to) return false;
    if (f.spread_max && g.due_spread_days > Number(f.spread_max)) return false;
    if (f.customer) {
      const q = f.customer.toLocaleLowerCase("tr");
      const hit = g.customers.some((c) => c.toLocaleLowerCase("tr").includes(q))
        || g.orders.some((o) => o.customer.toLocaleLowerCase("tr").includes(q));
      if (!hit) return false;
    }
    if (f.status === "recommended" && !g.recommended) return false;
    if (f.status === "optional" && g.recommended) return false;
    if (f.status === "progress" && !g.has_progress) return false;
    if (f.status === "waiting" && g.has_progress) return false;
    return true;
  });
}

/** Aynı stok kodlu açık siparişler için üretim partisi önerileri; tolerans, filtre, etki analizi ve toplu uygulama. */
export default function MergePanel({ planCtx, onChanged, onAutoPlan }: { planCtx: PlanCtx; onChanged: () => void; onAutoPlan: () => Promise<void> }) {
  const { can } = useAuth();
  const [tolerance, setTolerance] = useState(5);
  const [recFilters, setRecFilters] = useState<MergeFilters>({ ...EMPTY_FILTERS });
  const [optFilters, setOptFilters] = useState<MergeFilters>({ ...EMPTY_FILTERS });
  const groups = useAsync(() => api.get<MergeGroup[]>(`/api/plan/merge-suggestions${qs({ tolerance_days: tolerance })}`), [tolerance]);
  const batches = useAsync(() => api.get<ProductionBatch[]>("/api/plan/production-batches"), []);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [openKey, setOpenKey] = useState("");
  const [bulkBusy, setBulkBusy] = useState(false);
  const [impact, setImpact] = useState<MergeImpact | null>(null);
  const [pending, setPending] = useState<MergeGroup[]>([]);
  const reloadAll = () => { groups.reload(); batches.reload(); onChanged(); };

  const recommended = useMemo(() => filterGroups(groups.data?.filter((g) => g.recommended) ?? [], recFilters), [groups.data, recFilters]);
  const optional = useMemo(() => filterGroups(groups.data?.filter((g) => !g.recommended) ?? [], optFilters), [groups.data, optFilters]);

  const dissolve = async (b: ProductionBatch) => {
    const nos = b.orders.map((o) => o.order_no).join(", ");
    if (!confirm(`${b.batch_no} üretim partisi dağıtılsın mı? (${nos})`)) return;
    setErr("");
    try {
      await api.del(`/api/plan/merge/${b.id}`);
      setMsg(`${b.batch_no} dağıtıldı (${b.orders.length} sipariş).`);
      reloadAll();
    } catch (e) { setErr((e as Error).message); }
  };

  const executeMerges = async (rows: MergeGroup[], runAuto: boolean) => {
    setBulkBusy(true); setErr("");
    let ok = 0;
    const fails: string[] = [];
    for (const g of rows) {
      try {
        const earliest = g.orders.map((o) => o.effective_due_date || o.due_date).sort()[0];
        const batchNo = `URT-${g.item_code}-${earliest.replace(/-/g, "")}-${ok + 1}`;
        await api.post<ProductionBatch>("/api/plan/merge", { order_ids: g.orders.map((o) => o.id), order_no: batchNo, due_date: earliest });
        ok++;
      } catch (e) {
        fails.push(`${g.item_code}: ${(e as Error).message}`);
      }
    }
    setImpact(null);
    setPending([]);
    setMsg(`${ok} üretim partisi oluşturuldu.${runAuto ? " Otomatik plan yenilendi." : " Planı «Otomatik planla» ile güncelleyin."}`);
    if (fails.length) setErr(fails.slice(0, 5).join("\n"));
    reloadAll();
    if (runAuto && ok > 0) await onAutoPlan();
    setBulkBusy(false);
  };

  const previewAndApply = async (rows: MergeGroup[], _title: string) => {
    const eligible = rows.filter((g) => g.orders.length >= 2);
    if (!eligible.length) { setErr("Uygulanacak grup yok."); return; }
    setBulkBusy(true); setErr("");
    try {
      const data = await api.post<MergeImpact>("/api/plan/merge/impact", {
        merge_groups: eligible.map((g) => ({ order_ids: g.orders.map((o) => o.id) })),
        start_week: planCtx.start_week,
        weeks: planCtx.weeks,
        work_center_ids: planCtx.work_center_ids.length ? planCtx.work_center_ids : null,
        mode: planCtx.mode,
      });
      setImpact(data);
      setPending(eligible);
    } catch (e) { setErr((e as Error).message); } finally { setBulkBusy(false); }
  };

  const renderTable = (
    rows: MergeGroup[],
    title: string,
    filters: MergeFilters,
    setFilters: (f: MergeFilters) => void,
    bulkLabel: string,
  ) => rows.length === 0 && !groups.data?.length ? null : (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 12, margin: "16px 0 8px", flexWrap: "wrap" }}>
        <h3 style={{ margin: 0 }}>{title} ({rows.length})</h3>
        {can("poweruser") && rows.length > 0 && (
          <button onClick={() => previewAndApply(rows, bulkLabel)} disabled={bulkBusy}>
            Etki analizi ({rows.length})
          </button>
        )}
        <button className="secondary small" onClick={() => setFilters({ ...EMPTY_FILTERS })}>Filtreleri temizle</button>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th></th>
              <th>Stok kodu</th>
              <th className="num">Sipariş</th>
              <th className="num">Toplam miktar</th>
              <th>Termin aralığı</th>
              <th className="num">Termin farkı</th>
              <th>Müşteriler</th>
              <th>Durum</th>
            </tr>
            <tr style={{ background: "#f8fafc" }}>
              <td></td>
              <td><input placeholder="Kod" value={filters.item_code} onChange={(e) => setFilters({ ...filters, item_code: e.target.value })} style={{ width: "100%", minWidth: 80 }} /></td>
              <td><input placeholder="min" value={filters.order_count_min} onChange={(e) => setFilters({ ...filters, order_count_min: e.target.value })} style={{ width: 44 }} title="Min sipariş" />–<input placeholder="max" value={filters.order_count_max} onChange={(e) => setFilters({ ...filters, order_count_max: e.target.value })} style={{ width: 44 }} title="Max sipariş" /></td>
              <td><input placeholder="min" value={filters.total_qty_min} onChange={(e) => setFilters({ ...filters, total_qty_min: e.target.value })} style={{ width: 52 }} />–<input placeholder="max" value={filters.total_qty_max} onChange={(e) => setFilters({ ...filters, total_qty_max: e.target.value })} style={{ width: 52 }} /></td>
              <td><input type="date" value={filters.due_from} onChange={(e) => setFilters({ ...filters, due_from: e.target.value })} title="En erken termin ≥" /> <input type="date" value={filters.due_to} onChange={(e) => setFilters({ ...filters, due_to: e.target.value })} title="En geç termin ≤" /></td>
              <td><input placeholder="≤ gün" value={filters.spread_max} onChange={(e) => setFilters({ ...filters, spread_max: e.target.value })} style={{ width: 56 }} /></td>
              <td><input placeholder="Müşteri" value={filters.customer} onChange={(e) => setFilters({ ...filters, customer: e.target.value })} style={{ width: "100%", minWidth: 90 }} /></td>
              <td>
                <select value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value as MergeFilters["status"] })}>
                  <option value="">Tümü</option>
                  <option value="progress">Üretim başladı</option>
                  <option value="waiting">Bekliyor</option>
                </select>
              </td>
            </tr>
          </thead>
          <tbody>
            {rows.map((g) => (
              <Fragment key={g.cluster_key || `${g.item_id}-${g.recommended}`}>
                <tr
                  onClick={() => setOpenKey(openKey === g.cluster_key ? "" : g.cluster_key)}
                  style={{ cursor: "pointer", background: openKey === g.cluster_key ? "#f0f7ff" : undefined }}
                >
                  <td>{openKey === g.cluster_key ? "▼" : "▶"}</td>
                  <td><b>{g.item_code}</b> <span className="muted">{g.item_name}</span></td>
                  <td className="num">{g.order_count}</td>
                  <td className="num">{fmt(g.total_qty, 0)}</td>
                  <td>{g.earliest_due}{g.latest_due !== g.earliest_due && ` → ${g.latest_due}`}</td>
                  <td className="num">{g.due_spread_days} gün</td>
                  <td className="muted">{g.customers.join(", ") || "—"}</td>
                  <td>
                    {g.has_progress ? <span className="badge warn">üretim başladı</span> : g.recommended ? <span className="badge ok">önerilen</span> : <span className="badge muted">opsiyonel</span>}
                  </td>
                </tr>
                {openKey === g.cluster_key && (
                  <tr>
                    <td colSpan={8} style={{ background: "#f8fafc", padding: 0 }}>
                      <GroupDetail g={g} canAct={can("poweruser")} onPreview={(rows) => previewAndApply(rows, "tek grup")} onDone={(m) => { setMsg(m); reloadAll(); setOpenKey(""); }} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
            {rows.length === 0 && <tr><td colSpan={8} className="muted">Filtreye uyan kayıt yok.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );

  return (
    <>
      <p className="muted" style={{ marginTop: 0 }}>
        Aynı stok kodundan birden fazla açık sipariş varsa termin toleransına göre gruplanır.
        Tablo başlıklarının altındaki alanlarla filtreleyin. <b>Etki analizi</b> birleştirme + otomatik plan yenileme sonrası termin kaymalarını ve haftalık yük farkını gösterir; onay sonrası uygulayın.
      </p>
      <div className="row" style={{ marginBottom: 10 }}>
        <label>Termin toleransı (gün)
          <input type="number" min={0} max={365} value={tolerance} onChange={(e) => setTolerance(Math.max(0, Number(e.target.value)))} style={{ width: 72 }} />
        </label>
        <span className="muted">±{tolerance} gün içindeki terminler otomatik önerilir; dışındakiler opsiyonel listede gösterilir.</span>
      </div>
      {msg && <div className="success" style={{ marginBottom: 8 }}>{msg}</div>}
      <ErrorText err={err || groups.err} />

      {renderTable(recommended, "Önerilen birleştirmeler", recFilters, setRecFilters, "önerilen")}
      {renderTable(optional, "Opsiyonel birleştirmeler", optFilters, setOptFilters, "opsiyonel")}
      {groups.data?.length === 0 && <p className="muted">Üretim birleştirme önerisi yok.</p>}

      {(batches.data?.length ?? 0) > 0 && (
        <>
          <h2>Açık üretim partileri</h2>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Parti no</th><th>Stok</th><th className="num">Miktar</th><th>Termin</th><th>Siparişler</th><th></th></tr></thead>
              <tbody>
                {batches.data!.map((b) => (
                  <tr key={b.id}>
                    <td><b>{b.batch_no}</b></td>
                    <td>{b.item_code} <span className="muted">{b.item_name}</span></td>
                    <td className="num">{fmt(b.quantity, 0)}</td>
                    <td>{b.due_date}</td>
                    <td>
                      {b.orders.map((o) => (
                        <span key={o.order_id} className="chip" style={{ marginRight: 4 }} title={`${o.customer} · ${o.due_date}`}>
                          {o.order_no}{o.position_no ? `/${o.position_no}` : ""} · {fmt(o.quantity, 0)}
                        </span>
                      ))}
                    </td>
                    <td>{can("poweruser") && <button className="danger small" onClick={() => dissolve(b)}>Dağıt</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {impact && (
        <div className="panel" style={{ position: "fixed", inset: "5% 8%", zIndex: 1000, overflow: "auto", boxShadow: "0 8px 40px rgba(0,0,0,.2)" }}>
          <h2 style={{ marginTop: 0 }}>Birleştirme etki analizi</h2>
          <p className="muted">{impact.note}</p>
          <p><b>{impact.merge_count}</b> parti · <b>{impact.order_count}</b> sipariş · <b className={impact.delayed_count ? "bad" : "ok"}>{impact.delayed_count}</b> siparişte termin kayması riski</p>
          {impact.batches.length > 0 && (
            <>
              <h3>Oluşturulacak partiler</h3>
              <ul>{impact.batches.map((b) => <li key={b.batch_no}><b>{b.item_code}</b> · {b.order_count} sip. · {fmt(b.quantity, 0)} ad · {b.order_nos.join(", ")}</li>)}</ul>
            </>
          )}
          <h3>Ötelenebilecek diğer siparişler ({impact.delayed_orders.length})</h3>
          {impact.delayed_orders.length === 0 ? <p className="muted">Mevcut plana göre başka sipariş termininde kayma beklenmiyor.</p> : (
            <div className="table-wrap"><table>
              <thead><tr><th>Sipariş</th><th>Müşteri</th><th>Stok</th><th>Termin</th><th>Bitiş (önce)</th><th>Bitiş (sonra)</th><th className="num">Kayma</th></tr></thead>
              <tbody>{impact.delayed_orders.slice(0, 100).map((r) => (
                <tr key={r.order_id}>
                  <td><b>{r.order_no}</b>{r.position_no && `/${r.position_no}`}</td>
                  <td>{r.customer}</td><td>{r.item_code}</td><td>{r.due_date}</td>
                  <td>{r.before_end ?? "—"}</td><td>{r.after_end ?? "—"}</td>
                  <td className="num" style={{ color: "var(--bad)", fontWeight: 600 }}>+{r.delay_days} gün</td>
                </tr>
              ))}</tbody>
            </table></div>
          )}
          <h3>Haftalık yük değişimi (iş merkezi × hafta)</h3>
          {impact.load_deltas.length === 0 ? <p className="muted">Haftalık yük dağılımında anlamlı fark yok.</p> : (
            <div className="table-wrap"><table>
              <thead><tr><th>İş merkezi</th><th>Hafta</th><th className="num">Önce (sa)</th><th className="num">Sonra (sa)</th><th className="num">Fark</th></tr></thead>
              <tbody>{impact.load_deltas.slice(0, 80).map((d, i) => (
                <tr key={i}>
                  <td><b>{d.work_center_code}</b></td><td>{weekLong(d.week_start)}</td>
                  <td className="num">{fmt(d.before_hours)}</td><td className="num">{fmt(d.after_hours)}</td>
                  <td className="num" style={{ color: d.delta_hours > 0 ? "var(--bad)" : "var(--ok)", fontWeight: 600 }}>{d.delta_hours > 0 ? "+" : ""}{fmt(d.delta_hours)}</td>
                </tr>
              ))}</tbody>
            </table></div>
          )}
          <div className="row" style={{ marginTop: 16 }}>
            <button onClick={() => executeMerges(pending, false)} disabled={bulkBusy}>Yalnızca birleştir</button>
            <button onClick={() => executeMerges(pending, true)} disabled={bulkBusy}>Birleştir ve otomatik planla</button>
            <button className="secondary" onClick={() => { setImpact(null); setPending([]); }}>Vazgeç</button>
          </div>
        </div>
      )}
    </>
  );
}

function GroupDetail({ g, canAct, onPreview, onDone }: { g: MergeGroup; canAct: boolean; onPreview: (rows: MergeGroup[]) => void; onDone: (msg: string) => void }) {
  const [sel, setSel] = useState<number[]>(g.orders.map((o) => o.id));
  const [batchNo, setBatchNo] = useState("");
  const [due, setDue] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const chosen = g.orders.filter((o) => sel.includes(o.id));
  const qty = chosen.reduce((s, o) => s + o.quantity, 0);
  const earliest = chosen.map((o) => o.effective_due_date || o.due_date).sort()[0] ?? "";
  const toggle = (id: number) => setSel((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  const autoNo = `URT-${g.item_code}-${(due || earliest).replace(/-/g, "")}`;
  const partialGroup = (): MergeGroup => ({
    ...g,
    orders: chosen,
    order_count: chosen.length,
    total_qty: qty,
    earliest_due: earliest,
    latest_due: chosen.map((o) => o.effective_due_date || o.due_date).sort().slice(-1)[0] ?? earliest,
    customers: [...new Set(chosen.map((o) => o.customer).filter(Boolean))],
  });

  const submit = async () => {
    if (chosen.length < 2) return;
    onPreview([partialGroup()]);
  };

  return (
    <div style={{ padding: 12 }}>
      <table style={{ width: "100%" }}>
        <thead><tr><th></th><th>Sipariş</th><th>Poz</th><th>Müşteri</th><th className="num">Miktar</th><th>Termin</th><th>Not</th></tr></thead>
        <tbody>
          {g.orders.map((o) => (
            <tr key={o.id} onClick={() => canAct && toggle(o.id)} style={{ cursor: canAct ? "pointer" : undefined, background: sel.includes(o.id) ? "#eaf4fd" : undefined }}>
              <td><input type="checkbox" checked={sel.includes(o.id)} readOnly disabled={!canAct} /></td>
              <td><b>{o.order_no}</b></td>
              <td>{o.position_no || "—"}</td>
              <td>{o.customer}</td>
              <td className="num">{fmt(o.quantity, 0)}</td>
              <td>{o.effective_due_date || o.due_date}</td>
              <td className="muted">{o.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {canAct && (
        <div className="row" style={{ marginTop: 10, alignItems: "flex-end" }}>
          <label>Parti no<input value={batchNo} onChange={(e) => setBatchNo(e.target.value)} placeholder={autoNo} /></label>
          <label>Üretim termin<input type="date" value={due || earliest} onChange={(e) => setDue(e.target.value)} /></label>
          <span><b>{chosen.length}</b> sipariş → <b>{fmt(qty, 0)}</b> adet</span>
          <button onClick={submit} disabled={busy || chosen.length < 2}>Etki analizi</button>
        </div>
      )}
      <ErrorText err={err} />
    </div>
  );
}
