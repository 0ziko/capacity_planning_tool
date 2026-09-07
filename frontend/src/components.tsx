import { useEffect, useRef, useState } from "react";
import { api, isoWeekInputValue, mondayFromIsoWeek, weekLabel, type WorkCenter } from "./api";

export function useWorkCenters() {
  const [wcs, setWcs] = useState<WorkCenter[]>([]);
  const reload = () => api.get<WorkCenter[]>("/api/workcenters").then(setWcs);
  useEffect(() => {
    reload();
  }, []);
  return { wcs, reload };
}

/**
 * İş merkezi çoklu seçimi: açılır liste + arama + onay kutuları + seçili chip'ler.
 * Boş seçim = "tümü" (veya onlyPlanned ise planlanan tümü).
 */
export function WcMultiSelect({ wcs, value, onChange, onlyPlanned = false, label = "İş merkezleri" }: { wcs: WorkCenter[]; value: number[]; onChange: (v: number[]) => void; onlyPlanned?: boolean; label?: string }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const list = onlyPlanned ? wcs.filter((w) => w.is_planned) : wcs;
  const nq = q.trim().toLocaleLowerCase("tr");
  const filtered = nq ? list.filter((w) => `${w.code} ${w.name}`.toLocaleLowerCase("tr").includes(nq)) : list;
  const selected = list.filter((w) => value.includes(w.id));
  const allLabel = onlyPlanned ? "Planlanan tümü" : "Tümü";

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const toggle = (id: number) => onChange(value.includes(id) ? value.filter((x) => x !== id) : [...value, id]);
  const selectShown = () => onChange(Array.from(new Set([...value, ...filtered.map((w) => w.id)])));
  const clear = () => onChange([]);

  return (
    <div className="ms" ref={ref}>
      <span className="ms-label">
        {label} <span className="muted">(boş = {allLabel.toLocaleLowerCase("tr")})</span>
      </span>
      <div className="ms-box">
        <button type="button" className="ms-trigger" onClick={() => setOpen((o) => !o)} aria-haspopup="listbox" aria-expanded={open}>
          {selected.length === 0 ? (
            <span className="ms-all">{allLabel} ({list.length})</span>
          ) : (
            <span className="ms-chips">
              {selected.slice(0, 4).map((w) => (
                <span key={w.id} className="chip" title={w.name}>
                  {w.code}
                  <span
                    className="chip-x"
                    role="button"
                    aria-label={`${w.code} kaldır`}
                    onClick={(e) => {
                      e.stopPropagation();
                      toggle(w.id);
                    }}
                  >
                    ×
                  </span>
                </span>
              ))}
              {selected.length > 4 && <span className="chip more">+{selected.length - 4}</span>}
            </span>
          )}
          <span className="ms-caret">{open ? "▲" : "▼"}</span>
        </button>
        {open && (
          <div className="ms-pop" role="listbox" aria-multiselectable>
            <input autoFocus className="ms-search" placeholder="Kod veya ad ile ara…" value={q} onChange={(e) => setQ(e.target.value)} />
            <div className="ms-actions">
              <button type="button" className="secondary small" onClick={selectShown}>
                {nq ? "Listelenenleri seç" : "Tümünü seç"}
              </button>
              <button type="button" className="secondary small" onClick={clear} disabled={value.length === 0}>
                Temizle
              </button>
              <span className="muted">{selected.length}/{list.length} seçili</span>
            </div>
            <div className="ms-list">
              {filtered.map((w) => {
                const on = value.includes(w.id);
                return (
                  <div key={w.id} className={`ms-item ${on ? "on" : ""}`} role="option" aria-selected={on} onClick={() => toggle(w.id)}>
                    <input type="checkbox" checked={on} readOnly tabIndex={-1} />
                    <span className="ms-code">{w.code}</span>
                    <span className="ms-name">{w.name}</span>
                    {w.is_planned && <span className="badge ok" title="Planlanıyor (pilot)">P</span>}
                  </div>
                );
              })}
              {filtered.length === 0 && <div className="muted" style={{ padding: 8 }}>Eşleşen iş merkezi yok.</div>}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export function UtilBadge({ u }: { u: number }) {
  const cls = u > 1.0001 ? "bad" : u > 0.9 ? "warn" : "ok";
  return <span className={`badge ${cls}`}>{Math.round(u * 100)}%</span>;
}

export function Bar({ ratio, cls = "" }: { ratio: number; cls?: string }) {
  const tone = ratio > 1.0001 ? "bad" : ratio > 0.9 ? "warn" : "";
  return (
    <div className={`bar ${cls}`.trim()}>
      <span className={tone} style={{ width: `${Math.min(ratio * 100, 100)}%` }} />
    </div>
  );
}

export function StatusBadge({ s }: { s: string }) {
  const map: Record<string, [string, string]> = {
    ahead: ["ok", "Önde"],
    on_track: ["ok", "Planda"],
    behind: ["bad", "Geride"],
    no_plan: ["muted", "Plan yok"],
    not_started: ["muted", "Başlamadı"],
  };
  const [cls, label] = map[s] ?? ["muted", s];
  return <span className={`badge ${cls}`}>{label}</span>;
}

/** Yalnizca ISO hafta secimi (gun takvimi yok). value = Pazartesi YYYY-MM-DD. */
export function WeekPicker({ value, onChange, label = "Hafta" }: { value: string; onChange: (mondayIso: string) => void; label?: string }) {
  return (
    <label>
      {label}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <input type="week" value={isoWeekInputValue(value)} onChange={(e) => onChange(mondayFromIsoWeek(e.target.value))} />
        <span className="muted">{weekLabel(value)}</span>
      </div>
    </label>
  );
}

export function ErrorText({ err }: { err: string }) {
  return err ? <div className="error">{err}</div> : null;
}

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const run = () => {
    setLoading(true);
    setErr("");
    fn().then(setData).catch((e) => setErr((e as Error).message)).finally(() => setLoading(false));
  };
  useEffect(run, deps); // eslint-disable-line react-hooks/exhaustive-deps
  return { data, err, loading, reload: run };
}
