import { useEffect, useState } from "react";
import { api, fmt, type ErpExcelPreview, type ErpExcelResult } from "./api";

function qs(params: Record<string, string | boolean | string[]>) {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (Array.isArray(v)) v.forEach((x) => u.append(k, x));
    else u.set(k, String(v));
  }
  return u.toString();
}

/** ERP Excel ("Sipariş Ana Veri" + "Depo - Ana Veri") ön izleme ve uygulama penceresi. */
export default function ErpExcelModal({ file, target, onClose, onApplied }: { file: File; target: "orders" | "stock"; onClose: () => void; onApplied: () => void }) {
  const [doOrders, setDoOrders] = useState(target === "orders");
  const [doStock, setDoStock] = useState(target === "stock");
  const [closeMissing, setCloseMissing] = useState(true);
  const [zeroMissing, setZeroMissing] = useState(true);
  const [warehouses, setWarehouses] = useState<string[]>(["69"]);
  const [preview, setPreview] = useState<ErpExcelPreview | null>(null);
  const [result, setResult] = useState<ErpExcelResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    let alive = true;
    setBusy(true); setErr("");
    api.uploadLong<ErpExcelPreview>(`/api/imports/erp-excel/preview?${qs({ warehouses, close_missing: closeMissing, zero_missing: zeroMissing })}`, file)
      .then((p) => { if (alive) setPreview(p); })
      .catch((e) => { if (alive) setErr((e as Error).message); })
      .finally(() => { if (alive) setBusy(false); });
    return () => { alive = false; };
  }, [file, warehouses, closeMissing, zeroMissing]);

  const apply = async () => {
    setBusy(true); setErr("");
    try {
      const r = await api.uploadLong<ErpExcelResult>(`/api/imports/erp-excel/apply?${qs({ do_orders: doOrders, do_stock: doStock, warehouses, close_missing: closeMissing, zero_missing: zeroMissing })}`, file);
      setResult(r);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };

  const o = preview?.orders; const s = preview?.stock;
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="panel modal" style={{ width: 760, maxWidth: "95vw" }} onClick={(e) => e.stopPropagation()}>
        <h3 style={{ margin: "0 0 6px" }}>ERP Excel'den güncelle</h3>
        <p className="muted" style={{ margin: "0 0 10px" }}>{file.name} · Sipariş: MIKTAR kalan açık miktar, INCKEYNO poz no, REZERV siparişe ayrılmış mamul (ihtiyaçtan düşülür). Depo: seçili depoların BAKIYE toplamı ürün stoğu olur.</p>
        {err && <div className="error">{err}</div>}
        {preview?.missing_sheets.length ? <div className="error">Eksik sayfa: {preview.missing_sheets.join(", ")}</div> : null}
        {result ? (
          <div>
            <div className="success">Eşitleme tamamlandı.</div>
            {result.orders && <p>Siparişler: {result.orders.inserted} yeni, {result.orders.updated} güncellendi, {result.orders.closed} kapatıldı, {result.orders.reserved} ERP rezervi yazıldı. Reddedilen {result.orders.error_count}, atlanan {result.orders.skipped_count}.</p>}
            {result.orders && result.orders.errors.length > 0 && <ul className="errors" style={{ fontSize: 12 }}>{result.orders.errors.slice(0, 30).map((e, i) => <li key={i}>{e}</li>)}</ul>}
            {result.stock && <p>Depo ({result.stock.warehouses}): {result.stock.adjustments} üründe düzeltme, +{fmt(result.stock.increase)} / −{fmt(result.stock.decrease)} adet. Programda olmayan stok kodu: {result.stock.unknown_item_count}.</p>}
            <button type="button" onClick={onApplied}>Kapat</button>
          </div>
        ) : (
          <>
            <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 10 }}>
              <label style={{ display: "flex", gap: 6, alignItems: "center" }}><input type="checkbox" checked={doOrders} onChange={(e) => setDoOrders(e.target.checked)} /> Açık siparişleri güncelle</label>
              <label style={{ display: "flex", gap: 6, alignItems: "center" }}><input type="checkbox" checked={doStock} onChange={(e) => setDoStock(e.target.checked)} /> Bitmiş ürün stoğunu güncelle</label>
              <label style={{ display: "flex", gap: 6, alignItems: "center" }} title="Dosyada olmayan açık siparişler kapatılır (silinmez; plan geçmişi kalır)"><input type="checkbox" checked={closeMissing} onChange={(e) => setCloseMissing(e.target.checked)} /> Listede olmayan siparişi kapat</label>
              <label style={{ display: "flex", gap: 6, alignItems: "center" }} title="Seçili depolarda görünmeyen ürünün stoğu 0'a çekilir"><input type="checkbox" checked={zeroMissing} onChange={(e) => setZeroMissing(e.target.checked)} /> Dosyada olmayan ürün stoğu 0</label>
            </div>
            {s && (
              <div style={{ marginBottom: 10 }}>
                <span className="muted">Bitmiş ürün depoları: </span>
                {s.warehouses.map((w) => (
                  <label key={w.code} style={{ marginRight: 12 }}>
                    <input type="checkbox" checked={warehouses.includes(w.code)} onChange={(e) => setWarehouses(e.target.checked ? [...warehouses, w.code] : warehouses.filter((x) => x !== w.code))} /> {w.code} <span className="muted">({w.rows} satır)</span>
                  </label>
                ))}
              </div>
            )}
            {busy && !preview && <p className="muted">Dosya okunuyor…</p>}
            {o && (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <div className="table-wrap">
                  <table>
                    <thead><tr><th colSpan={2}>Siparişler</th></tr></thead>
                    <tbody>
                      <tr><td>Dosya satırı</td><td>{o.file_rows} (geçerli {o.valid_rows}, atlanan {o.skipped_count})</td></tr>
                      <tr><td>Yeni sipariş</td><td>{o.new}</td></tr>
                      <tr><td>Güncellenecek</td><td>{o.existing}</td></tr>
                      <tr><td>Kapatılacak (dosyada yok)</td><td style={{ color: o.would_close_count ? "var(--warn)" : undefined }}>{closeMissing ? o.would_close_count : `0 (kapatma kapalı; ${o.would_close_count} aday)`}</td></tr>
                      <tr><td>Programda olmayan stok kodu</td><td style={{ color: o.unknown_item_count ? "var(--bad)" : undefined }}>{o.unknown_item_count}</td></tr>
                      <tr><td>Rotası eksik ürün (reddedilir)</td><td style={{ color: o.no_routing_count ? "var(--bad)" : undefined }}>{o.no_routing_count}</td></tr>
                    </tbody>
                  </table>
                  {(o.no_routing.length > 0 || o.unknown_items.length > 0) && <ul className="errors" style={{ fontSize: 12, maxHeight: 140, overflow: "auto" }}>{o.no_routing.map((x, i) => <li key={"r" + i}>{x}</li>)}{o.unknown_items.map((x, i) => <li key={"u" + i}>Stok kartı yok: {x}</li>)}</ul>}
                </div>
                <div className="table-wrap">
                  <table>
                    <thead><tr><th colSpan={2}>Bitmiş ürün stoğu</th></tr></thead>
                    <tbody>
                      <tr><td>Dosya satırı</td><td>{s!.file_rows}</td></tr>
                      <tr><td>Seçili depoda ürün</td><td>{s!.items_in_file}</td></tr>
                      <tr><td>Düzeltilecek ürün</td><td>{s!.adjustments} (+{fmt(s!.increase)} / −{fmt(s!.decrease)})</td></tr>
                      <tr><td>Programda olmayan stok kodu</td><td>{s!.unknown_item_count}</td></tr>
                    </tbody>
                  </table>
                  {s!.examples.length > 0 && <ul className="muted" style={{ fontSize: 12, maxHeight: 140, overflow: "auto", paddingLeft: 18 }}>{s!.examples.map((x, i) => <li key={i}>{x}</li>)}</ul>}
                </div>
              </div>
            )}
            <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
              <button type="button" disabled={busy || !preview || (!doOrders && !doStock)} onClick={() => void apply()}>{busy ? "Çalışıyor…" : "Uygula"}</button>
              {busy && preview && <span className="muted">Sunucuda işleniyor; büyük dosyada 1-2 dakika sürebilir, pencereyi kapatmayın.</span>}
              <button type="button" className="secondary" onClick={onClose}>Vazgeç</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
