import { useEffect, useState } from "react";
import { api, fmt, qs, type Item, type ItemDetail } from "../api";
import { ErrorText, StringMultiSelect, useAsync, useWorkCenters } from "../components";

interface ItemHours { item_code: string; item_name?: string; quantity: number; total_hours: number; operations: { seq: number; operation_name: string; work_center_code: string; cycle_time_sec: number; setup_time_min: number; hours: number }[] }
interface ItemGroups { main_groups: string[]; sub_groups: string[] }

export default function Items() {
  const { wcs } = useWorkCenters();
  const [q, setQ] = useState("");
  const [mainGroups, setMainGroups] = useState<string[]>([]);
  const [subGroups, setSubGroups] = useState<string[]>([]);
  const [groupOpts, setGroupOpts] = useState<ItemGroups>({ main_groups: [], sub_groups: [] });
  const [sel, setSel] = useState<Item | null>(null);
  const [qty, setQty] = useState(1);

  useEffect(() => {
    api.get<ItemGroups>("/api/items/groups").then(setGroupOpts).catch(() => setGroupOpts({ main_groups: [], sub_groups: [] }));
  }, []);

  const listParams = { q: q || undefined, main_group: mainGroups.length ? mainGroups : undefined, sub_group: subGroups.length ? subGroups : undefined, limit: 300 };
  const list = useAsync(() => api.get<Item[]>(`/api/items${qs(listParams)}`), [q, mainGroups.join(","), subGroups.join(",")]);
  const detail = useAsync(() => (sel ? api.get<ItemDetail>(`/api/items/${sel.id}`) : Promise.resolve(null)), [sel?.id]);
  const hours = useAsync(() => (sel ? api.get<ItemHours>(`/api/requirements/item${qs({ item_code: sel.code, quantity: qty })}`) : Promise.resolve(null)), [sel?.code, qty]);
  const wcCode = (id: number) => wcs.find((w) => w.id === id)?.code ?? id;

  return (
    <>
      <h1>Stok / BOM / Rota</h1>
      <div className="panel row">
        <label>Ara<input value={q} onChange={(e) => setQ(e.target.value)} placeholder="stok kodu, ad, grup" /></label>
        <StringMultiSelect label="Ana Grup" options={groupOpts.main_groups} value={mainGroups} onChange={setMainGroups} />
        <StringMultiSelect label="Alt Grup" options={groupOpts.sub_groups} value={subGroups} onChange={setSubGroups} />
        {(mainGroups.length > 0 || subGroups.length > 0) && (
          <button className="secondary" onClick={() => { setMainGroups([]); setSubGroups([]); }}>Grup filtrelerini temizle</button>
        )}
        <span className="muted">BOM ve rota verileri Excel import ile yüklenir.</span>
      </div>
      <ErrorText err={list.err || detail.err} />
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1.4fr", gap: 14 }}>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Stok Kodu</th><th>Ad</th><th>Ana Grup</th><th>Alt Grup</th><th>Ürün Grubu</th></tr></thead>
            <tbody>
              {list.data?.map((i) => (
                <tr key={i.id} onClick={() => setSel(i)} style={{ cursor: "pointer", background: sel?.id === i.id ? "#e3f2fd" : undefined }}>
                  <td><b>{i.code}</b></td>
                  <td>{i.name}</td>
                  <td>{i.main_group || <span className="muted">—</span>}</td>
                  <td>{i.sub_group || <span className="muted">—</span>}</td>
                  <td>{i.product_group}</td>
                </tr>
              ))}
              {list.data?.length === 0 && <tr><td colSpan={5} className="muted">Kayıt yok.</td></tr>}
            </tbody>
          </table>
        </div>
        <div>
          {!sel && <div className="panel muted">Soldan bir stok kodu seçin.</div>}
          {sel && detail.data && (
            <>
              <div className="panel">
                <h2 style={{ marginTop: 0 }}>{detail.data.code} — {detail.data.name}</h2>
                <p className="muted">{detail.data.main_group && <>Ana Grup: <b>{detail.data.main_group}</b> · </>}{detail.data.sub_group && <>Alt Grup: <b>{detail.data.sub_group}</b> · </>}Ürün Grubu: <b>{detail.data.product_group}</b></p>
                <div className="row">
                  <label>Miktar<input type="number" min={1} value={qty} onChange={(e) => setQty(Number(e.target.value) || 1)} /></label>
                  <div className="kpi"><span className="v">{fmt(hours.data?.total_hours, 2)} saat</span><span className="l">Toplam iş gücü ihtiyacı</span></div>
                </div>
                <table>
                  <thead><tr><th>Sıra</th><th>Operasyon</th><th>Yarımamül Kodu</th><th>İş Merkezi</th><th className="num">Çevrim (sn)</th><th className="num">Setup (dk)</th><th className="num">Saat ({qty} adet)</th></tr></thead>
                  <tbody>
                    {detail.data.operations.map((op) => {
                      const h = hours.data?.operations.find((o) => o.seq === op.seq)?.hours;
                      return <tr key={op.id}><td>{op.seq}</td><td>{op.operation_name}</td><td><code>{op.semi_finished_code || "—"}</code></td><td>{wcCode(op.work_center_id)}</td><td className="num">{fmt(op.cycle_time_sec)}</td><td className="num">{fmt(op.setup_time_min)}</td><td className="num">{fmt(h, 2)}</td></tr>;
                    })}
                    {detail.data.operations.length === 0 && <tr><td colSpan={7} className="muted">Rota tanımı yok</td></tr>}
                  </tbody>
                </table>
              </div>
              <div className="panel">
                <h2 style={{ marginTop: 0 }}>BOM (hammadde)</h2>
                <table>
                  <thead><tr><th>Bileşen</th><th>Ad</th><th className="num">Miktar</th><th>Birim</th><th className="num">× {qty}</th></tr></thead>
                  <tbody>
                    {detail.data.bom_lines.map((b) => <tr key={b.id}><td>{b.component_code}</td><td>{b.component_name}</td><td className="num">{fmt(b.quantity, 3)}</td><td>{b.unit}</td><td className="num">{fmt(b.quantity * qty, 3)}</td></tr>)}
                    {detail.data.bom_lines.length === 0 && <tr><td colSpan={5} className="muted">BOM yok</td></tr>}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}
