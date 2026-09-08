import { Fragment, useMemo, useState } from "react";
import { type CoShipmentOptions, type CoShipmentSelection, type Order } from "../../api";

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
    g.positions.push({ position_no: o.position_no || "—", order_id: o.id, item_code: o.item_code });
    map.set(o.order_no, g);
  }
  return Array.from(map.values()).sort((a, b) => a.due_date.localeCompare(b.due_date) || a.order_no.localeCompare(b.order_no));
}

export default function CoShipmentPanel({
  orders,
  value,
  onChange,
}: {
  orders: Order[];
  value: CoShipmentOptions;
  onChange: (v: CoShipmentOptions) => void;
}) {
  const [open, setOpen] = useState(value.enabled);
  const [expanded, setExpanded] = useState<string | null>(null);
  const groups = useMemo(() => groupOpenOrders(orders), [orders]);

  const selectedOrders = new Set(value.selections.map((s) => s.order_no));
  const selMap = new Map<string, CoShipmentSelection>(value.selections.map((s) => [s.order_no, s]));

  const targetFor = (due: string) => {
    if (!value.enabled) return "—";
    const d = new Date(due + "T12:00:00");
    d.setDate(d.getDate() - value.ready_before_delivery_days);
    return d.toLocaleDateString("tr-TR");
  };

  const toggleEnabled = (on: boolean) => {
    setOpen(on);
    onChange({ ...value, enabled: on, selections: on ? value.selections : [] });
  };

  const toggleOrder = (orderNo: string, on: boolean) => {
    const next = value.selections.filter((s) => s.order_no !== orderNo);
    if (on) next.push({ order_no: orderNo, position_nos: null });
    onChange({ ...value, enabled: true, selections: next });
  };

  const togglePosition = (orderNo: string, pos: string, on: boolean) => {
    const cur = selMap.get(orderNo);
    const group = groups.find((g) => g.order_no === orderNo);
    if (!group) return;
    const allPos = group.positions.map((p) => p.position_no).filter((p) => p !== "—");
    let posList: string[] | null;
    if (!cur) {
      posList = on ? [pos] : [];
    } else if (cur.position_nos === null) {
      posList = on ? null : allPos.filter((p) => p !== pos);
    } else {
      posList = on ? [...new Set([...cur.position_nos, pos])] : cur.position_nos.filter((p) => p !== pos);
    }
    if (posList && posList.length === 0) {
      onChange({ ...value, selections: value.selections.filter((s) => s.order_no !== orderNo) });
      return;
    }
    if (posList && posList.length === allPos.length) posList = null;
    const next = value.selections.filter((s) => s.order_no !== orderNo);
    next.push({ order_no: orderNo, position_nos: posList });
    onChange({ ...value, enabled: true, selections: next });
  };

  const isPosOn = (orderNo: string, pos: string) => {
    const s = selMap.get(orderNo);
    if (!s) return false;
    if (s.position_nos === null) return true;
    return s.position_nos.includes(pos);
  };

  const selectAll = () => onChange({
    ...value,
    enabled: true,
    selections: groups.map((g) => ({ order_no: g.order_no, position_nos: null })),
  });
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
                onChange={(e) => onChange({ ...value, ready_before_delivery_days: Math.max(0, Number(e.target.value) || 0) })}
                style={{ width: 72, marginLeft: 8 }}
              />
            </label>
            <button type="button" className="secondary small" onClick={selectAll}>Tüm siparişleri seç</button>
            <button type="button" className="secondary small" onClick={clearAll}>Seçimi kaldır</button>
            <span className="muted">{value.selections.length} sipariş seçili</span>
          </div>

          {groups.length === 0 ? (
            <p className="muted">Açık sipariş yok.</p>
          ) : (
            <div className="table-wrap" style={{ maxHeight: 280 }}>
              <table>
                <thead>
                  <tr>
                    <th>Seç</th>
                    <th>Sipariş</th>
                    <th>Müşteri</th>
                    <th>Termin</th>
                    <th className="num">Poz</th>
                    <th>Hedef hazır</th>
                  </tr>
                </thead>
                <tbody>
                  {groups.map((g) => {
                    const on = selectedOrders.has(g.order_no);
                    const isExp = expanded === g.order_no;
                    return (
                      <Fragment key={g.order_no}>
                        <tr key={g.order_no} className={on ? "on" : ""}>
                          <td>
                            <input type="checkbox" checked={on} onChange={(e) => toggleOrder(g.order_no, e.target.checked)} />
                          </td>
                          <td>
                            <button type="button" className="linkish" onClick={() => setExpanded(isExp ? null : g.order_no)}>
                              {g.order_no} {isExp ? "▾" : "▸"}
                            </button>
                          </td>
                          <td>{g.customer}</td>
                          <td>{fmtDate(g.due_date)}</td>
                          <td className="num">{g.positions.length}</td>
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
                                    disabled={!on}
                                    onChange={(e) => togglePosition(g.order_no, p.position_no, e.target.checked)}
                                  />
                                  Poz {p.position_no} · {p.item_code}
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
          <p className="muted" style={{ margin: "8px 0 0", fontSize: "0.92em" }}>
            Mod kapalıyken veya sipariş seçilmezken planlama mevcut algoritma ile çalışır.
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
