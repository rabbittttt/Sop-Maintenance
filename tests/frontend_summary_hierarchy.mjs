import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {randomUUID,randomFillSync} from 'node:crypto';
import vm from 'node:vm';

const source=readFileSync(new URL('../web/app.js',import.meta.url),'utf8');
const month='2026-10',summary='智界 R7 2026款汇总';
const totalLabel='车型总量（不分版本）';
const parent={id:'summary-total',brand:'智界',model:'R7 2026款',catalog_model:'R7 2026款',version:totalLabel,
  plan_level:'model',summary_generation:summary,summary_member_product_ids:['member-total','a1','a2','b1'],active:true,revision:3};
const derived={id:'summary-version',brand:'智界',model:'R7 2026款',version:'Max',generation:summary,
  source_generations:[summary],summary_generation:summary,member_product_ids:['a1','b1'],read_only:true,active:true,revision:0};
const standalone={id:'standalone',brand:'享界',model:'S9 2026款',version:'Max',generation:'享界 S9 2026款',active:true,revision:1};
const members=[
  {id:'member-total',brand:'智界',model:'R7 2026款',catalog_model:'R7',version:totalLabel,plan_level:'model',
    generation:'智界 R7 2026款',parent_generation:summary,active:true,revision:4},
  {id:'a1',brand:'智界',model:'R7 2026款',catalog_model:'R7',version:'Max',generation:'智界 R7 2026款',parent_generation:summary,active:true,revision:2},
  {id:'a2',brand:'智界',model:'R7 2026款',catalog_model:'R7',version:'Pro',generation:'智界 R7 2026款',parent_generation:summary,active:true,revision:2},
  {id:'b1',brand:'智界',model:'R7 2025款',catalog_model:'R7',version:'Max',generation:'智界 R7 2025款',parent_generation:summary,active:true,revision:2},
];
const initial={current_month:month,plan_end:'2027-12',months:[month],now:'2026-10-29T12:00:00+08:00',meta:{},
  settings:{generation_mode:'summary'},generation_view:{mode:'summary',hierarchy:true,rule_signature:'live-rule'},
  products:[parent,derived,standalone],
  plans:[{product_id:parent.id,month,sop:null,auto_sop:80,auto_partial:false,revision:2},
    {product_id:derived.id,month,sop:40,read_only:true,revision:0},{product_id:standalone.id,month,sop:30,revision:1}],
  sales:[{product_id:derived.id,month,metric:'locked_orders',value:6}],
  model_sales:[{generation:summary,month,metric:'locked_orders',value:1000}],shipments:[],
  generation_members:{products:members,
    plans:[{product_id:'member-total',month,sop:50,revision:5},{product_id:'a1',month,sop:10,revision:6},
      {product_id:'a2',month,sop:20,revision:7},{product_id:'b1',month,sop:30,revision:8}],
    sales:[{product_id:'a1',month,metric:'locked_orders',value:1},{product_id:'a2',month,metric:'locked_orders',value:2},
      {product_id:'b1',month,metric:'locked_orders',value:3}],
    model_sales:[{generation:'智界 R7 2026款',month,metric:'locked_orders',value:600},
      {generation:'智界 R7 2025款',month,metric:'locked_orders',value:400}],
    groups:[{summary_generation:summary,parent_product_id:parent.id,member_product_ids:members.map(p=>p.id),brand:parent.brand,model:parent.model}]},
  workbook_sync:{status:'absent'}};
const nodes=new Map(),storage=new Map(),toasts=[];
const element=()=>({innerHTML:'',textContent:'',value:'',disabled:false,inert:false,focus(){},setAttribute(){},removeAttribute(){}});
for(const selector of ['#app','#grid-error','#table-view-name','#current-time','#owner-btn','#page-title'])nodes.set(selector,element());
const context=vm.createContext({initial:structuredClone(initial),
  document:{querySelector:s=>nodes.get(s)||null,querySelectorAll:()=>[]},
  localStorage:{getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v)},
  crypto:{randomUUID,getRandomValues:a=>randomFillSync(a)},clearTimeout(){},setTimeout(callback){callback();},
  notify:(text,error)=>toasts.push({text,error})});
vm.runInContext(source.split("document.addEventListener('click'")[0],context);
const run=code=>vm.runInContext(code,context),plain=value=>JSON.parse(JSON.stringify(value));
run("let searchTimer;state=initial;owner='测试';metric='locked_orders';toast=notify;indexData();planning()");
const app=nodes.get('#app');

