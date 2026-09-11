import { useCallback, useEffect, useMemo, useState } from "react";
import { api, fmt, qs, type Item, type Order, type OrderIn } from "../api";
import { useAuth } from "../auth";
import { ErrorText } from "../components";
import RevenueAnalysis from "./orders/RevenueAnalysis";

function splitCodes(raw: string): string[] {
  return raw
    .split(/[,;]/)
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
}

function matchesItemCode(raw: string, code: string): boolean {
  const parts = splitCodes(raw);
  if (!parts.length) return true;
  const c = code.toLowerCase();
  return parts.some((p) => c.includes(p));
}

function matchesText(needle: string, hay: string): boolean {
  const n = needle.trim().toLowerCase();
  if (!n) return true;
  return hay.toLowerCase().includes(n);
}

const STATUS_LABEL: Record<string, string> = { open: "Açık", closed: "Kapalı", forecast: "Tahmin" };
const PLAN_LABEL: Record<string, string> = { unplanned: "Planlanmadı", planned: "Planlanan", partial: "Kısmi", late: "Gecikmeli", on_time: "Zamanında", no_ops: "Rota yok", closed: "Kapalı", forecast: "Tahmin" };
const RES_LABEL: Record<string, string> = { none: "Rezerv yok", partial: "Kısmi rezerv", full: "Tam rezerv" };
const MARKET_LABEL: Record<string, string> = { domestic: "Yerli", export: "Yurtdışı" };

type Tab = "orders" | "revenue";

