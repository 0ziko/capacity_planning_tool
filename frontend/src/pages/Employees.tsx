import { useState } from "react";
import { api, qs, type Employee } from "../api";
import { useAuth } from "../auth";
import { ErrorText, useAsync, useWorkCenters } from "../components";

const empty = { code: "", name: "", work_center_id: null as number | null, is_active: true };

export default function Employees() {
  const { can } = useAuth();
  const { wcs } = useWorkCenters();
  const [filter, setFilter] = useState<number | "">("");
  const [edit, setEdit] = useState<Partial<Employee> | null>(null);
  const [err, setErr] = useState("");
  const list = useAsync(() => api.get<Employee[]>(`/api/employees${qs({ work_center_id: filter })}`), [filter]);
  const wcName = (id: number | null) => wcs.find((w) => w.id === id)?.code ?? "—";

  const save = async () => {
    if (!edit) return;
    setErr("");
    try {
      const body = { ...empty, ...edit, id: undefined, work_center_id: edit.work_center_id ? Number(edit.work_center_id) : null };
      if (edit.id) await api.put(`/api/employees/${edit.id}`, body);
      else await api.post("/api/employees", body);
      setEdit(null);
      list.reload();
    } catch (e) {
      setErr((e as Error).message);
    }
  };

  return (
    <>
      <h1>Personel</h1>
      <div className="panel row">
        <label>
          İş merkezi
          <select value={filter} onChange={(e) => setFilter(e.target.value === "" ? "" : Number(e.target.value))}>
            <option value="">Tümü</option>
            {wcs.map((w) => <option key={w.id} value={w.id}>{w.code} — {w.name}</option>)}
          </select>
        </label>
        {can("poweruser") && <button onClick={() => setEdit({ ...empty })}>+ Personel</button>}
        <span className="muted">Toplu yükleme için “Excel Import” sayfasını kullanın.</span>
      </div>
      <ErrorText err={err || list.err} />
      {edit && (
        <div className="panel row">
          <label>Sicil<input value={edit.code ?? ""} onChange={(e) => setEdit({ ...edit, code: e.target.value })} /></label>
          <label>Ad Soyad<input value={edit.name ?? ""} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></label>
          <label>İş merkezi
            <select value={edit.work_center_id ?? ""} onChange={(e) => setEdit({ ...edit, work_center_id: e.target.value === "" ? null : Number(e.target.value) })}>
              <option value="">—</option>
              {wcs.map((w) => <option key={w.id} value={w.id}>{w.code}</option>)}
            </select>
          </label>
          <label>Aktif<input type="checkbox" checked={edit.is_active ?? true} onChange={(e) => setEdit({ ...edit, is_active: e.target.checked })} /></label>
          <button onClick={save}>Kaydet</button>
          <button className="secondary" onClick={() => setEdit(null)}>Vazgeç</button>
        </div>
      )}
      <div className="table-wrap">
        <table>
          <thead><tr><th>Sicil</th><th>Ad Soyad</th><th>İş Merkezi</th><th>Aktif</th><th></th></tr></thead>
          <tbody>
            {list.data?.map((e) => (
              <tr key={e.id}>
                <td>{e.code}</td>
                <td>{e.name}</td>
                <td>{wcName(e.work_center_id)}</td>
                <td>{e.is_active ? "E" : "H"}</td>
                <td>{can("poweruser") && (<><button className="secondary small" onClick={() => setEdit(e)}>Düzenle</button> <button className="danger small" onClick={async () => { if (confirm("Silinsin mi?")) { await api.del(`/api/employees/${e.id}`); list.reload(); } }}>Sil</button></>)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted">{list.data?.length ?? 0} kayıt</p>
    </>
  );
}
