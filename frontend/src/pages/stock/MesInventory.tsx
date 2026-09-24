import { useState } from "react";
import { api, fmt } from "../../api";
import { ErrorText, useAsync } from "../../components";

type Balance = { material_code: string; opening_qty: number; produced_qty: number; consumed_qty: number; balance: number; pending_consumption_qty: number; available_qty: number };
type Movement = { detail_id: string; day: string; material_code: string; output_code: string; kind: string; quantity: number; balance_after: number; machine_code: string; operation_name?: string; standard_hours: number };
type Pending = { detail_id: string; output_code: string; day: string; quantity: number; reason: string; input_candidates: Record<string,number>[] };
type Report = { rows: Balance[]; movements: Movement[]; pending: Pending[]; notes: string[] };

export default function MesInventory() {
  const [day, setDay] = useState(new Date().toLocaleDateString("en-CA"));
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState("");
  const report = useAsync(() => api.get<Report>(`/api/mes/inventory?as_of=${day}`), [day]);
  const data = report.data;
  const rows = (data?.rows ?? []).filter(r => r.material_code.includes(search.trim()));
  const moves = (data?.movements ?? []).filter(r => selected ? r.material_code === selected : r.material_code.includes(search.trim()));
  return <section className="panel">
    <h2>Kümülatif yarımamul stoğu ve hareketleri</h2>
    <p>Açılış stoğu: <b>0 — kullanıcı onayıyla</b>. Girişler ve tüketimler tarihe kadar birikir; hafta değişince sıfırlanmaz. Satıra tıklayarak hareketlerini görebilirsin.</p>
    <div className="row"><label>Tarih dahil<input type="date" value={day} onChange={e => e.target.value && setDay(e.target.value)} /></label><label>Malzeme kodu<input value={search} onChange={e => {setSearch(e.target.value);setSelected("");}} /></label><button onClick={() => report.reload()}>Yenile</button><button onClick={() => {setError(""); api.download(`/api/mes/inventory.xlsx?as_of=${day}`).catch(e => setError(e instanceof Error ? e.message : String(e)));}}>Excel</button></div>
    <ErrorText err={report.err || error} />{report.loading && <p>Hareketler hesaplanıyor…</p>}
    <div className="table-wrap"><table><thead><tr><th>Malzeme</th><th>Üretim girişi</th><th>Bilinen tüketim</th><th>Kalan stok</th><th>Belirsiz tüketim için ayrılan</th><th>Planlamada kullanılabilir</th></tr></thead><tbody>{rows.map(r => <tr key={r.material_code} onClick={() => setSelected(r.material_code)} style={{cursor:"pointer",background:selected===r.material_code?"#e3f2fd":undefined}}><td>{r.material_code}</td><td>{fmt(r.produced_qty)}</td><td>{fmt(r.consumed_qty)}</td><td style={{color:r.balance<0?"#b45309":undefined}}>{fmt(r.balance)}</td><td>{fmt(r.pending_consumption_qty)}</td><td>{fmt(r.available_qty)}</td></tr>)}{!rows.length && <tr><td colSpan={6}>Bu tarih ve seçimde MES stok hareketi yok.</td></tr>}</tbody></table></div>
    <p className="muted">Belirsiz tüketim stoktan düşülmez. Olası önceki aşamalar planlamada yeniden kullanıma ayrılmaz. Negatif bakiye eksik giriş veya tüketim tanımı kontrolü gerektirir.</p>
    <h3>{selected || "Seçimdeki tüm kodlar"} — stok hareketleri</h3>{selected && <button onClick={() => setSelected("")}>Tümünü göster</button>}
    <div className="table-wrap" style={{maxHeight:420,overflow:"auto"}}><table><thead><tr><th>Gün</th><th>MES detay ID</th><th>Malzeme</th><th>Hareket</th><th>Miktar</th><th>Bakiye</th><th>Üretilen kod</th><th>Makine / operasyon</th></tr></thead><tbody>{moves.map((m,i) => <tr key={`${m.detail_id}-${m.material_code}-${m.kind}-${i}`}><td>{m.day}</td><td>{m.detail_id}</td><td>{m.material_code}</td><td>{m.kind==="production"?"Üretim girişi":"Üretimde tüketim"}</td><td>{fmt(m.quantity)}</td><td>{fmt(m.balance_after)}</td><td>{m.output_code}</td><td>{m.machine_code} / {m.operation_name || "Tanım bekliyor"}</td></tr>)}</tbody></table></div>
    <p className="muted">Aynı günün girişleri önce listelenir; bu sıralama gün içi saat sırası değildir. İşçilik üretim beyanında bir kez sayılır; tüketim hareketi ikinci kez işçilik yazmaz.</p>
    {!!data?.pending.length && <details><summary>Tüketimi uzlaştırılacak {data.pending.length} üretim kaydı</summary>{data.pending.map(p => <p key={p.detail_id}><b>{p.output_code} · {p.day} · {p.quantity} adet · MES {p.detail_id}</b><br />{p.reason}<br />{p.input_candidates.map(c => Object.entries(c).map(([code,q])=>`${code}: ${q} adet/adet`).join(", ")).join(" veya ") || "Önceki aşama henüz belirlenemiyor."}</p>)}</details>}
  </section>;
}
