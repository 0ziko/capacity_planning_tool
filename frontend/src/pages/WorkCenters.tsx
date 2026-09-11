import { Fragment, useMemo, useState } from "react";
import { api, fmt, mondayOf, qs, type Capacity, type CapacitySource, type Machine, type Shift, type WorkCenter } from "../api";
import { useAuth } from "../auth";
import { ErrorText, useAsync, useWorkCenters } from "../components";
import WcWeeksPanel from "./WcWeeksPanel";

const DAYS = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"];
const emptyWc = {
  code: "", name: "", description: "", is_active: true, is_planned: false, capacity_unit_hours: 10, default_efficient_hours: 4,
  area_code: "", area_name: "", capacity_source: "work_center" as CapacitySource, planning_reserve_pct: 0,
};
const emptyShift: Shift = { name: "Gündüz", weekdays: "0,1,2,3,4", start_time: "08:00", end_time: "18:00", headcount: 0, efficient_hours_per_person: null };
const emptyMachine: Machine = { code: "", name: "", description: "", is_active: true };

const SOURCE_LABEL: Record<CapacitySource, string> = { work_center: "İş merkezi personeli", machines: "Makine atamaları" };
const SOURCE_SHORT: Record<CapacitySource, string> = { work_center: "Personel", machines: "Makine" };

type WcFilters = {
  code: string;
  area: string;
  planned: "" | "yes" | "no";
  source: "" | CapacitySource;
  active: "" | "yes" | "no";
};

const EMPTY_FILTERS: WcFilters = { code: "", area: "", planned: "", source: "", active: "" };

function wcBody(wc: Partial<WorkCenter>) {
  const { id: _id, shifts: _s, machines: _m, employee_count: _e, machine_employee_count: _me, capacity_headcount: _ch, ...rest } = wc;
  return { ...emptyWc, ...rest };
}

function filterWcs(rows: WorkCenter[], f: WcFilters): WorkCenter[] {
  return rows.filter((wc) => {
    if (f.code) {
      const q = f.code.toLocaleLowerCase("tr");
      if (!wc.code.toLocaleLowerCase("tr").includes(q) && !wc.name.toLocaleLowerCase("tr").includes(q)) return false;
    }
    if (f.area) {
      const q = f.area.toLocaleLowerCase("tr");
      if (!wc.area_code.toLocaleLowerCase("tr").includes(q) && !wc.area_name.toLocaleLowerCase("tr").includes(q)) return false;
    }
    if (f.planned === "yes" && !wc.is_planned) return false;
    if (f.planned === "no" && wc.is_planned) return false;
    if (f.source && wc.capacity_source !== f.source) return false;
    if (f.active === "yes" && !wc.is_active) return false;
    if (f.active === "no" && wc.is_active) return false;
    return true;
  });
}

