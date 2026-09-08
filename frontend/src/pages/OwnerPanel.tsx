import { useCallback, useEffect, useMemo, useState } from "react";
import { api, qs } from "../api";
import { ErrorText, useAsync } from "../components";
import { useAuth } from "../auth";

interface OwnerStats {
  orders: number;
  production_batches: number;
  items: number;
  work_centers: number;
  plan_lines: number;
  import_logs: number;
  import_datasets: { key: string; purge_key: string; title: string; count: number }[];
  extra_purges: { key: string; title: string; danger: string; count: number }[];
}

interface OwnerRecord {
  id: number;
  label: string;
  subtitle: string;
  username: string;
  datetime: string | null;
}

interface OwnerRecords {
  target: string;
  title: string;
  total_count: number;
  filtered_count: number;
  has_datetime: boolean;
  records: OwnerRecord[];
  offset: number;
  limit: number;
  users: string[];
  import_batches: { id: number; filename: string; username: string; created_at: string | null; inserted: number; updated: number; has_errors: boolean }[];
}

interface PurgeResult { target: string; deleted: number; scope: string }

const PAGE = 100;

const MASTER_PURGES = [
  { key: "orders", title: "Tüm siparişler", desc: "Açık, kapalı ve birleştirilmiş tüm siparişler + üretim partileri" },
  { key: "items", title: "Tüm stok kodları", desc: "Stok kartları, BOM, rota ve bağlı tüm kayıtlar" },
  { key: "workcenters", title: "Tüm iş merkezleri", desc: "İş merkezleri, vardiyalar, makineler; plan ve rota bağlantıları" },
] as const;

function fmtDt(iso: string | null) {
  if (!iso) return "—";
  if (iso.length === 10) return new Date(iso + "T00:00:00").toLocaleDateString("tr-TR");
  return new Date(iso).toLocaleString("tr-TR", { dateStyle: "short", timeStyle: "short" });
}

