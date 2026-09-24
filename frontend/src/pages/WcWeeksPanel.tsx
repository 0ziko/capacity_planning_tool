import { useEffect, useRef, useState } from "react";
import { api, fmt, qs, shortDate, weekLabel, type WcWeek } from "../api";
import { ErrorText, useAsync } from "../components";

/**
 * Bir iş merkezinin hafta hafta iş gücü profili: kişi / kişi başı verimli saat / çalışma günü.
 * Boş kişi = eksik giriş; saat/gün boşsa varsayılan. Dolu hücre = o haftaya özel istisna.
 */
export default function WcWeeksPanel({ wcId, wcCode, start, weeks = 12, canEdit, onChanged, onSavingChange }: {
  wcId: number; wcCode?: string; start: string; weeks?: number; canEdit: boolean; onChanged?: () => void; onSavingChange?: (saving: boolean) => void;
}) {
  const rows = useAsync(() => api.get<WcWeek[]>(`/api/workcenters/${wcId}/weeks${qs({ start, weeks })}`), [wcId, start, weeks]);
  const [err, setErr] = useState("");
  const [saving, setSaving] = useState(false);
  const saveLock = useRef(false);
  const overrides = (rows.data ?? []).filter((r) => r.has_override).length;
  const missingFields = (r: WcWeek) => [
    r.ov_headcount === null ? "kişi" : null,
    r.ov_efficient_hours_per_person === null ? "verimli saat" : null,
    r.ov_working_days === null ? "çalışma günü" : null,
  ].filter(Boolean);

  const save = async (r: WcWeek, patch: Partial<{ headcount: number | null; efficient_hours_per_person: number | null; working_days: number | null; line_hours_per_day: number | null; note: string; overtime_headcount: number | null; overtime_days: number | null; overtime_hours_per_person: number | null; weekend_overtime_headcount: number | null; weekend_overtime_days: number | null; weekend_overtime_hours_per_person: number | null; overtime_proposed: boolean; overtime_extra_headcount: number | null }>) => {
    if (saveLock.current) return;
    saveLock.current = true;
    setSaving(true);
    setErr("");
    const body = {
      headcount: r.ov_headcount, efficient_hours_per_person: r.ov_efficient_hours_per_person, working_days: r.ov_working_days, note: r.note,
      line_hours_per_day: r.line_hours_per_day,
      overtime_headcount: r.ov_overtime_headcount ?? null, overtime_days: r.ov_overtime_days ?? null, overtime_hours_per_person: r.ov_overtime_hours_per_person ?? null,
      weekend_overtime_headcount: r.ov_weekend_overtime_headcount ?? null, weekend_overtime_days: r.ov_weekend_overtime_days ?? null, weekend_overtime_hours_per_person: r.ov_weekend_overtime_hours_per_person ?? null,
      overtime_proposed: r.overtime_proposed ?? false,
      overtime_extra_headcount: r.overtime_extra_headcount || null,
      ...patch,
    };
    try {
      onSavingChange?.(true);
      await api.put(`/api/workcenters/${wcId}/weeks/${r.week_start}`, body);
      await rows.reload();
      onChanged?.();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      saveLock.current = false;
      setSaving(false);
      onSavingChange?.(false);
    }
  };
  const clear = async (r: WcWeek) => {
    if (saveLock.current) return;
    saveLock.current = true;
    setSaving(true);
    setErr("");
    try {
      onSavingChange?.(true);
      await api.del(`/api/workcenters/${wcId}/weeks/${r.week_start}`);
      await rows.reload();
      onChanged?.();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      saveLock.current = false;
      setSaving(false);
      onSavingChange?.(false);
    }
  };

  if (rows.data?.[0]?.planning_mode === "line") return <StationWeeksPanel wcId={wcId} start={start} weeks={weeks} canEdit={canEdit} onChanged={onChanged} onSavingChange={onSavingChange} />;

  return (
    <div style={{ padding: "6px 0" }}>
      <div className="row" style={{ marginBottom: 6 }}>
        <span>
          <b>{wcCode ? `${wcCode} — ` : ""}Haftalık iş gücü</b>
          <span className="muted"> · {overrides} haftada istisna tanımlı · boş kişi = eksik; saat/gün = varsayılan</span>
        </span>
      </div>
      <ErrorText err={err || rows.err} />
      {rows.data?.some((r) => missingFields(r).length > 0) && (
        <p className="muted">Kişi sayısı boşsa iş gücü tanımsızdır; planlamadan önce düzeltme veya sıfır kapasite onayı gerekir. Saat/gün boşsa varsayılan kullanılır.</p>
      )}
      <div className="table-wrap">
        <table style={{ width: "auto" }}>
          <thead>
            <tr>
              <th>Hafta</th>
              <th>Giriş durumu</th>
              <th className="num" title="Kapasite hesabında kullanılan kişi sayısı">Kişi</th>
              <th className="num" title="Kişi başı günlük verimli (net üretken) saat">Verimli saat / kişi</th>
              <th className="num" title="Haftadaki çalışma günü sayısı (Pzt'den itibaren)">Çalışma günü</th>
              <th className="num" title="18:00-21:00 penceresinde fazla mesai yapacak kişi (haftanın kişi sayısını aşamaz)">FM kişi</th>
              <th className="num" title="Fazla mesai uygulanan gün sayısı (boş = tüm çalışma günleri)">FM gün</th>
              <th className="num" title="Kişi başı fazla mesai saati, en fazla 2,5 (18:00-21:00)">FM saat</th>
              <th className="num" title="Cumartesi/Pazar 08:00-18:00 fazla mesaisine kalacak kişi (mavi yaka onayı; haftanın kişi sayısını aşamaz)">HS kişi</th>
              <th className="num" title="Hafta sonu fazla mesai günü: 1 = Cumartesi, 2 = Cumartesi + Pazar">HS gün</th>
              <th className="num" title="Kişi başı hafta sonu fazla mesai saati, en fazla 8,5 (08:00-18:00, mola düşülmüş)">HS saat</th>
              <th className="num" title="Mesaiye alınabilecek ek kişi (komşu merkezden destek). Fazla mesai kişi sınırı = haftanın kişi sayısı + ek kişi; plan önerileri de bu havuzu kullanır">Ek kişi (mesai)</th>
              <th className="num" title="Fazla mesai yapan kişinin nominal saati: bu hafta / yıl başından beri birikim (yasal eşik 270 sa/yıl)">FM kişi başı hafta / yıl</th>
              <th className="num" title="Normal vardiya + fazla mesai (fazla mesai vardiya verim oranıyla eklenir)">Haftalık kapasite (saat)</th>
              <th>Not</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.data?.map((r) => (
              <tr key={r.week_start} style={{ background: r.has_override ? "#fff7ed" : undefined }}>
                <td title={`Hafta başlangıcı: ${r.week_start}`}><b>{weekLabel(r.week_start)}</b> <span className="muted">{shortDate(r.week_start)}</span></td>
                <td><span className={`badge ${missingFields(r).length ? "warn" : "ok"}`} title={missingFields(r).length ? `Eksik: ${missingFields(r).join(", ")}` : "Kişi, verimli saat ve çalışma günü girilmiş"}>{missingFields(r).length ? "Eksik giriş" : "Tanımlı"}</span></td>
                <td className="num"><Cell value={r.ov_headcount} placeholder={r.default_headcount} step={1} disabled={!canEdit || saving || rows.loading || !!rows.err} onSave={(v) => save(r, { headcount: v })} /></td>
                <td className="num"><Cell value={r.ov_efficient_hours_per_person} placeholder={r.default_efficient_hours} step={0.25} disabled={!canEdit || saving || rows.loading || !!rows.err} onSave={(v) => save(r, { efficient_hours_per_person: v })} /></td>
                <td className="num"><Cell value={r.ov_working_days} placeholder={r.default_working_days} step={1} min={0} max={7} disabled={!canEdit || saving || rows.loading || !!rows.err} onSave={(v) => save(r, { working_days: v })} /></td>
                <td className="num"><Cell value={r.ov_overtime_headcount ?? null} placeholder={0} step={1} min={0} max={r.headcount} disabled={!canEdit || saving || rows.loading || !!rows.err} onSave={(v) => save(r, { overtime_headcount: v })} /></td>
                <td className="num"><Cell value={r.ov_overtime_days ?? null} placeholder={r.working_days} step={1} min={0} max={7} disabled={!canEdit || saving || rows.loading || !!rows.err || !r.ov_overtime_headcount} onSave={(v) => save(r, { overtime_days: v })} /></td>
                <td className="num"><Cell value={r.ov_overtime_hours_per_person ?? null} placeholder={2.5} step={0.5} min={0} max={2.5} disabled={!canEdit || saving || rows.loading || !!rows.err || !r.ov_overtime_headcount} onSave={(v) => save(r, { overtime_hours_per_person: v })} /></td>
                <td className="num"><Cell value={r.ov_weekend_overtime_headcount ?? null} placeholder={0} step={1} min={0} max={r.headcount} disabled={!canEdit || saving || rows.loading || !!rows.err} onSave={(v) => save(r, { weekend_overtime_headcount: v })} /></td>
                <td className="num"><Cell value={r.ov_weekend_overtime_days ?? null} placeholder={2} step={1} min={0} max={2} disabled={!canEdit || saving || rows.loading || !!rows.err || !r.ov_weekend_overtime_headcount} onSave={(v) => save(r, { weekend_overtime_days: v })} /></td>
                <td className="num"><Cell value={r.ov_weekend_overtime_hours_per_person ?? null} placeholder={8.5} step={0.5} min={0} max={8.5} disabled={!canEdit || saving || rows.loading || !!rows.err || !r.ov_weekend_overtime_headcount} onSave={(v) => save(r, { weekend_overtime_hours_per_person: v })} /></td>
                <td className="num"><Cell value={r.overtime_extra_headcount || null} placeholder={0} step={1} min={0} disabled={!canEdit || saving || rows.loading || !!rows.err} onSave={(v) => save(r, { overtime_extra_headcount: v })} /></td>
                <td className="num" style={{ color: (r.overtime_person_hours_ytd ?? 0) > (r.overtime_legal_yearly_hours ?? 270) ? "var(--warn)" : undefined }} title={(r.overtime_person_hours_ytd ?? 0) > (r.overtime_legal_yearly_hours ?? 270) ? "Yasal 270 sa/yıl eşiği aşıldı (bilgi)" : undefined}>
                  {r.overtime_person_hours_week ? `${fmt(r.overtime_person_hours_week, 1)} / ${fmt(r.overtime_person_hours_ytd, 0)}` : (r.overtime_person_hours_ytd ? `— / ${fmt(r.overtime_person_hours_ytd, 0)}` : "—")}
                </td>
                <td className="num" style={{ fontWeight: 600 }} title={r.overtime_capacity_hours ? `Normal ${fmt(r.base_capacity_hours, 1)} + fazla mesai ${fmt(r.overtime_capacity_hours, 1)} (hafta sonu ${fmt(r.weekend_overtime_capacity_hours, 1)}; verim oranı ${fmt(r.overtime_efficiency_ratio, 2)})` : undefined}>
                  {fmt(r.capacity_hours, 1)}{r.overtime_capacity_hours ? <span className="muted" style={{ fontWeight: 400 }}> (+FM {fmt(r.overtime_capacity_hours, 1)})</span> : null}
                  {r.overtime_proposed && canEdit ? <button className="secondary small" style={{ marginLeft: 6 }} title="Plan önerisi olan fazla mesaiyi onayla (kalıcı kapasite olur)" disabled={saving} onClick={() => save(r, { overtime_proposed: false })}>Onay bekliyor</button> : r.overtime_proposed ? <span className="badge warn" style={{ marginLeft: 6 }}>Onay bekliyor</span> : null}
                </td>
                <td><NoteCell value={r.note} disabled={!canEdit || saving || rows.loading || !!rows.err} onSave={(v) => save(r, { note: v })} /></td>
                <td>{canEdit && r.has_override && <button className="secondary small" disabled={saving || rows.loading} title="Haftalık girişleri temizle; kişi sayısı yeniden eksik sayılır" onClick={() => clear(r)}>Varsayılan</button>}</td>
              </tr>
            ))}
            {!rows.data && <tr><td colSpan={8} className="muted">Yükleniyor…</td></tr>}
          </tbody>
        </table>
      </div>
      <p className="muted" style={{ margin: "6px 0 0" }}>
        Kişi sayısı ve kişi başı günlük verimli saat tüm vardiyaların toplamıdır; vardiya sayısıyla tekrar çarpılmaz. Kapasite toplamında takvimdeki tam gün tatiller düşülür. Değişiklikler bir sonraki hesaplamada kullanılır; mevcut plan satırları kendiliğinden yeniden yerleştirilmez. Toplu giriş için Excel Import → “Haftalık İş Gücü”.
      </p>
    </div>
  );
}

