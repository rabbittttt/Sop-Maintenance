import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {randomUUID,randomFillSync} from 'node:crypto';
import vm from 'node:vm';

const nodes=new Map(),storage=new Map(),requests=[];
const element=()=>({innerHTML:'',textContent:'',value:'',focus(){},setAttribute(){},removeAttribute(){},open:false});
for(const selector of ['#app','#grid-error','#current-time','#page-title','#owner-btn','#edit-dialog'])nodes.set(selector,element());
const initial={current_month:'2026-10',plan_end:'2027-12',now:'2026-10-30T12:00:00+08:00',meta:{},
 settings:{generation_mode:'summary'},generation_view:{mode:'summary',hierarchy:true,rule_signature:'rule'},
 products:[{id:'known',brand:'享界',model:'已有车型 2026款',version:'Max',active:true,revision:1}],plans:[],sales:[],model_sales:[],shipments:[],
 generation_members:{products:[],plans:[],sales:[],model_sales:[],groups:[]},workbook_sync:{status:'absent'}};
let serverState=structuredClone(initial),result;
const context=vm.createContext({initial:structuredClone(initial),
 document:{querySelector:s=>nodes.get(s)||null,querySelectorAll:()=>[]},
 localStorage:{getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v)},
 crypto:{randomUUID,getRandomValues:a=>randomFillSync(a)},clearTimeout(){},setTimeout(callback){callback();},
 fetch:async(path,options={})=>{
  requests.push({path,body:options.body?JSON.parse(options.body):null});
  if(path==='/api/plans/batch-edit')return {ok:true,status:200,json:async()=>result};
  if(path==='/api/state')return {ok:true,status:200,json:async()=>structuredClone(serverState)};
  throw Error('Unexpected request '+path);
 }});
const source=readFileSync(new URL('../web/app.js',import.meta.url),'utf8');
vm.runInContext(source.split("document.addEventListener('click'")[0],context);
const run=code=>vm.runInContext(code,context),plain=value=>JSON.parse(JSON.stringify(value));
run("let searchTimer;state=initial;owner='测试';metric='locked_orders';toast=()=>{};openDialog=()=>{throw Error('unexpected modal')};indexData();planning()");
assert.match(nodes.get('#app').innerHTML,/data-action="add-model"/,'New models have a visible entry before entering maintenance.');
assert.equal(run("writeActions.has('add-model')"),true);
run("brand='享界';model='已有车型 2026款';query='Max';planning()");
await run("handleAction('add-model',{})");
assert.match(nodes.get('#app').innerHTML,/data-new-row=/);
assert.match(nodes.get('#app').innerHTML,/无锁单也可提前排量/);
assert.equal((nodes.get('#app').innerHTML.match(/data-action="grid-add"/g)||[]).length,1);
run("const newModel=[...gridDraft.rows.values()].find(r=>r.isNew&&!r.virtual);newModel.brand='智界';newModel.model='筹备车型 2027款';newModel.values['2027-03']='123'");
const payload=plain(run('gridPayload()'));
assert.equal(payload.entries.length,1);assert.equal(payload.entries[0].plan_level,'model');
assert.equal(payload.entries[0].version,'车型总量（不分版本）');
assert.deepEqual(payload.entries[0].plans,[{month:'2027-03',sop:123}]);
assert.equal(payload.changes.length,0);assert.equal(payload.catalog_changes.length,0);
const manual={id:'manual-future',brand:'智界',model:'筹备车型 2027款',version:'车型总量（不分版本）',plan_level:'model',
 source_backed:false,source_present:false,active:true,revision:1,start_month:'2026-10'};
serverState={...structuredClone(initial),products:[...initial.products,manual],plans:[{product_id:manual.id,month:'2027-03',sop:123,revision:1}]};
result={ok:true,created_count:1,saved_count:1,entry_ids:{[payload.entries[0].client_id]:manual.id}};
await run('saveGrid()');
assert.equal(run('gridDraft'),null);assert.equal(run('brand'),'');assert.equal(run('model'),'');assert.equal(run('query'),'');
assert.match(nodes.get('#app').innerHTML,/筹备车型 2027款/,'The saved model is not hidden by the previous model/search filters.');
assert.equal(run("sumPlan(products(),'2027-03','sop').value"),123);
assert.equal(run("sumSales([state.products.find(p=>p.id==='manual-future')],'2027-03',salesIndex,true).value"),null);
assert.equal(run('state.sales.length'),0);assert.equal(run('state.model_sales.length'),0);
assert(!requests.some(r=>r.path.includes('plan-workbook')),'Creating a future model must not require a maintenance workbook.');

// New Wenjie models remain discoverable even if the old display toggle was off.
run("showWenjie=false;brand='享界';query='Max';model='已有车型 2026款';planning()");
await run("handleAction('add-model',{})");
run("const wenjie=[...gridDraft.rows.values()].find(r=>r.isNew&&!r.virtual);wenjie.brand='问界';wenjie.model='筹备车型 2028款';wenjie.values['2027-04']='8'");
const second=plain(run('gridPayload()'));
serverState={...structuredClone(serverState),products:[...serverState.products,{...manual,id:'manual-wenjie',brand:'问界',model:'筹备车型 2028款'}],
 plans:[...serverState.plans,{product_id:'manual-wenjie',month:'2027-04',sop:8,revision:1}]};
result={ok:true,created_count:1,saved_count:1,entry_ids:{[second.entries[0].client_id]:'manual-wenjie'}};
await run('saveGrid()');
assert.equal(run('showWenjie'),true);assert.equal(storage.get('sop-show-wenjie'),'true');
assert.match(nodes.get('#app').innerHTML,/筹备车型 2028款/);

// A fresh catalog can add its very first model through the same entry.
run("state={...initial,products:[],plans:[]};gridDraft=null;indexData();planning()");
await run("handleAction('add-model',{})");
assert.equal(run('gridDraft.rows.size'),1);assert.equal(run('[...gridDraft.rows.values()][0].isModel'),true);
console.log('PASS: visible future-model creation uses the inline table, accepts totals without sales/versions, preserves blank locks and clears stale filters.');
