import { useEffect, useState } from "react";
import { api, fmt, qs, type Item, type Order, type OrderIn } from "../api";
import { useAuth } from "../auth";
import { ErrorText, WcMultiSelect, useAsync, useWorkCenters } from "../components";

interface ReqLine { work_center_id: number; work_center_code: string; item_code: string; operation_seq: number; operation_name: string; quantity: number; hours: number }
interface ReqOut { lines: ReqLine[]; by_work_center: { work_center_id: number; work_center_code: string; hours: number }[]; total_hours: number }

const STATUS_LABEL: Record<string, string> = { open: "Açık", closed: "Kapalı", merged: "Birleştirildi" };

export default function Orders() {
  const { can } = useAuth();
  const { wcs } = useWorkCenters();
  const [status, setStatus] = useState("open");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [wcIds, setWcIds] = useState<number[]>([]);
  const [selCodes, setSelCodes] = useState<string[]>([]);
  const [editing, setEditing] = useState<Order | "new" | null>(null);
  const [msg, setMsg] = useState("");
  const orders = useAsync(() => api.get<Order[]>(`/api/orders${qs({ status, due_from: from, due_to: to })}`), [status, from, to]);
  const req = useAsync(
    () => api.post<ReqOut>("/api/requirements", { work_center_ids: wcIds.length ? wcIds : null, item_codes: selCodes.length ? selCodes : null, due_from: from || null, due_to: to || null }),
    [wcIds.join(","), selCodes.join(","), from, to, orders.data?.length]
  );
  const toggleCode = (c: string) => setSelCodes((s) => (s.includes(c) ? s.filter((x) => x !== c) : [...s, c]));
  const unitOf = (wcId: number) => wcs.find((w) => w.id === wcId)?.capacity_unit_hours ?? 10;
  const afterSave = (o: Order, created: boolean) => {
    setEditing(null);
    setMsg(`${o.order_no} / ${o.item_code} ${created ? "eklendi" : "güncellendi"}.`);
    orders.reload();
  };
  const remove = async (o: Order) => {
    if (!confirm(`${o.order_no} / ${o.item_code} siparişi ve plan satırları silinsin mi?`)) return;
    try {
      await api.del(`/api/orders/${o.id}`);
      setMsg(`${o.order_no} silindi.`);
      orders.reload();
    } catch (e) { setMsg((e as Error).message); }
  };

  return (
    <>
      <h1>Siparişler & İş Gücü İhtiyacı</h1>
      <div className="panel row">
        <label>Durum<select value={status} onChange={(e) => setStatus(e.target.value)}><option value="open">Açık</option><option value="closed">Kapalı</option><option value="merged">Birleştirilmiş</option><option value="">Tümü</option></select></label>
        <label>Termin (başlangıç)<input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        <label>Termin (bitiş)<input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
        <WcMultiSelect wcs={wcs} value={wcIds} onChange={setWcIds} />
        {selCodes.length > 0 && <button className="secondary" onClick={() => setSelCodes([])}>Stok seçimini temizle ({selCodes.length})</button>}
        {can("poweruser") && <button onClick={() => { setEditing("new"); setMsg(""); }} disabled={editing === "new"}>+ Yeni sipariş</button>}
      </div>
      <ErrorText err={orders.err || req.err} />
      {msg && <div className="success" style={{ marginBottom: 8 }}>{msg}</div>}
      {editing && <OrderForm initial={editing === "new" ? null : editing} onSaved={afterSave} onCancel={() => setEditing(null)} />}

      <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr", gap: 14 }}>
        <div>
          <h2>{STATUS_LABEL[status] ?? "Tüm"} siparişler <span className="muted">(satıra tıklayarak ihtiyaç hesabını seçili stoklara daraltın)</span></h2>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Sipariş</th><th>Müşteri</th><th>Termin</th><th>Stok</th><th className="num">Miktar</th><th>Not</th><th></th></tr></thead>
              <tbody>
                {orders.data?.map((o) => (
                  <tr key={o.id} onClick={() => toggleCode(o.item_code)} style={{ cursor: "pointer", background: selCodes.includes(o.item_code) ? "#e3f2fd" : undefined }}>
                    <td>
                      {o.order_no}
                      {o.status === "merged" && <span className="badge muted" style={{ marginLeft: 6 }} title={`Birleşik sipariş id ${o.merged_into_id}`}>birleştirildi</span>}
                      {o.status === "closed" && <span className="badge muted" style={{ marginLeft: 6 }}>kapalı</span>}
                    </td>
                    <td>{o.customer}</td><td>{o.due_date}</td>
                    <td><b>{o.item_code}</b> <span className="muted">{o.item_name}</span></td>
                    <td className="num">{fmt(o.quantity, 0)}</td>
                    <td className="muted" title={o.note}>{o.note.length > 40 ? o.note.slice(0, 40) + "…" : o.note}</td>
                    <td onClick={(e) => e.stopPropagation()} style={{ whiteSpace: "nowrap" }}>
                      {can("poweruser") && o.status !== "merged" && (
                        <>
                          <button className="secondary small" onClick={() => { setEditing(o); setMsg(""); }}>Düzenle</button>{" "}
                          {o.status === "open"
                            ? <button className="secondary small" onClick={async () => { await api.patch(`/api/orders/${o.id}/status?status=closed`); orders.reload(); }}>Kapat</button>
                            : <button className="secondary small" onClick={async () => { await api.patch(`/api/orders/${o.id}/status?status=open`); orders.reload(); }}>Aç</button>}{" "}
                          <button className="danger small" onClick={() => remove(o)}>Sil</button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
                {orders.data?.length === 0 && <tr><td colSpan={7} className="muted">Sipariş yok.</td></tr>}
              </tbody>
            </table>
          </div>
          <p className="muted">{orders.data?.length ?? 0} sipariş</p>
        </div>
        <div>
          <h2>İş merkezi bazlı ihtiyaç</h2>
          <div className="panel kpi"><span className="v">{fmt(req.data?.total_hours)} saat</span><span className="l">Toplam iş gücü ihtiyacı (seçime göre)</span></div>
          <div className="table-wrap" style={{ maxHeight: 260 }}>
            <table>
              <thead><tr><th>İş Merkezi</th><th className="num">Saat</th><th className="num">Birim</th></tr></thead>
              <tbody>{req.data?.by_work_center.map((r) => <tr key={r.work_center_id}><td><b>{r.work_center_code}</b></td><td className="num">{fmt(r.hours)}</td><td className="num">{fmt(r.hours / unitOf(r.work_center_id))}</td></tr>)}</tbody>
            </table>
          </div>
          <h2>Operasyon detayı</h2>
          <div className="table-wrap" style={{ maxHeight: 320 }}>
            <table>
              <thead><tr><th>İş Merkezi</th><th>Stok</th><th>Op.</th><th className="num">Miktar</th><th className="num">Saat</th></tr></thead>
              <tbody>{req.data?.lines.map((l, i) => <tr key={i}><td>{l.work_center_code}</td><td>{l.item_code}</td><td>{l.operation_seq} {l.operation_name}</td><td className="num">{fmt(l.quantity, 0)}</td><td className="num">{fmt(l.hours, 2)}</td></tr>)}</tbody>
            </table>
          </div>
        </div>
      </div>
    </>
  );
}

/** Tekil sipariş ekleme / düzenleme formu. */
function OrderForm({ initial, onSaved, onCancel }: { initial: Order | null; onSaved: (o: Order, created: boolean) => void; onCancel: () => void }) {
  const [form, setForm] = useState<OrderIn>({
    order_no: initial?.order_no ?? "",
    customer: initial?.customer ?? "",
    due_date: initial?.due_date ?? "",
    item_code: initial?.item_code ?? "",
    quantity: initial?.quantity ?? 0,
    note: initial?.note ?? "",
  });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [items, setItems] = useState<Item[]>([]);
  const [q, setQ] = useState("");
  useEffect(() => {
    const t = setTimeout(() => api.get<Item[]>(`/api/items${qs({ q: q || undefined, limit: 50 })}`).then(setItems).catch(() => setItems([])), 200);
    return () => clearTimeout(t);
  }, [q]);
  const picked = items.find((i) => i.code.toLocaleUpperCase("tr") === form.item_code.trim().toLocaleUpperCase("tr"));
  const set = (k: keyof OrderIn, v: string | number) => setForm((f) => ({ ...f, [k]: v }));
  const valid = form.order_no.trim() && form.item_code.trim() && form.due_date && form.quantity > 0;
  const submit = async () => {
    setErr(""); setBusy(true);
    try {
      const body = { ...form, order_no: form.order_no.trim(), item_code: form.item_code.trim() };
      const o = initial ? await api.put<Order>(`/api/orders/${initial.id}`, body) : await api.post<Order>("/api/orders", body);
      onSaved(o, !initial);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <div className="panel" style={{ borderLeft: "4px solid var(--primary)" }}>
      <h2 style={{ marginTop: 0 }}>{initial ? `Siparişi düzenle — ${initial.order_no}` : "Yeni sipariş"}</h2>
      <div className="row" style={{ alignItems: "flex-end" }}>
        <label>Sipariş no *<input value={form.order_no} onChange={(e) => set("order_no", e.target.value)} placeholder="örn. SP-2026-001" autoFocus /></label>
        <label>Müşteri<input value={form.customer} onChange={(e) => set("customer", e.target.value)} /></label>
        <label>
          Stok kodu *
          <input list="order-item-codes" value={form.item_code} onChange={(e) => { set("item_code", e.target.value); setQ(e.target.value); }} placeholder="Kod yazın / listeden seçin" />
          <datalist id="order-item-codes">{items.map((i) => <option key={i.id} value={i.code}>{i.name}</option>)}</datalist>
        </label>
        <label>Miktar *<input type="number" min={0} step="1" value={form.quantity || ""} onChange={(e) => set("quantity", Number(e.target.value))} /></label>
        <label>Termin *<input type="date" value={form.due_date} onChange={(e) => set("due_date", e.target.value)} /></label>
        <label style={{ minWidth: 220 }}>Not<input value={form.note} onChange={(e) => set("note", e.target.value)} /></label>
        <button onClick={submit} disabled={!valid || busy}>{initial ? "Kaydet" : "Ekle"}</button>
        <button className="secondary" onClick={onCancel}>Vazgeç</button>
      </div>
      <div className="muted" style={{ marginTop: 6 }}>
        {picked ? <>Seçilen stok: <b>{picked.code}</b> — {picked.name} {picked.product_group && <>· {picked.product_group}</>}</> : form.item_code.trim() ? "Bu kod stok listesinde bulunamadı; önce Stok / BOM / Rota ekranından tanımlanmalı." : "Stok kodu, rota (operasyon + çevrim süresi) tanımlı bir kayıt olmalı; ihtiyaç ve plan bu rotadan hesaplanır."}
        {initial && " · Stok kodu değiştirilirse mevcut plan satırları silinir."}
      </div>
      <ErrorText err={err} />
    </div>
  );
}
