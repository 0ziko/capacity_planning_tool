import { useState } from "react";
import { api, fmt, type MergeGroup, type Order } from "../../api";
import { useAuth } from "../../auth";
import { ErrorText, useAsync } from "../../components";

/** Aynı stok kodlu (dolayısıyla aynı rotalı) açık siparişleri birleştirme önerileri ve aksiyonu. */
export default function MergePanel({ onChanged }: { onChanged: () => void }) {
  const { can } = useAuth();
  const groups = useAsync(() => api.get<MergeGroup[]>("/api/plan/merge-suggestions"), []);
  const merged = useAsync(() => api.get<Order[]>("/api/orders?status=merged"), []);
  const openOrders = useAsync(() => api.get<Order[]>("/api/orders?status=open"), []);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const reloadAll = () => { groups.reload(); merged.reload(); openOrders.reload(); onChanged(); };

  const unmerge = async (o: Order, sources: Order[]) => {
    if (!confirm(`${o.order_no} birleştirmesi geri alınsın mı? ${sources.map((s) => s.order_no).join(", ")} yeniden açılır; birleşik siparişin plan satırları silinir.`)) return;
    setErr("");
    try {
      await api.del(`/api/plan/merge/${o.id}`);
      setMsg(`${o.order_no} geri alındı (${sources.length} sipariş yeniden açıldı). Planı güncellemek için Otomatik planla'yı çalıştırın.`);
      reloadAll();
    } catch (e) { setErr((e as Error).message); }
  };

  // mevcut birleşik siparişler: kaynakları merged_into_id ile grupla
  const mergedBy = new Map<number, Order[]>();
  for (const s of merged.data ?? []) {
    if (s.merged_into_id === null) continue;
    if (!mergedBy.has(s.merged_into_id)) mergedBy.set(s.merged_into_id, []);
    mergedBy.get(s.merged_into_id)!.push(s);
  }
  const mergedOrders = (openOrders.data ?? []).filter((o) => mergedBy.has(o.id));

  return (
    <>
      <p className="muted" style={{ marginTop: 0 }}>
        Aynı stok kodundan birden fazla açık sipariş varsa (aynı rota ve operasyonlar) burada listelenir. Seçtiğiniz siparişler tek bir birleşik siparişte toplanır:
        miktarlar toplanır, termin en erken termin olur (değiştirilebilir), kaynak siparişler “birleştirildi” durumuna geçer ve plan satırları silinir. İşlem geri alınabilir.
      </p>
      {msg && <div className="success" style={{ marginBottom: 8 }}>{msg}</div>}
      <ErrorText err={err || groups.err} />

      {groups.data?.length === 0 && <div className="panel muted">Birleştirme önerisi yok: her stok kodu için en fazla bir açık sipariş var.</div>}
      {groups.data?.map((g) => <GroupCard key={g.item_id} g={g} canAct={can("poweruser")} onDone={(m) => { setMsg(m); reloadAll(); }} />)}

      {mergedOrders.length > 0 && (
        <>
          <h2>Mevcut birleşik siparişler</h2>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Birleşik sipariş</th><th>Müşteri</th><th>Stok</th><th className="num">Miktar</th><th>Termin</th><th>Kaynak siparişler</th><th></th></tr></thead>
              <tbody>
                {mergedOrders.map((o) => {
                  const src = mergedBy.get(o.id) ?? [];
                  return (
                    <tr key={o.id}>
                      <td><b>{o.order_no}</b></td><td>{o.customer}</td><td>{o.item_code}</td>
                      <td className="num">{fmt(o.quantity, 0)}</td><td>{o.due_date}</td>
                      <td>{src.map((s) => <span key={s.id} className="chip" style={{ marginRight: 4 }} title={`${s.customer} · termin ${s.due_date}`}>{s.order_no} · {fmt(s.quantity, 0)}</span>)}</td>
                      <td>{can("poweruser") && <button className="danger small" onClick={() => unmerge(o, src)}>Birleştirmeyi geri al</button>}</td>
                    </tr>
                  );
                })}
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
  const [orderNo, setOrderNo] = useState("");
  const [due, setDue] = useState("");
  const [customer, setCustomer] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const chosen = g.orders.filter((o) => sel.includes(o.id));
  const qty = chosen.reduce((s, o) => s + o.quantity, 0);
  const earliest = chosen.map((o) => o.due_date).sort()[0] ?? "";
  const custs = Array.from(new Set(chosen.map((o) => o.customer).filter(Boolean)));
  const toggle = (id: number) => setSel((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  const autoNo = `BRL-${g.item_code}-${(due || earliest).replace(/-/g, "")}`;

  const submit = async () => {
    if (!confirm(`${chosen.map((o) => o.order_no).join(" + ")} → ${orderNo.trim() || autoNo} (${fmt(qty, 0)} adet, termin ${due || earliest}) olarak birleştirilsin mi?`)) return;
    setBusy(true); setErr("");
    try {
      const m = await api.post<Order>("/api/plan/merge", { order_ids: sel, order_no: orderNo.trim() || null, due_date: due || null, customer: customer.trim() || null });
      onDone(`${m.order_no} oluşturuldu (${fmt(m.quantity, 0)} adet, termin ${m.due_date}). Planı güncellemek için Otomatik planla'yı çalıştırın.`);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };

  return (
    <div className="panel" style={{ marginBottom: 10 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div>
          <b>{g.item_code}</b> <span className="muted">{g.item_name}</span> · <b>{g.order_count}</b> sipariş · toplam <b>{fmt(g.total_qty, 0)}</b> adet · termin {g.earliest_due}{g.latest_due !== g.earliest_due && ` → ${g.latest_due}`}
          {" "}· müşteriler: {g.customers.join(", ") || "-"}
          {g.has_progress && <span className="badge warn" style={{ marginLeft: 8 }} title="Bu siparişlerden en az birinde üretim kaydı var; birleştirince ilerleme takibi sipariş no eşleşmesini kaybedebilir.">üretim başladı</span>}
        </div>
      </div>
      <table style={{ width: "auto", marginTop: 6 }}>
        <thead><tr><th></th><th>Sipariş</th><th>Müşteri</th><th className="num">Miktar</th><th>Termin</th><th>Not</th></tr></thead>
        <tbody>
          {g.orders.map((o) => (
            <tr key={o.id} onClick={() => canAct && toggle(o.id)} style={{ cursor: canAct ? "pointer" : undefined, background: sel.includes(o.id) ? "#eaf4fd" : undefined }}>
              <td><input type="checkbox" checked={sel.includes(o.id)} readOnly disabled={!canAct} /></td>
              <td><b>{o.order_no}</b></td><td>{o.customer}</td><td className="num">{fmt(o.quantity, 0)}</td><td>{o.due_date}</td><td className="muted">{o.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {canAct && (
        <div className="row" style={{ marginTop: 8, alignItems: "flex-end" }}>
          <label>Birleşik sipariş no<input value={orderNo} onChange={(e) => setOrderNo(e.target.value)} placeholder={autoNo} style={{ minWidth: 220 }} /></label>
          <label>Termin<input type="date" value={due || earliest} onChange={(e) => setDue(e.target.value)} /></label>
          <label>Müşteri<input value={customer} onChange={(e) => setCustomer(e.target.value)} placeholder={custs.join(" + ")} style={{ minWidth: 220 }} /></label>
          <span><b>{chosen.length}</b> sipariş → <b>{fmt(qty, 0)}</b> adet</span>
          <button onClick={submit} disabled={busy || chosen.length < 2}>Birleştir</button>
        </div>
      )}
      <ErrorText err={err} />
    </div>
  );
}
