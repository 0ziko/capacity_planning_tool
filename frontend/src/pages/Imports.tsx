import { useState } from "react";
import { api, type ImportKind, type ImportResult } from "../api";
import { useAuth } from "../auth";
import { ErrorText, useAsync } from "../components";

interface LogRow { id: number; kind: string; filename: string; username: string; inserted: number; updated: number; errors: string[]; created_at: string }

const ORDER = ["workcenters", "machines", "shifts", "wc_weeks", "employees", "items", "bom", "routing", "op_rules", "orders", "production", "downtime", "stock_receipts"];
const HINT: Record<string, string> = {
  workcenters: "Önce iş merkezleri. Alan Kodu/Adı ile gruplanır; Kapasite Kaynağı: İM (personel/vardiya) veya Makine (makine atamaları).",
  machines: "İş merkezi altındaki makineler. Makine kodu benzersizdir.",
  shifts: "Günler: 0=Pzt … 6=Paz (örn. 0,1,2,3,4). Kişi sayısı 0 ise personel listesinden sayılır.",
  wc_weeks: "Haftaya özel iş gücü: Hafta = Pazartesi tarihi ya da 2026-W37. Boş bırakılan alan varsayılanı korur; tüm alanlar boşsa istisna silinir.",
  employees: "Kimin hangi iş merkezinde (ve isteğe bağlı hangi makinede) çalıştığı → verimli kapasite.",
  items: "Stok kodları (BOM/rota yüklerken bilinmeyen kodlar otomatik oluşturulur).",
  bom: "Hammadde satırları.",
  routing: "Aşamalı tezgah sırası + çevrim süresi (sn/adet). Kapasite ihtiyacının kaynağı.",
  op_rules: "Senaryo matrisi: Kural = Bitiş (önceki bitince) ya da Çevrim (önceki N çevrim tamamlayınca). Stok Kodu boşsa ürün grubu geneli.",
  orders: "Aynı Sipariş No + Stok Kodu tekrar yüklenirse güncellenir.",
  production: "Bir önceki günün üretimi. Aynı gün/iş merkezi/stok/op/sipariş satırı üzerine yazılır.",
  downtime: "Bir günün duruşları yeniden yüklenirse o gün/iş merkezi için eskiler silinir.",
  stock_receipts: "Depoya giren bitmiş ürün (siparişten bağımsız). Rezervasyon Stok & Rezervasyon sayfasından yapılır.",
};

export default function Imports() {
  const { can } = useAuth();
  const kinds = useAsync(() => api.get<ImportKind[]>("/api/imports/kinds"), []);
  const log = useAsync(() => api.get<LogRow[]>("/api/imports/log?limit=30"), []);
  const [results, setResults] = useState<Record<string, ImportResult | string>>({});
  const [busy, setBusy] = useState<string | null>(null);

  const upload = async (kind: string, file: File | undefined) => {
    if (!file) return;
    setBusy(kind);
    try {
      const r = await api.upload<ImportResult>(`/api/imports/${kind}`, file);
      setResults((s) => ({ ...s, [kind]: r }));
      log.reload();
    } catch (e) {
      setResults((s) => ({ ...s, [kind]: (e as Error).message }));
    } finally {
      setBusy(null);
    }
  };
  const sorted = [...(kinds.data ?? [])].sort((a, b) => ORDER.indexOf(a.kind) - ORDER.indexOf(b.kind));

  return (
    <>
      <h1>Excel Import / Yedek</h1>
      <div className="panel row">
        <button onClick={() => api.download("/api/backup.xlsx", "kapasite_yedek.xlsx")}>⬇ Tek tıkla Excel yedeği (tüm tablolar)</button>
        <span className="muted">Yedek sayfaları import şablonlarıyla aynı formattadır; gerekirse geri yüklenebilir.</span>
      </div>
      <ErrorText err={kinds.err} />
      <div className="grid">
        {sorted.map((k, i) => {
          const r = results[k.kind];
          return (
            <div className="panel" key={k.kind}>
              <h2 style={{ marginTop: 0 }}>{i + 1}. {k.title}</h2>
              <div className="muted" style={{ marginBottom: 6 }}>{HINT[k.kind]}</div>
              <div className="muted">Sütunlar: {k.columns.map((c) => <span key={c} style={{ fontWeight: k.required.includes(c) ? 700 : 400 }}>{c}; </span>)}</div>
              <div className="row" style={{ marginTop: 8 }}>
                <button className="secondary small" onClick={() => api.download(`/api/imports/template/${k.kind}`, `sablon_${k.kind}.xlsx`)}>Şablon indir</button>
                {can("poweruser") && (
                  <label style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                    <input type="file" accept=".xlsx" disabled={busy === k.kind} onChange={(e) => { upload(k.kind, e.target.files?.[0]); e.target.value = ""; }} />
                  </label>
                )}
              </div>
              {typeof r === "string" && <div className="error">{r}</div>}
              {r && typeof r !== "string" && (
                <>
                  <div className="success">Eklendi: {r.inserted} · Güncellendi: {r.updated} · Hata: {r.errors.length}</div>
                  {r.errors.length > 0 && <ul className="errors">{r.errors.slice(0, 50).map((e, j) => <li key={j}>{e}</li>)}</ul>}
                </>
              )}
            </div>
          );
        })}
      </div>

      <h2>Son importlar</h2>
      <div className="table-wrap" style={{ maxHeight: 300 }}>
        <table>
          <thead><tr><th>Tarih</th><th>Tür</th><th>Dosya</th><th>Kullanıcı</th><th className="num">Eklenen</th><th className="num">Güncellenen</th><th className="num">Hata</th></tr></thead>
          <tbody>{log.data?.map((l) => <tr key={l.id} title={l.errors.join("\n")}><td>{l.created_at?.replace("T", " ").slice(0, 16)}</td><td>{l.kind}</td><td>{l.filename}</td><td>{l.username}</td><td className="num">{l.inserted}</td><td className="num">{l.updated}</td><td className="num" style={{ color: l.errors.length ? "var(--bad)" : undefined }}>{l.errors.length}</td></tr>)}</tbody>
        </table>
      </div>
    </>
  );
}
