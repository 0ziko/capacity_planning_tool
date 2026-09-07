import { Fragment, useEffect, useState } from "react";
import { api, fmt, qs, type Flow, type FlowTransition, type Rule, type RuleKind, type RuleRow, type ScenarioGroup } from "../api";
import { useAuth } from "../auth";
import { ErrorText, useAsync } from "../components";

/**
 * Senaryo Matrisi — ürünün operasyonel akış şeması + operasyon geçiş kısıtları.
 * Ürün grubu seçilir; oklar üzerindeki kural tıklanarak düzenlenir. Grup içindeki bir stok kodu seçilirse
 * o stok için özel kural yazılır (grup kuralını ezer).
 */
export default function Scenarios() {
  const { can } = useAuth();
  const canEdit = can("poweruser");
  const groups = useAsync(() => api.get<ScenarioGroup[]>("/api/scenarios/groups"), []);
  const [group, setGroup] = useState<string | null>(null);
  const [itemCode, setItemCode] = useState<string>("");
  const [sel, setSel] = useState<number | null>(null); // seçili geçiş (index)
  const [err, setErr] = useState("");
  useEffect(() => { if (group === null && groups.data?.length) setGroup(groups.data[0].product_group); }, [groups.data, group]);
  useEffect(() => { setItemCode(""); setSel(null); }, [group]);

  const flow = useAsync(() => (group === null ? Promise.resolve(null) : api.get<Flow>(`/api/scenarios/flow${qs({ product_group: group, item_code: itemCode || undefined })}`)), [group, itemCode]);
  const rules = useAsync(() => (group === null ? Promise.resolve([] as RuleRow[]) : api.get<RuleRow[]>(`/api/scenarios/rules${qs({ product_group: group })}`)), [group, flow.data]);
  const refresh = () => { flow.reload(); groups.reload(); };

  const save = async (t: FlowTransition, rule: RuleKind, lag: number, wait: number, note: string) => {
    setErr("");
    try {
      await api.put("/api/scenarios/rules", {
        scope: itemCode ? "item" : "group", product_group: group, item_code: itemCode || null,
        from_op: t.from_op, to_op: t.to_op, from_wip_code: t.from_wip_code || "", to_wip_code: t.to_wip_code || "",
        rule, lag_cycles: lag, wait_minutes: wait, note,
      });
      refresh();
    } catch (e) { setErr((e as Error).message); }
  };
  const remove = async (id: number) => {
    setErr("");
    try { await api.del(`/api/scenarios/rules/${id}`); refresh(); } catch (e) { setErr((e as Error).message); }
  };

  const f = flow.data;
  const selT = f && sel !== null ? f.transitions[sel] : null;

  return (
    <>
      <h1>Senaryo Matrisi</h1>
      <p className="muted" style={{ marginTop: -6 }}>
        Ürünün operasyon akışı üzerinde, bir sonraki operasyonun <b>ne zaman başlayabileceğini</b> tanımlayın:
        <i> önceki bitince</i> (varsayılan) ya da <i>önceki N çevrim tamamlayınca</i> (iç içe çalışma); araya bekleme süresi (dk) eklenebilir.
        Kurallar terminleme (yeni iş) ve otomatik planlamada kullanılır. Ürün grubu geneli için yazılır; gerekirse grup içindeki bir stok kodu için özelleştirilir.
      </p>
      <ErrorText err={err || groups.err || flow.err} />

      <div className="wc-tabs">
        {groups.data?.map((g) => (
          <button key={g.product_group || "__"} className={`wc-tab ${group === g.product_group ? "active" : ""}`} onClick={() => setGroup(g.product_group)} title={g.operations.join(" → ")}>
            <b>{g.product_group || "(grupsuz)"}</b> <span className="muted">{g.item_count} stok · {g.operations.length} op.</span>{" "}
            <span className={`badge ${g.rule_count ? "ok" : "muted"}`}>{g.rule_count} kural</span>
          </button>
        ))}
        {groups.data && groups.data.length === 0 && <span className="muted">Rotası tanımlı stok yok. Önce Stok / Rota yükleyin.</span>}
      </div>

      {f && (
        <>
          <div className="panel row" style={{ alignItems: "center" }}>
            <label>Kapsam
              <select value={itemCode} onChange={(e) => { setItemCode(e.target.value); setSel(null); }}>
                <option value="">Ürün grubu geneli: {f.product_group || "(grupsuz)"}</option>
                {f.items.map((i) => (
                  <option key={i.code} value={i.code}>
                    {i.code} — {i.name}{i.differs ? " · rota farklı" : ""}{i.item_rules ? ` · ${i.item_rules} özel kural` : ""}
                  </option>
                ))}
              </select>
            </label>
            <span className="muted">
              {itemCode
                ? <>Stok koduna özel kurallar yazılıyor (<b>{f.item_code}</b> — {f.item_name}). Özel kural, grup kuralını ezer.</>
                : <>Grup geneli kurallar yazılıyor. Rotası farklı ya da özel senaryosu olan stoklar için yukarıdan stok kodu seçin.</>}
            </span>
          </div>

          {/* Akış şeması */}
          <div className="flow">
            {f.nodes.map((n, i) => (
              <Fragment key={n.name}>
                {i > 0 && <Arrow t={f.transitions[i - 1]} active={sel === i - 1} onClick={() => setSel(sel === i - 1 ? null : i - 1)} />}
                <div className="flow-node" title={`${n.item_count} stokta · ort. çevrim ${n.cycle_time_sec ?? "-"} sn`}>
                  <div className="flow-node-name">{n.name}</div>
                  {n.semi_finished_codes?.length > 0 && <div style={{ fontSize: 11 }}><code>{n.semi_finished_codes.join(", ")}</code></div>}
                  <div className="muted" style={{ fontSize: 12 }}>{n.work_centers.join(", ") || "İM yok"}</div>
                  {n.cycle_time_sec !== null && <div className="muted" style={{ fontSize: 11 }}>~{fmt(n.cycle_time_sec, 0)} sn/adet</div>}
                  {!itemCode && n.item_count < f.items.length && <div className="muted" style={{ fontSize: 11 }} title="Gruptaki bazı stoklarda bu operasyon yok">{n.item_count}/{f.items.length} stok</div>}
                </div>
              </Fragment>
            ))}
            {f.nodes.length === 0 && <span className="muted">Bu kapsamda operasyon yok.</span>}
          </div>
          {f.nodes.length > 1 && sel === null && <p className="muted">Kuralı düzenlemek için iki operasyon arasındaki oka tıklayın.</p>}

          {selT && <RuleEditor key={`${itemCode}|${selT.from_op}|${selT.to_op}`} t={selT} scopeItem={itemCode} canEdit={canEdit} onSave={(r, l, w, n) => save(selT, r, l, w, n)} onDelete={remove} />}

          {/* Matris */}
          <h2>Geçiş matrisi <span className="muted" style={{ fontWeight: 400 }}>— {itemCode ? `${f.item_code} için etkin kurallar` : `${f.product_group || "(grupsuz)"} grubu`}</span></h2>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Önceki operasyon</th><th>Yarımamül</th><th></th><th>Sonraki operasyon</th><th>Yarımamül</th><th>Etkin kural</th><th>Kaynak</th><th>Grup kuralı</th>{itemCode && <th>Stok kuralı</th>}<th>Not</th><th></th></tr></thead>
              <tbody>
                {f.transitions.map((t, i) => (
                  <tr key={i} style={{ cursor: "pointer", background: sel === i ? "#eef2ff" : undefined }} onClick={() => setSel(i)}>
                    <td><b>{t.from_op}</b></td><td className="muted"><code>{t.from_wip_code || "—"}</code></td><td>→</td><td><b>{t.to_op}</b></td><td className="muted"><code>{t.to_wip_code || "—"}</code></td>
                    <td><RuleBadge r={t.effective} /></td>
                    <td><SourceBadge s={t.effective.source} /></td>
                    <td className="muted">{t.group_rule ? t.group_rule.description : "—"}</td>
                    {itemCode && <td className="muted">{t.item_rule ? t.item_rule.description : "—"}</td>}
                    <td className="muted">{t.effective.note}</td>
                    <td>{canEdit && t.effective.id && t.effective.source === (itemCode ? "item" : "group") && <button className="danger small" onClick={(e) => { e.stopPropagation(); remove(t.effective.id!); }}>Sil</button>}</td>
                  </tr>
                ))}
                {f.transitions.length === 0 && <tr><td colSpan={11} className="muted">Tek operasyonlu akış — geçiş yok.</td></tr>}
              </tbody>
            </table>
          </div>

          {/* Gruptaki stoklar */}
          {!itemCode && (
            <>
              <h2>Gruptaki stoklar <span className="muted" style={{ fontWeight: 400 }}>— rotası farklı olanlar ve özel kuralı olanlar işaretli</span></h2>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Stok</th><th>Ad</th><th>Rota</th><th>Durum</th><th></th></tr></thead>
                  <tbody>
                    {f.items.map((i) => (
                      <tr key={i.code}>
                        <td><b>{i.code}</b></td><td>{i.name}</td>
                        <td className="muted">{i.operations.join(" → ")}</td>
                        <td>
                          {i.differs && <span className="badge warn" title="Bu stokun operasyon dizisi grubun genel akışından farklı">rota farklı</span>}{" "}
                          {i.item_rules > 0 && <span className="badge ok">{i.item_rules} özel kural</span>}
                          {!i.differs && !i.item_rules && <span className="badge muted">grup kuralları</span>}
                        </td>
                        <td><button className="secondary small" onClick={() => { setItemCode(i.code); setSel(null); }}>Özelleştir</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          {/* Tüm kurallar (grup) */}
          {rules.data && rules.data.length > 0 && (
            <>
              <h2>Tanımlı kurallar <span className="muted" style={{ fontWeight: 400 }}>— {f.product_group || "(grupsuz)"} ({rules.data.length})</span></h2>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Kapsam</th><th>Önceki</th><th>Önceki YM</th><th>Sonraki</th><th>Sonraki YM</th><th>Kural</th><th>Not</th><th></th></tr></thead>
                  <tbody>
                    {rules.data.map((r) => (
                      <tr key={r.id}>
                        <td>{r.scope === "item" ? <span className="badge ok">stok · {r.item_code}</span> : <span className="badge muted">grup</span>}</td>
                        <td>{r.from_op}</td><td className="muted"><code>{r.from_wip_code || "—"}</code></td><td>{r.to_op}</td><td className="muted"><code>{r.to_wip_code || "—"}</code></td><td>{r.description}</td><td className="muted">{r.note}</td>
                        <td>{canEdit && <button className="danger small" onClick={() => remove(r.id)}>Sil</button>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
          <p className="muted">Toplu yükleme: Excel Import → “Senaryo Kuralları” (operasyon + yarımamül kodu kolonları). Günlük üretim import’unda yarımamül kodu + miktar yeterlidir.</p>
        </>
      )}
    </>
  );
}

function Arrow({ t, active, onClick }: { t: FlowTransition; active: boolean; onClick: () => void }) {
  const r = t.effective;
  return (
    <button className={`flow-arrow ${r.source} ${active ? "active" : ""}`} onClick={onClick} title="Kuralı düzenle">
      <span className="flow-arrow-rule">{r.rule === "cycles" ? (r.lag_cycles > 0 ? `${fmt(r.lag_cycles, 0)} çevrim sonra` : "birlikte") : "bitince"}{r.wait_minutes ? ` +${fmt(r.wait_minutes, 0)} dk` : ""}</span>
      <span className="flow-arrow-line">⟶</span>
      <span className="muted" style={{ fontSize: 11 }}>{r.source === "item" ? "stok kuralı" : r.source === "group" ? "grup kuralı" : "varsayılan"}</span>
    </button>
  );
}

function RuleBadge({ r }: { r: Rule }) {
  return <span className={`badge ${r.rule === "cycles" ? "ok" : "muted"}`}>{r.description}</span>;
}
function SourceBadge({ s }: { s: Rule["source"] }) {
  return <span className={`badge ${s === "item" ? "ok" : s === "group" ? "warn" : "muted"}`}>{s === "item" ? "stok" : s === "group" ? "grup" : "varsayılan"}</span>;
}

function RuleEditor({ t, scopeItem, canEdit, onSave, onDelete }: { t: FlowTransition; scopeItem: string; canEdit: boolean; onSave: (r: RuleKind, lag: number, wait: number, note: string) => void; onDelete: (id: number) => void }) {
  const own = scopeItem ? t.item_rule : t.group_rule; // bu kapsamda yazili kural
  const base = own ?? t.effective;
  const [rule, setRule] = useState<RuleKind>(base.rule);
  const [lag, setLag] = useState(base.lag_cycles);
  const [wait, setWait] = useState(base.wait_minutes);
  const [note, setNote] = useState(own?.note ?? "");
  return (
    <div className="panel" style={{ borderLeft: "4px solid #4f46e5" }}>
      <h2 style={{ marginTop: 0 }}>
        {t.from_op} → {t.to_op}
        {(t.from_wip_code || t.to_wip_code) && <span className="muted" style={{ fontWeight: 400 }}> · <code>{t.from_wip_code || "?"}</code> → <code>{t.to_wip_code || "?"}</code></span>}
        <span className="muted" style={{ fontWeight: 400 }}> · {scopeItem ? `${scopeItem} için özel kural` : "ürün grubu kuralı"}</span>
      </h2>
      {scopeItem && t.group_rule && <p className="muted">Grup kuralı: {t.group_rule.description}. {own ? "Bu stok için ezildi." : "Kaydederseniz bu stok için ezilir."}</p>}
      <div className="row" style={{ alignItems: "flex-end" }}>
        <label>Sonraki operasyon ne zaman başlar?
          <select value={rule} onChange={(e) => setRule(e.target.value as RuleKind)} disabled={!canEdit}>
            <option value="finish">Önceki operasyon bitince</option>
            <option value="cycles">Önceki operasyon N çevrim tamamlayınca (iç içe)</option>
          </select>
        </label>
        {rule === "cycles" && (
          <label>Çevrim (adet) sayısı N
            <input type="number" min={0} step={1} value={lag} onChange={(e) => setLag(Number(e.target.value))} disabled={!canEdit} style={{ width: 110 }} />
          </label>
        )}
        <label>Ek bekleme (dk)
          <input type="number" min={0} step={5} value={wait} onChange={(e) => setWait(Number(e.target.value))} disabled={!canEdit} style={{ width: 110 }} title="Kuruma, soğuma, taşıma vb. öngörülen bekleme" />
        </label>
        <label>Not<input value={note} onChange={(e) => setNote(e.target.value)} disabled={!canEdit} placeholder="örn. 5 parça sıvandıktan sonra forma başlar" /></label>
        {canEdit && <button onClick={() => onSave(rule, lag, wait, note)}>Kaydet</button>}
        {canEdit && own?.id && <button className="danger" onClick={() => onDelete(own.id!)}>{scopeItem ? "Özel kuralı sil" : "Kuralı sil"}</button>}
      </div>
      <p className="muted" style={{ marginBottom: 0 }}>
        Örnek: <i>Sıvama → Forma</i> için “5 çevrim tamamlayınca” ⇒ 5 parça sıvandığında forma başlar; forma sıvamadan önce bitemez (son parça sıvamadan sonra gelir).
      </p>
    </div>
  );
}
