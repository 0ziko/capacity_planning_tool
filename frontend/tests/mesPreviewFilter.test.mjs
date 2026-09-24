import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";
const source = readFileSync(new URL("../src/pages/mesPreviewFilter.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } });
const { filterMesPreview } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
const rows = [
  {detail_id:"free", preview_category:"free_stock", action:"new", mapping:{status:"free_stock",consumption_status:"pending"}},
  {detail_id:"reconcile", preview_category:"pending", action:"unchanged", mapping:{status:"mapped",consumption_status:"pending"}},
  {detail_id:"known", preview_category:"mapped", action:"updated", mapping:{status:"mapped",consumption_status:"known"}},
  {detail_id:"error", preview_category:"unresolved", action:"unchanged", mapping:{status:"unresolved"}},
];
test("free stock with uncertain consumption never appears in reconciliation filter", () => {
  assert.deepEqual(filterMesPreview(rows,"pending").map(r=>r.detail_id), ["reconcile"]);
  assert.deepEqual(filterMesPreview(rows,"free_stock").map(r=>r.detail_id), ["free"]);
  assert.deepEqual(filterMesPreview(rows,"unresolved").map(r=>r.detail_id), ["error"]);
});
test("new or updated is a separate action filter; all retains every row", () => {
  assert.deepEqual(filterMesPreview(rows,"all"),rows);
  assert.deepEqual(filterMesPreview(rows,"changed").map(r=>r.detail_id),["free","known"]);
  assert.deepEqual(filterMesPreview([],"pending"),[]);
});
