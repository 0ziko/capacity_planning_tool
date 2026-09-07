import { useMemo, useState } from "react";
import { api, fmt, mondayOf, qs, type Capacity, type CapacitySource, type Machine, type Shift, type WorkCenter } from "../api";
import { useAuth } from "../auth";
import { ErrorText, useAsync, useWorkCenters } from "../components";

const DAYS = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"];
const emptyWc = {
  code: "", name: "", description: "", is_active: true, is_planned: false, capacity_unit_hours: 10, default_efficient_hours: 4,
  area_code: "", area_name: "", capacity_source: "work_center" as CapacitySource,
};
const emptyShift: Shift = { name: "Gündüz", weekdays: "0,1,2,3,4", start_time: "08:00", end_time: "18:00", headcount: 0, efficient_hours_per_person: null };
const emptyMachine: Machine = { code: "", name: "", description: "", is_active: true };

const SOURCE_LABEL: Record<CapacitySource, string> = { work_center: "İş merkezi personeli", machines: "Makine atamaları" };

/** API'ye gonderilecek is merkezi govdesi (hesaplanan/iliskili alanlar cikarilir). */
function wcBody(wc: Partial<WorkCenter>) {
  const { id: _id, shifts: _s, machines: _m, employee_count: _e, machine_employee_count: _me, capacity_headcount: _ch, ...rest } = wc;
  return { ...emptyWc, ...rest };
}

