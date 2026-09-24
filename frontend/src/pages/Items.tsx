import { useEffect, useState } from "react";
import { api, fmt, qs, type Item, type ItemDetail, type OperationOut, type ResourceModelStats, type TimeBasis } from "../api";
import { useAuth } from "../auth";
import { ErrorText, StringMultiSelect, useAsync, useWorkCenters } from "../components";
import BomTreeView from "../components/BomTreeView";

interface ItemHours { item_code: string; item_name?: string; quantity: number; total_hours: number | null; line_hours: number | null; labor_hours: number | null; has_line_operations: boolean; missing_operation_count: number; operations: { seq: number; operation_name: string; work_center_code: string; cycle_time_sec: number; setup_time_min: number; hours: number | null; missing_reason: string | null }[] }
function displayHours(value: number | null | undefined): string {
  if (value === undefined) return "—";
  if (value === null) return "Hesaplanamadı";
  if (value > 0 && value < 0.01) return `${fmt(value, 6)} sa (${fmt(value * 3600, 3)} sn)`;
  return `${fmt(value, 2)} sa`;
}
interface ItemGroups { main_groups: string[]; sub_groups: string[] }

const TIME_BASIS_OPTS: { v: TimeBasis; l: string }[] = [
  { v: "legacy_unspecified", l: "Mevcut rota süresi (türü belirtilmemiş)" },
  { v: "labor_seconds_per_unit", l: "İşgücü sn/adet" },
  { v: "machine_seconds_per_cycle", l: "Makine sn/çevrim" },
];

