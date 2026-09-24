import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import ts from 'typescript';
const {outputText}=ts.transpileModule(readFileSync(new URL('../src/pages/planning/revisionDiff.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}});
const {filterDiffs,summarize,unmetChangeIds,deltaLabel,buildDueDrafts,applyToDrafts,draftsToChanges,diffsToCsv,shiftIsoDate}=await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);

const row=(o)=>({order_id:1,order_no:'S-1',position_no:'',customer:'M01',item_code:'P1',quantity:10,due_date:'2026-11-01',due_before:'2026-11-01',due_after:'2026-10-18',end_before:'2026-10-30',end_after:'2026-10-20',delta_days:-10,status_before:'on_time',status_after:'on_time',lateness_before:-2,lateness_after:2,change_kind:'',change_id:null,requested:false,met:null,pushed:false,pulled_forward:true,newly_late:false,bumped:false,...o});
const rows=[
 row({order_id:1,order_no:'S-1',requested:true,met:true,change_kind:'revised_due_date',change_id:11}),
 row({order_id:2,order_no:'S-2',requested:true,met:false,change_kind:'revised_due_date',change_id:12,status_after:'late'}),
 row({order_id:3,order_no:'S-3',customer:'M07',pushed:true,pulled_forward:false,delta_days:5,newly_late:true,status_after:'late'}),
 row({order_id:4,order_no:'S-4',customer:'M08',pushed:true,pulled_forward:false,delta_days:2}),
 row({order_id:5,order_no:'S-5',pulled_forward:false,delta_days:0,bumped:true}),
];

test('filters isolate requested, unmet, pushed, newly late and bumped rows',()=>{
 assert.deepEqual(filterDiffs(rows,'requested').map(r=>r.order_no),['S-1','S-2']);
 assert.deepEqual(filterDiffs(rows,'unmet').map(r=>r.order_no),['S-2']);
 assert.deepEqual(filterDiffs(rows,'pushed').map(r=>r.order_no),['S-3','S-4']);
 assert.deepEqual(filterDiffs(rows,'newly_late').map(r=>r.order_no),['S-3']);
 assert.deepEqual(filterDiffs(rows,'bumped').map(r=>r.order_no),['S-5']);
 assert.deepEqual(filterDiffs(rows,'all','m07').map(r=>r.order_no),['S-3']);
 assert.equal(filterDiffs(rows,'all').length,5);
});

test('summary matches server rule and unmet ids point at draft changes',()=>{
 const s=summarize(rows);
 assert.deepEqual(s,{requested:2,met:1,unmet:1,pushed:2,newly_late:1,pulled_forward:2,unchanged:1,bumped:1});
 assert.deepEqual(unmetChangeIds(rows),[12]);
 assert.equal(deltaLabel(5),'+5 gün'); assert.equal(deltaLabel(-3),'−3 gün'); assert.equal(deltaLabel(0),'0'); assert.equal(deltaLabel(null),'—');
});

test('due drafts keep per-order dates, shift relative to effective due and only changed rows become changes',()=>{
 const orders=[{id:1,order_no:'A',position_no:'',customer:'K',item_code:'P',due_date:'2026-11-01',effective_due_date:'2026-10-25',planned_end:'2026-10-20'},{id:2,order_no:'B',position_no:'10',customer:'K',item_code:'P',due_date:'2026-11-08',effective_due_date:'2026-11-08',planned_end:null}];
 let drafts=buildDueDrafts(orders,[2,1],{1:'2026-10-01'});
 assert.deepEqual(drafts.map(d=>[d.order_no,d.current_due,d.new_due]),[['B','2026-11-08',''],['A','2026-10-25','2026-10-01']]);
 drafts=applyToDrafts(drafts,'shift',-14);
 assert.deepEqual(drafts.map(d=>d.new_due),['2026-10-25','2026-10-11']);
 drafts=applyToDrafts(drafts,'same','2026-10-25');
 assert.deepEqual(draftsToChanges(drafts),[{entity_type:'order',entity_id:2,field:'revised_due_date',new_value:'2026-10-25'}]);
 assert.equal(shiftIsoDate('2026-03-01',-1),'2026-02-28');
 assert.equal(shiftIsoDate('bad',1),'');
});

test('csv export is Excel friendly and escapes quotes',()=>{
 const csv=diffsToCsv([row({customer:'A "B"'})]);
 assert.ok(csv.startsWith('﻿"Sipariş";'));
 assert.ok(csv.includes('"A ""B"""'));
 assert.equal(csv.split('\n').length,2);
});