export default function WorkCenters() {
  const { can } = useAuth();
  const { wcs, reload } = useWorkCenters();
  const [week, setWeek] = useState(mondayOf(new Date()));
  const [edit, setEdit] = useState<Partial<WorkCenter> | null>(null);
  const [err, setErr] = useState("");
  const [groupByArea, setGroupByArea] = useState(true);
  const cap = useAsync(() => api.get<Capacity[]>(`/api/capacity${qs({ start: week })}`), [week, wcs.length]);
  const capBy = Object.fromEntries((cap.data ?? []).map((c) => [c.work_center_id, c]));
  const canEdit = can("poweruser");
  const refresh = () => { reload(); cap.reload(); };

  // Alan kodu -> is merkezleri (alan tanimsizlar en sonda)
  const groups = useMemo(() => {
    const m = new Map<string, { name: string; wcs: WorkCenter[] }>();
    for (const wc of wcs) {
      const key = wc.area_code || "";
      const g = m.get(key) ?? { name: wc.area_name || "", wcs: [] };
      if (!g.name && wc.area_name) g.name = wc.area_name;
      g.wcs.push(wc);
      m.set(key, g);
    }
    return Array.from(m.entries()).sort(([a], [b]) => (a === "" ? 1 : b === "" ? -1 : a.localeCompare(b, "tr")));
  }, [wcs]);
  const areaOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const wc of wcs) if (wc.area_code && !seen.has(wc.area_code)) seen.set(wc.area_code, wc.area_name);
    return Array.from(seen.entries());
  }, [wcs]);

  const save = async () => {
    if (!edit) return;
    setErr("");
    try {
      const body = wcBody(edit);
      if (edit.id) await api.put(`/api/workcenters/${edit.id}`, body);
      else await api.post("/api/workcenters", body);
      setEdit(null);
      refresh();
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  const remove = async (wc: WorkCenter) => {
    if (!confirm(`${wc.code} silinsin mi? Bağlı vardiyalar ve makineler silinir.`)) return;
    setErr("");
    try {
      await api.del(`/api/workcenters/${wc.id}`);
      refresh();
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  const patch = async (wc: WorkCenter, changes: Partial<WorkCenter>) => {
    setErr("");
    try {
      await api.put(`/api/workcenters/${wc.id}`, { ...wcBody(wc), ...changes });
      refresh();
    } catch (e) {
      setErr((e as Error).message);
    }
  };

  const totalMachines = wcs.reduce((s, w) => s + w.machines.length, 0);
  const machineMode = wcs.filter((w) => w.capacity_source === "machines").length;

  return (
    <>
      <h1>İş Merkezleri</h1>
      <div className="panel row">
        <label>
          Kapasite haftası
          <input type="date" value={week} onChange={(e) => setWeek(mondayOf(new Date(e.target.value)))} />
        </label>
        <label style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
          <input type="checkbox" checked={groupByArea} onChange={(e) => setGroupByArea(e.target.checked)} /> Alana göre grupla
        </label>
        {canEdit && <button onClick={() => setEdit({ ...emptyWc })}>+ Yeni iş merkezi</button>}
        <span className="muted">
          {wcs.length} iş merkezi · {areaOptions.length} alan · {totalMachines} makine
          {machineMode > 0 && <> · {machineMode} iş merkezinde kapasite makine atamalarından</>}
        </span>
      </div>
      <p className="muted" style={{ marginTop: -6 }}>
        Pilot yaklaşım: yalnızca “Planlanıyor” işaretli iş merkezleri otomatik planlamaya girer.
        <b> Kapasite kaynağı</b>: <i>İş merkezi personeli</i> → vardiya kişi sayısı, yoksa iş merkezine bağlı personel;
        <i> Makine atamaları</i> → yalnızca bu iş merkezinin aktif makinelerine atanmış personel sayılır (makine detayını aktifleştirmek için).
      </p>
      <ErrorText err={err || cap.err} />

      {edit && (
        <div className="panel">
          <h2>{edit.id ? `Düzenle: ${edit.code}` : "Yeni iş merkezi"}</h2>
          <div className="row">
            <label>Kod<input value={edit.code ?? ""} onChange={(e) => setEdit({ ...edit, code: e.target.value })} /></label>
            <label>Ad<input value={edit.name ?? ""} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></label>
            <label>Açıklama<input value={edit.description ?? ""} onChange={(e) => setEdit({ ...edit, description: e.target.value })} /></label>
          </div>
          <div className="row">
            <label>Alan kodu
              <input
                list="area-codes"
                value={edit.area_code ?? ""}
                placeholder="örn. PRS"
                onChange={(e) => {
                  const code = e.target.value.toUpperCase();
                  const known = areaOptions.find(([c]) => c === code);
                  setEdit({ ...edit, area_code: code, area_name: known && !edit.area_name ? known[1] : edit.area_name });
                }}
              />
              <datalist id="area-codes">{areaOptions.map(([c, n]) => <option key={c} value={c}>{n}</option>)}</datalist>
            </label>
            <label>Alan adı<input value={edit.area_name ?? ""} placeholder="örn. PRESHANELER" onChange={(e) => setEdit({ ...edit, area_name: e.target.value })} /></label>
            <label>Kapasite kaynağı
              <select value={edit.capacity_source ?? "work_center"} onChange={(e) => setEdit({ ...edit, capacity_source: e.target.value as CapacitySource })}>
                <option value="work_center">{SOURCE_LABEL.work_center}</option>
                <option value="machines">{SOURCE_LABEL.machines}</option>
              </select>
            </label>
          </div>
          <div className="row">
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
            <tr>
              <th>Kod</th><th>Ad</th>{!groupByArea && <th>Alan</th>}<th>Planlanıyor</th>
              <th className="num" title="Kapasite hesabında kullanılan kişi sayısı (vardiya kişi sayısı girilmişse o geçerlidir)">Kişi</th>
              <th>Kapasite kaynağı</th><th>Makineler</th><th>Vardiyalar</th>
              <th className="num">Haftalık kapasite (saat)</th><th className="num">Birim</th><th></th>
            </tr>
          </thead>
          <tbody>
            {(groupByArea ? groups : [["", { name: "", wcs }] as const]).map(([areaCode, g]) => (
              <GroupRows
                key={areaCode || "__none"}
                header={groupByArea ? (areaCode ? `${areaCode} — ${g.name || areaCode}` : "Alan tanımsız") : null}
                colSpan={groupByArea ? 10 : 11}
              >
                {g.wcs.map((wc) => (
                  <WcRow
                    key={wc.id}
                    wc={wc}
                    cap={capBy[wc.id]}
                    canEdit={canEdit}
                    showArea={!groupByArea}
                    onEdit={() => setEdit(wc)}
                    onDelete={() => remove(wc)}
                    onTogglePlanned={() => patch(wc, { is_planned: !wc.is_planned })}
                    onSource={(s) => patch(wc, { capacity_source: s })}
                    onChanged={refresh}
                  />
                ))}
              </GroupRows>
            ))}
            {wcs.length === 0 && <tr><td colSpan={11} className="muted">İş merkezi yok. “+ Yeni iş merkezi” ile ekleyin veya Excel Import kullanın.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}

function GroupRows({ header, colSpan, children }: { header: string | null; colSpan: number; children: React.ReactNode }) {
  return (
    <>
      {header && (
        <tr>
          <td colSpan={colSpan} style={{ background: "#eef2f7", fontWeight: 600, padding: "6px 8px" }}>{header}</td>
        </tr>
      )}
      {children}
    </>
  );
}

function WcRow({ wc, cap, canEdit, showArea, onEdit, onDelete, onTogglePlanned, onSource, onChanged }: {
  wc: WorkCenter; cap?: Capacity; canEdit: boolean; showArea: boolean;
  onEdit: () => void; onDelete: () => void; onTogglePlanned: () => void; onSource: (s: CapacitySource) => void; onChanged: () => void;
}) {
  const [open, setOpen] = useState<"" | "shifts" | "machines">("");
  const toggle = (k: "shifts" | "machines") => setOpen(open === k ? "" : k);
  const isMachines = wc.capacity_source === "machines";
  const noAssigned = isMachines && wc.machine_employee_count === 0;
  return (
    <>
      <tr style={{ opacity: wc.is_active ? 1 : 0.5 }}>
        <td><b>{wc.code}</b></td>
        <td>{wc.name}</td>
        {showArea && <td>{wc.area_code ? <span title={wc.area_name}>{wc.area_code}</span> : <span className="muted">—</span>}</td>}
        <td><input type="checkbox" checked={wc.is_planned} disabled={!canEdit} onChange={onTogglePlanned} /></td>
        <td className="num" title={`İş merkezi personeli: ${wc.employee_count} · Makinelere atanan: ${wc.machine_employee_count}`}>
          {wc.capacity_headcount}
          {isMachines && <span className="muted"> / {wc.employee_count}</span>}
        </td>
        <td>
          <select value={wc.capacity_source} disabled={!canEdit} onChange={(e) => onSource(e.target.value as CapacitySource)} title="Kapasite kişi sayısının kaynağı">
            <option value="work_center">{SOURCE_LABEL.work_center}</option>
            <option value="machines">{SOURCE_LABEL.machines}</option>
          </select>
          {noAssigned && <div className="muted" style={{ color: "#b45309", fontSize: 12 }}>⚠ Makinelere atanmış personel yok → kapasite 0</div>}
        </td>
        <td>
          <button className={`secondary small${open === "machines" ? " active" : ""}`} onClick={() => toggle("machines")}>
            {wc.machines.length} makine {open === "machines" ? "▲" : "▼"}
          </button>
        </td>
        <td>
          <button className={`secondary small${open === "shifts" ? " active" : ""}`} onClick={() => toggle("shifts")}>
            {wc.shifts.length} vardiya {open === "shifts" ? "▲" : "▼"}
          </button>
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
          <td colSpan={showArea ? 11 : 10} style={{ background: "#f8fafc" }}>
            {open === "shifts" ? <Shifts wc={wc} canEdit={canEdit} onChanged={onChanged} /> : <Machines wc={wc} canEdit={canEdit} onChanged={onChanged} />}
          </td>
        </tr>
      )}
    </>
  );
}

function Machines({ wc, canEdit, onChanged }: { wc: WorkCenter; canEdit: boolean; onChanged: () => void }) {
  const [draft, setDraft] = useState<Machine | null>(null);
  const [err, setErr] = useState("");
  const save = async () => {
    if (!draft) return;
    setErr("");
    try {
      const body = { code: draft.code.trim(), name: draft.name, description: draft.description, is_active: draft.is_active };
      if (draft.id) await api.put(`/api/machines/${draft.id}`, body);
      else await api.post(`/api/workcenters/${wc.id}/machines`, body);
      setDraft(null);
      onChanged();
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  const remove = async (m: Machine) => {
    if (!confirm(`${m.code} makinesi silinsin mi? Bu makineye atanmış personelin makine ataması kaldırılır.`)) return;
    setErr("");
    try {
      await api.del(`/api/machines/${m.id}`);
      onChanged();
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  return (
    <div style={{ padding: "6px 0" }}>
      <table style={{ width: "auto" }}>
        <thead><tr><th>Makine kodu</th><th>Ad</th><th>Açıklama</th><th>Aktif</th><th className="num">Atanan personel</th><th></th></tr></thead>
        <tbody>
          {wc.machines.map((m) => (
            <tr key={m.id} style={{ opacity: m.is_active ? 1 : 0.5 }}>
              <td><b>{m.code}</b></td>
              <td>{m.name}</td>
              <td className="muted">{m.description}</td>
              <td>{m.is_active ? "E" : "H"}</td>
              <td className="num">{m.employee_count ?? 0}</td>
              <td>{canEdit && (<><button className="secondary small" onClick={() => setDraft(m)}>Düzenle</button> <button className="danger small" onClick={() => remove(m)}>Sil</button></>)}</td>
            </tr>
          ))}
          {wc.machines.length === 0 && (
            <tr><td colSpan={6} className="muted">Makine tanımı yok. Makine detayı isteğe bağlıdır; “Kapasite kaynağı = Makine atamaları” seçildiğinde yalnızca makinelere atanan personel sayılır.</td></tr>
          )}
        </tbody>
      </table>
      <p className="muted" style={{ margin: "6px 0 0" }}>
        Personelin makineye atanması <b>Personel</b> sayfasından yapılır (personel satırı → Makine). Toplu yükleme için Excel Import → “Makineler” / “Personel” (Makine Kodu sütunu).
      </p>
      {canEdit && !draft && <button className="secondary small" style={{ marginTop: 6 }} onClick={() => setDraft({ ...emptyMachine })}>+ Makine ekle</button>}
      {draft && (
        <div className="row" style={{ marginTop: 8 }}>
          <label>Makine kodu<input value={draft.code} placeholder={`örn. ${wc.code}-01`} onChange={(e) => setDraft({ ...draft, code: e.target.value })} /></label>
          <label>Ad<input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></label>
          <label>Açıklama<input value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></label>
          <label>Aktif<input type="checkbox" checked={draft.is_active} onChange={(e) => setDraft({ ...draft, is_active: e.target.checked })} /></label>
          <button onClick={save} disabled={!draft.code.trim()}>Kaydet</button>
          <button className="secondary" onClick={() => setDraft(null)}>Vazgeç</button>
          <ErrorText err={err} />
        </div>
      )}
      {!draft && <ErrorText err={err} />}
    </div>
  );
}

function Shifts({ wc, canEdit, onChanged }: { wc: WorkCenter; canEdit: boolean; onChanged: () => void }) {
  const [draft, setDraft] = useState<Shift | null>(null);
  const [err, setErr] = useState("");
  const isMachines = wc.capacity_source === "machines";
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
            const hc = isMachines ? wc.machine_employee_count : s.headcount > 0 ? s.headcount : wc.employee_count;
            const eff = s.efficient_hours_per_person ?? wc.default_efficient_hours;
            return (
              <tr key={s.id}>
                <td>{s.name}</td>
                <td>{s.weekdays.split(",").filter(Boolean).map((d) => DAYS[Number(d)]).join(" ")}</td>
                <td>{s.start_time.slice(0, 5)}–{s.end_time.slice(0, 5)}</td>
                <td className="num">
                  {isMachines
                    ? <span className="muted" title="Kapasite kaynağı makine atamaları: vardiya kişi sayısı yok sayılır">{hc} (makine)</span>
                    : s.headcount > 0 ? s.headcount : <span className="muted">{hc} (personel)</span>}
                </td>
                <td className="num">{s.efficient_hours_per_person ?? <span className="muted">{eff} (vars.)</span>}</td>
                <td className="num">{fmt(hc * eff)}</td>
                <td>{canEdit && (<><button className="secondary small" onClick={() => setDraft(s)}>Düzenle</button> <button className="danger small" onClick={async () => { await api.del(`/api/shifts/${s.id}`); onChanged(); }}>Sil</button></>)}</td>
              </tr>
            );
          })}
          {wc.shifts.length === 0 && (
            <tr><td colSpan={7} className="muted">
              Vardiya tanımı yok — varsayılan Pzt–Cum 08:00–18:00, {isMachines ? "makinelere atanan personel" : "personel"} sayısı × {wc.default_efficient_hours} saat kullanılır.
            </td></tr>
          )}
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