function Cell({ value, placeholder, step, min, max, disabled, onSave }: { value: number | null; placeholder: number; step: number; min?: number; max?: number; disabled: boolean; onSave: (v: number | null) => void }) {
  const [txt, setTxt] = useState(value === null ? "" : String(value));
  useEffect(() => setTxt(value === null ? "" : String(value)), [value]);
  const commit = () => {
    const v = txt.trim() === "" ? null : Number(txt.replace(",", "."));
    if (v !== null && Number.isNaN(v)) { setTxt(value === null ? "" : String(value)); return; }
    if ((v ?? null) === (value ?? null)) return;
    onSave(v);
  };
  return (
    <input
      type="number" step={step} min={min} max={max} value={txt} placeholder={String(placeholder)} disabled={disabled}
      onChange={(e) => setTxt(e.target.value)} onBlur={commit} onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
      style={{ width: 80, textAlign: "right", fontWeight: value === null ? 400 : 600, color: value === null ? "#64748b" : undefined }}
      title={value === null ? `Varsayılan: ${placeholder}` : `İstisna (varsayılan ${placeholder})`}
    />
  );
}

function NoteCell({ value, disabled, onSave }: { value: string; disabled: boolean; onSave: (v: string) => void }) {
  const [txt, setTxt] = useState(value);
  useEffect(() => setTxt(value), [value]);
  return (
    <input value={txt} disabled={disabled} placeholder="örn. izin, bayram, fazla mesai" style={{ width: 200 }}
      onChange={(e) => setTxt(e.target.value)} onBlur={() => { if (txt !== value) onSave(txt); }} onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />
  );
}


