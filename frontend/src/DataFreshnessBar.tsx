import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type DataFreshness, type DataFreshnessCheckpoint } from "./api";
import { useAsync } from "./components";

function fmtShort(iso: string | null) {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? iso + "T12:00:00" : iso);
  if (iso.length === 10) return d.toLocaleDateString("tr-TR");
  return d.toLocaleString("tr-TR", { dateStyle: "short", timeStyle: "short" });
}

function statusLabel(c: DataFreshnessCheckpoint) {
  if (c.update_source === "manual_ack") return "Manuel onay";
  if (c.status === "ok") return "Güncel";
  if (c.status === "stale") return "Güncellenmeli";
  return "Eksik";
}

function itemClass(c: DataFreshnessCheckpoint) {
  if (c.update_source === "manual_ack") return "ok manual-ack";
  return c.status;
}

export default function DataFreshnessBar() {
  const { data, reload, err } = useAsync(() => api.get<DataFreshness>("/api/data-freshness"), []);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [confirmErr, setConfirmErr] = useState<string | null>(null);

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

  const onConfirmNoChange = useCallback(
    async (checkpointKey: string) => {
      setConfirmErr(null);
      setConfirming(checkpointKey);
      try {
        await api.post<DataFreshness>(`/api/data-freshness/checkpoints/${checkpointKey}/confirm-no-change`);
        reload();
      } catch (e) {
        setConfirmErr(e instanceof Error ? e.message : "Onay kaydedilemedi");
      } finally {
        setConfirming(null);
      }
    },
    [reload],
  );

  if (!data && !err) return null;

  const attention = data?.needs_attention ?? false;

  return (
    <div className={`data-freshness-bar${attention ? " attention" : ""}`} role="status" aria-live="polite">
      <div className="data-freshness-head">
        <strong>Günlük veri durumu</strong>
        {data && (
          <span className="muted">
            {" · "}
            {new Date(data.today + "T12:00:00").toLocaleDateString("tr-TR", { weekday: "long", day: "numeric", month: "long" })}
          </span>
        )}
        {attention && <span className="data-freshness-alert"> — Bugün güncellenmesi gereken veri var</span>}
        <Link to="/imports" className="data-freshness-link">
          Import sayfası →
        </Link>
      </div>
      {err && <div className="error" style={{ marginTop: 6 }}>{err}</div>}
      {confirmErr && <div className="error" style={{ marginTop: 6 }}>{confirmErr}</div>}
      {data && (
        <div className="data-freshness-grid">
          {data.checkpoints.map((c) => (
            <div key={c.key} className={`data-freshness-item ${itemClass(c)}`} title={c.detail}>
              <span className={`data-freshness-dot ${itemClass(c)}`} />
              <Link to={`/imports?kind=${c.import_kind}`} className="data-freshness-label">
                {c.label}
              </Link>
              <span className={`data-freshness-status ${itemClass(c)}`}>{statusLabel(c)}</span>
              {c.update_source === "manual_ack" && c.confirmed_no_change_at ? (
                <span className="data-freshness-meta muted">
                  <span className="data-freshness-manual-tag">Manuel onay</span>
                  {" · "}
                  {fmtShort(c.confirmed_no_change_at)}
                  {c.confirmed_no_change_by ? ` · ${c.confirmed_no_change_by}` : ""}
                </span>
              ) : (
                <span className="data-freshness-meta muted">
                  {c.last_import_at ? fmtShort(c.last_import_at) : "Import yok"}
                  {c.last_import_by ? ` · ${c.last_import_by}` : ""}
                </span>
              )}
              {c.update_source === "manual_ack" && c.last_import_at && (
                <span className="data-freshness-submeta muted">
                  Son import: {fmtShort(c.last_import_at)}
                  {c.last_import_by ? ` · ${c.last_import_by}` : ""}
                </span>
              )}
              {c.status !== "ok" && <span className="data-freshness-warn">{c.detail}</span>}
              {c.can_confirm_no_change && (
                <button
                  type="button"
                  className="data-freshness-confirm-btn"
                  disabled={confirming === c.key}
                  onClick={() => void onConfirmNoChange(c.key)}
                >
                  {confirming === c.key ? "Kaydediliyor…" : "Bugün değişiklik yok — onayla"}
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
