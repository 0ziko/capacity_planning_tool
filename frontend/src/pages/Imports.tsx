import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, fmt, type ImportKind, type ImportResult, type OrderImportPreview } from "../api";
import { useAuth } from "../auth";
import { ErrorText, useAsync } from "../components";

interface LogRow { id: number; kind: string; filename: string; username: string; inserted: number; updated: number; errors: string[]; created_at: string }

const ORDER = ["workcenters", "istasyonlar", "machines", "production_bom", "shifts", "wc_weeks", "employees", "items", "bom", "routing", "op_rules", "orders", "production", "downtime", "stock_receipts"];
const HINT: Record<string, string> = {
  workcenters: "Önce iş merkezleri. Alan Kodu/Adı ile gruplanır; Kapasite Kaynağı: İM (personel/vardiya) veya Makine (makine atamaları).",
  istasyonlar: "İstasyonlar.xlsx formatı: istasyon kodu, tanım, bağlı iş merkezi adı. Listedeki olmayan istasyonlar pasif yapılır.",
  machines: "İş merkezi altındaki istasyon/makine listesi. Kod benzersizdir; wc_code veya iş merkezi adı ile eşleşir.",
  production_bom: "ERP RECETELER (BOM.xlsx): tüm mamul rotası ve BOM tek seferde yüklenir. Önce istasyon import önerilir.",
  shifts: "Günler: 0=Pzt … 6=Paz (örn. 0,1,2,3,4). Kişi sayısı 0 ise personel listesinden sayılır.",
  wc_weeks: "Haftaya özel iş gücü: Hafta = Pazartesi tarihi ya da 2026-W37. Boş bırakılan alan varsayılanı korur; tüm alanlar boşsa istisna silinir.",
  employees: "Kimin hangi iş merkezinde (ve isteğe bağlı hangi makinede) çalıştığı → verimli kapasite.",
  items: "Stok kodları (BOM/rota yüklerken bilinmeyen kodlar otomatik oluşturulur).",
  bom: "Hammadde satırları.",
  routing: "Aşamalı tezgah sırası + çevrim süresi (sn/adet). Kapasite ihtiyacının kaynağı.",
  op_rules: "Senaryo matrisi: Kural = Bitiş (önceki bitince) ya da Çevrim (önceki N çevrim tamamlayınca). Stok Kodu boşsa ürün grubu geneli.",
  orders: "Günlük açık sipariş listesi: önce fark özeti gösterilir; hata varsa Excel raporu indirilip düzeltilir. Eşleşen satırlar güncellenir; listede olmayan açık siparişleri silmek isteğe bağlıdır.",
  production: "Günlük üretim beyanı. Yarımamül kodu + miktar yeterli; sipariş no zorunlu değildir. Boş bırakılırsa üretim belirli bir siparişe bağlanmaz — açık siparişlere termin sırasıyla (FIFO) dağıtılır. Aynı yarımamül kodu birden fazla rotada olsa bile stok kodu zorunlu değildir; plan önceliğine göre otomatik dağıtılır. Bitmiş stok bağlantısı ayrı yapılır.",
  downtime: "Bir günün duruşları yeniden yüklenirse o gün/iş merkezi için eskiler silinir.",
  stock_receipts: "Depoya giren bitmiş ürün (siparişten bağımsız). Rezervasyon Stok & Rezervasyon sayfasından yapılır.",
};

