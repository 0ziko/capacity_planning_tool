import { useEffect, useState } from "react";
import { api, type WorkCenter } from "./api";

export function useWorkCenters() {
  const [wcs, setWcs] = useState<WorkCenter[]>([]);
  const reload = () => api.get<WorkCenter[]>("/api/workcenters").then(setWcs);
  useEffect(() => {
    reload();
  }, []);
  return { wcs, reload };
}

export function WcMultiSelect({ wcs, value, onChange, onlyPlanned = false }: { wcs: WorkCenter[]; value: number[]; onChange: (v: number[]) => void; onlyPlanned?: boolean }) {
  const list = onlyPlanned ? wcs.filter((w) => w.is_planned) : wcs;
  return (
    <label>
      İş merkezleri (boş = {onlyPlanned ? "planlanan tümü" : "tümü"})
      <select multiple value={value.map(String)} onChange={(e) => onChange(Array.from(e.target.selectedOptions).map((o) => Number(o.value)))} style={{ minWidth: 200, minHeight: 90 }}>
        {list.map((w) => (
          <option key={w.id} value={w.id}>
            {w.code} — {w.name}
          </option>
        ))}
      </select>
    </label>
  );
}

export function UtilBadge({ u }: { u: number }) {
  const cls = u > 1.0001 ? "bad" : u > 0.9 ? "warn" : "ok";
  return <span className={`badge ${cls}`}>{Math.round(u * 100)}%</span>;
}

export function Bar({ ratio }: { ratio: number }) {
  const cls = ratio > 1.0001 ? "bad" : ratio > 0.9 ? "warn" : "";
  return (
    <div className="bar">
      <span className={cls} style={{ width: `${Math.min(ratio * 100, 100)}%` }} />
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