type StationWeek = { week_start: string; stations: { machine_id: number; code: string; name: string; required_crew_size: number | null; working_hours: number | null; capacity_hours: number; required_labor_hours: number }[] };
function StationWeeksPanel({wcId, start, weeks, canEdit, onChanged, onSavingChange}: {wcId: number; start: string; weeks: number; canEdit: boolean; onChanged?: () => void; onSavingChange?: (v: boolean) => void}) {
  const rows = useAsync(() => api.get<StationWeek[]>(`/api/workcenters/${wcId}/station-weeks${qs({start, weeks})}`), [wcId, start, weeks]);
  const [err, setErr] = useState("");
  const [saving, setSaving] = useState(false);
  const lock = useRef(false);
  async function save(machineId: number, week: string, working_hours: number | null) {
    if (lock.current) return;
    lock.current = true; setSaving(true); onSavingChange?.(true); setErr("");
    try {
      await api.put(`/api/machines/${machineId}/weeks/${week}`, {working_hours});
      await rows.reload(); onChanged?.();
    } catch(e) { setErr((e as Error).message); }
    finally {lock.current = false; setSaving(false); onSavingChange?.(false);}
  }
  return <div>
    <h3>İstasyonların haftalık hat saatleri</h3>
    <p>Her istasyonun hafta boyunca üretime ayıracağı toplam saati girin. Gerekli ekip, İstasyonlar sekmesinden tanımlanır. Boş saat veya ekip kapasite oluşturmaz; 0 saat, o hafta çalışılmayacağını belirtir.</p>
    <ErrorText err={err || rows.err} />
    {rows.data?.[0]?.stations.length === 0 && <p>Önce aktif istasyon tanımlayın.</p>}
    <div className="table-wrap"><table><thead><tr><th>Hafta</th><th>İstasyon</th><th>Gerekli ekip (kişi)</th><th>Haftalık toplam saat</th><th>Gerekli kişi-saat</th><th>Durum</th></tr></thead>
      {rows.data?.map(w => <tbody key={w.week_start}>
        {w.stations.map(s => <tr key={s.machine_id}>
          <td>{weekLabel(w.week_start)} <span className="muted">{shortDate(w.week_start)}</span></td>
          <td>{s.code} {s.name}</td><td>{s.required_crew_size ?? "Eksik giriş"}</td>
          <td><Cell value={s.working_hours} placeholder={0} step={0.25} min={0} max={168} disabled={!canEdit || saving || rows.loading || !!rows.err} onSave={v => save(s.machine_id,w.week_start,v)} /></td>
          <td>{s.working_hours !== null && s.required_crew_size !== null ? fmt(s.required_labor_hours,2) : "—"}</td>
          <td>{s.working_hours === null || s.required_crew_size === null ? "Eksik giriş" : "Tanımlı"}</td>
        </tr>)}
        <tr style={{fontWeight:600, background:"#edf3f8"}}><td>{weekLabel(w.week_start)} toplam</td><td colSpan={2}>İş merkezi</td><td>{fmt(w.stations.reduce((n,s) => n+s.capacity_hours,0),2)} hat-sa</td><td>{fmt(w.stations.reduce((n,s) => n+s.required_labor_hours,0),2)} kişi-sa</td><td /></tr>
      </tbody>)}
    </table></div>
    <p className="muted">Kişi başı verimli saat kullanılmaz. Mola, vardiya ve günlük takvim ayrıca düşülmez. Operasyon yalnızca tanımlı uygun istasyonlarda planlanır. Değişikliklerden sonra planı yeniden hesaplayın.</p>
  </div>;
}
