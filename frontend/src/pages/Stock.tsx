import { useState } from "react";
import { api, fmt, qs, type AutoReserveResult, type Item, type OrderStockRow, type Receipt, type Reservation, type Shipment, type StockRow } from "../api";
import { useAuth } from "../auth";
import { ErrorText, useAsync } from "../components";

type Tab = "free" | "reservations" | "orders" | "receipts" | "shipments";
const TABS: { id: Tab; label: string; hint: string }[] = [
  { id: "free", label: "Serbest stok", hint: "Depodaki bitmiş ürün: eldeki, rezerve, serbest ve açık talep" },
  { id: "reservations", label: "Rezervasyonlar", hint: "Siparişlere ayrılmış ürünler — sevk et, taşı, kaldır" },
  { id: "orders", label: "Sipariş karşılama", hint: "Açık siparişlerin rezerve / sevk / kalan durumu" },
  { id: "receipts", label: "Depo girişi", hint: "Üretimden depoya alınan bitmiş ürün kayıtları" },
  { id: "shipments", label: "Sevkler", hint: "Sevk edilen (stoktan düşen) miktarlar" },
];

/**
 * Stok & Rezervasyon: sipariş, plana termin verir; üretim siparişten bağımsız ilerler ve depoya girer.
 * Depodaki ürün siparişe REZERVE edilir (manuel öncelikli, kalanı termin sırasıyla otomatik) ve sevkle stoktan düşer.
 */