// Root totals contain only summaries and independent models. Members are lookup-only.
assert.equal(run('state.products.length'),3);
assert.equal(run('allProducts().length'),7);
assert.equal(run("sumPlan(products(),'2026-10','sop').value"),110,'The parent uses member model totals, without adding members again.');
assert.equal(run("sumPlan(allProducts().filter(p=>p.parent_generation),'2026-10','sop').value"),80);
assert.equal(run("sumSales(state.products.filter(p=>p.brand==='智界'),'2026-10',salesIndex,true).value"),1000);
assert.equal(run("sumSales(allProducts().filter(p=>p.model==='R7 2026款'&&p.parent_generation),'2026-10',salesIndex,true).value"),600,
  'Expanded member models retain their original vehicle lock rows.');
assert.doesNotMatch(app.innerHTML,/id="generation-filter"|仅单代际|仅汇总代际/);
assert.doesNotMatch(app.innerHTML,/data-version="a1"|R7 2025款/,'Members start collapsed.');

// A member can have the same display label as its parent; their keys must differ.
context.parent=parent;context.member=members[1];
const parentKey=run('modelGroupKey(parent)'),memberKey=run('modelGroupKey(member)');
assert.notEqual(parentKey,memberKey);
context.parentKey=parentKey;context.memberKey=memberKey;
run('toggleModel(parentKey)');
assert.match(app.innerHTML,/R7 2025款/);
assert.doesNotMatch(app.innerHTML,/data-version="a1"/,'Expanding the summary reveals members before versions.');
run('toggleModel(memberKey)');
assert.match(app.innerHTML,/data-version="a1"/);assert.match(app.innerHTML,/data-version="a2"/);
const versionRows=[...app.innerHTML.matchAll(/<tr\b[^>]*data-plan-metric="(locks|sop)"[^>]*data-version="(a1|a2)"/g)].map(m=>[m[1],m[2]]);
assert.deepEqual(versionRows,[['locks','a1'],['locks','a2'],['sop','a1'],['sop','a2']],
  'Expanded versions keep lock rows before plan rows.');
assert.equal(run("sumPlan(products(),'2026-10','sop').value"),110);

// Each previous committee level has its own control and full-year total.
const legacyVisibility=vm.createContext({localStorage:{getItem:key=>key==='sop-show-committee'?'false':null}});
vm.runInContext(source.split("document.addEventListener('click'")[0],legacyVisibility);
assert.deepEqual(plain(vm.runInContext('showCommitteeLevels',legacyVisibility)),[false,false,false]);
const newerVisibility=vm.createContext({localStorage:{getItem:key=>key==='sop-show-committee'?'false':key==='sop-show-committee-levels'?'[true,false,true]':null}});
vm.runInContext(source.split("document.addEventListener('click'")[0],newerVisibility);
assert.deepEqual(plain(vm.runInContext('showCommitteeLevels',newerVisibility)),[true,false,true],'The three new choices override the old preference.');
run(`state.committee={previous_period:'2026-09',amounts:[
  {level:1,model_key:parentKey,month:'2026-01',sop:100},
  {level:1,model_key:parentKey,month:'2026-10',sop:0},
  {level:1,model_key:parentKey,month:'2026-12',sop:20},
  {level:2,model_key:parentKey,month:'2026-11',sop:5},
  {level:3,model_key:parentKey,month:'2026-10',sop:0}
]};indexData();planning()`);
const committeeRow=level=>app.innerHTML.match(new RegExp(`<tr class="committee-row" data-committee-level="${level}" data-committee-model="[^\"]+"[^]*?<\\/tr>`))?.[0];
assert.deepEqual(plain(run('showCommitteeLevels')),[true,true,true]);
assert.match(app.innerHTML,/上期计委[^]*?data-committee-level="1"[^]*?data-committee-level="2"[^]*?data-committee-level="3"[^]*?data-action="toggle-wenjie"/);
assert.match(app.innerHTML,/rowspan="5"/);
assert.match(committeeRow(1),/data-plan-year="2026">120<\/td>/,'The rolling annual column includes January outside the visible M+6 window.');
assert.match(committeeRow(3),/data-plan-year="2026">0<\/td>/,'Explicit zero stays visible.');
const levelTwoCells=[...committeeRow(2).matchAll(/<td\b[^>]*>[^]*?<\/td>/g)].map(match=>match[0]);
assert.match(levelTwoCells[5],/^<td class="number"><\/td>$/,'Undecided October stays blank.');
assert.match(levelTwoCells[6],/>5<\/td>$/,'November keeps its partial decision.');
await run("handleAction('toggle-committee',{dataset:{committeeLevel:'2'}})");
assert.deepEqual(plain(run('showCommitteeLevels')),[true,false,true]);
assert.equal(committeeRow(2),undefined);
assert.match(app.innerHTML,/rowspan="4"/);
assert.equal(storage.get('sop-show-committee-levels'),'[true,false,true]');
run("range='annual';planYear='2026';planning()");
assert.match(committeeRow(1),/data-plan-year="2026">120<\/td>/,'The annual view keeps the same full-year committee amount.');
assert.match(committeeRow(3),/data-plan-year="2026">0<\/td>/);
run("planYear='2027';planning()");
assert.match(committeeRow(1),/data-plan-year="2027"><\/td><\/tr>$/,'A year with no prior decision remains blank.');
run("range='rolling';bulkPlan();gridRow('summary-total').values['2026-10']='120'");
await run("handleAction('toggle-committee',{dataset:{committeeLevel:'3'}})");
assert.equal(run("gridRow('summary-total').values['2026-10']"),'120','Changing a level while editing must preserve the draft.');
run('gridDraft=null');
const currentView=plain(run('viewConfig()'));
context.currentView=currentView;
run("tableViews={active:'committee-view',views:[{id:'committee-view',name:'计委视图',config:currentView}]};selectedView='committee-view';planning()");
assert.match(run('viewControls()'),/<option value="committee-view" selected/);
run("restoreViewConfig({...currentView,showCommitteeLevels:undefined,showCommittee:false})");
assert.deepEqual(plain(run('showCommitteeLevels')),[false,false,false],'An old saved view with the single switch off hides all three levels.');
run("restoreViewConfig({...currentView,showCommitteeLevels:undefined,showCommittee:true})");
assert.deepEqual(plain(run('showCommitteeLevels')),[true,true,true]);
run("tableViews={active:'committee-view',views:[{id:'committee-view',name:'计委视图',config:{...currentView,showCommitteeLevels:undefined,showCommittee:true}}]};selectedView='committee-view'");
assert.match(run('viewControls()'),/<option value="committee-view" selected/,'A legacy view stays selected after migration.');