function PurgeModal({ target, title, onClose, onDone }: { target: string; title: string; onClose: () => void; onDone: () => void }) {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [username, setUsername] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [data, setData] = useState<OwnerRecords | null>(null);
  const [loadErr, setLoadErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [confirm, setConfirm] = useState("");
  const [purgeErr, setPurgeErr] = useState("");
  const [purgeOk, setPurgeOk] = useState("");
  const [purgeBusy, setPurgeBusy] = useState(false);

  const filterParams = useMemo(() => {
    const p: Record<string, unknown> = { target, offset: page * PAGE, limit: PAGE };
    if (from) p.from = from;
    if (to) p.to = to;
    if (username) p.username = username;
    if (search.trim()) p.search = search.trim();
    return p;
  }, [target, from, to, username, search, page]);

  const load = useCallback(async () => {
    setBusy(true);
    setLoadErr("");
    try {
      const d = await api.get<OwnerRecords>(`/api/owner/records${qs(filterParams)}`);
      setData(d);
    } catch (e) { setLoadErr((e as Error).message); }
    finally { setBusy(false); }
  }, [filterParams]);

  useEffect(() => { load(); }, [load]);

  const toggle = (id: number) => setSelected((s) => {
    const n = new Set(s);
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });

  const pageIds = data?.records.map((r) => r.id) ?? [];
  const allPageSelected = pageIds.length > 0 && pageIds.every((id) => selected.has(id));

  const selectPage = () => setSelected((s) => {
    const n = new Set(s);
    if (allPageSelected) pageIds.forEach((id) => n.delete(id));
    else pageIds.forEach((id) => n.add(id));
    return n;
  });

  const selectFiltered = async () => {
    try {
      const q: Record<string, unknown> = { target };
      if (from) q.from = from;
      if (to) q.to = to;
      if (username) q.username = username;
      if (search.trim()) q.search = search.trim();
      const r = await api.get<{ ids: number[]; count: number }>(`/api/owner/records/ids${qs(q)}`);
      setSelected(new Set(r.ids));
    } catch (e) { setPurgeErr((e as Error).message); }
  };

  const clearSelection = () => setSelected(new Set());

  const purgeBody = (opts: { deleteAll?: boolean; selectFiltered?: boolean }) => {
    const b: Record<string, unknown> = { target, confirm };
    if (opts.deleteAll) return { ...b, delete_all: true };
    if (opts.selectFiltered) {
      if (from) b.from_dt = from;
      if (to) b.to_dt = to;
      if (username) b.username = username;
      if (search.trim()) b.search = search.trim();
      return { ...b, select_filtered: true };
    }
    return { ...b, ids: [...selected] };
  };

  const runPurge = async (opts: { deleteAll?: boolean; selectFiltered?: boolean }) => {
    if (confirm.trim().toUpperCase() !== "SIL") return;
    setPurgeBusy(true);
    setPurgeErr("");
    setPurgeOk("");
    try {
      const body = purgeBody(opts);
      const r = await api.post<PurgeResult>("/api/owner/purge", body);
      setPurgeOk(`${r.deleted} kayıt silindi`);
      setConfirm("");
      setSelected(new Set());
      onDone();
      load();
    } catch (e) { setPurgeErr((e as Error).message); }
    finally { setPurgeBusy(false); }
  };

  const canDeleteSelected = selected.size > 0 && confirm.trim().toUpperCase() === "SIL" && !purgeBusy;
  const totalPages = data ? Math.max(1, Math.ceil(data.filtered_count / PAGE)) : 1;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal panel" style={{ maxWidth: 920, width: "96vw", maxHeight: "92vh", overflow: "auto", display: "flex", flexDirection: "column" }} onClick={(e) => e.stopPropagation()}>
        <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
          <div>
            <h2 style={{ margin: 0 }}>{title}</h2>
            <p className="muted" style={{ margin: "4px 0 0" }}>
              Kayıtları tıklayarak seçin · Tarih aralığı yalnızca listeyi filtreler · Silme geri alınamaz
            </p>
          </div>
          <button className="secondary small" onClick={onClose}>Kapat</button>
        </div>

        <div className="row" style={{ flexWrap: "wrap", gap: 8, margin: "12px 0" }}>
          <label>Başlangıç<input type="date" value={from} onChange={(e) => { setFrom(e.target.value); setPage(0); }} /></label>
          <label>Bitiş<input type="date" value={to} onChange={(e) => { setTo(e.target.value); setPage(0); }} /></label>
          {data && data.users.length > 0 && (
            <label>Kullanıcı
              <select value={username} onChange={(e) => { setUsername(e.target.value); setPage(0); }}>
                <option value="">Tümü</option>
                {data.users.map((u) => <option key={u} value={u}>{u}</option>)}
              </select>
            </label>
          )}
          <label>Ara<input value={search} onChange={(e) => { setSearch(e.target.value); setPage(0); }} placeholder="Kod, ad…" /></label>
          <button className="secondary small" onClick={() => { setPage(0); load(); }} disabled={busy}>Yenile</button>
        </div>

        {loadErr && <div className="error">{loadErr}</div>}
        {data && (
          <p style={{ margin: "0 0 8px" }}>
            Filtre: <b>{data.filtered_count}</b> / {data.total_count} kayıt
            {!data.has_datetime && <span className="muted"> · Tarih filtresi bu veri setinde kayıt alanını etkilemez</span>}
            {selected.size > 0 && <span> · <b>{selected.size}</b> seçili</span>}
          </p>
        )}

        <div className="row" style={{ gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
          <button className="secondary small" onClick={selectPage} disabled={!pageIds.length}>{allPageSelected ? "Sayfadaki seçimi kaldır" : "Sayfadaki tümünü seç"}</button>
          <button className="secondary small" onClick={selectFiltered} disabled={!data?.filtered_count}>Filtredeki tümünü seç ({data?.filtered_count ?? 0})</button>
          {selected.size > 0 && <button className="secondary small" onClick={clearSelection}>Seçimi temizle</button>}
        </div>

        <div className="table-wrap" style={{ flex: 1, minHeight: 200, maxHeight: 340, overflow: "auto" }}>
          {busy && !data ? <p className="muted">Yükleniyor…</p> : (
            <table>
              <thead>
                <tr>
                  <th style={{ width: 32 }}></th>
                  {data?.has_datetime && <th>Tarih</th>}
                  <th>Kullanıcı</th>
                  <th>Kayıt</th>
                  <th>Detay</th>
                </tr>
              </thead>
              <tbody>
                {data?.records.map((r) => (
                  <tr
                    key={r.id}
                    onClick={() => toggle(r.id)}
                    style={{ cursor: "pointer", background: selected.has(r.id) ? "var(--row-selected, rgba(59,130,246,0.12))" : undefined }}
                  >
                    <td><input type="checkbox" checked={selected.has(r.id)} readOnly /></td>
                    {data.has_datetime && <td>{fmtDt(r.datetime)}</td>}
                    <td>{r.username}</td>
                    <td><b>{r.label}</b></td>
                    <td className="muted">{r.subtitle || "—"}</td>
                  </tr>
                ))}
                {data && data.records.length === 0 && (
                  <tr><td colSpan={data.has_datetime ? 5 : 4} className="muted">Kayıt yok</td></tr>
                )}
              </tbody>
            </table>
          )}
        </div>

        {data && data.filtered_count > PAGE && (
          <div className="row" style={{ justifyContent: "center", gap: 8, margin: "8px 0" }}>
            <button className="secondary small" disabled={page <= 0} onClick={() => setPage((p) => p - 1)}>← Önceki</button>
            <span className="muted">Sayfa {page + 1} / {totalPages}</span>
            <button className="secondary small" disabled={(page + 1) * PAGE >= data.filtered_count} onClick={() => setPage((p) => p + 1)}>Sonraki →</button>
          </div>
        )}

        {data && data.import_batches.length > 0 && (
          <details style={{ marginTop: 8 }}>
            <summary className="muted" style={{ cursor: "pointer" }}>Import geçmişi ({data.import_batches.length}) — bilgi amaçlı</summary>
            <div className="table-wrap" style={{ maxHeight: 120, marginTop: 6 }}>
              <table>
                <thead><tr><th>Tarih/saat</th><th>Kullanıcı</th><th>Dosya</th><th className="num">+/-</th></tr></thead>
                <tbody>
                  {data.import_batches.map((b) => (
                    <tr key={b.id}>
                      <td>{fmtDt(b.created_at)}</td>
                      <td>{b.username}</td>
                      <td>{b.filename}{b.has_errors && <span className="error"> · hata</span>}</td>
                      <td className="num">+{b.inserted} / ~{b.updated}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        )}

        <section style={{ borderTop: "1px solid var(--border)", paddingTop: 12, marginTop: 12 }}>
          <label>Onaylamak için <b>SIL</b> yazın
            <input value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder="SIL" />
          </label>
          {purgeErr && <div className="error">{purgeErr}</div>}
          {purgeOk && <div className="ok">{purgeOk}</div>}
          <div className="row" style={{ justifyContent: "flex-end", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
            <button className="danger" disabled={!canDeleteSelected} onClick={() => runPurge({})}>
              Seçilenleri sil ({selected.size})
            </button>
            <button className="danger" disabled={confirm.trim().toUpperCase() !== "SIL" || purgeBusy || !data?.filtered_count} onClick={() => runPurge({ selectFiltered: true })}>
              Filtredeki tümünü sil ({data?.filtered_count ?? 0})
            </button>
            <button className="danger" disabled={confirm.trim().toUpperCase() !== "SIL" || purgeBusy} onClick={() => runPurge({ deleteAll: true })}>
              Tüm veriyi sil ({data?.total_count ?? 0})
            </button>
          </div>
        </section>
      </div>
    </div>
  );
}

function PurgeAction({ target, label, count, danger, onDone }: { target: string; label: string; count?: number; danger?: boolean; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button className={danger ? "danger small" : "secondary small"} onClick={() => setOpen(true)}>
        Detay / Sil{count != null ? ` (${count})` : ""}
      </button>
      {open && <PurgeModal target={target} title={label} onClose={() => setOpen(false)} onDone={onDone} />}
    </>
  );
}

export default function OwnerPanel() {
  const { can } = useAuth();
  const stats = useAsync(() => api.get<OwnerStats>("/api/owner/stats"), []);

  if (!can("owner")) return <div className="error">Bu sayfaya yalnızca owner erişebilir.</div>;

  const s = stats.data;
  const reload = () => stats.reload();

  return (
    <>
      <h1>Owner Panel</h1>
      <p className="muted">Kayıtları listeleyip tıklayarak çoklu seçim yapabilir, yalnızca seçtiklerinizi veya filtreye uyan tüm kayıtları silebilirsiniz. Önce Excel yedek alın.</p>

      {stats.err && <ErrorText err={String(stats.err)} />}

      <section className="panel" style={{ borderColor: "var(--bad)" }}>
        <h2 style={{ marginTop: 0, color: "var(--bad)" }}>Ana veri setleri</h2>
        <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
          {MASTER_PURGES.map((p) => (
            <PurgeAction
              key={p.key}
              target={p.key}
              label={p.title}
              count={s ? (p.key === "orders" ? s.orders : p.key === "items" ? s.items : s.work_centers) : undefined}
              danger
              onDone={reload}
            />
          ))}
        </div>
      </section>

      <section className="panel">
        <h2 style={{ marginTop: 0 }}>Import veri setleri</h2>
        {!s ? <p className="muted">Yükleniyor…</p> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Veri seti</th><th className="num">Kayıt</th><th></th></tr></thead>
              <tbody>
                {s.import_datasets.map((d) => (
                  <tr key={d.key}>
                    <td><b>{d.title}</b><br /><span className="muted">{d.key}</span></td>
                    <td className="num">{d.count}</td>
                    <td><PurgeAction target={d.purge_key} label={d.title} count={d.count} danger={d.count > 0} onDone={reload} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel">
        <h2 style={{ marginTop: 0 }}>Diğer silinebilir veriler</h2>
        {!s ? null : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Veri</th><th className="num">Kayıt</th><th></th></tr></thead>
              <tbody>
                {s.extra_purges.map((e) => (
                  <tr key={e.key}>
                    <td>{e.title}</td>
                    <td className="num">{e.count}</td>
                    <td><PurgeAction target={e.key} label={e.title} count={e.count} danger={e.danger === "high" && e.count > 0} onDone={reload} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
