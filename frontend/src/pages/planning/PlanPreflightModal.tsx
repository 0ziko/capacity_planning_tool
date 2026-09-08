import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmt, type AutoPlanRequest, type PlanPreflight } from "../../api";

function fmtDt(iso: string | null) {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? iso + "T12:00:00" : iso);
  return iso.length === 10
    ? d.toLocaleDateString("tr-TR")
    : d.toLocaleString("tr-TR", { dateStyle: "short", timeStyle: "short" });
}

export default function PlanPreflightModal({
  req,
  onClose,
  onConfirm,
}: {
  req: AutoPlanRequest;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const [data, setData] = useState<PlanPreflight | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(true);
  const [capAck, setCapAck] = useState(false);
  const [dailyAck, setDailyAck] = useState(false);

  useEffect(() => {
    setBusy(true);
    setErr("");
    api.post<PlanPreflight>("/api/plan/auto/preflight", req)
      .then(setData)
      .catch((e) => setErr((e as Error).message))
      .finally(() => setBusy(false));
  }, [JSON.stringify(req)]);

  const staleDaily = data?.daily_data.filter((d) => d.status !== "ok") ?? [];
  const allDailyOk = staleDaily.length === 0;

  const canProceed = data?.can_plan
    && (!data.needs_capacity_ack || capAck)
    && (!data.needs_daily_data_ack || dailyAck);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal panel" style={{ maxWidth: 720, width: "96vw" }} onClick={(e) => e.stopPropagation()}>
        <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
          <h2 style={{ margin: 0 }}>Planlama ön kontrol</h2>
          <button className="secondary small" onClick={onClose}>Kapat</button>
        </div>
        <p className="muted" style={{ marginTop: 4 }}>
          Otomatik planlamadan önce veri bütünlüğü kontrol edilir. Açık sipariş havuzunda rotası tanımlı olmayan stok varsa planlama engellenir; kapasite ve günlük veri uyarılarında onayınız gerekir.
        </p>

        {busy && <p className="muted">Kontroller çalışıyor…</p>}
        {err && <div className="error">{err}</div>}

        {data && (
          <>
            <p style={{ margin: "8px 0" }}>
              <b>{data.order_count}</b> açık sipariş / parti plan kapsamında.
              <span className="muted"> · Kontrol günü: {new Date(data.today + "T12:00:00").toLocaleDateString("tr-TR")}</span>
            </p>

            <section style={{ marginBottom: 14 }}>
              <h3 style={{ margin: "0 0 6px", color: data.no_routing.length ? "var(--bad)" : "var(--ok)" }}>
                1. Rota tanımı {data.no_routing.length ? `— ${data.no_routing.length} stok kodu açık siparişlerde rotası eksik` : "— tamam"}
              </h3>
              {data.no_routing.length === 0 ? (
                <p className="muted" style={{ margin: 0 }}>Plan kapsamındaki tüm açık sipariş ve partilerin rotası tanımlı.</p>
              ) : (
                <>
                  <p className="muted" style={{ margin: "0 0 8px", fontSize: "0.92em" }}>
                    Yalnızca açık sipariş / parti havuzu kontrol edilir; sistemde rotası olmayan ama talebi bulunmayan stok kodları bu listeye girmez.
                  </p>
                  <div className="table-wrap" style={{ maxHeight: 160 }}>
                    <table>
                      <thead><tr><th>Stok kodu</th><th>Ad</th><th className="num">Sipariş / parti</th><th>Örnek</th></tr></thead>
                      <tbody>
                        {data.no_routing.map((r) => (
                          <tr key={r.item_code}>
                            <td><b>{r.item_code}</b></td>
                            <td>{r.item_name || "—"}</td>
                            <td className="num">{r.order_count}</td>
                            <td className="muted">{r.order_nos.slice(0, 3).join(", ")}{r.order_nos.length > 3 ? "…" : ""}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="error" style={{ margin: "8px 0 0" }}>Açık siparişlerde rotası olmayan stok kodu varken planlama yapılamaz.</p>
                </>
              )}
            </section>

            <section style={{ marginBottom: 14 }}>
              <h3 style={{ margin: "0 0 6px", color: data.no_capacity.length ? "var(--warn)" : "var(--ok)" }}>
                2. İş merkezi kapasitesi {data.no_capacity.length ? `— ${data.no_capacity.length} uyarı` : "— tamam"}
              </h3>
              {data.no_capacity.length === 0 ? (
                <p className="muted" style={{ margin: 0 }}>İhtiyaç duyulan iş merkezlerinde planlanabilir kapasite var.</p>
              ) : (
                <>
                  <div className="table-wrap" style={{ maxHeight: 160 }}>
                    <table>
                      <thead><tr><th>İş merkezi</th><th className="num">İhtiyaç (sa)</th><th className="num">Kapasite (sa)</th><th className="num">Kişi</th><th>Açıklama</th></tr></thead>
                      <tbody>
                        {data.no_capacity.map((r) => (
                          <tr key={r.work_center_id}>
                            <td><b>{r.work_center_code}</b></td>
                            <td className="num">{fmt(r.needed_hours, 0)}</td>
                            <td className="num">{fmt(r.capacity_hours, 0)}</td>
                            <td className="num">{r.headcount}</td>
                            <td className="muted">{r.detail}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <label style={{ display: "flex", gap: 8, alignItems: "flex-start", marginTop: 8, cursor: "pointer" }}>
                    <input type="checkbox" checked={capAck} onChange={(e) => setCapAck(e.target.checked)} />
                    <span>Kapasite atanmamış iş merkezlerini biliyorum; plan çıktısını bu eksiklikleri göz önünde bulundurarak onaylayacağım.</span>
                  </label>
                </>
              )}
            </section>

            <section style={{ marginBottom: 14 }}>
              <h3 style={{ margin: "0 0 6px", color: allDailyOk ? "var(--ok)" : "var(--warn)" }}>
                3. Günlük operasyonel veri {allDailyOk ? "— tamam" : `— ${staleDaily.length} güncellenmeli`}
              </h3>
              <p className="muted" style={{ margin: "0 0 8px", fontSize: "0.92em" }}>
                Bitmiş stok, yarımamül (üretimden), üretim çıktıları ve açık siparişler bugün import edilmiş olmalı.
              </p>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Veri</th><th>Durum</th><th>Son import</th><th>Kullanıcı</th></tr></thead>
                  <tbody>
                    {data.daily_data.map((d) => (
                      <tr key={d.key}>
                        <td>
                          <Link to={`/imports?kind=${d.import_kind}`}>{d.label}</Link>
                        </td>
                        <td>
                          <span className={`badge ${d.status === "ok" ? "ok" : d.status === "missing" ? "bad" : "warn"}`}>
                            {d.status === "ok" ? "Güncel" : d.status === "missing" ? "Eksik" : "Güncellenmeli"}
                          </span>
                        </td>
                        <td className="muted">{fmtDt(d.last_import_at)}</td>
                        <td className="muted">{d.last_import_by || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {!allDailyOk && (
                <>
                  <ul style={{ margin: "8px 0", paddingLeft: 18, fontSize: "0.92em" }}>
                    {staleDaily.map((d) => (
                      <li key={d.key} className="muted">{d.detail}</li>
                    ))}
                  </ul>
                  <label style={{ display: "flex", gap: 8, alignItems: "flex-start", cursor: "pointer" }}>
                    <input type="checkbox" checked={dailyAck} onChange={(e) => setDailyAck(e.target.checked)} />
                    <span>Günlük verilerin bugün güncellenmediğini / eksik olduğunu biliyorum; planlamayı yine de onaylıyorum.</span>
                  </label>
                </>
              )}
            </section>

            <div className="row" style={{ justifyContent: "flex-end", gap: 8 }}>
              <button className="secondary" onClick={onClose}>Vazgeç</button>
              <button onClick={onConfirm} disabled={!canProceed}>
                {data.can_plan ? "Planlamayı başlat" : "Planlama yapılamaz"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
