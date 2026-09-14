import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

// Execute the pure TypeScript selection logic using the project's compiler.
const source = readFileSync(new URL("../src/pages/planning/coShipmentSelection.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } });
const { emptyFilters, filterShipmentOrders, selectShipmentPositions } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
const orders = [
  { id: 1, order_no: "A", position_no: "10", customer: "Y", item_code: "X" },
  { id: 2, order_no: "A", position_no: "20", customer: "Y", item_code: "Z" },
  { id: 3, order_no: "B", position_no: "10", customer: "Q", item_code: "X" },
  { id: 4, order_no: "C", position_no: "", customer: "Y", item_code: "X" },
];

test("all fields match the same position; values in a field use OR", () => {
  const f = { ...emptyFilters(), item_code: ["X"], customer: ["Y"] };
  assert.deepEqual(filterShipmentOrders(orders, f).map(o => o.id), [1, 4]);
  assert.deepEqual(filterShipmentOrders(orders, { ...f, customer: ["Y", "Q"] }).map(o => o.id), [1, 3, 4]);
  assert.deepEqual(filterShipmentOrders(orders, { ...f, order_no: ["A"], position_no: ["20"] }), []);
  assert.deepEqual(filterShipmentOrders(orders, { ...f, order_no: ["A"], position_no: ["10"] }).map(o => o.id), [1]);
  assert.deepEqual(filterShipmentOrders(orders, emptyFilters()), orders);
});

test("filtered select does not select hidden positions and preserves hidden orders", () => {
  const selection = selectShipmentPositions([{ order_no: "B", position_nos: null }], orders, [orders[0]], true);
  assert.deepEqual(selection, [{ order_no: "B", position_nos: null }, { order_no: "A", position_nos: ["10"] }]);
  assert.deepEqual(selectShipmentPositions(selection, orders, [orders[0]], false), [{ order_no: "B", position_nos: null }]);
});

test("partial uncheck expands all-positions selection; reselect restores all", () => {
  const partial = selectShipmentPositions([{ order_no: "A", position_nos: null }], orders, [orders[0]], false);
  assert.deepEqual(partial, [{ order_no: "A", position_nos: ["20"] }]);
  assert.deepEqual(selectShipmentPositions(partial, orders, [orders[0]], true), [{ order_no: "A", position_nos: null }]);
});

test("empty position numbers are preserved as API values", () => {
  const rows = [...orders, { ...orders[3], id: 5, position_no: "20" }];
  const selection = selectShipmentPositions([], rows, [orders[3]], true);
  assert.deepEqual(selection, [{ order_no: "C", position_nos: [""] }]);
  assert.deepEqual(filterShipmentOrders(rows, { ...emptyFilters(), position_no: ["—"] }).map(o => o.id), [4]);
});

test("bulk selection covers all filtered pages without duplicate positions", () => {
  const rows = Array.from({ length: 120 }, (_, i) => ({ ...orders[0], id: i + 1, order_no: `ORD-${i}` }));
  const selected = selectShipmentPositions([], rows, rows, true);
  assert.equal(selected.length, 120);
  assert.deepEqual(selectShipmentPositions(selected, rows, rows, true), selected);
  assert.deepEqual(selectShipmentPositions(selected, rows, rows, false), []);
});
