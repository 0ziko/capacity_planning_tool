import { useEffect, useRef, useState } from "react";
import { api, fmt, type AutoReserveResult, type OrderStockRow } from "../../api";
import { ErrorText } from "../../components";

type PreviewLine = OrderStockRow & { allocate: number; remaining_after: number; free_before: number; free_after: number };
type Preview = { preview_token: string; rows: PreviewLine[]; reserved_qty: number; items: number };

export default function AutoReserveDialog({ itemIds, onClose, onDone }: {
  itemIds: number[] | null; onClose: () => void; onDone: (result: AutoReserveResult) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const inFlight = useRef(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => { dialog.current?.showModal(); }, []);
  useEffect(() => {
    let active = true;
    setLoading(true); setPreview(null); setErr("");
    api.post<Preview>("/api/stock/reservations/auto/preview", { item_ids: itemIds })
      .then((data) => { if (active) setPreview(data); })
      .catch((e: Error) => { if (active) setErr(e.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [itemIds, revision]);
  const confirm = async () => {
    if (!preview?.rows.length || inFlight.current) return;
    inFlight.current = true; setSaving(true); setErr("");
    try {
      const result = await api.post<AutoReserveResult>("/api/stock/reservations/auto", { preview_token: preview.preview_token });
      onDone(result);
    } catch (e) {
      setErr((e as Error).message); setPreview(null);
    } finally { inFlight.current = false; setSaving(false); }
  };
  return <dialog ref={dialog} className="stock-auto-dialog" aria-labelledby="auto-reserve-title" onCancel={(e) => {
    e.preventDefault(); if (!inFlight.current) onClose();
  }}>
    <h2 id="auto-reserve-title">Otomatik rezervasyon önizlemesi</h2>
    <p>{itemIds ? "Seçilen ürünün" : "Tüm ürünlerin"} serbest stoğu, en erken terminli açık siparişten başlayarak dağıtılır. Mevcut rezervasyonlar korunur.</p>
    {!itemIds && <p className="muted">Kapsam: tüm açık siparişler. Ekrandaki poz filtresi bu işleme uygulanmaz.</p>}
    <ErrorText err={err} />
    {loading && <p role="status">Stok ve siparişler kontrol ediliyor…</p>}
    {preview && <>
      <div className="panel"><b>{preview.rows.length}</b> sipariş satırına, <b>{preview.items}</b> üründen toplam <b>{fmt(preview.reserved_qty, 3)}</b> adet ayrılacak. Henüz rezervasyon yapılmadı.</div>
      {preview.rows.length ? <div className="table-wrap" style={{ maxHeight: "50vh", overflow: "auto" }}>
        <table>
          <thead><tr><th>Stok kodu / adı</th><th>Sipariş / Poz</th><th>Müşteri</th><th>Termin</th><th className="num">Sipariş</th><th className="num">Sevk</th><th className="num">Mevcut rezerve</th><th className="num">İhtiyaç</th><th className="num">Ayrılacak</th><th className="num">Sonraki kalan</th><th className="num">Serbest stok önce → sonra</th></tr></thead>
          <tbody>{preview.rows.map((r) => <tr key={r.order_id}>
            <td><b>{r.item_code}</b><br /><span className="muted">{r.item_name}</span></td>
            <td>{r.order_no}<br /><span className="muted">Poz: {r.position_no || "—"}</span></td>
            <td>{r.customer}</td><td style={{ whiteSpace: "nowrap" }}>{r.due_date}</td>
            <td className="num">{fmt(r.quantity, 3)}</td><td className="num">{fmt(r.shipped, 3)}</td>
            <td className="num">{fmt(r.reserved, 3)}</td><td className="num">{fmt(r.remaining, 3)}</td>
            <td className="num" style={{ color: "var(--ok)", fontWeight: 700 }}>{fmt(r.allocate, 3)}</td>
            <td className="num">{fmt(r.remaining_after, 3)}</td>
            <td className="num">{fmt(r.free_before, 3)} → {fmt(r.free_after, 3)}</td>
          </tr>)}</tbody>
        </table>
      </div> : <p>Dağıtılabilecek serbest stok veya karşılanmamış açık sipariş bulunmuyor.</p>}
    </>}
    <div className="row" style={{ marginTop: 16, justifyContent: "flex-end" }}>
      <button className="secondary" onClick={onClose} disabled={saving} autoFocus>Vazgeç</button>
      <button className="secondary" onClick={() => setRevision((r) => r + 1)} disabled={loading || saving}>Önizlemeyi yenile</button>
      <button onClick={confirm} disabled={loading || saving || !preview?.rows.length}>{saving ? "Rezervasyonlar kaydediliyor…" : "Onayla ve rezerve et"}</button>
    </div>
  </dialog>;
}
