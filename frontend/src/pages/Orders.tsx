import { useState } from "react";
import { api, fmt, qs, type Order } from "../api";
import { useAuth } from "../auth";
import { ErrorText, WcMultiSelect, useAsync, useWorkCenters } from "../components";

interface ReqLine { work_center_id: number; work_center_code: string; item_code: string; operation_seq: number; operation_name: string; quantity: number; hours: number }
interface ReqOut { lines: ReqLine[]; by_work_center: { work_center_id: number; work_center_code: string; hours: number }[]; total_hours: number }

export default function Orders() {
  const { can } = useAuth();
  const { wcs } = useWorkCenters();
  const [status, setStatus] = useState("open");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [wcIds, setWcIds] = useState<number[]>([]);
  const [selCodes, setSelCodes] = useState<string[]>([]);
  const orders = useAsync(() => api.get<Order[]>(`/api/orders${qs({ status, due_from: from, due_to: to })}`), [status, from, to]);
  const req = useAsync(
    () => api.post<ReqOut>("/api/requirements", { work_center_ids: wcIds.length ? wcIds : null, item_codes: selCodes.length ? selCodes : null, due_from: from || null, due_to: to || null }),
    [wcIds.join(","), selCodes.join(","), from, to, orders.data?.length]
  );
  const toggleCode = (c: string) => setSelCodes((s) => (s.includes(c) ? s.filter((x) => x !== c) : [...s, c]));
  const unitOf = (wcId: number) => wcs.find((w) => w.id === wcId)?.capacity_unit_hours ?? 10;

  return (
    <>
      <h1>Siparişler & İş Gücü İhtiyacı</h1>
      <div className="panel row">
        <label>Durum<select value={status} onChange={(e) => setStatus(e.target.value)}><option value="open">Açık</option><option value="closed">Kapalı</option><option value="">Tümü</option></select></label>
        <label>Termin (başlangıç)<input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        <label>Termin (bitiş)<input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
        <WcMultiSelect wcs={wcs} value={wcIds} onChange={setWcIds} />
        {selCodes.length > 0 && <button className="secondary" onClick={() => setSelCodes([])}>Stok seçimini temizle ({selCodes.length})</button>}
      </div>
      <ErrorText err={orders.err || req.err} />

      <div style={{ display: "grid", gridTemplateColumns: "1.3fr 1fr", gap: 14 }}>
        <div>
          <h2>Açık siparişler <span className="muted">(satıra tıklayarak ihtiyaç hesabını seçili stoklara daraltın)</span></h2>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Sipariş</th><th>Müşteri</th><th>Termin</th><th>Stok</th><th className="num">Miktar</th><th></th></tr></thead>
              <tbody>
                {orders.data?.map((o) => (
                  <tr key={o.id} onClick={() => toggleCode(o.item_code)} style={{ cursor: "pointer", background: selCodes.includes(o.item_code) ? "#e3f2fd" : undefined }}>
                    <td>{o.order_no}</td><td>{o.customer}</td><td>{o.due_date}</td><td><b>{o.item_code}</b></td><td className="num">{fmt(o.quantity, 0)}</td>
                    <td onClick={(e) => e.stopPropagation()}>
                      {can("poweruser") && (o.status === "open"
                        ? <button className="secondary small" onClick={async () => { await api.patch(`/api/orders/${o.id}/status?status=closed`); orders.reload(); }}>Kapat</button>
                        : <button className="secondary small" onClick={async () => { await api.patch(`/api/orders/${o.id}/status?status=open`); orders.reload(); }}>Aç</button>)}
                    </td>
                  </tr>
                ))}
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
