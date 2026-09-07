import { useState } from "react";
import { api, fmt, mondayOf, qs, type Capacity, type Shift, type WorkCenter } from "../api";
import { useAuth } from "../auth";
import { ErrorText, useAsync, useWorkCenters } from "../components";

const DAYS = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"];
const emptyWc = { code: "", name: "", description: "", is_active: true, is_planned: false, capacity_unit_hours: 10, default_efficient_hours: 4 };
const emptyShift: Shift = { name: "Gündüz", weekdays: "0,1,2,3,4", start_time: "08:00", end_time: "18:00", headcount: 0, efficient_hours_per_person: null };

export default function WorkCenters() {
  const { can } = useAuth();
  const { wcs, reload } = useWorkCenters();
  const [week, setWeek] = useState(mondayOf(new Date()));
  const [edit, setEdit] = useState<Partial<WorkCenter> | null>(null);
  const [err, setErr] = useState("");
  const cap = useAsync(() => api.get<Capacity[]>(`/api/capacity${qs({ start: week })}`), [week, wcs.length]);
  const capBy = Object.fromEntries((cap.data ?? []).map((c) => [c.work_center_id, c]));

  const save = async () => {
    if (!edit) return;
    setErr("");
    try {
      const body = { ...emptyWc, ...edit, shifts: undefined, employee_count: undefined, id: undefined };
      if (edit.id) await api.put(`/api/workcenters/${edit.id}`, body);
      else await api.post("/api/workcenters", body);
      setEdit(null);
      await reload();
      cap.reload();
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  const remove = async (wc: WorkCenter) => {
    if (!confirm(`${wc.code} silinsin mi? Bağlı vardiyalar silinir.`)) return;
    await api.del(`/api/workcenters/${wc.id}`);
    reload();
  };
  const togglePlanned = async (wc: WorkCenter) => {
    await api.put(`/api/workcenters/${wc.id}`, { ...wc, is_planned: !wc.is_planned });
    reload();
  };

  return (
    <>
      <h1>İş Merkezleri</h1>
      <div className="panel row">
        <label>
          Kapasite haftası
          <input type="date" value={week} onChange={(e) => setWeek(mondayOf(new Date(e.target.value)))} />
        </label>
        {can("poweruser") && <button onClick={() => setEdit({ ...emptyWc })}>+ Yeni iş merkezi</button>}
        <span className="muted">Pilot yaklaşım: yalnızca “Planlanıyor” işaretli iş merkezleri otomatik planlamaya girer.</span>
      </div>
      <ErrorText err={err || cap.err} />

      {edit && (
        <div className="panel">
          <h2>{edit.id ? `Düzenle: ${edit.code}` : "Yeni iş merkezi"}</h2>
          <div className="row">
            <label>Kod<input value={edit.code ?? ""} onChange={(e) => setEdit({ ...edit, code: e.target.value })} /></label>
            <label>Ad<input value={edit.name ?? ""} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></label>
            <label>Açıklama<input value={edit.description ?? ""} onChange={(e) => setEdit({ ...edit, description: e.target.value })} /></label>
            <label>1 birim = saat<input type="number" step="0.5" value={edit.capacity_unit_hours ?? 10} onChange={(e) => setEdit({ ...edit, capacity_unit_hours: Number(e.target.value) })} /></label>
            <label>Kişi başı verimli saat (varsayılan)<input type="number" step="0.25" value={edit.default_efficient_hours ?? 4} onChange={(e) => setEdit({ ...edit, default_efficient_hours: Number(e.target.value) })} /></label>
            <label>Planlanıyor<input type="checkbox" checked={!!edit.is_planned} onChange={(e) => setEdit({ ...edit, is_planned: e.target.checked })} /></label>
            <label>Aktif<input type="checkbox" checked={edit.is_active ?? true} onChange={(e) => setEdit({ ...edit, is_active: e.target.checked })} /></label>
            <button onClick={save}>Kaydet</button>
            <button className="secondary" onClick={() => setEdit(null)}>Vazgeç</button>
          </div>
        </div>
      )}

      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Kod</th><th>Ad</th><th>Planlanıyor</th><th className="num">Personel</th><th>Vardiyalar</th><th className="num">Haftalık kapasite (saat)</th><th className="num">Birim</th><th></th></tr>
          </thead>
          <tbody>
            {wcs.map((wc) => (
              <WcRow key={wc.id} wc={wc} cap={capBy[wc.id]} canEdit={can("poweruser")} onEdit={() => setEdit(wc)} onDelete={() => remove(wc)} onToggle={() => togglePlanned(wc)} onChanged={() => { reload(); cap.reload(); }} />
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function WcRow({ wc, cap, canEdit, onEdit, onDelete, onToggle, onChanged }: { wc: WorkCenter; cap?: Capacity; canEdit: boolean; onEdit: () => void; onDelete: () => void; onToggle: () => void; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <tr style={{ opacity: wc.is_active ? 1 : 0.5 }}>
        <td><b>{wc.code}</b></td>
        <td>{wc.name}</td>
        <td>
          <input type="checkbox" checked={wc.is_planned} disabled={!canEdit} onChange={onToggle} />
        </td>
        <td className="num">{wc.employee_count}</td>
        <td>
          <button className="secondary small" onClick={() => setOpen(!open)}>{wc.shifts.length} vardiya {open ? "▲" : "▼"}</button>
        </td>
        <td className="num">{cap ? fmt(cap.capacity_hours) : "…"}</td>
        <td className="num">{cap ? `${fmt(cap.capacity_units)} (× ${wc.capacity_unit_hours} sa)` : ""}</td>
        <td>
          {canEdit && (
            <>
              <button className="secondary small" onClick={onEdit}>Düzenle</button>{" "}
              <button className="danger small" onClick={onDelete}>Sil</button>
            </>
          )}
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={8} style={{ background: "#f8fafc" }}>
            <Shifts wc={wc} canEdit={canEdit} onChanged={onChanged} />
          </td>
        </tr>
      )}
    </>
  );
}

function Shifts({ wc, canEdit, onChanged }: { wc: WorkCenter; canEdit: boolean; onChanged: () => void }) {
  const [draft, setDraft] = useState<Shift | null>(null);
  const [err, setErr] = useState("");
  const save = async () => {
    if (!draft) return;
    try {
      const body = { ...draft, efficient_hours_per_person: draft.efficient_hours_per_person === null || (draft.efficient_hours_per_person as unknown) === "" ? null : Number(draft.efficient_hours_per_person) };
      if (draft.id) await api.put(`/api/shifts/${draft.id}`, body);
      else await api.post(`/api/workcenters/${wc.id}/shifts`, body);
      setDraft(null);
      onChanged();
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  const toggleDay = (d: number) => {
    if (!draft) return;
    const set = new Set(draft.weekdays.split(",").filter(Boolean).map(Number));
    set.has(d) ? set.delete(d) : set.add(d);
    setDraft({ ...draft, weekdays: Array.from(set).sort().join(",") });
  };
  return (
    <div style={{ padding: "6px 0" }}>
      <table style={{ width: "auto" }}>
        <thead><tr><th>Vardiya</th><th>Günler</th><th>Saat</th><th className="num">Kişi</th><th className="num">Kişi başı verimli saat</th><th className="num">Günlük verimli saat</th><th></th></tr></thead>
        <tbody>
          {wc.shifts.map((s) => {
            const hc = s.headcount > 0 ? s.headcount : wc.employee_count;
            const eff = s.efficient_hours_per_person ?? wc.default_efficient_hours;
            return (
              <tr key={s.id}>
                <td>{s.name}</td>
                <td>{s.weekdays.split(",").filter(Boolean).map((d) => DAYS[Number(d)]).join(" ")}</td>
                <td>{s.start_time.slice(0, 5)}–{s.end_time.slice(0, 5)}</td>
                <td className="num">{s.headcount > 0 ? s.headcount : <span className="muted">{hc} (personel)</span>}</td>
                <td className="num">{s.efficient_hours_per_person ?? <span className="muted">{eff} (vars.)</span>}</td>
                <td className="num">{fmt(hc * eff)}</td>
                <td>{canEdit && (<><button className="secondary small" onClick={() => setDraft(s)}>Düzenle</button> <button className="danger small" onClick={async () => { await api.del(`/api/shifts/${s.id}`); onChanged(); }}>Sil</button></>)}</td>
              </tr>
            );
          })}
          {wc.shifts.length === 0 && <tr><td colSpan={7} className="muted">Vardiya tanımı yok — varsayılan Pzt–Cum 08:00–18:00, personel sayısı × {wc.default_efficient_hours} saat kullanılır.</td></tr>}
        </tbody>
      </table>
      {canEdit && !draft && <button className="secondary small" style={{ marginTop: 6 }} onClick={() => setDraft({ ...emptyShift })}>+ Vardiya ekle</button>}
      {draft && (
        <div className="row" style={{ marginTop: 8 }}>
          <label>Ad<input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></label>
          <label>Günler<div>{DAYS.map((d, i) => <label key={i} style={{ display: "inline-flex", gap: 2, marginRight: 6, flexDirection: "row" }}><input type="checkbox" checked={draft.weekdays.split(",").includes(String(i))} onChange={() => toggleDay(i)} />{d}</label>)}</div></label>
          <label>Başlangıç<input type="time" value={draft.start_time.slice(0, 5)} onChange={(e) => setDraft({ ...draft, start_time: e.target.value })} /></label>
          <label>Bitiş<input type="time" value={draft.end_time.slice(0, 5)} onChange={(e) => setDraft({ ...draft, end_time: e.target.value })} /></label>
          <label>Kişi sayısı (0 = personel listesi)<input type="number" value={draft.headcount} onChange={(e) => setDraft({ ...draft, headcount: Number(e.target.value) })} /></label>
          <label>Kişi başı verimli saat (boş = vars.)<input type="number" step="0.25" value={draft.efficient_hours_per_person ?? ""} onChange={(e) => setDraft({ ...draft, efficient_hours_per_person: e.target.value === "" ? null : Number(e.target.value) })} /></label>
          <button onClick={save}>Kaydet</button>
          <button className="secondary" onClick={() => setDraft(null)}>Vazgeç</button>
          <ErrorText err={err} />
        </div>
      )}
    </div>
  );
}
