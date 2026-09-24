import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import ts from 'typescript';
const {outputText}=ts.transpileModule(readFileSync(new URL('../src/api.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}});
const {api,ApiError,trackedAutoPlan,trackedMesPreview,trackedMesImport,trackedMergeImpact,MERGE_IMPACT_JOB_KEY,MES_IMPORT_JOB_KEY,AUTO_PLAN_JOB_KEY}=await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
function storage(){
 const values=new Map();
 globalThis.sessionStorage={getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};
}
test('lost start response recovers using identity saved before posting',async()=>{
 storage(); let posts=0; let expected;
 api.post=async url=>{posts++;expected=sessionStorage.getItem(AUTO_PLAN_JOB_KEY);assert.ok(url.endsWith(expected));throw new ApiError(0,'timeout');};
 api.get=async url=>{assert.ok(url.endsWith(expected));return {status:'done',result:{created:12},phase:'Plan kaydedildi',elapsed_seconds:25};};
 assert.deepEqual(await trackedAutoPlan({weeks:8}),{created:12});
 assert.equal(posts,1);assert.equal(sessionStorage.getItem(AUTO_PLAN_JOB_KEY),null);
});
test('missing server job never automatically reposts a plan',async()=>{
 storage();sessionStorage.setItem(AUTO_PLAN_JOB_KEY,'old');
 api.post=async()=>assert.fail('must not repost');
 api.get=async()=>{throw new ApiError(404,'missing');};
 await assert.rejects(trackedAutoPlan(),/otomatik tekrarlanmadı/);
 assert.equal(sessionStorage.getItem(AUTO_PLAN_JOB_KEY),null);
});
test('temporary polling failure keeps identity for recovery',async()=>{
 storage();sessionStorage.setItem(AUTO_PLAN_JOB_KEY,'active');
 api.get=async()=>{throw new ApiError(0,'connection');};
 await assert.rejects(trackedAutoPlan(),/mevcut işin takibi/);
 assert.equal(sessionStorage.getItem(AUTO_PLAN_JOB_KEY),'active');
});
test('MES preview polls without importing or uploading again',async()=>{
 globalThis.window={setTimeout:fn=>fn()};
 let uploads=0;const phases=[];
 api.upload=async url=>{assert.equal(url,'/api/mes/preview/jobs');uploads++;return {id:'mes1',status:'queued',phase:'Sırada',elapsed_seconds:0};};
 api.get=async url=>{assert.equal(url,'/api/mes/preview/jobs/mes1');return {status:'done',result:{token:'review-token'}};};
 assert.deepEqual(await trackedMesPreview({},p=>phases.push(p)),{token:'review-token'});
 assert.equal(uploads,1);assert.ok(phases.some(p=>p.includes('Sırada')));
});
test('MES import survives a lost start response without re-uploading',async()=>{
 storage();let uploads=0;let key;
 api.upload=async(url,file,params)=>{uploads++;key=sessionStorage.getItem(MES_IMPORT_JOB_KEY);assert.equal(params.request_id,key);throw new ApiError(0,'timeout');};
 api.get=async url=>{assert.ok(url.endsWith(key));return {status:'done',phase:'Kaydedildi',result:{counts:{new:1}}};};
 assert.deepEqual(await trackedMesImport({},'token'),{counts:{new:1}});
 assert.equal(uploads,1);assert.equal(sessionStorage.getItem(MES_IMPORT_JOB_KEY),null);
});
test('MES import reload resumes only polling and keeps identity on disconnection',async()=>{
 storage();sessionStorage.setItem(MES_IMPORT_JOB_KEY,'existing');
 api.upload=async()=>assert.fail('must not upload again');
 api.get=async()=>{throw new ApiError(0,'disconnected');};
 await assert.rejects(trackedMesImport(),/aynı işlemin takibine/);
 assert.equal(sessionStorage.getItem(MES_IMPORT_JOB_KEY),'existing');
 api.get=async()=>({status:'done',result:{counts:{new:1}}});
 assert.deepEqual(await trackedMesImport(),{counts:{new:1}});
});
test('MES import missing job does not silently repeat approval',async()=>{
 storage();sessionStorage.setItem(MES_IMPORT_JOB_KEY,'lost');
 api.get=async()=>{throw new ApiError(404,'missing');};
 await assert.rejects(trackedMesImport(),/otomatik tekrarlanmadı/);
 assert.equal(sessionStorage.getItem(MES_IMPORT_JOB_KEY),null);
});
test('merge analysis recovers same selection without duplicate request',async()=>{
 storage();const req={merge_groups:[{order_ids:[1,2]}]};let posts=0;
 api.post=async(url)=>{posts++;return {id:JSON.parse(sessionStorage.getItem(MERGE_IMPACT_JOB_KEY)).id};};
 api.get=async()=>{throw new ApiError(0,'connection');};
 await assert.rejects(trackedMergeImpact(req,()=>{},()=>true),/Aynı seçimle/);
 api.get=async()=>({status:'done',phase:'Tamamlandı',result:{merge_count:1}});
 assert.deepEqual(await trackedMergeImpact(req,()=>{},()=>true),{merge_count:1});
 assert.equal(posts,1);assert.equal(sessionStorage.getItem(MERGE_IMPACT_JOB_KEY),null);
});
test('merge analysis never uses cached results for a different selection',async()=>{
 storage();sessionStorage.setItem(MERGE_IMPACT_JOB_KEY,JSON.stringify({id:'old',payload:JSON.stringify({weeks:1})}));
 let posts=0;
 api.post=async()=>{posts++;return {id:'new'};};
 api.get=async url=>{assert.ok(url.endsWith('/new'));return {status:'done',result:{merge_count:2}};};
 assert.deepEqual(await trackedMergeImpact({weeks:2},()=>{},()=>true),{merge_count:2});
 assert.equal(posts,1);
});
