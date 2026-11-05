import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source=readFileSync(new URL('../web/app.js',import.meta.url),'utf8');
const element=()=>({innerHTML:'',textContent:'',value:'detail',disabled:false,inert:false,attrs:{},focusCount:0,
  setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];},focus(){this.focusCount++;}});
const app=element(),control=element(),scroll={scrollLeft:91,scrollTop:12},dialog={open:false};
const nodes=new Map([['#app',app],['#generation-filter',control],['#current-time',element()],['#page-title',element()],['#edit-dialog',dialog]]);
const requests=[],toasts=[];
const base={current_month:'2026-10',plan_end:'2027-12',now:'2026-10-28T12:00:00+08:00',
  meta:{},products:[{id:'member',brand:'问界',model:'M9',version:'Max',active:true}],
  plans:[],sales:[],model_sales:[],settings:{generation_mode:'detail'},
  generation_view:{mode:'detail',rule_signature:'first',warnings:[]},workbook_sync:{status:'absent'}};
let serverState=structuredClone(base),status={status:'absent',generation_view:serverState.generation_view};
let post,reads=0,readFailures=[];
const connectionFailure=message=>Object.assign(new Error(message),{connection:true});
const ruleFailure=message=>Object.assign(new Error(message),{status:400});
const context=vm.createContext({localStorage:{getItem:()=>null},
  document:{hidden:false,querySelector:s=>nodes.get(s)||null,querySelectorAll:s=>s==='.table-scroll'?[scroll]:[]},
  requestAPI:async(path,body)=>{requests.push({path,body});
    if(path==='/api/settings')return post(body);
    if(path==='/api/plan-workbook/status')return status;
    if(path==='/api/state'){reads++;if(readFailures.length)throw readFailures.shift();return structuredClone(serverState);}
    throw new Error(path);},
  notify:(text,error)=>toasts.push({text,error}),
  setTimeout:callback=>{callback();return 1;},clearTimeout(){},
});
vm.runInContext(source.split("document.addEventListener('click'")[0],context);
const run=s=>vm.runInContext(s,context);
context.originalAPI=run('api');
context.initialState=structuredClone(base);
run('state=initialState;api=requestAPI;toast=notify;indexData();planning()');
assert.match(app.innerHTML,/id="generation-filter"[^]*?value="detail" selected[^]*?value="summary"/);
assert.equal(run("exportScope({allProducts:true})"),'全部品牌 · 全部车型 · 单代际');

// Mode changes serialize edits and only render the server's replacement scope.
let release;
post=()=>new Promise(resolve=>{release=resolve;});
run("model='M9';query='Max';selected='member';matchProduct='member'");
context.control=control;
const changing=run("changeGenerationMode('summary',control)");
assert.equal(control.disabled,true);assert.equal(app.inert,true);assert.equal(app.attrs['aria-busy'],'true');
await run("changeGenerationMode('summary',control)");
assert.equal(requests.filter(r=>r.path==='/api/settings').length,1);
serverState={...structuredClone(base),settings:{generation_mode:'summary'},
  generation_view:{mode:'summary',rule_signature:'first',warnings:['部分成员暂无销量']},
  products:[{id:'group',brand:'问界',model:'F2N',version:'Max',read_only:true,member_product_ids:['member']},
            {id:'unrelated',brand:'智界',model:'R7',version:'Pro',active:true}],
  plans:[{product_id:'group',month:'2026-10',sop:300}]};
release({ok:true});await changing;
assert.equal(control.disabled,false);assert.equal(app.inert,false);assert.equal(control.value,'summary');
assert.equal(control.focusCount,1);assert.equal(run('model'),'');assert.equal(run('matchProduct'),'');
assert.match(app.innerHTML,/汇总代际计划由成员单代际自动相加，只读/);
assert.match(app.innerHTML,/部分成员暂无销量/);
assert.equal(run("canEditProduct(state.products[0],'2026-10')"),false);
assert.equal(run("canEditProduct(state.products[1],'2026-10')"),true);
const row=run("planVersionRows([state.products[0]],['2026-10'])");
assert.doesNotMatch(row,/data-edit=/);assert.match(row,/自动汇总 · 只读/);
assert.equal(run("exportScope({allProducts:true})"),'全部品牌 · 全部车型 · 汇总代际');
assert.doesNotMatch(run("editorOptions('group')"),/value="group"/);