// Explicit summary values take precedence, including zero; clearing restores member sums.
run("state.plans.find(p=>p.product_id==='summary-total').sop=120;indexData()");
assert.equal(run("sumPlan(products(),'2026-10','sop').value"),150);
run("state.plans.find(p=>p.product_id==='summary-total').sop=0;state.plans.find(p=>p.product_id==='summary-total').auto_partial=true;indexData()");
assert.equal(run("sumPlan(products(),'2026-10','sop').value"),30);
assert.equal(run("sumPlan([parent],'2026-10','sop').partial||false"),false,'An explicit zero is complete even when member fallback is partial.');
run("state.plans.find(p=>p.product_id==='summary-total').sop=null;state.plans.find(p=>p.product_id==='summary-total').auto_partial=false;indexData()");
assert.equal(run("sumPlan(products(),'2026-10','sop').value"),110);

// Export keeps parent/child row identity separate, and editable cells contain raw inputs.
const exportWithDetails=plain(run("buildPlanExport({includeLocks:true,includeVersions:true,selectedMonths:['2026-10']})"));
assert.deepEqual([...exportWithDetails.selectedProductIds].sort(),['summary-total','standalone','member-total','a1','a2','b1'].sort());
assert(!exportWithDetails.selectedProductIds.includes('summary-version'),'Derived aggregate versions are not maintenance inputs.');
assert.equal(exportWithDetails.rowParentGenerations.length,exportWithDetails.rows.length);
const valueColumn=exportWithDetails.headers.indexOf(month);
const parentEffectiveIndex=exportWithDetails.rows.findIndex((row,index)=>row[0]==='车型汇总'&&row[1]==='智界'&&row[2]==='R7 2026款'&&row[4]==='SOP计划量'&&!exportWithDetails.rowParentGenerations[index]);
const parentLockIndex=exportWithDetails.rows.findIndex((row,index)=>row[0]==='车型汇总'&&row[1]==='智界'&&row[2]==='R7 2026款'&&row[4]==='锁单数'&&!exportWithDetails.rowParentGenerations[index]);
const childLockIndex=exportWithDetails.rows.findIndex((row,index)=>row[0]==='车型汇总'&&row[2]==='R7 2026款'&&row[4]==='锁单数'&&exportWithDetails.rowParentGenerations[index]===summary);
assert(parentEffectiveIndex>=0&&parentLockIndex>=0&&childLockIndex>=0);
assert.equal(exportWithDetails.rows[parentEffectiveIndex][valueColumn],80);
assert.equal(exportWithDetails.rows[parentLockIndex][valueColumn],1000,'Including maintenance details cannot drop the parent vehicle lock quantity.');
assert.equal(exportWithDetails.rows[childLockIndex][valueColumn],600);
const parentInputIndex=exportWithDetails.rowProductIds.findIndex((id,index)=>id==='summary-total'&&exportWithDetails.rows[index][4]==='SOP计划量');
const memberInputIndex=exportWithDetails.rowProductIds.findIndex((id,index)=>id==='member-total'&&exportWithDetails.rows[index][4]==='SOP计划量');
assert(parentInputIndex>=0&&memberInputIndex>=0);
assert.equal(exportWithDetails.rows[parentInputIndex][valueColumn],null,'The editable parent total must remain blank when only fallback is known.');
assert.equal(exportWithDetails.rowParentGenerations[parentInputIndex],null);
assert.equal(exportWithDetails.rows[memberInputIndex][valueColumn],50);
assert.equal(exportWithDetails.rowParentGenerations[memberInputIndex],summary);
const rootOnlyExport=plain(run("buildPlanExport({includeLocks:true,includeVersions:false,selectedMonths:['2026-10']})"));
assert(rootOnlyExport.selectedProductIds.every(id=>initial.products.some(p=>p.id===id)));
assert(rootOnlyExport.rowParentGenerations.every(value=>value==null));
assert(rootOnlyExport.rows.every(row=>row[0]==='车型汇总'));
assert.equal(run("exportScope({allProducts:true})"),'全部品牌 · 全部车型');
const independentExport=plain(run("buildPlanExport({includeLocks:false,includeVersions:true,selectedMonths:['2026-10']},state.products.filter(p=>p.brand==='享界'))"));
assert.deepEqual(independentExport.selectedProductIds,['standalone'],'A filtered parent scope must not pull unrelated members into the workbook.');

