import { useState } from "react";
import { api, fmt, type MergeGroup, type ProductionBatch } from "../../api";
import { useAuth } from "../../auth";
import { ErrorText, useAsync } from "../../components";

/** Aynı stok kodlu açık siparişler için üretim partisi önerileri; siparişler ayrı kalır. */
export default function MergePanel({ onChanged }: { onChanged: () => void }) {
  const { can } = useAuth();
  const groups = useAsync(() => api.get<MergeGroup[]>("/api/plan/merge-suggestions"), []);
  const batches = useAsync(() => api.get<ProductionBatch[]>("/api/plan/production-batches"), []);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const reloadAll = () => { groups.reload(); batches.reload(); onChanged(); };

  const dissolve = async (b: ProductionBatch) => {
    const nos = b.orders.map((o) => o.order_no).join(", ");
    if (!confirm(`${b.batch_no} üretim partisi dağıtılsın mı? (${nos}) Parti plan satırları silinir; siparişler açık kalır.`)) return;
    setErr("");
    try {
      await api.del(`/api/plan/merge/${b.id}`);
      setMsg(`${b.batch_no} dağıtıldı (${b.orders.length} sipariş). Planı güncellemek için Otomatik planla'yı çalıştırın.`);
      reloadAll();
    } catch (e) { setErr((e as Error).message); }
  };

  return (
    <>
      <p className="muted" style={{ marginTop: 0 }}>
        Aynı stok kodundan birden fazla açık sipariş varsa burada listelenir. Seçtiğiniz siparişler tek bir <b>üretim partisinde</b> birlikte planlanır;
        siparişler her zaman ayrı kalır, stok ve rezervasyon sipariş bazında devam eder. Parti dağıtılabilir.
      </p>
      {msg && <div className="success" style={{ marginBottom: 8 }}>{msg}</div>}
      <ErrorText err={err || groups.err} />

      {groups.data?.length === 0 && <div className="panel muted">Üretim birleştirme önerisi yok: partide olmayan her stok kodu için en fazla bir açık sipariş var.</div>}
      {groups.data?.map((g) => <GroupCard key={g.item_id} g={g} canAct={can("poweruser")} onDone={(m) => { setMsg(m); reloadAll(); }} />)}

      {(batches.data?.length ?? 0) > 0 && (
        <>
          <h2>Açık üretim partileri</h2>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Parti no</th><th>Stok</th><th className="num">Toplam miktar</th><th>Termin</th><th>Siparişler</th><th></th></tr></thead>
              <tbody>
                {batches.data!.map((b) => (
                  <tr key={b.id}>
                    <td><b>{b.batch_no}</b></td>
                    <td>{b.item_code} <span className="muted">{b.item_name}</span></td>
                    <td className="num">{fmt(b.quantity, 0)}</td>
                    <td>{b.due_date}</td>
                    <td>
                      {b.orders.map((o) => (
                        <span key={o.order_id} className="chip" style={{ marginRight: 4 }} title={`${o.customer} · termin ${o.due_date}`}>
                          {o.order_no}{o.position_no ? ` / ${o.position_no}` : ""} · {fmt(o.quantity, 0)}
                        </span>
                      ))}
                    </td>
                    <td>{can("poweruser") && <button className="danger small" onClick={() => dissolve(b)}>Partiyi dağıt</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}

function GroupCard({ g, canAct, onDone }: { g: MergeGroup; canAct: boolean; onDone: (msg: string) => void }) {
  const [sel, setSel] = useState<number[]>(g.orders.map((o) => o.id));
  const [batchNo, setBatchNo] = useState("");
  const [due, setDue] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const chosen = g.orders.filter((o) => sel.includes(o.id));
  const qty = chosen.reduce((s, o) => s + o.quantity, 0);
  const earliest = chosen.map((o) => o.due_date).sort()[0] ?? "";
  const toggle = (id: number) => setSel((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  const autoNo = `URT-${g.item_code}-${(due || earliest).replace(/-/g, "")}`;

  const submit = async () => {
    if (!confirm(`${chosen.map((o) => o.order_no).join(" + ")} → parti ${batchNo.trim() || autoNo} (${fmt(qty, 0)} adet üretim, termin ${due || earliest})? Siparişler ayrı kalır.`)) return;
    setBusy(true); setErr("");
    try {
      const b = await api.post<ProductionBatch>("/api/plan/merge", { order_ids: sel, order_no: batchNo.trim() || null, due_date: due || null });
      onDone(`${b.batch_no} oluşturuldu (${fmt(b.quantity, 0)} adet, ${b.orders.length} sipariş). Planı güncellemek için Otomatik planla'yı çalıştırın.`);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };

  return (
    <div className="panel" style={{ marginBottom: 10 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div>
          <b>{g.item_code}</b> <span className="muted">{g.item_name}</span> · <b>{g.order_count}</b> sipariş · toplam <b>{fmt(g.total_qty, 0)}</b> adet · termin {g.earliest_due}{g.latest_due !== g.earliest_due && ` → ${g.latest_due}`}
          {" "}· müşteriler: {g.customers.join(", ") || "-"}
          {g.has_progress && <span className="badge warn" style={{ marginLeft: 8 }} title="Bu siparişlerden en az birinde üretim kaydı var; parti üretiminde ilerleme sipariş no ile takip edilir.">üretim başladı</span>}
        </div>
      </div>
      <table style={{ width: "auto", marginTop: 6 }}>
        <thead><tr><th></th><th>Sipariş</th><th>Poz</th><th>Müşteri</th><th className="num">Miktar</th><th>Termin</th><th>Not</th></tr></thead>
        <tbody>
          {g.orders.map((o) => (
            <tr key={o.id} onClick={() => canAct && toggle(o.id)} style={{ cursor: canAct ? "pointer" : undefined, background: sel.includes(o.id) ? "#eaf4fd" : undefined }}>
              <td><input type="checkbox" checked={sel.includes(o.id)} readOnly disabled={!canAct} /></td>
              <td><b>{o.order_no}</b></td><td>{o.position_no || <span className="muted">—</span>}</td><td>{o.customer}</td><td className="num">{fmt(o.quantity, 0)}</td><td>{o.due_date}</td><td className="muted">{o.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {canAct && (
        <div className="row" style={{ marginTop: 8, alignItems: "flex-end" }}>
          <label>Parti no<input value={batchNo} onChange={(e) => setBatchNo(e.target.value)} placeholder={autoNo} style={{ minWidth: 220 }} /></label>
          <label>Üretim termin<input type="date" value={due || earliest} onChange={(e) => setDue(e.target.value)} title="Partinin planlanacağı termin" /></label>
          <span><b>{chosen.length}</b> sipariş → <b>{fmt(qty, 0)}</b> adet üretim</span>
          <button onClick={submit} disabled={busy || chosen.length < 2}>Üretim partisi oluştur</button>
        </div>
      )}
      <ErrorText err={err} />
    </div>
  );
}
