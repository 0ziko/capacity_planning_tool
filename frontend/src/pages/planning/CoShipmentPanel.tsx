import { Fragment, useMemo, useState } from "react";
import { type CoShipmentOptions, type CoShipmentSelection, type Order } from "../../api";
import { ErrorText, StringMultiSelect } from "../../components";
import { emptyFilters, filterFields, filterShipmentOrders, filterValue, selectShipmentPositions } from "./coShipmentSelection";

const FILTER_LABELS = { order_no: "Sipariş", position_no: "Poz", customer: "Müşteri", item_code: "Stok kodu" };
const PAGE_SIZE = 50;

function fmtDate(iso: string) {
  return new Date(iso + "T12:00:00").toLocaleDateString("tr-TR");
}

export type OrderGroup = {
  order_no: string;
  customer: string;
  due_date: string;
  positions: { position_no: string; order_id: number; item_code: string }[];
};

export function groupOpenOrders(orders: Order[]): OrderGroup[] {
  const map = new Map<string, OrderGroup>();
  for (const o of orders) {
    if (!o.item_code) continue;
    const g = map.get(o.order_no) ?? {
      order_no: o.order_no,
      customer: o.customer || "—",
      due_date: o.effective_due_date || o.due_date,
      positions: [],
    };
    g.positions.push({ position_no: o.position_no || "", order_id: o.id, item_code: o.item_code });
    map.set(o.order_no, g);
  }
  return Array.from(map.values()).sort((a, b) => a.due_date.localeCompare(b.due_date) || a.order_no.localeCompare(b.order_no));
}