export default function Stock() {
  const { can } = useAuth();
  const canEdit = can("poweruser");
  const [tab, setTab] = useState<Tab>("free");
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const summary = useAsync(() => api.get<StockRow[]>("/api/stock/summary"), []);
  const reservations = useAsync(() => api.get<Reservation[]>("/api/stock/reservations"), []);
  const orders = useAsync(() => api.get<OrderStockRow[]>("/api/stock/orders"), []);
  const refresh = () => { summary.reload(); reservations.reload(); orders.reload(); };

  const run = async (fn: () => Promise<unknown>, okMsg?: string) => {
    setErr(""); setMsg("");
    try {
      const r = await fn();
      if (okMsg) setMsg(okMsg);
      else if (r && typeof r === "object" && "message" in (r as object)) setMsg(String((r as AutoReserveResult).message));
      refresh();
      return true;
    } catch (e) { setErr((e as Error).message); return false; }
  };
  const autoAll = () => run(() => api.post<AutoReserveResult>("/api/stock/reservations/auto", {}));
  const autoItem = (id: number) => run(() => api.post<AutoReserveResult>("/api/stock/reservations/auto", { item_ids: [id] }));

  const rows = summary.data ?? [];
  const totFree = rows.reduce((s, r) => s + r.free, 0);
  const totRes = rows.reduce((s, r) => s + r.reserved, 0);
  const totDemand = rows.reduce((s, r) => s + r.open_demand, 0);
  const manualCount = (reservations.data ?? []).filter((r) => r.source === "manual").length;

  return (
    <>
      <h1>Stok & Rezervasyon</h1>
      <p className="muted" style={{ marginTop: -6 }}>
        Sipariş plana <b>termini</b> verir; üretim siparişten bağımsız ilerler. Son operasyon (ör. paketleme) üretim beyanı otomatik olarak <b>depoya girer</b>;
        ek olarak manuel depo girişi/çıkışı yapabilirsiniz. Depodaki ürün siparişe <b>rezerve</b> edilir:
        önce takip etmek istediğiniz kritik siparişleri <b>manuel</b> rezerve edin, kalanı için <b>otomatik rezervasyon</b> serbest stoğu termin sırasıyla dağıtır.
        Manuel rezervasyon önceliklidir (yer açmak için otomatik rezervasyonlar çözülür, manuel olanlara dokunulmaz). Sevk ile stoktan düşer; sipariş tamamen sevk edilince kapanır.
      </p>

      <div className="panel row" style={{ alignItems: "center" }}>
        <span><b>{fmt(totFree, 0)}</b> serbest · <b>{fmt(totRes, 0)}</b> rezerve ({manualCount} manuel) · <b>{fmt(totDemand, 0)}</b> açık talep · {rows.filter((r) => r.free > 0).length} üründe serbest stok var</span>
        {canEdit && <button onClick={autoAll} disabled={totFree <= 0} title="Tüm ürünlerde serbest stoğu açık siparişlere termin sırasıyla rezerve et">⚡ Tümünü otomatik rezerve et</button>}
        {canEdit && <ReceiptForm onAdded={() => run(async () => null, "Depo girişi kaydedildi.")} onError={setErr} />}
      </div>
      <ErrorText err={err || summary.err || reservations.err || orders.err} />
      {msg && <p style={{ color: "var(--ok)" }}>{msg}</p>}

      <div className="tabs">
        {TABS.map((t) => (
          <button key={t.id} className={`tab ${tab === t.id ? "active" : ""}`} onClick={() => setTab(t.id)} title={t.hint}>
            {t.label}
            {t.id === "reservations" && reservations.data && <span className="badge muted" style={{ marginLeft: 6 }}>{reservations.data.length}</span>}
            {t.id === "free" && <span className="badge ok" style={{ marginLeft: 6 }}>{rows.filter((r) => r.free > 0).length}</span>}
          </button>
        ))}
      </div>

      {tab === "free" && <FreeStock rows={rows} orders={orders.data ?? []} canEdit={canEdit} onAuto={autoItem} onReserve={(orderId, itemId, qty, note) => run(() => api.post("/api/stock/reservations", { item_id: itemId, order_id: orderId, quantity: qty, note }), "Rezervasyon yapıldı.")} />}
      {tab === "reservations" && <Reservations rows={reservations.data ?? []} orders={orders.data ?? []} canEdit={canEdit}
        onShip={(id, qty, date, note) => run(() => api.post(`/api/stock/reservations/${id}/ship`, { quantity: qty, ship_date: date, note }), "Sevk kaydedildi; stoktan düşüldü.")}
        onRelease={(id) => run(() => api.del(`/api/stock/reservations/${id}`), "Rezervasyon kaldırıldı (serbest stoğa döndü).")}
        onMove={(id, orderId) => run(() => api.patch(`/api/stock/reservations/${id}/move${qs({ order_id: orderId })}`), "Rezervasyon taşındı.")} />}
      {tab === "orders" && <OrdersTab rows={orders.data ?? []} summary={rows} canEdit={canEdit} onReserve={(orderId, itemId, qty) => run(() => api.post("/api/stock/reservations", { item_id: itemId, order_id: orderId, quantity: qty }), "Rezervasyon yapıldı.")} />}
      {tab === "receipts" && <Receipts canEdit={canEdit} onChanged={refresh} />}
      {tab === "shipments" && <Shipments canEdit={canEdit} onChanged={refresh} />}
    </>
  );
}

