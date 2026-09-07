import { useEffect, useState } from "react";
import { api, fmt, qs, shortDate, weekLabel, type WcWeek } from "../api";
import { ErrorText, useAsync } from "../components";

/**
 * Bir iş merkezinin hafta hafta iş gücü profili: kişi / kişi başı verimli saat / çalışma günü.
 * Boş hücre = varsayılan (vardiya, personel ya da makine ataması). Dolu hücre = o haftaya özel istisna.
 */
export default function WcWeeksPanel({ wcId, wcCode, start, weeks = 12, canEdit, onChanged }: {
  wcId: number; wcCode?: string; start: string; weeks?: number; canEdit: boolean; onChanged?: () => void;
}) {
  const rows = useAsync(() => api.get<WcWeek[]>(`/api/workcenters/${wcId}/weeks${qs({ start, weeks })}`), [wcId, start, weeks]);
  const [err, setErr] = useState("");
  const overrides = (rows.data ?? []).filter((r) => r.has_override).length;

  const save = async (r: WcWeek, patch: Partial<{ headcount: number | null; efficient_hours_per_person: number | null; working_days: number | null; note: string }>) => {
    setErr("");
    const body = {
      headcount: r.ov_headcount, efficient_hours_per_person: r.ov_efficient_hours_per_person, working_days: r.ov_working_days, note: r.note,
      ...patch,
    };
    try {
      await api.put(`/api/workcenters/${wcId}/weeks/${r.week_start}`, body);
      rows.reload();
      onChanged?.();
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  const clear = async (r: WcWeek) => {
    setErr("");
    try {
      await api.del(`/api/workcenters/${wcId}/weeks/${r.week_start}`);
      rows.reload();
      onChanged?.();
    } catch (e) {
      setErr((e as Error).message);
    }
  };

  return (
    <div style={{ padding: "6px 0" }}>
      <div className="row" style={{ marginBottom: 6 }}>
        <span>
          <b>{wcCode ? `${wcCode} — ` : ""}Haftalık iş gücü</b>
          <span className="muted"> · {overrides} haftada istisna tanımlı · boş hücre = varsayılan (vardiya / personel / makine ataması)</span>
        </span>
      </div>
      <ErrorText err={err || rows.err} />
      <div className="table-wrap">
        <table style={{ width: "auto" }}>
          <thead>
            <tr>
              <th>Hafta</th>
              <th className="num" title="Kapasite hesabında kullanılan kişi sayısı">Kişi</th>
              <th className="num" title="Kişi başı günlük verimli (net üretken) saat">Verimli saat / kişi</th>
              <th className="num" title="Haftadaki çalışma günü sayısı (Pzt'den itibaren)">Çalışma günü</th>
              <th className="num">Haftalık kapasite (saat)</th>
              <th>Not</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.data?.map((r) => (
              <tr key={r.week_start} style={{ background: r.has_override ? "#fff7ed" : undefined }}>
                <td title={`Hafta başlangıcı: ${r.week_start}`}><b>{weekLabel(r.week_start)}</b> <span className="muted">{shortDate(r.week_start)}</span></td>
                <td className="num"><Cell value={r.ov_headcount} placeholder={r.default_headcount} step={1} disabled={!canEdit} onSave={(v) => save(r, { headcount: v })} /></td>
                <td className="num"><Cell value={r.ov_efficient_hours_per_person} placeholder={r.default_efficient_hours} step={0.25} disabled={!canEdit} onSave={(v) => save(r, { efficient_hours_per_person: v })} /></td>
                <td className="num"><Cell value={r.ov_working_days} placeholder={r.default_working_days} step={1} min={0} max={7} disabled={!canEdit} onSave={(v) => save(r, { working_days: v })} /></td>
                <td className="num" style={{ fontWeight: 600 }}>{fmt(r.capacity_hours, 1)}</td>
                <td><NoteCell value={r.note} disabled={!canEdit} onSave={(v) => save(r, { note: v })} /></td>
                <td>{canEdit && r.has_override && <button className="secondary small" title="Bu haftanın istisnasını kaldır (varsayılana dön)" onClick={() => clear(r)}>Varsayılan</button>}</td>
              </tr>
            ))}
            {!rows.data && <tr><td colSpan={7} className="muted">Yükleniyor…</td></tr>}
          </tbody>
        </table>
      </div>
      <p className="muted" style={{ margin: "6px 0 0" }}>
        Değişiklikler kapasite, otomatik plan, terminleme ve duruş analizine anında yansır. Toplu giriş için Excel Import → “Haftalık İş Gücü”.
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
