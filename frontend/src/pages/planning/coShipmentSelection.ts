import type { CoShipmentSelection, Order } from "../../api";

export const filterFields = ["order_no", "position_no", "customer", "item_code"] as const;
export type FilterField = typeof filterFields[number];
export type ShipmentFilters = Record<FilterField, string[]>;
export const emptyFilters = (): ShipmentFilters => ({ order_no: [], position_no: [], customer: [], item_code: [] });
export const filterValue = (order: Order, field: FilterField) => order[field] || "—";

/** Match all fields on the same position; values within a field are alternatives. */
export function filterShipmentOrders(orders: Order[], filters: ShipmentFilters): Order[] {
  const sets = filterFields.map((field) => new Set(filters[field]));
  return orders.filter((order) => filterFields.every((field, i) => !sets[i].size || sets[i].has(filterValue(order, field))));
}

/** Change only matching positions, preserving selections hidden by a filter. */
export function selectShipmentPositions(
  selections: CoShipmentSelection[], allOrders: Order[], visible: Order[], checked: boolean,
): CoShipmentSelection[] {
  const all = new Map<string, Set<string>>();
  const changes = new Map<string, Set<string>>();
  for (const order of allOrders) {
    if (!all.has(order.order_no)) all.set(order.order_no, new Set());
    all.get(order.order_no)!.add(order.position_no || "");
  }
  for (const order of visible) {
    if (!changes.has(order.order_no)) changes.set(order.order_no, new Set());
    changes.get(order.order_no)!.add(order.position_no || "");
  }
  const result = new Map(selections.map((s) => [s.order_no, s]));
  for (const [orderNo, positions] of changes) {
    const current = result.get(orderNo);
    const selected = new Set(current?.position_nos === null ? all.get(orderNo) : current?.position_nos ?? []);
    for (const position of positions) checked ? selected.add(position) : selected.delete(position);
    if (!selected.size) result.delete(orderNo);
    else result.set(orderNo, { order_no: orderNo, position_nos: selected.size === all.get(orderNo)?.size ? null : [...selected] });
  }
  return [...result.values()];
}
