import { useState } from "react";
import { api, qs } from "../api";
import { ErrorText } from "../components";

export default function WcWeeksExcel({ start, ids, canEdit, onImported }: {
  start: string; ids: number[]; canEdit: boolean; onImported: () => void;
}) {
  const [weeks, setWeeks] = useState(12);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const exportFile = async () => {
    setBusy(true); setError(""); setMessage("");
    try { await api.download(`/api/exports/wc-weeks.xlsx${qs({ start, weeks, work_center_ids: ids })}`); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  const importFile = async () => {
    if (!file || busy) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const result = await api.upload<{ inserted: number; updated: number; errors: string[] }>("/api/imports/wc_weeks", file);
      if (result.errors.length) setError(`Hiçbir haftalık kayıt değiştirilmedi. ${result.errors.join(" · ")}`);
      else { setMessage(`${result.inserted} haftalık kayıt eklendi, ${result.updated} kayıt güncellendi. Diğer kayıtlar korundu.`); onImported(); }
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  return <div className="panel">
    <b>Haftalık iş gücü — Excel ile toplu güncelle</b>
    <div className="row" style={{ marginTop: 8, alignItems: "flex-end" }}>
      <label>Hafta sayısı<select value={weeks} onChange={(e) => setWeeks(Number(e.target.value))} disabled={busy}>
        {[1, 2, 4, 8, 12].map((n) => <option key={n} value={n}>{n} hafta</option>)}
      </select></label>
      <button className="secondary" onClick={exportFile} disabled={busy || ids.length === 0}>Haftalık iş gücünü Excel’e aktar</button>
      {canEdit && <>
        <label>Doldurduğun Excel<input type="file" accept=".xlsx,.xlsm" disabled={busy} onChange={(e) => { setFile(e.target.files?.[0] ?? null); setError(""); setMessage(""); }} /></label>
        <button onClick={importFile} disabled={busy || !file}>Excel’i içe aktar</button>
      </>}
    </div>
    <p className="muted">{start} haftasından başlayarak listedeki {ids.length} iş merkezi dışa aktarılır. Mavi alanları doldurun: kişi, verimli saat/kişi, çalışma günü ve not. Boş = varsayılan; 0 = sıfır.</p>
    <p className="muted">İçe aktarma dosyadaki iş merkezi ve haftaları günceller; ekran filtresiyle sınırlanmaz. Dosyada olmayan haftalar korunur. Yeni iş merkezi oluşturulmaz.</p>
    {busy && <p role="status">Excel işleniyor…</p>}
    <ErrorText err={error} />
    {message && <p role="status" style={{ color: "var(--ok)" }}>{message}</p>}
  </div>;
}