export default function Orders() {
  const { can } = useAuth();
  const [tab, setTab] = useState<Tab>("orders");
  const [status, setStatus] = useState("open");
  const [position, setPosition] = useState("");
  const [customer, setCustomer] = useState("");
  const [orderNo, setOrderNo] = useState("");
  const [itemCode, setItemCode] = useState("");
  const [market, setMarket] = useState("");
  const [planStatus, setPlanStatus] = useState("");
  const [resStatus, setResStatus] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [editing, setEditing] = useState<Order | "new" | null>(null);
  const [msg, setMsg] = useState("");
  const [rows, setRows] = useState<Order[] | null>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const [debouncedItemCode, setDebouncedItemCode] = useState("");

  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedItemCode(itemCode), 300);
    return () => window.clearTimeout(t);
  }, [itemCode]);

  const loadOrders = useCallback(() => {
    const url = `/api/orders${qs({
      status,
      due_from: from || undefined,
      due_to: to || undefined,
      position: position.trim() || undefined,
      customer: customer.trim() || undefined,
      order_no: orderNo.trim() || undefined,
      item_code: debouncedItemCode.trim() || undefined,
      market: market || undefined,
      plan_status: planStatus || undefined,
      reservation_status: resStatus || undefined,
    })}`;
    setLoading(true);
    setErr("");
    return api
      .get<Order[]>(url)
      .then(setRows)
      .catch((e) => {
        setErr((e as Error).message);
        setRows([]);
      })
      .finally(() => setLoading(false));
  }, [status, from, to, position, customer, orderNo, debouncedItemCode, market, planStatus, resStatus]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErr("");
    const url = `/api/orders${qs({
      status,
      due_from: from || undefined,
      due_to: to || undefined,
      position: position.trim() || undefined,
      customer: customer.trim() || undefined,
      order_no: orderNo.trim() || undefined,
      item_code: debouncedItemCode.trim() || undefined,
      market: market || undefined,
      plan_status: planStatus || undefined,
      reservation_status: resStatus || undefined,
    })}`;
    api
      .get<Order[]>(url)
      .then((d) => {
        if (!cancelled) setRows(d);
      })
      .catch((e) => {
        if (!cancelled) {
          setErr((e as Error).message);
          setRows([]);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [status, from, to, position, customer, orderNo, debouncedItemCode, market, planStatus, resStatus]);

  const visibleRows = useMemo(() => {
    const list = rows ?? [];
    return list.filter(
      (o) =>
        matchesItemCode(itemCode, o.item_code) &&
        matchesText(orderNo, o.order_no) &&
        matchesText(position, o.position_no || "") &&
        matchesText(customer, o.customer || ""),
    );
  }, [rows, itemCode, orderNo, position, customer]);

  const afterSave = (o: Order, created: boolean) => {
    setEditing(null);
    setMsg(`${o.order_no}${o.position_no ? ` / poz ${o.position_no}` : ""} / ${o.item_code} ${created ? "eklendi" : "güncellendi"}.`);
    void loadOrders();
  };

  const remove = async (o: Order) => {
    if (!confirm(`${o.order_no} / ${o.item_code} siparişi ve plan satırları silinsin mi?`)) return;
    try {
      await api.del(`/api/orders/${o.id}`);
      setMsg(`${o.order_no} silindi.`);
      void loadOrders();
    } catch (e) { setMsg((e as Error).message); }
  };

  return (
    <>
      <h1>Siparişler</h1>
      <div className="tabs">
        <button type="button" className={`tab ${tab === "orders" ? "active" : ""}`} onClick={() => setTab("orders")}>Sipariş listesi</button>
        <button type="button" className={`tab ${tab === "revenue" ? "active" : ""}`} onClick={() => setTab("revenue")}>Ciro analizi</button>
      </div>

      {tab === "revenue" && <RevenueAnalysis />}

      {tab === "orders" && (
        <>
          <div className="panel row">
            <label>Durum<select value={status} onChange={(e) => setStatus(e.target.value)}><option value="open">Açık</option><option value="forecast">Tahmin</option><option value="closed">Kapalı</option><option value="">Tümü</option></select></label>
            <label>Sipariş no<input value={orderNo} onChange={(e) => setOrderNo(e.target.value)} placeholder="filtre" /></label>
            <label>Poz no<input value={position} onChange={(e) => setPosition(e.target.value)} placeholder="filtre" /></label>
            <label>Stok kodu<input value={itemCode} onChange={(e) => setItemCode(e.target.value)} placeholder="6010527 veya 6010527, 6012081" title="Virgülle birden fazla stok kodu" /></label>
            <label>Müşteri<input value={customer} onChange={(e) => setCustomer(e.target.value)} placeholder="filtre" /></label>
            <label>Pazar<select value={market} onChange={(e) => setMarket(e.target.value)}><option value="">Tümü</option><option value="domestic">Yerli</option><option value="export">Yurtdışı</option></select></label>
            <label>Plan durumu<select value={planStatus} onChange={(e) => setPlanStatus(e.target.value)}><option value="">Tümü</option><option value="planned">Planlanan</option><option value="forecast">Tahmin</option><option value="unplanned">Planlanmadı</option><option value="partial">Kısmi</option><option value="late">Gecikmeli</option><option value="on_time">Zamanında</option><option value="no_ops">Rota yok</option></select></label>
            <label>Rezervasyon<select value={resStatus} onChange={(e) => setResStatus(e.target.value)}><option value="">Tümü</option><option value="none">Rezerv yok</option><option value="partial">Kısmi</option><option value="full">Tam</option></select></label>
            <label>Termin (başlangıç)<input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
            <label>Termin (bitiş)<input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
            {can("poweruser") && <button onClick={() => { setEditing("new"); setMsg(""); }} disabled={editing === "new"}>+ Yeni sipariş</button>}
          </div>
          <ErrorText err={err} />
          {msg && <div className="success" style={{ marginBottom: 8 }}>{msg}</div>}
          {editing && <OrderForm initial={editing === "new" ? null : editing} onSaved={afterSave} onCancel={() => setEditing(null)} />}

          <h2>
            {STATUS_LABEL[status] ?? "Tüm"} siparişler
            {loading && <span className="muted" style={{ fontSize: "0.85em", fontWeight: "normal" }}> · yükleniyor…</span>}
          </h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Sipariş</th><th>Poz</th><th>Müşteri</th><th>Pazar</th>
                  <th>Sipariş tarihi</th><th>Termin</th><th>Revize termin</th><th>Planlanan teslim</th>
                  <th>Stok</th><th className="num">Miktar</th><th className="num">Birim fiyat</th><th className="num">Ciro</th>
                  <th>Plan</th><th>Rezerv</th><th>Not</th><th></th>
                </tr>
              </thead>
              <tbody>
                {visibleRows.map((o) => (
                  <tr key={o.id}>
                    <td>
                      {o.order_no}
                      {o.status === "forecast" && <span className="badge ok" style={{ marginLeft: 6 }}>tahmin</span>}
                      {o.status === "closed" && <span className="badge muted" style={{ marginLeft: 6 }}>kapalı</span>}
                    </td>
                    <td>{o.position_no || <span className="muted">—</span>}</td>
                    <td>{o.customer}</td>
                    <td>{MARKET_LABEL[o.market] ?? o.market}</td>
                    <td>{o.order_date || <span className="muted">—</span>}</td>
                    <td>{o.due_date}</td>
                    <td>{o.revised_due_date || <span className="muted">—</span>}</td>
                    <td>{o.planned_end || <span className="muted">—</span>}</td>
                    <td><b>{o.item_code}</b> <span className="muted">{o.item_name}</span></td>
                    <td className="num">{fmt(o.quantity, 0)}</td>
                    <td className="num" style={{ color: o.unit_price ? undefined : "var(--muted)" }}>{o.unit_price ? fmt(o.unit_price, 2) : "—"}</td>
                    <td className="num">{o.revenue ? fmt(o.revenue, 0) : "—"}</td>
                    <td><span className={`badge ${o.plan_status === "forecast" ? "ok" : o.plan_status === "on_time" ? "ok" : o.plan_status === "late" ? "bad" : o.plan_status === "unplanned" ? "warn" : "muted"}`}>{PLAN_LABEL[o.plan_status] ?? o.plan_status}</span></td>
                    <td><span className={`badge ${o.reservation_status === "full" ? "ok" : o.reservation_status === "none" ? "warn" : "muted"}`}>{RES_LABEL[o.reservation_status] ?? o.reservation_status}</span></td>
                    <td className="muted" title={o.note}>{o.note.length > 30 ? o.note.slice(0, 30) + "…" : o.note}</td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      {can("poweruser") && o.status !== "merged" && o.status !== "forecast" && (
                        <>
                          <button className="secondary small" onClick={() => { setEditing(o); setMsg(""); }}>Düzenle</button>{" "}
                          {o.status === "open"
                            ? <button className="secondary small" onClick={async () => { await api.patch(`/api/orders/${o.id}/status?status=closed`); void loadOrders(); }}>Kapat</button>
                            : <button className="secondary small" onClick={async () => { await api.patch(`/api/orders/${o.id}/status?status=open`); void loadOrders(); }}>Aç</button>}{" "}
                          <button className="danger small" onClick={() => remove(o)}>Sil</button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
                {!loading && visibleRows.length === 0 && <tr><td colSpan={16} className="muted">Sipariş yok.</td></tr>}
              </tbody>
            </table>
          </div>
          <p className="muted">
            {visibleRows.length} sipariş
            {rows && visibleRows.length !== rows.length && (
              <> <span className="muted">({rows.length} kayıttan filtrelendi)</span></>
            )}
            {" · "}toplam ciro <b>{fmt(visibleRows.reduce((s, o) => s + (o.revenue || 0), 0), 0)}</b>
            {!!visibleRows.some((o) => !o.unit_price) && <> · <span style={{ color: "var(--warn)" }}>{visibleRows.filter((o) => !o.unit_price).length} siparişte birim fiyat yok</span></>}
          </p>
        </>
      )}
    </>
  );
}

function OrderForm({ initial, onSaved, onCancel }: { initial: Order | null; onSaved: (o: Order, created: boolean) => void; onCancel: () => void }) {
  const [form, setForm] = useState<OrderIn>({
    order_no: initial?.order_no ?? "",
    position_no: initial?.position_no ?? "",
    customer: initial?.customer ?? "",
    order_date: initial?.order_date ?? "",
    due_date: initial?.due_date ?? "",
    revised_due_date: initial?.revised_due_date ?? "",
    market: initial?.market ?? "domestic",
    item_code: initial?.item_code ?? "",
    quantity: initial?.quantity ?? 0,
    unit_price: initial?.unit_price ?? 0,
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
  const set = (k: keyof OrderIn, v: string | number | null) => setForm((f) => ({ ...f, [k]: v }));
  const valid = form.order_no.trim() && form.item_code.trim() && form.due_date && form.quantity > 0;
  const submit = async () => {
    setErr(""); setBusy(true);
    try {
      const body: OrderIn = {
        ...form,
        order_no: form.order_no.trim(),
        position_no: form.position_no.trim(),
        item_code: form.item_code.trim(),
        order_date: form.order_date || null,
        revised_due_date: form.revised_due_date || null,
        market: form.market || "domestic",
      };
      const o = initial ? await api.put<Order>(`/api/orders/${initial.id}`, body) : await api.post<Order>("/api/orders", body);
      onSaved(o, !initial);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <div className="panel" style={{ borderLeft: "4px solid var(--primary)" }}>
      <h2 style={{ marginTop: 0 }}>{initial ? `Siparişi düzenle — ${initial.order_no}` : "Yeni sipariş"}</h2>
      <div className="row" style={{ alignItems: "flex-end" }}>
        <label>Sipariş no *<input value={form.order_no} onChange={(e) => set("order_no", e.target.value)} placeholder="örn. SP-2026-001" autoFocus /></label>
        <label title="Aynı sipariş numarasında birden fazla satır için pozisyon no">Poz no<input value={form.position_no} onChange={(e) => set("position_no", e.target.value)} placeholder="örn. 10" /></label>
        <label>Müşteri<input value={form.customer} onChange={(e) => set("customer", e.target.value)} /></label>
        <label>Pazar<select value={form.market ?? "domestic"} onChange={(e) => set("market", e.target.value)}><option value="domestic">Yerli</option><option value="export">Yurtdışı</option></select></label>
        <label>
          Stok kodu *
          <input list="order-item-codes" value={form.item_code} onChange={(e) => { set("item_code", e.target.value); setQ(e.target.value); }} placeholder="Kod yazın / listeden seçin" />
          <datalist id="order-item-codes">{items.map((i) => <option key={i.id} value={i.code}>{i.name}</option>)}</datalist>
        </label>
        <label>Miktar *<input type="number" min={0} step="1" value={form.quantity || ""} onChange={(e) => set("quantity", Number(e.target.value))} /></label>
        <label title="Birim satış fiyatı; ciro = miktar × birim fiyat">Birim fiyat<input type="number" min={0} step="0.01" value={form.unit_price || ""} onChange={(e) => set("unit_price", Number(e.target.value))} placeholder="0" /></label>
        <label>Ciro<input value={form.quantity && form.unit_price ? fmt(form.quantity * form.unit_price, 2) : "—"} readOnly style={{ background: "#f5f5f5", width: 110 }} /></label>
        <label>Sipariş tarihi<input type="date" value={form.order_date || ""} onChange={(e) => set("order_date", e.target.value || null)} /></label>
        <label>Termin *<input type="date" value={form.due_date} onChange={(e) => set("due_date", e.target.value)} /></label>
        <label title="Dolu ise planlama bu tarihi kullanır">Revize termin<input type="date" value={form.revised_due_date || ""} onChange={(e) => set("revised_due_date", e.target.value || null)} /></label>
        <label style={{ minWidth: 220 }}>Not<input value={form.note} onChange={(e) => set("note", e.target.value)} /></label>
        <button onClick={submit} disabled={!valid || busy}>{initial ? "Kaydet" : "Ekle"}</button>
        <button className="secondary" onClick={onCancel}>Vazgeç</button>
      </div>
      <div className="muted" style={{ marginTop: 6 }}>
        {picked ? <>Seçilen stok: <b>{picked.code}</b> — {picked.name} {picked.product_group && <>· {picked.product_group}</>}</> : form.item_code.trim() ? "Bu kod stok listesinde bulunamadı; önce Stok / BOM / Rota ekranından tanımlanmalı." : "Stok kodu, rota tanımlı bir kayıt olmalı."}
        {initial && " · Stok kodu değiştirilirse mevcut plan satırları silinir."}
        {" · Revize termin boş bırakılırsa planlama ilk termin tarihini kullanır."}
      </div>
      <ErrorText err={err} />
    </div>
  );
}
