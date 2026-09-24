import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import ts from 'typescript';
const {outputText}=ts.transpileModule(readFileSync(new URL('../src/pages/planning/ganttFlow.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}});
const {parseCodes,flowGroups,flowLayout}=await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
const base={order_no:'PARTI',position_no:'',batch_order_nos:['O1/1','O2/2'],item_code:'6001',semi_finished_code:'5001-02',operation_seq:10,planned_start:'2026-09-21',planned_end:'2026-09-22'};
test('exact multi selection and blank selection never return catalog',()=>{
 assert.deepEqual(parseCodes('6001, 6002;6001\n6003'),['6001','6002','6003']);
 assert.deepEqual(flowGroups([base],'item',[]),[]);
 assert.equal(flowGroups([base],'item',['600'])[0].bars.length,0);
 assert.equal(flowGroups([base],'order',['O2'])[0].bars.length,1);
 assert.equal(flowGroups([base],'order',['O'])[0].bars.length,0);
});
test('parallel operations share an entity row but keep dates and non-overlapping labels',()=>{
 const rows=[{...base,plan_line_id:1},{...base,plan_line_id:2}];
 assert.equal(flowGroups(rows,'item',['6001']).length,1);
 const layout=flowLayout(rows,'2026-09-21','2026-09-27',24);
 assert.deepEqual(layout.boxes.map(b=>[b.x,b.width,b.lane]),[[0,48,0],[0,48,1]]);
});