// A derived zero with a missing member is known-only, not a complete zero/year.
run("yearMonths('2026').forEach(month=>planIndex.set(key('group',month),{sop:0,partial:month==='2026-10',known_members:month==='2026-10'?1:2,total_members:2}))");
assert.equal(run("sumPlan([state.products[0]],'2026-10','sop').value"),0);
assert.match(run("quantity(sumPlan([state.products[0]],'2026-10','sop'))"),/仅已知项合计，仍有缺项/);
assert.equal(run("annualQuantity([state.products[0]],yearMonths('2026'),'sop').complete"),false);
assert.match(run("annualCell([state.products[0]],yearMonths('2026'),'sop')"),/12\/12 月 · 缺项/);
assert.doesNotMatch(run("annualCell([state.products[0]],yearMonths('2026'),'sop')"),/数据齐全/);
run('indexData()');
const calls=requests.length;
await run("editPlan('group','2026-10')");
assert.equal(requests.length,calls);assert.match(toasts.at(-1).text,/只读/);

// A rejected write reloads the authoritative mode, retaining the original table.
post=async()=>{throw new Error('先处理计划表同步冲突');};
await run("changeGenerationMode('detail',control)");
assert.equal(control.value,'summary');assert.equal(run('generationMode()'),'summary');
assert.match(toasts.at(-1).text,/同步冲突/);assert.equal(app.inert,false);

// Polling detects live rules even when there is no maintenance file.
status={status:'absent',generation_view:{...serverState.generation_view,rule_signature:'second'}};
serverState.generation_view=status.generation_view;
const before=reads;
await run('pollWorkbookStatus()');
assert.equal(reads,before+1);assert.equal(run('state.generation_view.rule_signature'),'second');
assert.equal(scroll.scrollLeft,91);assert.equal(scroll.scrollTop,12);
await run('pollWorkbookStatus()');assert.equal(reads,before+1,'Unchanged rule/status does not repeatedly reload.');
status={status:'error',generation_view:{mode:'summary',rule_signature:'',error:'汇总规则文件不存在'}};
await run('pollWorkbookStatus()');
assert.match(app.innerHTML,/代际范围暂不可用/);assert.match(app.innerHTML,/汇总规则文件不存在/);
assert.throws(()=>run("buildPlanExport({selectedMonths:['2026-10']})"),/规则文件不存在/);
status={status:'absent',generation_view:{mode:'summary',rule_signature:'third'}};
serverState.generation_view=status.generation_view;
await run('pollWorkbookStatus()');
assert.doesNotMatch(app.innerHTML,/代际范围暂不可用/);
assert.equal(run('generationViewError'),'');

// A write can commit before its response is lost. Retry only state reads, never the POST.
const postsBeforeRecovery=requests.filter(r=>r.path==='/api/settings').length;
const readsBeforeRecovery=reads;
post=async()=>{
  serverState=structuredClone(base);
  serverState.generation_view.rule_signature='after-lost-response';
  readFailures=[connectionFailure('第一次读取连接失败'),connectionFailure('第二次读取连接失败')];
  throw connectionFailure('写入响应丢失');
};
await run("changeGenerationMode('detail',control)");
assert.equal(requests.filter(r=>r.path==='/api/settings').length,postsBeforeRecovery+1,'A lost response must not replay the settings write.');
assert.equal(reads,readsBeforeRecovery+3,'State reads recover on the third attempt.');
assert.equal(run('generationMode()'),'detail');assert.equal(control.value,'detail');
assert.equal(run('generationViewError'),'');
assert.doesNotMatch(app.innerHTML,/业务服务连接中断|代际范围暂不可用/);
assert.equal(app.inert,false);assert.equal(control.disabled,false);