export default function Imports() {
  const { can } = useAuth();
  const [searchParams] = useSearchParams();
  const highlightKind = searchParams.get("kind") || "";
  const kinds = useAsync(() => api.get<ImportKind[]>("/api/imports/kinds"), []);
  const log = useAsync(() => api.get<LogRow[]>("/api/imports/log?limit=30"), []);
  const [results, setResults] = useState<Record<string, ImportResult | string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [orderDraft, setOrderDraft] = useState<{ file: File; preview: OrderImportPreview } | null>(null);
  const [orderPreviewErr, setOrderPreviewErr] = useState("");

  const upload = async (kind: string, file: File | undefined, removeMissing = false) => {
    if (!file) return;
    setBusy(kind);
    try {
      const params = kind === "orders" && removeMissing ? { remove_missing: true } : undefined;
      const r = await api.upload<ImportResult>(`/api/imports/${kind}`, file, params);
      setResults((s) => ({ ...s, [kind]: r }));
      log.reload();
      if (!r.errors.length) window.dispatchEvent(new Event("data-imported"));
    } catch (e) {
      setResults((s) => ({ ...s, [kind]: (e as Error).message }));
    } finally {
      setBusy(null);
    }
  };

  const pickOrdersFile = async (file: File | undefined) => {
    if (!file) return;
    setOrderPreviewErr("");
    setBusy("orders-preview");
    try {
      const preview = await api.upload<OrderImportPreview>("/api/imports/orders/preview", file);
      setOrderDraft({ file, preview });
    } catch (e) {
      setOrderPreviewErr((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const confirmOrdersImport = async (removeMissing: boolean) => {
    if (!orderDraft) return;
    const { file } = orderDraft;
    setOrderDraft(null);
    await upload("orders", file, removeMissing);
  };

  const sorted = [...(kinds.data ?? [])].sort((a, b) => ORDER.indexOf(a.kind) - ORDER.indexOf(b.kind));

  useEffect(() => {
    if (!highlightKind) return;
    const el = document.getElementById(`import-kind-${highlightKind}`);
    el?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [highlightKind, kinds.data]);

  return (
    <>
      <h1>Excel Import / Yedek</h1>
      <div className="panel row">
        <button onClick={() => api.download("/api/backup.xlsx", "kapasite_yedek.xlsx")}>⬇ Tek tıkla Excel yedeği (tüm tablolar)</button>
        <span className="muted">Yedek sayfaları import şablonlarıyla aynı formattadır; gerekirse geri yüklenebilir.</span>
      </div>
      <ErrorText err={kinds.err || orderPreviewErr} />
      <div className="grid">
        {sorted.map((k, i) => {
          const r = results[k.kind];
          return (
            <div className="panel" key={k.kind} id={`import-kind-${k.kind}`} style={highlightKind === k.kind ? { borderColor: "var(--warn)", boxShadow: "0 0 0 1px var(--warn)" } : undefined}>
              <h2 style={{ marginTop: 0 }}>{i + 1}. {k.title}</h2>
              <div className="muted" style={{ marginBottom: 6 }}>{HINT[k.kind]}</div>
              <div className="muted">Sütunlar: {k.columns.map((c) => <span key={c} style={{ fontWeight: k.required.includes(c) ? 700 : 400 }}>{c}; </span>)}</div>
              <div className="row" style={{ marginTop: 8 }}>
                <button className="secondary small" onClick={() => api.download(`/api/imports/template/${k.kind}`, `sablon_${k.kind}.xlsx`)}>Şablon indir</button>
                {can("poweruser") && (
                  <label style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                    <input
                      type="file"
                      accept=".xlsx"
                      disabled={busy === k.kind || busy === "orders-preview"}
                      onChange={(e) => {
                        const f = e.target.files?.[0];
                        e.target.value = "";
                        if (k.kind === "orders") pickOrdersFile(f);
                        else upload(k.kind, f);
                      }}
                    />
                  </label>
                )}
              </div>
              {typeof r === "string" && <div className="error">{r}</div>}
              {r && typeof r !== "string" && (
                <>
                  <div className="success">
                    Eklendi: {r.inserted} · Güncellendi: {r.updated}
                    {(r.removed ?? 0) > 0 && <> · Silindi: {r.removed}</>}
                    {" "}· Hata: {r.errors.length}
                  </div>
                  {r.errors.length > 0 && <ul className="errors">{r.errors.slice(0, 50).map((e, j) => <li key={j}>{e}</li>)}</ul>}
                </>
              )}
            </div>
          );
        })}
      </div>

      {orderDraft && (
        <OrderImportDialog
          file={orderDraft.file}
          preview={orderDraft.preview}
          busy={busy === "orders"}
          onCancel={() => setOrderDraft(null)}
          onConfirm={confirmOrdersImport}
        />
      )}

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

function OrderImportDialog({
  file,
  preview,
  busy,
  onCancel,
  onConfirm,
}: {
  file: File;
  preview: OrderImportPreview;
  busy: boolean;
  onCancel: () => void;
  onConfirm: (removeMissing: boolean) => void;
}) {
  const [removeMissing, setRemoveMissing] = useState(preview.only_in_system.length > 0);
  const [dlBusy, setDlBusy] = useState(false);
  const blocked = preview.parse_errors.length > 0;
  const errShow = preview.parse_errors.slice(0, 25);
  const errMore = preview.parse_errors.length - errShow.length;

  const downloadReport = async () => {
    setDlBusy(true);
    try {
      await api.uploadDownload("/api/imports/orders/preview.xlsx", file);
    } finally {
      setDlBusy(false);
    }
  };

  return (
    <div className="panel" style={{ marginTop: 16, borderLeft: "4px solid var(--primary)" }}>
      <h2 style={{ marginTop: 0 }}>Sipariş importu — önizleme</h2>
      <p className="muted" style={{ marginTop: 0 }}>
        Dosya: <b>{file.name}</b> · {preview.file_row_count} satır · sistemde {preview.system_open_count} açık sipariş ·
        {" "}{preview.only_in_file.length} yeni · {preview.updated.length} güncellenecek · {preview.unchanged_count} değişmeden kalacak
        {preview.only_in_system.length > 0 && <> · <span style={{ color: "var(--warn)" }}>{preview.only_in_system.length} sistemde var, listede yok</span></>}
      </p>

      <div className="row" style={{ marginBottom: 10 }}>
        <button className="secondary" onClick={downloadReport} disabled={dlBusy || busy}>
          {dlBusy ? "Hazırlanıyor…" : "Karşılaştırma raporunu Excel indir"}
        </button>
        <span className="muted">Hataları düzeltmek için <b>Düzenle ve yükle</b> sayfasını kullanın; termin sütunu YYYY-MM-DD formatındadır. Düzeltilmiş dosyayı doğrudan tekrar yükleyebilirsiniz.</span>
      </div>

      {blocked && (
        <div className="error" style={{ marginBottom: 10 }}>
          <b>Import onaylanamaz — {preview.error_rows.length || preview.parse_errors.length} satırda hata var.</b>
          <p style={{ margin: "6px 0" }}>
            En sık neden: Excel&apos;deki <b>stok kodu</b> sistemde tanımlı değil
            {preview.missing_item_codes.length > 0 && (
              <> ({preview.missing_item_codes.length} farklı kod: {preview.missing_item_codes.slice(0, 8).join(", ")}{preview.missing_item_codes.length > 8 ? "…" : ""})</>
            )}.
            Önce <b>Stok Kodları</b> importu ile eksik kodları ekleyin veya Excel raporundaki <b>Hatalar</b> sayfasında kodları düzeltin.
          </p>
          <ul className="errors">
            {errShow.map((e, i) => <li key={i}>{e}</li>)}
            {errMore > 0 && <li className="muted">… ve {errMore} hata daha (tam listeyi Excel raporunda görün)</li>}
          </ul>
        </div>
      )}

      {preview.only_in_system.length > 0 && (
        <>
          <h3 style={{ color: "var(--warn)", marginBottom: 6 }}>Sistemde var, yeni listede yok ({preview.only_in_system.length})</h3>
          <p className="muted" style={{ marginTop: 0 }}>Bu açık siparişler günlük listenizde yer almıyor. Silmek isterseniz aşağıdaki kutuyu işaretleyin; plan satırları ve rezervasyonları da kaldırılır.</p>
          <PreviewTable rows={preview.only_in_system} />
        </>
      )}

      {preview.only_in_file.length > 0 && (
        <>
          <h3 style={{ color: "var(--ok)", marginBottom: 6 }}>Listede var, sistemde yok — eklenecek ({preview.only_in_file.length})</h3>
          <PreviewTable rows={preview.only_in_file} showRow />
        </>
      )}

      {preview.updated.length > 0 && (
        <>
          <h3 style={{ marginBottom: 6 }}>Her iki tarafta var — güncellenecek ({preview.updated.length})</h3>
          <div className="table-wrap" style={{ maxHeight: 220 }}>
            <table>
              <thead><tr><th>Sipariş</th><th>Poz</th><th>Stok</th><th>Müşteri</th><th>Termin</th><th className="num">Miktar</th><th>Değişiklikler</th></tr></thead>
              <tbody>
                {preview.updated.map((r, i) => (
                  <tr key={i}>
                    <td><b>{r.order_no}</b></td>
                    <td>{r.position_no || "—"}</td>
                    <td>{r.item_code}</td>
                    <td>{r.customer || "—"}</td>
                    <td>{r.due_date ?? "—"}</td>
                    <td className="num">{r.quantity != null ? fmt(r.quantity, 0) : "—"}</td>
                    <td className="muted">{r.changes.join(" · ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {preview.only_in_system.length === 0 && preview.only_in_file.length === 0 && preview.updated.length === 0 && preview.unchanged_count > 0 && !blocked && (
        <p className="muted">Tüm satırlar sistemle aynı; yine de dosyayı içe aktarabilirsiniz (değişiklik olmaz).</p>
      )}

      <div className="row" style={{ alignItems: "center", marginTop: 12 }}>
        {preview.only_in_system.length > 0 && (
          <label style={{ flexDirection: "row", alignItems: "center", gap: 8, fontWeight: 600 }}>
            <input type="checkbox" checked={removeMissing} onChange={(e) => setRemoveMissing(e.target.checked)} disabled={blocked} />
            Listede olmayan {preview.only_in_system.length} açık siparişi sistemden sil
          </label>
        )}
        <button onClick={() => onConfirm(removeMissing && preview.only_in_system.length > 0)} disabled={busy || blocked}>
          {busy ? "Yükleniyor…" : "Importu onayla"}
        </button>
        <button className="secondary" onClick={onCancel} disabled={busy}>Vazgeç</button>
      </div>
    </div>
  );
}

function PreviewTable({ rows, showRow }: { rows: OrderImportPreview["only_in_system"]; showRow?: boolean }) {
  return (
    <div className="table-wrap" style={{ maxHeight: 220, marginBottom: 12 }}>
      <table>
        <thead>
          <tr>
            {showRow && <th>Satır</th>}
            <th>Sipariş</th><th>Poz</th><th>Stok</th><th>Müşteri</th><th>Termin</th><th className="num">Miktar</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.order_id ?? `${r.order_no}-${r.position_no}-${i}`}>
              {showRow && <td className="muted">{r.excel_row ?? "—"}</td>}
              <td><b>{r.order_no}</b></td>
              <td>{r.position_no || "—"}</td>
              <td>{r.item_code}</td>
              <td>{r.customer || "—"}</td>
              <td>{r.due_date ?? "—"}</td>
              <td className="num">{r.quantity != null ? fmt(r.quantity, 0) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