export default function CoShipmentPanel({
  orders,
  value,
  onChange,
  loading = false,
  error = "",
  onReload,
}: {
  orders: Order[];
  value: CoShipmentOptions;
  onChange: (v: CoShipmentOptions) => void;
  loading?: boolean;
  error?: string;
  onReload?: () => void;
}) {
  const open = value.enabled;
  const [expanded, setExpanded] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<string[]>([]);
  const [filters, setFilters] = useState(emptyFilters);
  const [page, setPage] = useState(0);
  const eligible = useMemo(() => orders.filter((o) => !!o.item_code), [orders]);
  const groups = useMemo(() => groupOpenOrders(eligible), [eligible]);
  const filtered = useMemo(() => filterShipmentOrders(eligible, filters), [eligible, filters]);
  const shownGroups = useMemo(() => groupOpenOrders(filtered), [filtered]);
  const fullGroups = useMemo(() => new Map(groups.map((g) => [g.order_no, g])), [groups]);
  const options = useMemo(() => Object.fromEntries(filterFields.map((field) => [field,
    [...new Set(eligible.map((o) => filterValue(o, field)))].sort((a, b) => a.localeCompare(b, "tr", { numeric: true })),
  ])) as Record<typeof filterFields[number], string[]>, [eligible]);
  const hasFilters = filterFields.some((field) => filters[field].length > 0);
  const pages = Math.max(1, Math.ceil(shownGroups.length / PAGE_SIZE));
  const currentPage = Math.min(page, pages - 1);

  const selMap = new Map<string, CoShipmentSelection>(value.selections.map((s) => [s.order_no, s]));
  const select = (rows: Order[], checked: boolean) => onChange({ ...value,
    selections: selectShipmentPositions(value.selections, eligible, rows, checked),
  });

  const targetFor = (due: string) => {
    if (!value.enabled) return "—";
    const d = new Date(due + "T12:00:00");
    d.setDate(d.getDate() - value.ready_before_delivery_days);
    return d.toLocaleDateString("tr-TR");
  };

  const toggleEnabled = (on: boolean) => {
    onChange({ ...value, enabled: on, selections: on ? value.selections : [] });
  };

  const toggleOrder = (orderNo: string, on: boolean) => {
    select(filtered.filter((o) => o.order_no === orderNo), on);
  };

  const togglePosition = (orderNo: string, pos: string, on: boolean) => {
    select(eligible.filter((o) => o.order_no === orderNo && (o.position_no || "") === pos), on);
  };

  const isPosOn = (orderNo: string, pos: string) => {
    const s = selMap.get(orderNo);
    if (!s) return false;
    if (s.position_nos === null) return true;
    return s.position_nos.includes(pos);
  };

  const selectAll = () => select(filtered, true);
  const clearAll = () => onChange({ ...value, selections: [] });

  return (
    <div className="panel co-shipment-panel">
      <label style={{ display: "flex", gap: 8, alignItems: "center", cursor: "pointer", marginBottom: open ? 10 : 0 }}>
        <input type="checkbox" checked={open} onChange={(e) => toggleEnabled(e.target.checked)} />
        <strong>Birlikte sevk modu</strong>
        <span className="muted">(seçili pozlar aynı haftada bitsin · termin − X gün hedefi)</span>
      </label>

      {open && (
        <>
          <div className="row" style={{ flexWrap: "wrap", gap: 12, marginBottom: 10 }}>
            <label>
              Terminden kaç gün önce hazır
              <input
                type="number"
                min={0}
                max={365}
                value={value.ready_before_delivery_days}
                onChange={(e) => onChange({ ...value, ready_before_delivery_days: Math.min(365, Math.max(0, Number(e.target.value) || 0)) })}
                style={{ width: 72, marginLeft: 8 }}
              />
            </label>
            <button type="button" className="secondary small" disabled={!filtered.length || loading || !!error} onClick={selectAll}>Listelenen pozları seç</button>
            <button type="button" className="secondary small" disabled={!filtered.length} onClick={() => select(filtered, false)}>Listelenenlerin seçimini kaldır</button>
            <button type="button" className="secondary small" disabled={!value.selections.length} onClick={clearAll}>Tüm sevk seçimini kaldır</button>
            <span className="muted">{value.selections.length} sipariş seçili</span>
          </div>

          <div className="row" style={{ flexWrap: "wrap", gap: 12, marginBottom: 10 }}>
            {filterFields.map((field) => <StringMultiSelect key={field} label={FILTER_LABELS[field]} options={options[field]} value={filters[field]}
              onChange={(selected) => { setFilters({ ...filters, [field]: selected }); setPage(0); setCollapsed([]); }} />)}
            <button type="button" className="secondary small" disabled={!hasFilters} onClick={() => { setFilters(emptyFilters()); setPage(0); }}>Filtreleri temizle</button>
          </div>
          <p className="muted">Aynı alandaki seçimler alternatif, farklı alanlar birlikte uygulanır. Filtreleme sevk seçimini değiştirmez.</p>
          <ErrorText err={error} />
          {error && onReload && <button type="button" className="secondary small" onClick={onReload}>Siparişleri yeniden yükle</button>}
          {loading && <p role="status">Siparişler yükleniyor…</p>}
          <p className="muted" role="status">{shownGroups.length} / {groups.length} sipariş · {filtered.length} / {eligible.length} poz listeleniyor</p>
          {shownGroups.length === 0 ? (
            !loading && !error && <p className="muted">{hasFilters ? "Filtrelere uygun sipariş veya poz yok." : "Açık sipariş yok."}</p>
          ) : (
            <div className="table-wrap" style={{ maxHeight: 280 }}>
              <table>
                <thead>
                  <tr>
                    <th>Seç</th>
                    <th>Sipariş</th>
                    <th>Müşteri</th>
                    <th>Termin</th>
                    <th className="num">Poz (görünen / toplam)</th>
                    <th>Hedef hazır</th>
                  </tr>
                </thead>
                <tbody>
                  {shownGroups.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE).map((g) => {
                    const selected = g.positions.filter((p) => isPosOn(g.order_no, p.position_no)).length;
                    const on = selected > 0;
                    const allOn = selected === g.positions.length;
                    const isExp = hasFilters ? !collapsed.includes(g.order_no) : expanded === g.order_no;
                    return (
                      <Fragment key={g.order_no}>
                        <tr key={g.order_no} className={on ? "on" : ""}>
                          <td>
                            <input type="checkbox" aria-label={`${g.order_no} listelenen pozları seç`} checked={allOn}
                              ref={(el) => { if (el) el.indeterminate = on && !allOn; }} onChange={(e) => toggleOrder(g.order_no, e.target.checked)} />
                          </td>
                          <td>
                            <button type="button" className="linkish" aria-expanded={isExp} onClick={() => {
                              if (hasFilters) setCollapsed(isExp ? [...collapsed, g.order_no] : collapsed.filter((n) => n !== g.order_no));
                              else setExpanded(isExp ? null : g.order_no);
                            }}>
                              {g.order_no} {isExp ? "▾" : "▸"}
                            </button>
                          </td>
                          <td>{g.customer}</td>
                          <td>{fmtDate(g.due_date)}</td>
                          <td className="num">{g.positions.length} / {fullGroups.get(g.order_no)?.positions.length}</td>
                          <td>{on ? targetFor(g.due_date) : "—"}</td>
                        </tr>
                        {isExp && (
                          <tr key={`${g.order_no}-pos`}>
                            <td colSpan={6} style={{ background: "var(--bg)", paddingLeft: 28 }}>
                              {g.positions.map((p) => (
                                <label key={p.order_id} style={{ display: "inline-flex", gap: 6, marginRight: 16, cursor: "pointer" }}>
                                  <input
                                    type="checkbox"
                                    checked={isPosOn(g.order_no, p.position_no)}
                                    onChange={(e) => togglePosition(g.order_no, p.position_no, e.target.checked)}
                                  />
                                  Poz {p.position_no || "—"} · {p.item_code}
                                </label>
                              ))}
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {pages > 1 && <div className="row" style={{ gap: 12, marginTop: 8 }}>
            <button type="button" className="secondary small" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Önceki</button>
            <span>Sayfa {currentPage + 1} / {pages}</span>
            <button type="button" className="secondary small" disabled={currentPage + 1 >= pages} onClick={() => setPage(currentPage + 1)}>Sonraki</button>
          </div>}
          <p className="muted" style={{ margin: "8px 0 0", fontSize: "0.92em" }}>
            Mod kapalıyken veya sipariş seçilmezken planlama mevcut algoritma ile çalışır. Toplu seçim tüm sayfalardaki filtreye uygun pozları kapsar.
          </p>
        </>
      )}
    </div>
  );
}

export const defaultCoShipment = (): CoShipmentOptions => ({
  enabled: false,
  ready_before_delivery_days: 3,
  selections: [],
});