export default function Items() {
  const { can } = useAuth();
  const canEdit = can("poweruser");
  const { wcs } = useWorkCenters();
  const [q, setQ] = useState("");
  const [mainGroups, setMainGroups] = useState<string[]>([]);
  const [subGroups, setSubGroups] = useState<string[]>([]);
  const [groupOpts, setGroupOpts] = useState<ItemGroups>({ main_groups: [], sub_groups: [] });
  const [sel, setSel] = useState<Item | null>(null);
  const [qty, setQty] = useState(1);

  useEffect(() => {
    api.get<ItemGroups>("/api/items/groups").then(setGroupOpts).catch(() => setGroupOpts({ main_groups: [], sub_groups: [] }));
  }, []);

  const listParams = { q: q || undefined, main_group: mainGroups.length ? mainGroups : undefined, sub_group: subGroups.length ? subGroups : undefined, limit: 300 };
  const list = useAsync(() => api.get<Item[]>(`/api/items${qs(listParams)}`), [q, mainGroups.join(","), subGroups.join(",")]);
  const [detailRev, setDetailRev] = useState(0);
  const detail = useAsync(() => (sel ? api.get<ItemDetail>(`/api/items/${sel.id}`) : Promise.resolve(null)), [sel?.id, detailRev]);
  const resStats = useAsync(() => api.get<ResourceModelStats>("/api/resource-model/stats"), []);
  const [editOp, setEditOp] = useState<OperationOut | null>(null);
  const [saveMsg, setSaveMsg] = useState("");
  const [conveyorEdit, setConveyorEdit] = useState(false);
  const [wipEdit, setWipEdit] = useState(false);
  const [wipQty, setWipQty] = useState("");
  const [wipDays, setWipDays] = useState("");
  const [wipMsg, setWipMsg] = useState("");
  const [wipSaved, setWipSaved] = useState<{ id: number; qty: number | null; days: number | null } | null>(null);
  const [annealing, setAnnealing] = useState(1);
  const [washing, setWashing] = useState(1);
  const [applyGroup, setApplyGroup] = useState(false);
  const [savingConveyor, setSavingConveyor] = useState(false);
  useEffect(() => { setConveyorEdit(false); setSaveMsg(""); setEditOp(null); }, [sel?.id]);
  const hours = useAsync(() => (sel ? api.get<ItemHours>(`/api/requirements/item${qs({ item_code: sel.code, quantity: qty })}`) : Promise.resolve(null)), [sel?.code, qty, detailRev]);
  const wcCode = (id: number) => wcs.find((w) => w.id === id)?.code ?? id;

  return (
    <>
      <h1>Stok / BOM / Rota</h1>
      <div className="panel row">
        <label>Ara<input value={q} onChange={(e) => setQ(e.target.value)} placeholder="stok kodu, ad, grup" /></label>
        <StringMultiSelect label="Ana Grup" options={groupOpts.main_groups} value={mainGroups} onChange={setMainGroups} />
        <StringMultiSelect label="Alt Grup" options={groupOpts.sub_groups} value={subGroups} onChange={setSubGroups} />
        {(mainGroups.length > 0 || subGroups.length > 0) && (
          <button className="secondary" onClick={() => { setMainGroups([]); setSubGroups([]); }}>Grup filtrelerini temizle</button>
        )}
        <span className="muted">BOM ve rota verileri Excel import ile yüklenir.</span>
        {resStats.data && (
          <span className="muted" title="Ayrıntılı günlük çizelge için tanım eksikleri">
            Ayrıntılı günlük çizelge: {resStats.data.missing_detailed_schedule_definition} operasyonun kaynak tanımı eksik
          </span>
        )}
      </div>
      <ErrorText err={list.err || detail.err} />
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1.4fr", gap: 14 }}>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Stok Kodu</th><th>Ad</th><th>Ana Grup</th><th>Alt Grup</th><th>Ürün Grubu</th></tr></thead>
            <tbody>
              {list.data?.map((i) => (
                <tr key={i.id} onClick={() => setSel(i)} style={{ cursor: "pointer", background: sel?.id === i.id ? "#e3f2fd" : undefined }}>
                  <td><b>{i.code}</b></td>
                  <td>{i.name}</td>
                  <td>{i.main_group || <span className="muted">—</span>}</td>
                  <td>{i.sub_group || <span className="muted">—</span>}</td>
                  <td>{i.product_group}</td>
                </tr>
              ))}
              {list.data?.length === 0 && <tr><td colSpan={5} className="muted">Kayıt yok.</td></tr>}
            </tbody>
          </table>
        </div>
        <div>
          {!sel && <div className="panel muted">Soldan bir stok kodu seçin.</div>}
          {sel && detail.data && (
            <>
              <div className="panel">
                <h2 style={{ marginTop: 0 }}>{detail.data.code} — {detail.data.name}</h2>
                <p className="muted">{detail.data.main_group && <>Ana Grup: <b>{detail.data.main_group}</b> · </>}{detail.data.sub_group && <>Alt Grup: <b>{detail.data.sub_group}</b> · </>}Ürün Grubu: <b>{detail.data.product_group}</b></p>
                <div className="row">
                  <label>Miktar<input type="number" min={1} value={qty} onChange={(e) => setQty(Number(e.target.value) || 1)} /></label>
                  <div className="kpi"><span className="v">{displayHours(hours.data?.labor_hours)}</span><span className="l">İş gücü ihtiyacı (kişi-saat)</span></div>
                  {hours.data?.has_line_operations && <div className="kpi"><span className="v">{displayHours(hours.data.line_hours)}</span><span className="l">Hat ihtiyacı (hat-saat)</span></div>}
                </div>
                <ErrorText err={hours.err} />
                {!!hours.data?.missing_operation_count && <p role="status" style={{color:"#9a5b00"}}>{hours.data.missing_operation_count} hat operasyonunda çevrim süresi veya dizilim çıkış aralığı eksik. Bu satırlar ve hat toplamı hesaplanamadı; eksik süreler sıfır kabul edilmez.</p>}
                {(detail.data.child_wips?.length ?? 0) > 0 && (
                  <p className="muted" style={{ marginBottom: 8 }}>
                    Alt yarı mamuller: {detail.data.child_wips!.map((w) => `${w.code} (${w.operation_count} op.)`).join(" · ")}
                  </p>
                )}
                {saveMsg && <p className="muted">{saveMsg}</p>}
                {canEdit && <button className="secondary" onClick={() => {
                  const values = (word: string) => [...new Set(detail.data!.operations.filter(o => o.operation_name.toLocaleUpperCase("tr-TR").replace(/İ/g, "I").includes(word)).map(o => o.units_per_cycle))];
                  const a = values("TAVLAMA"), w = values("YIKAMA");
                  setAnnealing(a.length === 1 ? a[0] : 1); setWashing(w.length === 1 ? w[0] : 1);
                  setApplyGroup(false); setConveyorEdit(true);
                }}>Tavlama / yıkama dizilimi</button>}
                {(() => {
                  const cur = wipSaved && wipSaved.id === sel.id ? wipSaved : { id: sel.id, qty: sel.max_wip_qty ?? null, days: sel.max_wip_days ?? null };
                  return (
                    <div className="panel" style={{ marginTop: 8 }}>
                      <b>Ara stok sınırı</b>{" "}
                      <span className="muted">
                        {cur.qty || cur.days
                          ? `azami ${cur.qty ? `${cur.qty} adet` : ""}${cur.qty && cur.days ? " / " : ""}${cur.days ? `${cur.days} gün` : ""} — öncül operasyon ardılın tüketiminden bu kadar önde üretilemez; plan son geçişi öncülü geciktirir, kapasite yoksa uyarı verir`
                          : "tanımsız (sınır yok). Yarımamül kartına azami adet ve/veya gün girilirse plan ara stoğu bu sınırda tutar."}
                      </span>
                      {canEdit && !wipEdit && <button className="secondary small" style={{ marginLeft: 8 }} onClick={() => { setWipQty(cur.qty ? String(cur.qty) : ""); setWipDays(cur.days ? String(cur.days) : ""); setWipMsg(""); setWipEdit(true); }}>Düzenle</button>}
                      {canEdit && wipEdit && (
                        <div className="row" style={{ marginTop: 6 }}>
                          <label>Azami ara stok (adet)<input type="number" min={0} step={1} value={wipQty} onChange={(e) => setWipQty(e.target.value)} placeholder="boş = sınır yok" /></label>
                          <label>Azami ara stok (gün)<input type="number" min={0} max={365} step={1} value={wipDays} onChange={(e) => setWipDays(e.target.value)} placeholder="boş = sınır yok" /></label>
                          <button onClick={async () => {
                            setWipMsg("");
                            try {
                              const r = await api.patch<{ max_wip_qty: number | null; max_wip_days: number | null }>(`/api/items/${sel.id}/wip-limits`, { max_wip_qty: wipQty === "" ? null : Number(wipQty), max_wip_days: wipDays === "" ? null : Number(wipDays) });
                              setWipSaved({ id: sel.id, qty: r.max_wip_qty, days: r.max_wip_days });
                              setWipEdit(false);
                              setWipMsg("Ara stok sınırı kaydedildi; bir sonraki otomatik plan / revizyon hesabında uygulanır.");
                            } catch (e) { setWipMsg(String(e)); }
                          }}>Kaydet</button>
                          <button className="secondary" onClick={() => setWipEdit(false)}>İptal</button>
                        </div>
                      )}
                      {wipMsg && <p className="muted" style={{ margin: "4px 0 0" }}>{wipMsg}</p>}
                    </div>
                  );
                })()}
                {canEdit && conveyorEdit && <div className="panel">
                  <p>Miktar ÷ birim adet × çevrim süresi. Boş değer 1 kabul edilir; hazırlık süresi bölünmez. Tekrarlı operasyonlar ve ara yıkamalar dahildir.</p>
                  <div className="row">
                    <label>Tavlama birim adet (adet/çevrim)<input type="number" min={1} step={1} value={annealing} onChange={e => setAnnealing(e.target.value === "" ? 1 : Number(e.target.value))} /></label>
                    <label>Yıkama birim adet (adet/çevrim)<input type="number" min={1} step={1} value={washing} onChange={e => setWashing(e.target.value === "" ? 1 : Number(e.target.value))} /></label>
                  </div>
                  <label><input type="checkbox" checked={applyGroup} disabled={!detail.data.product_group?.trim()} onChange={e => setApplyGroup(e.target.checked)} /> Aynı ürün grubundaki mevcut reçetelere uygula ({detail.data.product_group || "ürün grubu tanımlı değil"})</label>
                  <p className="muted">Kapsamdaki tüm tavlama ve yıkama satırları bu değerlere eşitlenir. Ortak yarımamül rotasının diğer mamulleri de etkilenir. Yeni reçetelerde varsayılan 1'dir. Kayıtlı plan için yeniden planlama gerekir.</p>
                  <p className="muted">Önceden aktarılmış MES kayıtlarının standart işçiliği aktarım anındaki değeri korur. Yeni dizilimle değerlendirmek için ilgili MES dosyasını tekrar aktarın.</p>
                  <button disabled={savingConveyor || !Number.isInteger(annealing) || annealing < 1 || !Number.isInteger(washing) || washing < 1} onClick={async () => {
                    setSavingConveyor(true); setSaveMsg("");
                    try {
                      const result = await api.patch<{annealing: number; washing: number}>(`/api/items/${sel.id}/conveyor-units`, {annealing_units: annealing, washing_units: washing, apply_to_group: applyGroup});
                      setSaveMsg(`${result.annealing} tavlama ve ${result.washing} yıkama operasyonu güncellendi.`);
                      setDetailRev(v => v + 1); setConveyorEdit(false);
                    } catch(e) { setSaveMsg(String(e)); } finally { setSavingConveyor(false); }
                  }}>Dizilimi kaydet</button>
                  <button className="secondary" onClick={() => setConveyorEdit(false)}>İptal</button>
                </div>}
                {canEdit && editOp && (
                  <div className="panel row" style={{ marginBottom: 10 }}>
                    <b>Op {editOp.seq} kaynak tanımı</b>
                    {editOp.missing_resource_definition && (
                      <p className="muted">Ayrıntılı günlük çizelge için süre türü veya makine tanımı eksik. Haftalık kapasite hesabı mevcut rota süreleriyle çalışır.</p>
                    )}
                    <label>Süre türü
                      <select value={editOp.time_basis} onChange={(e) => setEditOp({ ...editOp, time_basis: e.target.value as TimeBasis })}>
                        {TIME_BASIS_OPTS.map((o) => <option key={o.v} value={o.v}>{o.l}</option>)}
                      </select>
                    </label>
                    <label>Ekip (kişi)<input type="number" min={1} value={editOp.crew_size ?? ""} onChange={(e) => setEditOp({ ...editOp, crew_size: e.target.value ? Number(e.target.value) : null })} /></label>
                    <label>Adet/çevrim<input type="number" min={1} step={1} value={editOp.units_per_cycle} onChange={(e) => setEditOp({ ...editOp, units_per_cycle: Number(e.target.value) || 1 })} /></label>
                    <label>Dizilim çıkış aralığı (sn)<input type="number" min={0.001} step="any" value={editOp.line_interval_sec ?? ""} onChange={e => setEditOp({...editOp, line_interval_sec: e.target.value === "" ? null : Number(e.target.value)})} /></label>
                    <span className="muted">İlk grup BOM çevrim süresini, sonraki her grup dizilim çıkış aralığını kullanır. Son eksik grup tam grup sayılır.</span>
                    <label>Makine çevrim (sn)<input type="number" min={0} value={editOp.machine_cycle_time_sec ?? ""} onChange={(e) => setEditOp({ ...editOp, machine_cycle_time_sec: e.target.value === "" ? null : Number(e.target.value) })} /></label>
                    <button onClick={async () => {
                      setSaveMsg("");
                      try {
                        await api.patch(`/api/routing-operations/${editOp.id}`, {
                          time_basis: editOp.time_basis,
                          crew_size: editOp.crew_size,
                          units_per_cycle: editOp.units_per_cycle,
                          machine_cycle_time_sec: editOp.machine_cycle_time_sec,
                          line_interval_sec: editOp.line_interval_sec,
                        });
                        setSaveMsg("Kaydedildi.");
                        setEditOp(null);
                        setDetailRev((x) => x + 1);
                        resStats.reload?.();
                      } catch (e) {
                        setSaveMsg(String(e));
                      }
                    }}>Kaydet</button>
                    <button className="secondary" onClick={() => setEditOp(null)}>İptal</button>
                  </div>
                )}
                <table>
                  <thead><tr><th>Sıra</th><th>Operasyon</th><th>Yarımamül</th><th>İş Merkezi</th><th>Birincil istasyon</th><th>Alternatif istasyonlar</th><th className="num">Çevrim / çıkış (sn)</th><th className="num">Adet/çevrim</th><th className="num">Setup (dk)</th><th className="num">Saat ({qty} adet)</th>{canEdit && <th />}</tr></thead>
                  <tbody>
                    {detail.data.operations.map((op) => {
                      const need = hours.data?.operations.find((o) => o.seq === op.seq);
                      const h = need?.hours;
                      const alts = (op.stations ?? []).filter((s) => !s.is_primary).map((s) => s.machine_code).join(", ");
                      return (
                        <tr key={`${op.seq}-${op.id}-${op.wip_code ?? ""}`}>
                          <td>{op.seq}</td>
                          <td>{op.operation_name}</td>
                          <td><code>{op.semi_finished_code || "—"}</code></td>
                          <td>{wcCode(op.work_center_id)}</td>
                          <td><code>{op.primary_machine_code || "—"}</code></td>
                          <td className="muted">{alts || "—"}</td>
                          <td className="num">{op.planning_mode === "line" ? (op.line_interval_sec && op.line_interval_sec > 0 ? fmt(op.line_interval_sec, 3) : "") : fmt(op.cycle_time_sec)}</td>
                          <td className="num">{op.units_per_cycle || 1}</td>
                          <td className="num">{op.planning_mode === "line" ? "Uygulanmaz" : fmt(op.setup_time_min)}</td>
                          <td className="num" title={need?.missing_reason ?? (op.planning_mode === "line" ? "Hat-saat" : "Kişi-saat")}>{displayHours(h)}</td>
                          {canEdit && <td><button className="secondary" onClick={() => setEditOp(op)}>Kaynak</button></td>}
                        </tr>
                      );
                    })}
                    {detail.data.operations.length === 0 && <tr><td colSpan={canEdit ? 11 : 10} className="muted">Rota tanımı yok</td></tr>}
                  </tbody>
                </table>
              </div>
              <div className="panel">
                <h2 style={{ marginTop: 0 }}>Malzeme reçetesi (BOM)</h2>
                <BomTreeView
                  lines={detail.data.bom_lines}
                  qty={qty}
                  productCode={detail.data.code}
                  productName={detail.data.name}
                  operations={detail.data.operations.map((op) => ({
                    seq: op.seq,
                    operation_name: op.operation_name,
                    semi_finished_code: op.semi_finished_code,
                    wip_code: op.wip_code,
                  }))}
                />
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}