// A persistent outage hides stale editable values and names the connection failure.
const postsBeforeOutage=requests.filter(r=>r.path==='/api/settings').length;
const readsBeforeOutage=reads;
post=async()=>{
  serverState={...structuredClone(base),settings:{generation_mode:'summary'},
    generation_view:{mode:'summary',rule_signature:'confirmed-after-reconnect',warnings:[]},
    products:[{id:'group',brand:'问界',model:'F2N',version:'Max',read_only:true,member_product_ids:['member']}]};
  readFailures=[connectionFailure('第一次 GET 失败'),connectionFailure('第二次 GET 失败'),connectionFailure('最后一次 GET 失败')];
  throw connectionFailure('旧的 POST 错误');
};
await run("changeGenerationMode('summary',control)");
assert.equal(requests.filter(r=>r.path==='/api/settings').length,postsBeforeOutage+1);
assert.equal(reads,readsBeforeOutage+3);
assert.equal(run('generationMode()'),'detail','An uncertain response must not guess the new server mode.');
assert.equal(control.value,'detail');
assert.match(app.innerHTML,/业务服务连接中断/);assert.match(app.innerHTML,/最后一次 GET 失败/);
assert.doesNotMatch(app.innerHTML,/请修正规则|旧的 POST 错误/);
assert.equal(app.inert,false);assert.equal(control.disabled,false);
assert.throws(()=>run("buildPlanExport({selectedMonths:['2026-10']})"),/最后一次 GET 失败/);
await run('renderPage()');
assert.match(app.innerHTML,/业务服务连接中断/,'A regular render preserves the connection error kind.');
assert.doesNotMatch(app.innerHTML,/请修正规则/);

// Once polling reconnects, the authoritative committed mode replaces the old scope.
status={status:'absent',generation_view:serverState.generation_view};
await run('pollWorkbookStatus()');
assert.equal(run('generationMode()'),'summary');
assert.equal(run('generationViewError'),'');
assert.match(app.innerHTML,/汇总代际计划由成员单代际自动相加，只读/);
assert.doesNotMatch(app.innerHTML,/业务服务连接中断|代际范围暂不可用/);

// Configuration failures are not connection retries and must replace the old POST error.
const readsBeforeRuleFailure=reads;
post=async()=>{
  readFailures=[ruleFailure('恢复读取发现新的汇总规则配置错误')];
  throw connectionFailure('旧的写入网络错误');
};
await run("changeGenerationMode('detail',control)");
assert.equal(reads,readsBeforeRuleFailure+1,'Do not retry a confirmed rule/configuration error.');
assert.match(app.innerHTML,/代际范围暂不可用/);assert.match(app.innerHTML,/恢复读取发现新的汇总规则配置错误/);
assert.match(app.innerHTML,/请修正规则/);assert.doesNotMatch(app.innerHTML,/旧的写入网络错误|业务服务连接中断/);
status={status:'absent',generation_view:serverState.generation_view};
await run('pollWorkbookStatus()');
assert.equal(run('generationViewError'),'');

// An unfinished inline maintenance draft must survive a rejected selector change.
const beforeDraftRequests=requests.length;
run("gridDraft={owner:'未保存草稿',rows:new Map([['member',{values:{'2026-10':'123'}}]])}");
const draft=run('gridDraft');control.value='detail';
await assert.rejects(run("changeGenerationMode('detail',control)"),/保存|取消/);
assert.equal(requests.length,beforeDraftRequests,'Do not submit settings or reload while an edit draft exists.');
assert.equal(run('gridDraft'),draft);assert.equal(run("gridDraft.rows.get('member').values['2026-10']"),'123');
assert.equal(control.value,'summary');assert.equal(app.inert,false);
run('gridDraft=null');

// The real transport distinguishes network/truncated JSON failures from backend errors.
context.fetch=async()=>{throw new TypeError('Failed to fetch');};
await assert.rejects(run("originalAPI('/api/state')"),error=>error.connection===true);
context.fetch=async()=>({ok:true,status:200,json:async()=>{throw new SyntaxError('Unexpected end of JSON input');}});
await assert.rejects(run("originalAPI('/api/state')"),error=>error.connection===true);
context.fetch=async()=>({ok:false,status:400,json:async()=>({error:'明确的汇总规则错误'})});
await assert.rejects(run("originalAPI('/api/state')"),error=>error.status===400&&!error.connection&&/明确的汇总规则错误/.test(error.message));
console.log('PASS: generation switching recovers lost responses without replaying writes, preserves edit drafts, distinguishes connection/rule errors and restores authoritative scopes.');
