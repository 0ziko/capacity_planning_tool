import { useEffect } from "react";
import { Link } from "react-router-dom";
import { api, type DataFreshness } from "./api";
import { useAsync } from "./components";

function fmtShort(iso: string | null) {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? iso + "T12:00:00" : iso);
  if (iso.length === 10) return d.toLocaleDateString("tr-TR");
  return d.toLocaleString("tr-TR", { dateStyle: "short", timeStyle: "short" });
}

function statusLabel(s: string) {
  if (s === "ok") return "Güncel";
  if (s === "stale") return "Güncellenmeli";
  return "Eksik";
}

export default function DataFreshnessBar() {
  const { data, reload, err } = useAsync(() => api.get<DataFreshness>("/api/data-freshness"), []);

  useEffect(() => {
    const tick = () => reload();
    window.addEventListener("focus", tick);
    window.addEventListener("data-imported", tick);
    const id = window.setInterval(tick, 5 * 60 * 1000);
    return () => {
      window.removeEventListener("focus", tick);
      window.removeEventListener("data-imported", tick);
      window.clearInterval(id);
    };
  }, [reload]);

  if (!data && !err) return null;

  const attention = data?.needs_attention ?? false;

  return (
    <div className={`data-freshness-bar${attention ? " attention" : ""}`} role="status" aria-live="polite">
      <div className="data-freshness-head">
        <strong>Günlük veri durumu</strong>
        {data && <span className="muted"> · {new Date(data.today + "T12:00:00").toLocaleDateString("tr-TR", { weekday: "long", day: "numeric", month: "long" })}</span>}
        {attention && <span className="data-freshness-alert"> — Bugün güncellenmesi gereken veri var</span>}
        <Link to="/imports" className="data-freshness-link">Import sayfası →</Link>
      </div>
      {err && <div className="error" style={{ marginTop: 6 }}>{err}</div>}
      {data && (
        <div className="data-freshness-grid">
          {data.checkpoints.map((c) => (
            <Link
              key={c.key}
              to={`/imports?kind=${c.import_kind}`}
              className={`data-freshness-item ${c.status}`}
              title={c.detail}
            >
              <span className={`data-freshness-dot ${c.status}`} />
              <span className="data-freshness-label">{c.label}</span>
              <span className={`data-freshness-status ${c.status}`}>{statusLabel(c.status)}</span>
              <span className="data-freshness-meta muted">
                {c.last_import_at ? fmtShort(c.last_import_at) : "Import yok"}
                {c.last_import_by ? ` · ${c.last_import_by}` : ""}
              </span>
              {c.status !== "ok" && <span className="data-freshness-warn">{c.detail}</span>}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