// ---------------- Serbest stok ----------------
function FreeStock({ rows, orders, canEdit, onAuto, onReserve }: { rows: StockRow[]; orders: OrderStockRow[]; canEdit: boolean; onAuto: (itemId: number) => void; onReserve: (orderId: number, itemId: number, qty: number, note: string) => Promise<boolean> }) {
  const [pick, setPick] = useState<StockRow | null>(null);
  const [onlyFree, setOnlyFree] = useState(true);
  const shown = rows.filter((r) => !onlyFree || r.free > 0 || r.on_hand > 0);
  return (
    <>
      <div className="row" style={{ margin: "8px 0" }}>
        <label style={{ flexDirection: "row", alignItems: "center", gap: 6 }}><input type="checkbox" checked={onlyFree} onChange={(e) => setOnlyFree(e.target.checked)} /> Yalnızca stoğu olan ürünler</label>
        <span className="muted">Serbest = eldeki − rezerve. Açık talep = açık siparişlerin henüz rezerve/sevk edilmemiş miktarı.</span>
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Stok</th><th>Ad</th><th>Grup</th><th className="num">Eldeki</th><th className="num">Rezerve</th><th className="num">Serbest</th><th className="num">Sevk edilen</th><th className="num">Açık talep</th><th className="num">Açık sipariş</th><th></th></tr></thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.item_id} style={{ background: pick?.item_id === r.item_id ? "#eef2ff" : undefined }}>
                <td><b>{r.item_code}</b></td><td>{r.item_name}</td><td className="muted">{r.product_group}</td>
                <td className="num">{fmt(r.on_hand, 0)}</td><td className="num">{fmt(r.reserved, 0)}</td>
                <td className="num" style={{ fontWeight: 700, color: r.free > 0 ? "var(--ok)" : undefined }}>{fmt(r.free, 0)}</td>
                <td className="num muted">{fmt(r.shipped, 0)}</td>
                <td className="num" style={{ color: r.open_demand > r.free ? "var(--warn)" : undefined }}>{fmt(r.open_demand, 0)}</td>
                <td className="num">{r.open_orders}</td>
                <td style={{ whiteSpace: "nowrap" }}>
                  {canEdit && r.free > 0 && r.open_orders > 0 && (<>
                    <button className="secondary small" onClick={() => setPick(pick?.item_id === r.item_id ? null : r)}>Manuel rezerve</button>{" "}
                    <button className="secondary small" onClick={() => onAuto(r.item_id)} title="Bu ürünün serbest stoğunu termin sırasıyla dağıt">⚡ Otomatik</button>
                  </>)}
                  {r.free > 0 && r.open_orders === 0 && <span className="muted">açık sipariş yok</span>}
                </td>
              </tr>
            ))}
            {shown.length === 0 && <tr><td colSpan={10} className="muted">Stok kaydı yok. “+ Depo girişi” ile bitmiş ürün girin ya da Excel Import → “Depo Girişi”.</td></tr>}
          </tbody>
        </table>
      </div>
      {pick && <ManualReserve item={pick} orders={orders.filter((o) => o.item_id === pick.item_id && o.remaining > 0)} onReserve={async (oid, q, n) => { const ok = await onReserve(oid, pick.item_id, q, n); if (ok) setPick(null); }} onClose={() => setPick(null)} />}
    </>
  );
}