// Merely opening maintenance must never save an automatic value as a manual override.
run('bulkPlan()');
assert.equal(run("gridRow('summary-total').baselines['2026-10'].sop"),null);
let payload=plain(run('gridPayload()'));
assert.equal(payload.changes.length,0);assert.equal(payload.entries.length,0);assert.equal(payload.catalog_changes.length,0);
run("gridRow('summary-total').values['2026-10']='120'");
payload=plain(run('gridPayload()'));
assert.deepEqual(payload.changes,[{product_id:'summary-total',month,sop:120,revision:2}]);
assert.equal(payload.entries.length,0);assert.equal(payload.catalog_changes.length,0);
run("gridDraft=null;bulkPlan();gridRow('a1').values['2026-10']='15'");
payload=plain(run('gridPayload()'));
assert.deepEqual(payload.changes,[{product_id:'a1',month,sop:15,revision:6}]);assert.equal(payload.catalog_changes.length,0);
run('gridDraft=null');

// Direct summary maintenance works even without any member products or plans.
const savedMembers=run('state.generation_members');context.savedMembers=savedMembers;
run("state.generation_members={products:[],plans:[],sales:[],model_sales:[],groups:[]};state.plans.find(p=>p.product_id==='summary-total').auto_sop=null;indexData();bulkPlan();gridRow('summary-total').values['2026-10']='321'");
assert.equal(run("sumPlan([parent],'2026-10','sop').total"),1,'A summary without member plans is still an applicable editable plan.');
payload=plain(run('gridPayload()'));
assert.deepEqual(payload.changes,[{product_id:'summary-total',month,sop:321,revision:2}]);
run("gridDraft=null;state.generation_members=savedMembers;state.plans.find(p=>p.product_id==='summary-total').auto_sop=80;indexData();planning()");

// Hiding members changes presentation only, never the parent or brand aggregate.
run("hideRow('model',memberKey,'R7 2026款')");
assert.equal(run("rowHidden('model',parentKey)"),false);
assert.equal(run("rowHidden('model',memberKey)"),true);
assert.equal(run("sumPlan(products(),'2026-10','sop').value"),110);

// Previously saved single-generation views remain usable after unifying the hierarchy.
context.legacyHidden=JSON.stringify(['detail','brand','智界']);
run("tableViews={active:'legacy',views:[{id:'legacy',name:'原有视图',config:{generation_mode:'detail',hidden:{[legacyHidden]:'智界'},range:'rolling',showWenjie:true,showLocks:true,expanded:[]}}]};selectedView='legacy';applyTableView()");
assert.equal(run("tableViews.active"),'legacy');
assert.equal(run("rowHidden('brand','智界')"),true);
assert.equal(run("sumPlan(products(),'2026-10','sop').value"),110);
assert(!toasts.some(t=>/请先切换代际/.test(t.text)));
console.log('PASS: unified summary hierarchy, editable summary overrides, child expansion/locks, safe inline saves, no double counting and saved-view compatibility.');
