import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import WcWeeksPanel from "../WcWeeksPanel";
import { api, fmt, weekLabel, type AutoPlanRequest, type PlanPreflight } from "../../api";

const laborLabels: Record<string, string> = { headcount: "Kişi", efficient_hours_per_person: "Verimli saat / kişi", working_days: "Çalışma günü", line_hours_per_day: "Günlük hat saati" };

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
  preflightUrl = "/api/plan/auto/preflight",
  revision = false,
}: {
  req: AutoPlanRequest;
  onClose: () => void;
  onConfirm: (missingHeadcountAck: string | null) => void;
  preflightUrl?: string;
  revision?: boolean;
}) {
  const [data, setData] = useState<PlanPreflight | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(true);
  const [capAck, setCapAck] = useState(false);
  const [dailyAck, setDailyAck] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [checkVersion, setCheckVersion] = useState(0);
  const [editing, setEditing] = useState<{ id: number; code: string; week: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const editorRef = useRef<HTMLElement>(null);
  useEffect(() => { editorRef.current?.scrollIntoView({ block: "nearest" }); }, [editing]);
  const exportRoutes = async () => {
    setExporting(true); setErr("");
    try { await api.download("/api/plan/auto/preflight/no-routing.xlsx", "eksik_rotalar.xlsx", req); }
    catch (e) { setErr((e as Error).message); }
    finally { setExporting(false); }
  };

  useEffect(() => {
    let active = true;
    setBusy(true);
    setErr("");
    setData(null);
    setCapAck(false);
    setDailyAck(false);
    api.post<PlanPreflight>(preflightUrl, req)
      .then((result) => { if (active) setData(result); })
      .catch((e) => { if (active) setErr((e as Error).message); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [JSON.stringify(req), checkVersion, preflightUrl]);

  const staleDaily = data?.daily_data.filter((d) => d.status !== "ok") ?? [];
  const allDailyOk = staleDaily.length === 0;

  const canProceed = !busy && !editing && !saving && data?.can_plan
    && (!data.needs_capacity_ack || capAck)
    && (!data.needs_daily_data_ack || dailyAck);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal panel" style={{ maxWidth: 720, width: "96vw" }} onClick={(e) => e.stopPropagation()}>
        <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
          <h2 style={{ margin: 0 }}>{revision ? "Revizyon onay ön kontrolü" : "Seçili ufku yeniden planla"}</h2>
          <button className="secondary small" onClick={onClose}>Kapat</button>
        </div>
        <p className="muted" style={{ marginTop: 4 }}>
          {revision ? "Kontrol taslak değişikliklerini içerir. İş gücü girişlerini burada değiştirirseniz revizyonu yeniden hesaplamanız gerekir." : "Yalnızca seçili tarih aralığındaki otomatik plan satırları yenilenir; ufuk dışındaki planlar korunur. Ön kontrol tamamlandıktan sonra onayınızla uygulanır."}
        </p>

        {busy && <p className="muted">Kontroller çalışıyor…</p>}
        {err && <div className="error">{err}</div>}
        {editing && (
          <section ref={editorRef}>
            <h3>Eksik haftalık iş gücünü tamamla</h3>
            <WcWeeksPanel wcId={editing.id} wcCode={editing.code} start={editing.week} weeks={1} canEdit
              onSavingChange={setSaving} onChanged={() => { setCapAck(false); }} />
            <button disabled={saving} onClick={() => { setEditing(null); setCheckVersion((v) => v + 1); }}>
              {saving ? "Kaydediliyor…" : "Düzenlemeyi bitir ve yeniden kontrol et"}
            </button>
          </section>
        )}

        {data && (
          <>
            {!revision && <section style={{ marginBottom: 14, padding: "10px 12px", background: "var(--panel-alt, rgba(0,0,0,0.04))", borderRadius: 8 }}>
              <h3 style={{ margin: "0 0 8px" }}>Yeniden planlama kapsamı</h3>
              <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "4px 12px", fontSize: "0.95em" }}>
                <span className="muted">Tarih aralığı</span>
                <span>
                  {new Date(data.replace_scope.horizon_start + "T12:00:00").toLocaleDateString("tr-TR")}
                  {" – "}
                  {new Date(data.replace_scope.horizon_end_inclusive + "T12:00:00").toLocaleDateString("tr-TR")}
                </span>
                <span className="muted">İş merkezleri</span>
                <span>{data.replace_scope.work_center_codes.length ? data.replace_scope.work_center_codes.join(", ") : "Planlanan tüm merkezler"}</span>
                <span className="muted">Değiştirilecek modlar</span>
                <span>{data.replace_scope.replace_existing ? (data.replace_scope.replace_modes.join(", ") || "—") : "Mevcut otomatik plan korunur (ekleme)"}</span>
                <span className="muted">Etkilenecek satır</span>
                <span><b>{data.replace_scope.lines_to_replace}</b> plan satırı silinip yeniden yazılacak</span>
              </div>
            </section>}

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
                    <button className="secondary small" onClick={exportRoutes} disabled={exporting}>{exporting ? "Excel hazırlanıyor…" : "Eksik rotaları Excel’e aktar"}</button>
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
              <h3 style={{ margin: "0 0 6px", color: data.needs_capacity_ack ? "var(--warn)" : "var(--ok)" }}>
                2. İş merkezi kapasitesi {data.needs_capacity_ack ? "— kontrol edilmeli" : "— tamam"}
              </h3>
              {data.no_capacity.length === 0 ? (
                <p className="muted" style={{ margin: 0 }}>İhtiyaç duyulan iş merkezlerinde seçili haftaların tamamında kapasite var. Bu kontrol tüm ihtiyacın sığacağını garanti etmez.</p>
              ) : (
                <>
                  <div className="table-wrap" style={{ maxHeight: 160 }}>
                    <table>
                      <thead><tr><th>İş merkezi</th><th>Hafta</th><th className="num">Toplam brüt ihtiyaç (sa)</th><th className="num">Hafta kapasitesi (sa)</th><th className="num">Kişi</th><th>Açıklama</th></tr></thead>
                      <tbody>
                        {data.no_capacity.map((r) => (
                          <tr key={`${r.work_center_id}-${r.week_start}`}>
                            <td><b>{r.work_center_code}</b></td>
                            <td>{r.week_start ? weekLabel(r.week_start) : "—"}</td>
                            <td className="num">{fmt(r.needed_hours, 0)}</td>
                            <td className="num">{fmt(r.capacity_hours, 0)}</td>
                            <td className="num">{r.headcount}</td>
                            <td className="muted">{r.detail}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
              {(data.missing_labor_weeks ?? []).length > 0 && (
                <>
                  <p className="muted">Önce eksik girişleri düzeltebilirsiniz. Kişi sayısı boş kalan haftalar yalnızca aşağıdaki onayınızı verirseniz sıfır kapasiteyle planlanır; boş kayıtlar sıfır olarak kaydedilmez. Saat ve gün boşsa mevcut varsayılanları kullanılır.</p>
                  <div className="table-wrap" style={{ maxHeight: 180 }}>
                    <table>
                      <thead><tr><th>İş merkezi</th><th>Hafta</th><th>Eksik alanlar</th><th className="num">Devam edilirse kapasite (sa)</th><th></th></tr></thead>
                      <tbody>{data.missing_labor_weeks.map((r) => (
                        <tr key={`${r.work_center_id}-${r.week_start}`}>
                          <td>{r.work_center_code}</td><td>{weekLabel(r.week_start)}</td>
                          <td>{r.missing_fields.map((field) => laborLabels[field] ?? field).join(", ")}</td>
                          <td className="num">{fmt(r.capacity_hours, 1)}</td>
                          <td><button className="secondary small" disabled={!!editing} onClick={() => { setCapAck(false); setEditing({ id: r.work_center_id, code: r.work_center_code, week: r.week_start }); }}>Düzelt</button></td>
                        </tr>
                      ))}</tbody>
                    </table>
                  </div>
                </>
              )}
              {data.needs_capacity_ack && (
                <label style={{ display: "flex", gap: 8, alignItems: "flex-start", marginTop: 8, cursor: "pointer" }}>
                  <input type="checkbox" checked={capAck} disabled={!!editing || saving} onChange={(e) => setCapAck(e.target.checked)} />
                  <span>Eksikleri gördüm. Kişi sayısı boş kalan haftalarda kapasitenin sıfır kabul edilmesini ve diğer kapasite uyarılarıyla devam etmeyi onaylıyorum.</span>
                </label>
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
                  <thead><tr><th>Veri</th><th>Durum</th><th>Son işlem</th><th>Kullanıcı</th></tr></thead>
                  <tbody>
                    {data.daily_data.map((d) => (
                      <tr key={d.key}>
                        <td>
                          <Link to={`/imports?kind=${d.import_kind}`}>{d.label}</Link>
                        </td>
                        <td>
                          <span className={`badge ${d.update_source === "manual_ack" ? "info" : d.status === "ok" ? "ok" : d.status === "missing" ? "bad" : "warn"}`}>
                            {d.update_source === "manual_ack" ? "Manuel onay" : d.status === "ok" ? "Güncel" : d.status === "missing" ? "Eksik" : "Güncellenmeli"}
                          </span>
                        </td>
                        <td className="muted">
                          {d.update_source === "manual_ack" && d.confirmed_no_change_at
                            ? fmtDt(d.confirmed_no_change_at)
                            : fmtDt(d.last_import_at)}
                        </td>
                        <td className="muted">
                          {d.update_source === "manual_ack"
                            ? d.confirmed_no_change_by || "—"
                            : d.last_import_by || "—"}
                        </td>
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
              <button onClick={() => onConfirm(data.missing_headcount_token)} disabled={!canProceed}>
                {data.can_plan ? (revision ? "Revizyonu onayla ve devreye al" : "Seçili ufku yeniden planla") : "Planlama yapılamaz"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