function ManualReserve({ item, orders, onReserve, onClose }: { item: StockRow; orders: OrderStockRow[]; onReserve: (orderId: number, qty: number, note: string) => void; onClose: () => void }) {
  const [orderId, setOrderId] = useState<number | "">(orders[0]?.order_id ?? "");
  const o = orders.find((x) => x.order_id === orderId);
  const [qty, setQty] = useState<number>(Math.min(item.free, o?.remaining ?? item.free));
  const [note, setNote] = useState("");
  const pickOrder = (id: number) => { setOrderId(id); const oo = orders.find((x) => x.order_id === id); setQty(Math.min(item.free, oo?.remaining ?? item.free)); };
  return (
    <div className="panel" style={{ borderLeft: "4px solid #4f46e5" }}>
      <h2 style={{ marginTop: 0 }}>Manuel rezervasyon — {item.item_code} <span className="muted" style={{ fontWeight: 400 }}>{item.item_name} · serbest {fmt(item.free, 0)}</span></h2>
      <div className="table-wrap">
        <table style={{ width: "auto" }}>
          <thead><tr><th></th><th>Sipariş</th><th>Müşteri</th><th>Termin</th><th className="num">Miktar</th><th className="num">Rezerve</th><th className="num">Sevk</th><th className="num">Kalan</th><th>Plan bitişi</th></tr></thead>
          <tbody>
            {orders.map((x) => (
              <tr key={x.order_id} style={{ cursor: "pointer", background: orderId === x.order_id ? "#eef2ff" : undefined }} onClick={() => pickOrder(x.order_id)}>
                <td><input type="radio" checked={orderId === x.order_id} onChange={() => pickOrder(x.order_id)} /></td>
                <td><b>{x.order_no}</b></td><td>{x.customer}</td><td>{x.due_date}</td>
                <td className="num">{fmt(x.quantity, 0)}</td><td className="num">{fmt(x.reserved, 0)}</td><td className="num">{fmt(x.shipped, 0)}</td>
                <td className="num" style={{ fontWeight: 600 }}>{fmt(x.remaining, 0)}</td>
                <td className="muted">{x.planned_end ?? "-"}</td>
              </tr>
            ))}
            {orders.length === 0 && <tr><td colSpan={9} className="muted">Bu ürün için kalan ihtiyacı olan açık sipariş yok.</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="row" style={{ alignItems: "flex-end", marginTop: 8 }}>
        <label>Miktar<input type="number" min={0} step={1} value={qty} onChange={(e) => setQty(Number(e.target.value))} style={{ width: 110 }} /></label>
        <label>Not<input value={note} onChange={(e) => setNote(e.target.value)} placeholder="örn. kritik müşteri" /></label>
        <button onClick={() => orderId !== "" && onReserve(orderId, qty, note)} disabled={orderId === "" || qty <= 0}>Rezerve et</button>
        <button className="secondary" onClick={onClose}>Kapat</button>
        {o && qty > item.free && <span className="muted" style={{ color: "var(--warn)" }}>Serbest stoktan fazla: eksik {fmt(qty - item.free, 0)} için bu ürünün otomatik rezervasyonları çözülür (manuel olanlar korunur).</span>}
      </div>
    </div>
  );
}

// ---------------- Rezervasyonlar ----------------
function Reservations({ rows, orders, canEdit, onShip, onRelease, onMove }: {
  rows: Reservation[]; orders: OrderStockRow[]; canEdit: boolean;
  onShip: (id: number, qty: number | null, date: string, note: string) => void; onRelease: (id: number) => void; onMove: (id: number, orderId: number) => void;
}) {
  const [ship, setShip] = useState<Reservation | null>(null);
  const [move, setMove] = useState<Reservation | null>(null);
  const [qty, setQty] = useState<string>("");
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10));
  const [note, setNote] = useState("");
  const [target, setTarget] = useState<number | "">("");
  const [filter, setFilter] = useState("");
  const shown = rows.filter((r) => !filter || r.item_code.toLowerCase().includes(filter.toLowerCase()) || r.order_no.toLowerCase().includes(filter.toLowerCase()) || r.customer.toLowerCase().includes(filter.toLowerCase()));
  return (
    <>
      <div className="row" style={{ margin: "8px 0" }}>
        <label>Ara<input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="stok / sipariş / müşteri" /></label>
        <span className="muted">Rezervasyon = depodaki ürünün bir siparişe ayrılması. Sevk edilince stoktan düşer; kaldırılınca serbest stoğa döner.</span>
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Stok</th><th>Sipariş</th><th>Müşteri</th><th>Termin</th><th className="num">Sipariş miktarı</th><th className="num">Rezerve</th><th>Kaynak</th><th>Not</th><th>Oluşturan</th><th></th></tr></thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.id}>
                <td><b>{r.item_code}</b> <span className="muted">{r.item_name}</span></td>
                <td><b>{r.order_no}</b></td><td>{r.customer}</td><td>{r.due_date}</td>
                <td className="num">{fmt(r.order_qty, 0)}</td><td className="num" style={{ fontWeight: 600 }}>{fmt(r.quantity, 0)}</td>
                <td>{r.source === "manual" ? <span className="badge ok">manuel</span> : <span className="badge muted" title="Termin sırasına göre otomatik; manuel rezervasyona yer açmak için çözülebilir">otomatik</span>}</td>
                <td className="muted">{r.note}</td><td className="muted">{r.created_by}</td>
                <td style={{ whiteSpace: "nowrap" }}>
                  {canEdit && (<>
                    <button className="small" onClick={() => { setShip(r); setMove(null); setQty(String(r.quantity)); }}>Sevk</button>{" "}
                    <button className="secondary small" onClick={() => { setMove(r); setShip(null); setTarget(""); }} title="Başka bir siparişe taşı (manuel karar)">Taşı</button>{" "}
                    <button className="danger small" onClick={() => { if (confirm(`${r.order_no} için ${fmt(r.quantity, 0)} adet rezervasyon kaldırılsın mı?`)) onRelease(r.id); }}>Kaldır</button>
                  </>)}
                </td>
              </tr>
            ))}
            {shown.length === 0 && <tr><td colSpan={10} className="muted">Rezervasyon yok. Serbest stok sekmesinden manuel ya da otomatik rezervasyon yapın.</td></tr>}
          </tbody>
        </table>
      </div>
      {ship && (
        <div className="panel row" style={{ alignItems: "flex-end", borderLeft: "4px solid var(--ok)" }}>
          <span><b>Sevk:</b> {ship.item_code} → {ship.order_no} ({ship.customer}) · rezerve {fmt(ship.quantity, 0)}</span>
          <label>Miktar<input type="number" min={0} max={ship.quantity} step={1} value={qty} onChange={(e) => setQty(e.target.value)} style={{ width: 110 }} /></label>
          <label>Sevk tarihi<input type="date" value={date} onChange={(e) => setDate(e.target.value)} /></label>
          <label>Not<input value={note} onChange={(e) => setNote(e.target.value)} placeholder="irsaliye no vb." /></label>
          <button onClick={() => { onShip(ship.id, qty === "" ? null : Number(qty), date, note); setShip(null); }}>Sevk et</button>
          <button className="secondary" onClick={() => setShip(null)}>Vazgeç</button>
        </div>
      )}
      {move && (
        <div className="panel row" style={{ alignItems: "flex-end", borderLeft: "4px solid #4f46e5" }}>
          <span><b>Taşı:</b> {move.item_code} · {fmt(move.quantity, 0)} adet — {move.order_no} ({move.customer}) →</span>
          <label>Hedef sipariş
            <select value={target} onChange={(e) => setTarget(Number(e.target.value))}>
              <option value="">Seçin</option>
              {orders.filter((o) => o.item_id === move.item_id && o.order_id !== move.order_id && o.remaining > 0).map((o) => (
                <option key={o.order_id} value={o.order_id}>{o.order_no} · {o.customer} · termin {o.due_date} · kalan {fmt(o.remaining, 0)}</option>
              ))}
            </select>
          </label>
          <button onClick={() => { if (target !== "") { onMove(move.id, target); setMove(null); } }} disabled={target === ""}>Taşı</button>
          <button className="secondary" onClick={() => setMove(null)}>Vazgeç</button>
          <span className="muted">Taşınan rezervasyon “manuel” olur. Hedef siparişin kalanı yetmezse önce rezervasyonu kaldırıp yeniden bölerek rezerve edin.</span>
        </div>
      )}
    </>
  );
}