export default function WorkCenters() {
  const { can } = useAuth();
  const { wcs, reload } = useWorkCenters();
  const [week, setWeek] = useState(mondayOf(new Date()));
  const [edit, setEdit] = useState<Partial<WorkCenter> | null>(null);
  const [err, setErr] = useState("");
  const [filters, setFilters] = useState<WcFilters>({ ...EMPTY_FILTERS });
  const [openId, setOpenId] = useState<number | null>(null);
  const [detailTab, setDetailTab] = useState<"shifts" | "machines" | "weeks">("shifts");
  const cap = useAsync(() => api.get<Capacity[]>(`/api/capacity${qs({ start: week })}`), [week, wcs.length]);
  const capBy = Object.fromEntries((cap.data ?? []).map((c) => [c.work_center_id, c]));
  const canEdit = can("poweruser");
  const refresh = () => { reload(); cap.reload(); };

  const filtered = useMemo(() => filterWcs(wcs, filters), [wcs, filters]);
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
      if (openId === wc.id) setOpenId(null);
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

  const openRow = (wc: WorkCenter) => {
    if (openId === wc.id) {
      setOpenId(null);
      return;
    }
    setOpenId(wc.id);
    setDetailTab("shifts");
  };

  return (
    <>
      <h1>İş Merkezleri</h1>
      <div className="panel row">
        <label>
          Kapasite haftası
          <input type="date" value={week} onChange={(e) => setWeek(mondayOf(new Date(e.target.value)))} />
        </label>
        {canEdit && <button onClick={() => setEdit({ ...emptyWc })}>+ Yeni iş merkezi</button>}
        <button className="secondary small" onClick={() => setFilters({ ...EMPTY_FILTERS })}>Filtreleri temizle</button>
        <span className="muted">{filtered.length} / {wcs.length} iş merkezi</span>
      </div>
      <p className="muted" style={{ marginTop: -6 }}>
        Satıra tıklayarak vardiya, makine ve haftalık iş gücü detaylarını açın. Yalnızca <b>Planlanıyor</b> işaretli iş merkezleri otomatik planlamaya girer.
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
            <label>Kişi başı verimli saat<input type="number" step="0.25" value={edit.default_efficient_hours ?? 4} onChange={(e) => setEdit({ ...edit, default_efficient_hours: Number(e.target.value) })} /></label>
            <label title="Planlamada boş bırakılacak kapasite payı">Atıl kapasite %
              <input type="number" min={0} max={99} step={1} value={edit.planning_reserve_pct ?? 0} onChange={(e) => setEdit({ ...edit, planning_reserve_pct: Math.min(99, Math.max(0, Number(e.target.value))) })} style={{ width: 72 }} />
            </label>
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
              <th></th>
              <th>Kod</th>
              <th>Alan</th>
              <th>Plan</th>
              <th className="num">Kişi</th>
              <th>Kaynak</th>
              <th className="num">Mak / Var</th>
              <th className="num">Kapasite (sa)</th>
              <th className="num">Atıl %</th>
              <th>Durum</th>
            </tr>
            <tr style={{ background: "#f8fafc" }}>
              <td></td>
              <td><input placeholder="Kod / ad" value={filters.code} onChange={(e) => setFilters({ ...filters, code: e.target.value })} style={{ width: "100%", minWidth: 72 }} /></td>
              <td><input placeholder="Alan" value={filters.area} onChange={(e) => setFilters({ ...filters, area: e.target.value })} style={{ width: "100%", minWidth: 64 }} /></td>
              <td>
                <select value={filters.planned} onChange={(e) => setFilters({ ...filters, planned: e.target.value as WcFilters["planned"] })}>
                  <option value="">Tümü</option>
                  <option value="yes">Planlanan</option>
                  <option value="no">Planlanmayan</option>
                </select>
              </td>
              <td></td>
              <td>
                <select value={filters.source} onChange={(e) => setFilters({ ...filters, source: e.target.value as WcFilters["source"] })}>
                  <option value="">Tümü</option>
                  <option value="work_center">Personel</option>
                  <option value="machines">Makine</option>
                </select>
              </td>
              <td></td>
              <td></td>
              <td></td>
              <td>
                <select value={filters.active} onChange={(e) => setFilters({ ...filters, active: e.target.value as WcFilters["active"] })}>
                  <option value="">Tümü</option>
                  <option value="yes">Aktif</option>
                  <option value="no">Pasif</option>
                </select>
              </td>
            </tr>
          </thead>
          <tbody>
            {filtered.map((wc) => {
              const c = capBy[wc.id];
              const isMachines = wc.capacity_source === "machines";
              const noAssigned = isMachines && wc.machine_employee_count === 0;
              const open = openId === wc.id;
              return (
                <Fragment key={wc.id}>
                  <tr
                    onClick={() => openRow(wc)}
                    style={{ cursor: "pointer", opacity: wc.is_active ? 1 : 0.55, background: open ? "#f0f7ff" : undefined }}
                  >
                    <td>{open ? "▼" : "▶"}</td>
                    <td><b>{wc.code}</b> <span className="muted">{wc.name}</span></td>
                    <td>{wc.area_code ? <span title={wc.area_name}>{wc.area_code}</span> : <span className="muted">—</span>}</td>
                    <td>{wc.is_planned ? <span className="badge ok">plan</span> : <span className="badge muted">—</span>}</td>
                    <td className="num" title={`Personel: ${wc.employee_count} · Makine atamalı: ${wc.machine_employee_count}`}>
                      {wc.capacity_headcount}
                    </td>
                    <td>{SOURCE_SHORT[wc.capacity_source]}</td>
                    <td className="num">{wc.machines.length} / {wc.shifts.length}</td>
                    <td className="num">{c ? fmt(c.capacity_hours, 0) : "…"}</td>
                    <td className="num">{wc.planning_reserve_pct > 0 ? fmt(wc.planning_reserve_pct, 0) : "—"}</td>
                    <td>
                      {!wc.is_active && <span className="badge muted">pasif</span>}
                      {noAssigned && <span className="badge warn">personel yok</span>}
                      {wc.is_active && !noAssigned && wc.is_planned && <span className="badge ok">aktif</span>}
                    </td>
                  </tr>
                  {open && (
                    <tr>
                      <td colSpan={10} style={{ background: "#f8fafc", padding: 0 }}>
                        <WcDetail
                          wc={wc}
                          cap={c}
                          week={week}
                          tab={detailTab}
                          setTab={setDetailTab}
                          canEdit={canEdit}
                          onEdit={() => setEdit(wc)}
                          onDelete={() => remove(wc)}
                          onTogglePlanned={() => patch(wc, { is_planned: !wc.is_planned })}
                          onSource={(s) => patch(wc, { capacity_source: s })}
                          onChanged={refresh}
                        />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
            {filtered.length === 0 && (
              <tr><td colSpan={10} className="muted">{wcs.length === 0 ? "İş merkezi yok. «+ Yeni iş merkezi» veya Excel Import kullanın." : "Filtreye uyan kayıt yok."}</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}

function WcDetail({ wc, cap, week, tab, setTab, canEdit, onEdit, onDelete, onTogglePlanned, onSource, onChanged }: {
  wc: WorkCenter; cap?: Capacity; week: string; tab: "shifts" | "machines" | "weeks"; setTab: (t: "shifts" | "machines" | "weeks") => void;
  canEdit: boolean; onEdit: () => void; onDelete: () => void; onTogglePlanned: () => void; onSource: (s: CapacitySource) => void; onChanged: () => void;
}) {
  const isMachines = wc.capacity_source === "machines";
  return (
    <div style={{ padding: 12 }} onClick={(e) => e.stopPropagation()}>
      <div className="row" style={{ marginBottom: 10, alignItems: "center", flexWrap: "wrap", gap: 8 }}>
        <span className="muted">
          Birim: {wc.capacity_unit_hours} sa · Varsayılan {wc.default_efficient_hours} sa/kişi
          {cap && <> · {fmt(cap.capacity_units, 0)} birim</>}
        </span>
        {canEdit && (
          <>
            <button className="secondary small" onClick={onEdit}>Düzenle</button>
            <button className="secondary small" onClick={onTogglePlanned}>{wc.is_planned ? "Planlamadan çıkar" : "Planlamaya al"}</button>
            <select value={wc.capacity_source} onChange={(e) => onSource(e.target.value as CapacitySource)} style={{ fontSize: 12 }}>
              <option value="work_center">{SOURCE_LABEL.work_center}</option>
              <option value="machines">{SOURCE_LABEL.machines}</option>
            </select>
            <button className="danger small" onClick={onDelete}>Sil</button>
          </>
        )}
      </div>
      <div className="tabs" style={{ margin: "0 0 10px" }}>
        <button type="button" className={`tab ${tab === "shifts" ? "active" : ""}`} onClick={() => setTab("shifts")}>Vardiyalar ({wc.shifts.length})</button>
        <button type="button" className={`tab ${tab === "machines" ? "active" : ""}`} onClick={() => setTab("machines")}>İstasyonlar ({wc.machines.length})</button>
        <button type="button" className={`tab ${tab === "weeks" ? "active" : ""}`} onClick={() => setTab("weeks")}>Haftalık iş gücü</button>
      </div>
      {tab === "shifts" && <Shifts wc={wc} canEdit={canEdit} onChanged={onChanged} />}
      {tab === "machines" && <Machines wc={wc} canEdit={canEdit} onChanged={onChanged} />}
      {tab === "weeks" && <WcWeeksPanel wcId={wc.id} start={week} weeks={12} canEdit={canEdit} onChanged={onChanged} />}
      {isMachines && wc.machine_employee_count === 0 && (
        <p className="muted" style={{ color: "#b45309", marginTop: 8 }}>Kapasite kaynağı «Makine» seçili ama makinelere atanmış personel yok — kapasite 0 hesaplanır.</p>
      )}
    </div>
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
    if (!confirm(`${m.code} istasyonu silinsin mi?`)) return;
    setErr("");
    try {
      await api.del(`/api/machines/${m.id}`);
      onChanged();
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  return (
    <div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Kod</th><th>Ad</th><th>Açıklama</th><th>Aktif</th><th className="num">Personel</th><th></th></tr></thead>
          <tbody>
            {wc.machines.map((m) => (
              <tr key={m.id} style={{ opacity: m.is_active ? 1 : 0.5 }}>
                <td><b>{m.code}</b></td>
                <td>{m.name}</td>
                <td className="muted">{m.description || "—"}</td>
                <td>{m.is_active ? "E" : "H"}</td>
                <td className="num">{m.employee_count ?? 0}</td>
                <td>{canEdit && (<><button className="secondary small" onClick={() => setDraft(m)}>Düzenle</button>{" "}<button className="danger small" onClick={() => remove(m)}>Sil</button></>)}</td>
              </tr>
            ))}
            {wc.machines.length === 0 && <tr><td colSpan={6} className="muted">İstasyon tanımı yok.</td></tr>}
          </tbody>
        </table>
      </div>
      {canEdit && !draft && <button className="secondary small" style={{ marginTop: 6 }} onClick={() => setDraft({ ...emptyMachine })}>+ İstasyon ekle</button>}
      {draft && (
        <div className="row" style={{ marginTop: 8 }}>
          <label>Kod<input value={draft.code} placeholder={`${wc.code}-01`} onChange={(e) => setDraft({ ...draft, code: e.target.value })} /></label>
          <label>Ad<input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></label>
          <label>Açıklama<input value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></label>
          <label>Aktif<input type="checkbox" checked={draft.is_active} onChange={(e) => setDraft({ ...draft, is_active: e.target.checked })} /></label>
          <button onClick={save} disabled={!draft.code.trim()}>Kaydet</button>
          <button className="secondary" onClick={() => setDraft(null)}>Vazgeç</button>
        </div>
      )}
      <ErrorText err={err} />
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
    <div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Vardiya</th><th>Günler</th><th>Saat</th><th className="num">Kişi</th><th className="num">Verimli sa/kişi</th><th className="num">Günlük sa</th><th></th></tr></thead>
          <tbody>
            {wc.shifts.map((s) => {
              const hc = isMachines ? wc.machine_employee_count : s.headcount > 0 ? s.headcount : wc.employee_count;
              const eff = s.efficient_hours_per_person ?? wc.default_efficient_hours;
              return (
                <tr key={s.id}>
                  <td>{s.name}</td>
                  <td>{s.weekdays.split(",").filter(Boolean).map((d) => DAYS[Number(d)]).join(" ")}</td>
                  <td>{s.start_time.slice(0, 5)}–{s.end_time.slice(0, 5)}</td>
                  <td className="num">{hc}</td>
                  <td className="num">{eff}</td>
                  <td className="num">{fmt(hc * eff)}</td>
                  <td>{canEdit && (<><button className="secondary small" onClick={() => setDraft(s)}>Düzenle</button>{" "}<button className="danger small" onClick={async () => { await api.del(`/api/shifts/${s.id}`); onChanged(); }}>Sil</button></>)}</td>
                </tr>
              );
            })}
            {wc.shifts.length === 0 && (
              <tr><td colSpan={7} className="muted">Vardiya yok — varsayılan Pzt–Cum mesai kullanılır.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {canEdit && !draft && <button className="secondary small" style={{ marginTop: 6 }} onClick={() => setDraft({ ...emptyShift })}>+ Vardiya ekle</button>}
      {draft && (
        <div className="row" style={{ marginTop: 8 }}>
          <label>Ad<input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></label>
          <label>Günler<div>{DAYS.map((d, i) => <label key={i} style={{ display: "inline-flex", gap: 2, marginRight: 6, flexDirection: "row" }}><input type="checkbox" checked={draft.weekdays.split(",").includes(String(i))} onChange={() => toggleDay(i)} />{d}</label>)}</div></label>
          <label>Başlangıç<input type="time" value={draft.start_time.slice(0, 5)} onChange={(e) => setDraft({ ...draft, start_time: e.target.value })} /></label>
          <label>Bitiş<input type="time" value={draft.end_time.slice(0, 5)} onChange={(e) => setDraft({ ...draft, end_time: e.target.value })} /></label>
          <label>Kişi (0=personel)<input type="number" value={draft.headcount} onChange={(e) => setDraft({ ...draft, headcount: Number(e.target.value) })} /></label>
          <label>Verimli sa/kişi<input type="number" step="0.25" value={draft.efficient_hours_per_person ?? ""} onChange={(e) => setDraft({ ...draft, efficient_hours_per_person: e.target.value === "" ? null : Number(e.target.value) })} /></label>
          <button onClick={save}>Kaydet</button>
          <button className="secondary" onClick={() => setDraft(null)}>Vazgeç</button>
        </div>
      )}
      <ErrorText err={err} />
    </div>
  );
}
