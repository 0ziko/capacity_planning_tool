import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type LineDizilimPreview, type LineDizilimResult, fmt, type ImportKind, type ImportResult, type OrderImportPreview } from "../api";
import { useAuth } from "../auth";
import { ErrorText, useAsync } from "../components";

interface LogRow { id: number; kind: string; filename: string; username: string; inserted: number; updated: number; errors: string[]; created_at: string }

const ORDER = ["workcenters", "istasyonlar", "machines", "production_bom", "laser_times", "shifts", "wc_weeks", "items", "bom", "routing", "op_rules", "orders", "production", "downtime", "stock_receipts"];
const HINT: Record<string, string> = {
  workcenters: "Önce iş merkezleri. Alan Kodu/Adı ile gruplanır. İş gücü kapasitesi haftalık kişi girişinden hesaplanır; eski Kapasite Kaynağı alanı bu hesabı değiştirmez.",
  istasyonlar: "İstasyonlar.xlsx formatı: istasyon kodu, tanım, bağlı iş merkezi adı. Listedeki olmayan istasyonlar pasif yapılır.",
  machines: "İş merkezi altındaki istasyon/makine listesi. Kod benzersizdir; wc_code veya iş merkezi adı ile eşleşir.",
  production_bom: "ERP RECETELER (BOM.xlsx): tüm mamul rotası ve BOM tek seferde yüklenir. Önce istasyon import önerilir.",
  laser_times: "Yarı mamul lazer operasyonu (örn. 5909828-12) başına çevrim süresi (sn/adet) ve setup (dk/iş). ERP SURE ve 1,6 çarpanı yerine bu değerler kullanılır; reçeteler yeniden yüklendiğinde de korunur.",
  shifts: "Günler: 0=Pzt … 6=Paz (örn. 0,1,2,3,4). Kişi sayısı haftalık iş gücünden alınır; eski vardiya kişi alanı kapasiteyi değiştirmez.",
  wc_weeks: "Haftaya özel iş gücü: Hafta = Pazartesi tarihi ya da 2026-W37. Boş kişi sayısı eksik giriştir; planlama öncesinde düzeltme veya sıfır kapasite onayı gerekir. Saat/gün boşsa varsayılan kullanılır; tüm alanlar boşsa haftalık kayıt silinir.",
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

  const sorted = [...(kinds.data ?? [])].filter((k) => k.kind !== "employees").sort((a, b) => ORDER.indexOf(a.kind) - ORDER.indexOf(b.kind));

  useEffect(() => {
    if (!highlightKind) return;
    const el = document.getElementById(`import-kind-${highlightKind}`);
    el?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [highlightKind, kinds.data]);

  return (
    <>
      <h1>Excel Import / Veri dışa aktarım</h1>
      <div className="panel row">
        <button onClick={() => api.download("/api/backup.xlsx", "kapasite_veri_aktarim.xlsx")}>⬇ Excel veri dışa aktarımı</button>
        <span className="muted">Import şablonlarıyla uyumlu alan aktarımı; eksiksiz veritabanı geri dönüşü değildir. Tam yedek için <code>backend/scripts/backup_postgres.ps1</code> (pg_dump). Partiler ve revizyonlar referans sayfalar — otomatik geri yükleme vaadi yok.</span>
      </div>
      <ErrorText err={kinds.err || orderPreviewErr} />
      {can("poweruser") && <LineDizilimCard onApplied={() => { log.reload(); window.dispatchEvent(new Event("data-imported")); }} />}
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
          {(preview.no_routing_item_codes?.length ?? 0) > 0 && (
            <p style={{ margin: "6px 0" }}>
              <b>Rota tanımı eksik ürünler ({preview.no_routing_item_codes!.length}):</b> {preview.no_routing_item_codes!.slice(0, 10).join(", ")}{preview.no_routing_item_codes!.length > 10 ? "…" : ""}.
              Bu ürünlerin siparişi rota tanımlanmadan aktarılmaz; <b>Rota / Çevrim Süreleri</b> importu ile rotayı ekleyip dosyayı yeniden yükleyin.
            </p>
          )}
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


/** Saha tablosu: yıkama makinesi uygunluğu + konveyör dizilimi, tavlama dizilimi. Önce ön izleme, sonra uygula. */
function LineDizilimCard({ onApplied }: { onApplied: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<LineDizilimPreview | null>(null);
  const [result, setResult] = useState<LineDizilimResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const pick = async (f: File | undefined) => {
    if (!f) return;
    setFile(f); setPreview(null); setResult(null); setErr(""); setBusy(true);
    try { setPreview(await api.uploadLong<LineDizilimPreview>("/api/imports/line-dizilim/preview", f)); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const apply = async () => {
    if (!file) return;
    setBusy(true); setErr("");
    try { setResult(await api.uploadLong<LineDizilimResult>("/api/imports/line-dizilim/apply", file)); onApplied(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const p = preview;
  return (
    <div className="panel" id="import-kind-line-dizilim">
      <h2 style={{ marginTop: 0 }}>Hat dizilimi — saha tablosu (Tavlama / Yıkama)</h2>
      <div className="muted" style={{ marginBottom: 6 }}>
        "YIKAMA ÖNCELİK" sayfasında yk2/yk3/yk4 sütunları YK-02/YK-03/YK-04 makinelerinin konveyörüne tek seferde konan parça adedidir; boş hücre "bu makinede yıkanamaz". YK-05, YK-03 ile aynı kabul edilir. "TAVLAMA ÖNCELİK" dizilimi iki fırına yazılır. Saha tablosu geçerlidir: ilgili operasyonların istasyon bağları ve dizilimi bu dosyayla değiştirilir. BOM aktarımından SONRA çalıştırın.
      </div>
      <div className="row"><input type="file" accept=".xlsx" disabled={busy} onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; void pick(f); }} /></div>
      {busy && <p className="muted">Çalışıyor…</p>}
      {err && <div className="error">{err}</div>}
      {result ? (
        <div className="success">Uygulandı: {result.operations} operasyon ({result.washing} yıkama, {result.annealing} tavlama). Çakışma {result.conflict_count}, stok kartı yok {result.not_found_count}, operasyonu yok {result.no_ops_count}. Etki için otomatik planı yeniden çalıştırın.</div>
      ) : p && (
        <>
          {p.missing_sheets.length > 0 && <div className="error">Eksik sayfa: {p.missing_sheets.join(", ")}</div>}
          {p.missing_machines.length > 0 && <div className="error">Programda tanımlı olmayan makine: {p.missing_machines.join(", ")} (önce İş Merkezleri &gt; İstasyonlar)</div>}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginTop: 8 }}>
            <div className="table-wrap">
              <table>
                <thead><tr><th colSpan={2}>Dosya</th></tr></thead>
                <tbody>
                  <tr><td>Yıkama ürünü</td><td>{p.file.washing_items}</td></tr>
                  <tr><td>Tavlama ürünü</td><td>{p.file.annealing_items}</td></tr>
                  {p.file.washing_combos.map((c) => <tr key={c.machines}><td className="muted">Yıkanabildiği makineler: {c.machines}</td><td>{c.items}</td></tr>)}
                  <tr><td>Stok kartı olmayan kod</td><td style={{ color: p.not_found_count ? "var(--warn)" : undefined }}>{p.not_found_count}</td></tr>
                  <tr><td>Programda operasyonu olmayan</td><td style={{ color: p.no_ops_count ? "var(--warn)" : undefined }}>{p.no_ops_count}</td></tr>
                  <tr><td>Paylaşılan yarımamülde çakışma</td><td style={{ color: p.conflict_count ? "var(--warn)" : undefined }}>{p.conflict_count}</td></tr>
                </tbody>
              </table>
            </div>
            <div className="table-wrap">
              <table>
                <thead><tr><th colSpan={2}>Programda değişecek</th></tr></thead>
                <tbody>
                  <tr><td>Yıkama operasyonu</td><td>{p.operations.washing}</td></tr>
                  <tr><td>Tavlama operasyonu</td><td>{p.operations.annealing}</td></tr>
                  <tr><td>İstasyon bağı değişen</td><td>{p.operations.station_links_changed}</td></tr>
                  <tr><td>Dizilimi değişen</td><td>{p.operations.units_changed}</td></tr>
                  <tr><td>Birincil istasyonu değişen</td><td>{p.operations.primary_changed}</td></tr>
                  <tr><td>Açık sipariş hat-saati: yıkama</td><td>{fmt(p.open_order_hours.before.washing ?? 0)} → {fmt(p.open_order_hours.after.washing ?? 0)}</td></tr>
                  <tr><td>Açık sipariş hat-saati: tavlama</td><td>{fmt(p.open_order_hours.before.annealing ?? 0)} → {fmt(p.open_order_hours.after.annealing ?? 0)}</td></tr>
                </tbody>
              </table>
            </div>
          </div>
          {(p.conflicts.length > 0 || p.no_ops.length > 0 || p.not_found.length > 0 || p.bad_rows.length > 0) && (
            <ul className="muted" style={{ fontSize: 12, maxHeight: 160, overflow: "auto" }}>
              {p.conflicts.slice(0, 20).map((x, i) => <li key={"c" + i}>{x}</li>)}
              {p.no_ops.slice(0, 20).map((x, i) => <li key={"n" + i}>Operasyon yok: {x}</li>)}
              {p.not_found.slice(0, 20).map((x, i) => <li key={"u" + i}>Stok kartı yok: {x}</li>)}
              {p.bad_rows.slice(0, 20).map((x, i) => <li key={"b" + i}>{x}</li>)}
            </ul>
          )}
          <button type="button" disabled={busy || p.missing_machines.length > 0 || (p.operations.washing + p.operations.annealing) === 0} onClick={() => void apply()} style={{ marginTop: 8 }}>Uygula</button>
        </>
      )}
    </div>
  );
}