// ---------------- Sipariş karşılama ----------------
function OrdersTab({ rows, summary, canEdit, onReserve }: { rows: OrderStockRow[]; summary: StockRow[]; canEdit: boolean; onReserve: (orderId: number, itemId: number, qty: number) => void }) {
  const freeBy = new Map(summary.map((s) => [s.item_id, s.free]));
  const [onlyOpen, setOnlyOpen] = useState(true);
  const shown = rows.filter((r) => !onlyOpen || r.remaining > 0);
  return (
    <>
      <div className="row" style={{ margin: "8px 0" }}>
        <label style={{ flexDirection: "row", alignItems: "center", gap: 6 }}><input type="checkbox" checked={onlyOpen} onChange={(e) => setOnlyOpen(e.target.checked)} /> Yalnızca kalanı olan siparişler</label>
        <span className="muted">Kalan = sipariş − rezerve − sevk. “Serbest” sütunu o ürünün depodaki serbest stoğudur.</span>
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Sipariş</th><th>Müşteri</th><th>Termin</th><th>Stok</th><th className="num">Miktar</th><th className="num">Rezerve</th><th className="num">Sevk</th><th className="num">Kalan</th><th className="num">Serbest stok</th><th>Plan bitişi</th><th>Durum</th><th></th></tr></thead>
          <tbody>
            {shown.map((r) => {
              const free = freeBy.get(r.item_id) ?? 0;
              const late = r.planned_end && r.planned_end > r.due_date;
              return (
                <tr key={r.order_id}>
                  <td><b>{r.order_no}</b></td><td>{r.customer}</td><td>{r.due_date}</td>
                  <td>{r.item_code} <span className="muted">{r.item_name}</span></td>
                  <td className="num">{fmt(r.quantity, 0)}</td><td className="num">{fmt(r.reserved, 0)}</td><td className="num">{fmt(r.shipped, 0)}</td>
                  <td className="num" style={{ fontWeight: 600 }}>{fmt(r.remaining, 0)}</td>
                  <td className="num" style={{ color: free > 0 ? "var(--ok)" : undefined }}>{fmt(free, 0)}</td>
                  <td style={{ color: late ? "var(--bad)" : undefined }}>{r.planned_end ?? "-"}</td>
                  <td>
                    {r.remaining <= 0 ? <span className="badge ok">karşılandı</span>
                      : r.reserved + r.shipped > 0 ? <span className="badge warn">kısmi</span>
                      : free > 0 ? <span className="badge ok" title="Depoda serbest stok var">stoktan verilebilir</span>
                      : <span className="badge muted">üretim bekliyor</span>}
                  </td>
                  <td>{canEdit && r.remaining > 0 && free > 0 && <button className="secondary small" onClick={() => onReserve(r.order_id, r.item_id, Math.min(free, r.remaining))}>Rezerve et ({fmt(Math.min(free, r.remaining), 0)})</button>}</td>
                </tr>
              );
            })}
            {shown.length === 0 && <tr><td colSpan={12} className="muted">Açık sipariş yok.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}

// ---------------- Depo girişi ----------------
function ReceiptForm({ onAdded, onError }: { onAdded: () => void; onError: (m: string) => void }) {
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState("");
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10));
  const [qty, setQty] = useState<number>(0);
  const [lot, setLot] = useState("");
  const [note, setNote] = useState("");
  const items = useAsync(() => (open ? api.get<Item[]>("/api/items") : Promise.resolve([] as Item[])), [open]);
  const submit = async () => {
    onError("");
    try {
      await api.post("/api/stock/receipts", { item_code: code.trim(), receipt_date: date, quantity: qty, lot, note });
      setOpen(false); setCode(""); setQty(0); setLot(""); setNote("");
      onAdded();
    } catch (e) { onError((e as Error).message); }
  };
  if (!open) return <button className="secondary" onClick={() => setOpen(true)}>+ Depo girişi</button>;
  return (
    <div className="row" style={{ alignItems: "flex-end" }}>
      <label>Stok kodu
        <input list="stock-items" value={code} onChange={(e) => setCode(e.target.value)} placeholder="MAM-0001" />
        <datalist id="stock-items">{items.data?.map((i) => <option key={i.id} value={i.code}>{i.name}</option>)}</datalist>
      </label>
      <label>Tarih<input type="date" value={date} onChange={(e) => setDate(e.target.value)} /></label>
      <label>Miktar<input type="number" min={0} step={1} value={qty} onChange={(e) => setQty(Number(e.target.value))} style={{ width: 100 }} /></label>
      <label>Lot / parti<input value={lot} onChange={(e) => setLot(e.target.value)} style={{ width: 110 }} /></label>
      <label>Not<input value={note} onChange={(e) => setNote(e.target.value)} /></label>
      <button onClick={submit} disabled={!code.trim() || qty <= 0}>Kaydet</button>
      <button className="secondary" onClick={() => setOpen(false)}>Vazgeç</button>
    </div>
  );
}

function Receipts({ canEdit, onChanged }: { canEdit: boolean; onChanged: () => void }) {
  const rows = useAsync(() => api.get<Receipt[]>("/api/stock/receipts"), []);
  const [err, setErr] = useState("");
  const remove = async (r: Receipt) => {
    if (!confirm(`${r.item_code} · ${fmt(r.quantity, 0)} adet depo girişi silinsin mi?`)) return;
    setErr("");
    try { await api.del(`/api/stock/receipts/${r.id}`); rows.reload(); onChanged(); } catch (e) { setErr((e as Error).message); }
  };
  return (
    <>
      <p className="muted" style={{ margin: "8px 0" }}>Bitmiş ürün depoya girişi: son operasyon üretim beyanı otomatik kayıt oluşturur (<b>Üretim</b> kaynağı). İsteğe bağlı manuel giriş/Excel de kullanılabilir. Siparişe hangi stok gideceği rezervasyonla belirlenir.</p>
      <ErrorText err={err || rows.err} />
      <div className="table-wrap">
        <table>
          <thead><tr><th>Tarih</th><th>Stok</th><th>Ad</th><th className="num">Miktar</th><th>Lot</th><th>Not</th><th>Kaynak</th><th>Oluşturan</th><th></th></tr></thead>
          <tbody>
            {rows.data?.map((r) => (
              <tr key={r.id}>
                <td>{r.receipt_date}</td><td><b>{r.item_code}</b></td><td>{r.item_name}</td><td className="num">{fmt(r.quantity, 0)}</td>
                <td>{r.lot}</td>                <td className="muted">{r.note}</td><td className="muted">{r.source === "progress" ? "Üretim" : r.source === "import" ? "Excel" : "Manuel"}</td><td className="muted">{r.created_by}</td>
                <td>{canEdit && r.source !== "progress" && <button className="danger small" onClick={() => remove(r)}>Sil</button>}</td>
              </tr>
            ))}
            {rows.data && rows.data.length === 0 && <tr><td colSpan={9} className="muted">Depo girişi yok.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}

// ---------------- Sevkler ----------------
function Shipments({ canEdit, onChanged }: { canEdit: boolean; onChanged: () => void }) {
  const rows = useAsync(() => api.get<Shipment[]>("/api/stock/shipments"), []);
  const [err, setErr] = useState("");
  const undo = async (s: Shipment) => {
    if (!confirm(`${s.order_no} · ${fmt(s.quantity, 0)} adet sevk geri alınsın mı? Stok geri gelir ve aynı siparişe manuel rezervasyon olarak bağlanır.`)) return;
    setErr("");
    try { await api.del(`/api/stock/shipments/${s.id}`); rows.reload(); onChanged(); } catch (e) { setErr((e as Error).message); }
  };
  return (
    <>
      <p className="muted" style={{ margin: "8px 0" }}>Sevk edilen ürün stoktan düşer; sipariş tamamen sevk edildiğinde “kapalı” olur (planlamadan çıkar).</p>
      <ErrorText err={err || rows.err} />
      <div className="table-wrap">
        <table>
          <thead><tr><th>Tarih</th><th>Stok</th><th>Sipariş</th><th>Müşteri</th><th className="num">Miktar</th><th>Not</th><th>Oluşturan</th><th></th></tr></thead>
          <tbody>
            {rows.data?.map((s) => (
              <tr key={s.id}>
                <td>{s.ship_date}</td><td><b>{s.item_code}</b></td><td><b>{s.order_no}</b></td><td>{s.customer}</td><td className="num">{fmt(s.quantity, 0)}</td>
                <td className="muted">{s.note}</td><td className="muted">{s.created_by}</td>
                <td>{canEdit && <button className="secondary small" onClick={() => undo(s)}>Geri al</button>}</td>
              </tr>
            ))}
            {rows.data && rows.data.length === 0 && <tr><td colSpan={8} className="muted">Sevk kaydı yok.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}
