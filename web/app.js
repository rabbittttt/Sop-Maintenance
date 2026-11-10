/* S&OP workbench: all business records are persisted by the local service. */
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
// Resolve from the loaded script so both standalone / and the IIS /sop/ mount work.
const serviceBase = (() => {
  if (typeof location === 'undefined') return ''; // Standalone unit-test context.
  const script = document.currentScript?.src;
  const directory = new URL('.', script || location.href).pathname;
  return directory === '/' ? '' : directory.replace(/\/$/, '');
})();
const access = {gateway:!!serviceBase,canWrite:!serviceBase,user:'',message:serviceBase?'正在连接业务服务…':''};
const hasTrustedOperator = () => access.gateway || !!access.user;
const writeActions = new Set(['grid-save','grid-add','manage-products','new-product','rename-model','open-workbook','sync-workbook','resolve-db','resolve-workbook','edit-first','bulk-plan','export-plan','import-plan','detail-edit','edit-shipments','save-settings','refresh-sales','create-snapshot']);
const readOnlyMessage = '当前服务暂停写入或连接校验未通过，请稍后重试或联系维护人员。部门成员使用相同的业务功能。';
function serviceUrl(path) {
  if (typeof path !== 'string' || !path.startsWith('/api/') || /[\\\r\n]/.test(path)) throw new Error('无效的业务接口地址');
  return serviceBase + path;
}
function requireWrite() {
  if (access.canWrite) return;
  const error = new Error(access.message || readOnlyMessage);
  error.definite = true;
  throw error;
}
async function serviceFetch(path, options = {}, writeProbe = false) {
  const method = (options.method || 'GET').toUpperCase();
  const mutating = !['GET','HEAD'].includes(method);
  if (mutating && !(writeProbe && path === '/api/write-access')) requireWrite();
  let response;
  try { response = await fetch(serviceUrl(path), {credentials:'same-origin',...options}); }
  catch (cause) {
    const error = new Error('无法连接SOP业务服务，请确认计划工作台仍在运行后重试。');
    error.connection = true; error.cause = cause; throw error;
  }
  if (mutating && response.status === 403 && access.gateway) {
    access.canWrite = false; access.message = readOnlyMessage; applyAccessControls();
    if (!writeProbe) {const error = new Error(readOnlyMessage);error.definite = true;throw error;}
  }
  return response;
}
function applyAccessControls() {
  const banner = $('#access-status');
  if (banner) {
    banner.hidden = !access.gateway || access.canWrite;
    banner.textContent = access.canWrite ? '' : access.message || readOnlyMessage;
    banner.classList.toggle('read-only', !access.canWrite);
  }
  const ownerButton = $('#owner-btn');
  if (ownerButton && hasTrustedOperator()) {
    ownerButton.textContent = access.user || '企业用户';
    ownerButton.disabled = true;
    ownerButton.setAttribute('aria-label', '当前登录用户');
    ownerButton.title = '操作人取自 Windows 登录账户';
  }
  $$('[data-action],[data-edit],[data-catalog-edit],[data-catalog-bind],#generation-filter,#bridge-toggle,#edit-dialog button[type="submit"]').forEach(control => {
    const mutating = writeActions.has(control.dataset?.action) || control.hasAttribute?.('data-edit') || control.hasAttribute?.('data-catalog-edit') || control.hasAttribute?.('data-catalog-bind') || ['generation-filter','bridge-toggle'].includes(control.id) || control.type === 'submit';
    const unavailable = (mutating && !access.canWrite) || (access.gateway && control.dataset?.action === 'open-workbook');
    if (unavailable) {
      if (!control.disabled) control.dataset.accessDisabled = 'true';
      control.disabled = true;
      control.title = access.gateway && control.dataset?.action === 'open-workbook' ? '内网平台通过下载或导出使用Excel，不会打开服务器桌面程序' : readOnlyMessage;
    } else if (control.dataset?.accessDisabled === 'true') {
      control.disabled = false; delete control.dataset.accessDisabled;
    }
  });
  if (hasTrustedOperator()) $$('input[id$="-owner"]').forEach(control => {control.value = access.user;control.readOnly = true;control.setAttribute('aria-readonly','true');});
}
async function initializeAccess() {
  try {
    const signal = typeof AbortSignal !== 'undefined' && AbortSignal.timeout ? AbortSignal.timeout(5000) : undefined;
    const response = await serviceFetch('/api/session', {signal});
    if (!response.ok) throw new Error('登录信息暂不可用');
    const session = await response.json();
    if (!session || !['gateway','console'].includes(session.mode) || (serviceBase && session.mode !== 'gateway') || typeof session.canWrite !== 'boolean') throw new Error('登录信息暂不可用');
    access.gateway = session.mode === 'gateway';
    access.user = typeof session.user === 'string' ? session.user.trim() : '';
    if (access.user) owner = access.user;
    access.canWrite = !access.gateway && session.canWrite;
    if (access.gateway) {
      if (!access.user.trim()) throw new Error('登录信息暂不可用');
      if (session.canWrite) {
        const check = await serviceFetch('/api/write-access', {method:'POST',headers:{'Content-Type':'application/json'},body:'{}',signal}, true);
        if (check.ok) {const permitted = await check.json();access.canWrite = permitted.canWrite === true || permitted.ok === true;}
      }
      access.message = access.canWrite ? '' : readOnlyMessage;
    }
  } catch {
    // A root-only legacy console has no session API. Subpath deployments fail closed.
    access.canWrite = !serviceBase && !access.gateway;
    access.message = '暂时无法确认登录信息或服务状态，当前仅允许查看。请稍后刷新或联系维护人员。';
  }
  applyAccessControls();
}
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = v => v == null ? '—' : Number(v).toLocaleString('zh-CN', {maximumFractionDigits: 2});
const conflictValue = c => v => c.kind==='catalog' ? esc(typeof v==='boolean'?(v?'使用中':'停用'):v??'—') : fmt(v);
const conflictField = c => c.kind==='catalog' ? ({brand:'品牌',model:'车型',version:'版本名称',active:'启用状态'})[c.field]||'目录' : c.month||'';
const pct = v => v == null ? '—' : `${Number(v).toFixed(1)}%`;
const brands = ['智界','享界','尊界','尚界','问界'];
const titles = {planning:'SOP计划表', 'option-split':'选配拆分', reconcile:'资料对账', history:'历史快照', matching:'计划匹配率', data:'数据与规则'};
const statusText = v => ({official:'正式成绩',incomplete:'数据未齐全',missing:'实绩待维护',pending:'待结算',in_progress:'考核月未结束',settled:'已结算',corrected:'已更正',future:'未到取数日',missing_snapshot:'历史快照缺失',missing_plan:'计划待维护',undefined_zero:'双零口径待确认',complete:'可计分'})[v] || v;
let state, page = 'planning', brand = '', model = '', query = '', target = '', metric = '', range = 'rolling';
let historyRange = 'recent';
let planYear = '';
let optionMix=null, optionMixError='', optionBrand='', optionModel='', optionDimension='', optionRange='rolling', optionYear='', optionRequestId=0, optionExpanded=new Set();
// The old toggle text was ambiguous. Reset that saved choice once so the
// corrected model and version lock rows are visible; future choices persist.
const lockPreferenceKey = 'sop-show-locks-v2';
let showLocks = localStorage.getItem(lockPreferenceKey) !== 'false';
const wenjiePreferenceKey = 'sop-show-wenjie';
let showWenjie = localStorage.getItem(wenjiePreferenceKey) !== 'false';
const committeePreferenceKey = 'sop-show-committee';
const committeeLevelsPreferenceKey = 'sop-show-committee-levels';
function committeeVisibilityFromConfig(config, fallback) {
  const levels=config?.showCommitteeLevels;
  if(Array.isArray(levels)&&levels.length===3&&levels.every(value=>typeof value==='boolean'))return [...levels];
  if(typeof config?.showCommittee==='boolean')return Array(3).fill(config.showCommittee);
  return [...fallback];
}
function readCommitteeVisibility() {
  const legacy=localStorage.getItem(committeePreferenceKey)!=='false';
  try{return committeeVisibilityFromConfig({showCommitteeLevels:JSON.parse(localStorage.getItem(committeeLevelsPreferenceKey)||'null')},Array(3).fill(legacy));}
  catch{return Array(3).fill(legacy);}
}
let showCommitteeLevels=readCommitteeVisibility();
const visibleCommitteeLevels=()=>[1,2,3].filter(level=>showCommitteeLevels[level-1]);
const committeeLevels = ['普通保存','一级计委','二级计委','三级计委'];
let expanded = new Set(), allExpanded = false, selected = null, snapshotList = [], currentSnapshot = null;
let planIndex = new Map(), salesIndex = new Map(), shipmentIndex = new Map();
let committeeIndex = new Map(), currentCommitteeDecision = null;
let owner = localStorage.getItem('sop-owner') || '';
let matchMonth = '', matchProduct = '', comparison = null, activeRequest = 0;
let generationSwitchPending = false, generationViewError = '', generationErrorKind = 'rules';
let gridDraft=null, viewsRestored=false, viewNameDraft=null;
const modelPlanLabel='车型总量（不分版本）';
const isModelPlan=p=>p?.plan_level==='model'||p?.version===modelPlanLabel;
const modelGroupKey=p=>p.parent_generation?JSON.stringify(['member',p.parent_generation,p.brand,p.model]):`${p.brand}|${p.model}`;
function memberData(){return state?.generation_members||{};}
function hierarchicalPlanning(){return !!state?.generation_members;}
function allProducts(){return [...(state?.products||[]),...(memberData().products||[])];}
function allPlans(){return [...(state?.plans||[]),...(memberData().plans||[])];}
function allSales(){return [...(state?.sales||[]),...(memberData().sales||[])];}
function allModelSales(){return [...(state?.model_sales||[]),...(memberData().model_sales||[])];}
function planValue(product,month,field,data=planIndex){const r=data.get(key(product.id,month));return r?.[field]??(field==='sop'&&product.summary_generation&&isModelPlan(product)?r?.auto_sop:null)??null;}
function memberGroups(ps){const names=new Set(ps.map(p=>p.summary_generation).filter(Boolean));return (memberData().groups||[]).filter(g=>names.has(g.summary_generation));}
function childProducts(ps){const names=new Set(ps.map(p=>p.summary_generation).filter(Boolean));return (memberData().products||[]).filter(p=>names.has(p.parent_generation));}

function effectivePlanProducts(ps,month,field,data){
  const groups=new Map();for(const p of ps){const k=modelGroupKey(p);if(!groups.has(k))groups.set(k,[]);groups.get(k).push(p);}
  return [...groups.values()].flatMap(group=>{const totals=group.filter(p=>isModelPlan(p)&&((p.summary_generation&&!p.read_only)||planValue(p,month,field,data)!=null));return field==='sop'&&totals.length?totals:group.filter(p=>!isModelPlan(p));});
}
function gridUUID(){
  if(typeof crypto.randomUUID==='function')return crypto.randomUUID();
  const bytes=crypto.getRandomValues(new Uint8Array(16));bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
  const hex=[...bytes].map(value=>value.toString(16).padStart(2,'0')).join('');return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}
const tableViewKey='sop-table-views-v1';
function readTableViews(){try{const value=JSON.parse(localStorage.getItem(tableViewKey)||'{}');return {views:Array.isArray(value.views)?value.views.filter(v=>v&&typeof v.id==='string'&&typeof v.name==='string'&&v.config):[],active:typeof value.active==='string'?value.active:''};}catch{return {views:[],active:''};}}
const currentHiddenKey='sop-hidden-rows-v1';
function readHiddenRows(){try{const value=JSON.parse(localStorage.getItem(currentHiddenKey)||'null');return value&&typeof value==='object'&&!Array.isArray(value)?Object.fromEntries(Object.entries(value).filter(([,label])=>typeof label==='string')):null;}catch{return null;}}
let tableViews=readTableViews(), selectedView=tableViews.active, hiddenRows=readHiddenRows()||{};
function persistHiddenRows(){try{localStorage.setItem(currentHiddenKey,JSON.stringify(hiddenRows));return true;}catch{return false;}}
function refreshHiddenRows(){const saved=persistHiddenRows();planning();if(!saved)toast('当前行显示已更新，但浏览器未能记住此设置',true);}
function persistTableViews(){localStorage.setItem(tableViewKey,JSON.stringify(tableViews));}
function rowViewKey(level,id){return JSON.stringify([hierarchicalPlanning()?'summary':generationMode(),level,id]);}
function rowHidden(level,id){return Object.hasOwn(hiddenRows,rowViewKey(level,id))||(hierarchicalPlanning()&&['detail','summary'].some(mode=>Object.hasOwn(hiddenRows,JSON.stringify([mode,level,id]))));}
function productHidden(p){return rowHidden('brand',p.brand)||rowHidden('model',modelGroupKey(p))||rowHidden('version',p.id);}
function hideRow(level,id,label){hiddenRows[rowViewKey(level,id)]=label;refreshHiddenRows();}
function hiddenControl(level,id,label){return `<button class="row-visibility" type="button" data-hide-level="${level}" data-hide-id="${esc(id)}" data-hide-label="${esc(label)}" aria-label="隐藏${esc(label)}" title="隐藏此组行，不改变合计">隐藏</button>`;}
function viewConfig(){return {generation_mode:generationMode(),hidden:{...hiddenRows},range,planYear,brand,model,query,showLocks,showWenjie,allExpanded,expanded:[...expanded],showCommitteeLevels:[...showCommitteeLevels]};}
function compatibleHiddenRows(saved){
  const result={};
  for(const [key,label] of Object.entries(saved||{})){
    let parts;try{parts=JSON.parse(key);}catch{result[key]=label;continue;}
    const children=hierarchicalPlanning()&&parts[0]==='detail'&&parts[1]==='model'?(memberData().products||[]).filter(p=>`${p.brand}|${p.model}`===parts[2]):[];
    if(children.length)for(const child of children)result[JSON.stringify(['summary','model',modelGroupKey(child)])]=label;
    else result[key]=label;
  }
  return result;
}
function restoreViewConfig(config){
  hiddenRows=config.hidden&&typeof config.hidden==='object'&&!Array.isArray(config.hidden)?compatibleHiddenRows(config.hidden):{};
  range=config.range==='annual'?'annual':'rolling';planYear=String(config.planYear||'');brand=String(config.brand||'');model=String(config.model||'');query=String(config.query||'');
  showLocks=config.showLocks!==false;showWenjie=config.showWenjie!==false;showCommitteeLevels=committeeVisibilityFromConfig(config,showCommitteeLevels);allExpanded=!!config.allExpanded;expanded=new Set(Array.isArray(config.expanded)?config.expanded:[]);
  localStorage.setItem(lockPreferenceKey,String(showLocks));localStorage.setItem(wenjiePreferenceKey,String(showWenjie));localStorage.setItem(committeeLevelsPreferenceKey,JSON.stringify(showCommitteeLevels));
}
function restoreInitialView(){
  if(viewsRestored)return;viewsRestored=true;
  const workingHidden=readHiddenRows(),view=tableViews.views.find(v=>v.id===tableViews.active);
  if(view)restoreViewConfig(view.config);
  // The named view is a snapshot; subsequent hide/restore/delete actions are
  // the current working view and must survive a reload independently of it.
  if(workingHidden!==null)hiddenRows=compatibleHiddenRows(workingHidden);
}
function viewControls(){
  const entries=Object.entries(hiddenRows).filter(([k])=>{try{return hierarchicalPlanning()||JSON.parse(k)[0]===generationMode();}catch{return false;}});
  const active=tableViews.views.find(v=>v.id===selectedView),applied=active&&JSON.stringify({...active.config,showCommittee:undefined,showCommitteeLevels:committeeVisibilityFromConfig(active.config,showCommitteeLevels)})===JSON.stringify(viewConfig());
  return `<div class="saved-view-strip" role="group" aria-label="保存视图与恢复隐藏行"><div class="field inline-field view-field"><label for="table-view-name">视图</label><div class="view-combobox"><input id="table-view-name" aria-label="视图名称" maxlength="60" value="${esc(viewNameDraft??active?.name??'')}" placeholder="视图名称"><select id="saved-table-view" aria-label="选择已存视图"><option value="">当前视图</option>${tableViews.views.map(v=>`<option value="${esc(v.id)}" ${v.id===selectedView&&applied?'selected':''} >${esc(v.name)}</option>`).join('')}</select></div></div>${button('保存视图','view-save','small text-action')}${button('删除视图','view-delete','small text-action muted-action',selectedView?'':'disabled')}<span class="tool-divider" aria-hidden="true"></span>${button(`${planToolIcon('restore')}恢复隐藏行${entries.length?`（${entries.length}）`:''}`,'rows-restore','small text-action',`${entries.length?'':'disabled'}`)}${entries.length?`<label class="field inline-field"><span class="visually-hidden">恢复单行</span><select id="restore-hidden-row"><option value="">恢复单行…</option>${entries.map(([k,label])=>opt(k,label,'')).join('')}</select></label>`:''}</div>`;
}
function saveTableView(){const name=$('#table-view-name').value.trim();if(!name)throw new Error('请填写视图名称');const existing=tableViews.views.find(v=>v.name===name);const id=existing?.id||gridUUID();const view={id,name,config:viewConfig()};tableViews.views=tableViews.views.filter(v=>v.id!==id);tableViews.views.push(view);tableViews.active=id;selectedView=id;viewNameDraft=null;persistTableViews();const saved=persistHiddenRows();planning();toast(saved?'视图已保存在当前浏览器':'命名视图已保存，但浏览器未能记住当前隐藏状态',!saved);}
function applyTableView(){const view=tableViews.views.find(v=>v.id===selectedView);if(!view)throw new Error('请先选择一个已存视图');restoreViewConfig(view.config);viewNameDraft=null;tableViews.active=view.id;persistTableViews();const saved=persistHiddenRows();planning();toast(saved?'已应用保存的视图':'视图已应用，但浏览器未能记住当前隐藏状态',!saved);}
function gridLocked(){return !!(gridDraft?.saving||gridDraft?.uncertain);}
function gridRow(id){return gridDraft?.rows.get(id);}
function gridRender(){const scroll=$('.plan-card .table-scroll'),position=scroll?{left:scroll.scrollLeft,top:scroll.scrollTop}:null;planning();const next=$('.plan-card .table-scroll');if(next&&position){next.scrollLeft=position.left;next.scrollTop=position.top;}}
function gridLabel(row){
  const locked=gridLocked()||row.deleted,fields=row.isModel?['brand','model']:(row.original||row.anchor)?['version']:['brand','model','version'];
  const labels={brand:'品牌',model:'车型＋年款',version:'版本'};
  return `<div class="grid-row-labels${row.isModel?' model-labels':fields.length===1?' version-labels':''}">${fields.map(field=>`<label><span class="${fields.length===1?'visually-hidden':''}">${labels[field]}</span><input data-grid-id="${esc(row.id)}" data-grid-field="${field}" value="${esc(row[field])}"${row.isNew&&row.isModel?` placeholder="${field==='brand'?'填写品牌':'填写车型及年款，如 新车型 2027款'}"`:''} maxlength="200" aria-label="${labels[field]} ${esc(row.original?.version||'新增行')}" ${locked?'disabled':''}></label>`).join('')}<button type="button" class="button small grid-remove" data-grid-remove="${esc(row.id)}" title="删除按停用处理，保留历史" ${gridLocked()||gridDraft?.groups.get(row.group)?.deleted?'disabled':''}>${row.deleted?'撤销删除':row.isNew?'移除':'删除行'}</button></div>`;
}
function gridModelLabel(id,isOpen){
  const group=gridDraft.groups.get(id),disabled=gridLocked()||group.deleted;
  if(group.summary_generation)return `<strong>${esc(group.brand)} · ${esc(group.model)}</strong><div class="grid-row-actions"><button type="button" class="button small" data-expand="${esc(id)}" aria-expanded="${isOpen}" ${gridLocked()?'disabled':''}>${isOpen?'收起单代际':'展开单代际'}</button></div>`;
  return `<div class="grid-row-labels model-labels">${['brand','model'].map(field=>`<label><span>${field==='brand'?'品牌':'车型＋年款'}</span><input data-grid-group="${esc(id)}" data-grid-group-field="${field}" value="${esc(group[field])}" maxlength="200" aria-label="${field==='brand'?'品牌':'车型＋年款'} ${esc(id)}" ${disabled?'disabled':''}></label>`).join('')}</div><div class="grid-row-actions"><button type="button" class="button small" data-expand="${esc(id)}" aria-expanded="${isOpen}" ${gridLocked()?'disabled':''}>${isOpen?'收起版本':'展开版本'}</button><button type="button" class="button small" data-grid-add-brand="${esc(group.brand)}" data-grid-add-model="${esc(group.model)}" data-grid-anchor="${esc(id)}" ${disabled?'disabled':''}>新增版本</button><button type="button" class="button small" data-grid-remove-model="${esc(id)}" title="停用该车型下全部计划行，包含隐藏版本；历史保留" ${gridLocked()?'disabled':''}>${group.deleted?'撤销删除':'删除车型'}</button></div>`;
}
function gridChangeModel(id,field,value){
  const group=gridDraft?.groups.get(id);if(!group||group.summary_generation||gridLocked()||group.deleted||!['brand','model'].includes(field))return;
  group[field]=value;
  for(const row of gridDraft.rows.values())if(row.group===id||row.anchor===id)row[field]=value;
  gridDraft.request=null;
}
function gridRemoveModel(id){
  const group=gridDraft?.groups.get(id);if(!group||group.summary_generation||gridLocked())return;
  if(!group.deleted){group.beforeDelete=new Map();for(const row of gridDraft.rows.values())if(row.group===id||row.anchor===id){group.beforeDelete.set(row.id,row.deleted);row.deleted=true;}group.deleted=true;}
  else {for(const [rid,deleted] of group.beforeDelete)if(gridRow(rid))gridRow(rid).deleted=deleted;group.deleted=false;}
  gridDraft.request=null;gridRender();
}
function toggleModel(id){
  if(allExpanded){allExpanded=false;expanded=new Set(allProducts().map(modelGroupKey));expanded.delete(id);}
  else if(expanded.has(id))expanded.delete(id);else expanded.add(id);
  gridDraft?gridRender():planning();
}
function gridCell(row,month){
  const value=Object.hasOwn(row.values,month)?row.values[month]:(row.baselines[month]?.sop??''),old=row.baselines[month]?.sop??'';
  const label=month<state.current_month?'实际入库量':'SOP计划量';
  const fallback=row.isModel?(row.original?.summary_generation?row.baselines[month]?.auto_sop:sumPlan(allProducts().filter(p=>!isModelPlan(p)&&modelGroupKey(p)===row.group),month,'sop').value):null;
  return `<input class="grid-quantity${String(value)!==String(old)?' changed':''}" data-grid-id="${esc(row.id)}" data-grid-month="${month}"${row.isModel?` data-grid-model="${esc(row.model)}"`:''} inputmode="numeric" value="${esc(value)}" aria-label="${esc(row.isModel?row.model:row.version||'新增版本')} ${month} ${label}" placeholder="${fallback==null?'—':`汇总${fmt(fallback)}`}" ${gridLocked()||row.deleted?'disabled':''}>`;
}
function committeeScopeModels(){
  const options=new Map();
  for(const product of allProducts()){
    if(product.read_only||product.active===false||gridRow(product.id)?.deleted)continue;
    const key=modelGroupKey(product),group=gridDraft?.groups.get(key);
    if(group?.deleted)continue;
    options.set(key,{
      value:key,key,brand:(group?.brand||product.brand).trim(),model:(group?.model||product.model).trim(),
      parent:product.parent_generation,summary:!!product.summary_generation,
    });
  }
  for(const row of gridDraft?.rows.values()||[]){
    if(!row.isNew||!row.isModel||row.virtual||row.deleted||!row.brand.trim()||!row.model.trim())continue;
    const brand=row.brand.trim(),model=row.model.trim(),key=modelGroupKey({brand,model,parent_generation:row.parent_generation});
    if(![...options.values()].some(item=>item.key===key)){
      options.set(row.id,{value:row.id,key,brand,model,parent:row.parent_generation,summary:false});
    }
  }
  return [...options.values()].sort((a,b)=>a.brand.localeCompare(b.brand,'zh-CN')||a.model.localeCompare(b.model,'zh-CN'));
}
function committeeScopeOptions(scope){
  const models=committeeScopeModels();
  if(scope==='brands')return [...new Set(models.map(item=>item.brand))].map(brand=>({value:brand,label:brand}));
  return models.map(({value,brand,model,parent,summary})=>({
    value,label:[brand,model,parent?`单代际 · ${parent}`:summary?'汇总代际':''].filter(Boolean).join(' · '),
  }));
}
function committeeSelectionControls(){
  const draft=gridDraft.committee;
  if(!draft?.level)return '';
  if(draft.scope==='all')return `<span class="committee-scope-total">已选 ${committeeScopeModels().length} 个车型</span>`;
  if(!['brands','models'].includes(draft.scope))return '';
  const options=committeeScopeOptions(draft.scope),selected=new Set(draft[draft.scope==='brands'?'brands':'model_keys']);
  const count=options.filter(item=>selected.has(item.value)).length;
  return `<fieldset class="committee-options"${gridLocked()?' disabled':''}><legend>${draft.scope==='brands'?'品牌':'车型及年款'} · 已选 <span id="grid-committee-count">${count}</span></legend><div class="committee-options-list">${options.length?options.map(item=>`<label><input type="checkbox" data-committee-selection value="${esc(item.value)}"${selected.has(item.value)?' checked':''}>${esc(item.label)}</label>`).join(''):'<span>暂无可选范围</span>'}</div></fieldset>`;
}
function committeeFields(){
  const draft=gridDraft.committee;
  if(!draft)return '';
  const disabled=gridLocked()?' disabled':'';
  const levelField=`<label class="field inline-field">决策标签<select id="grid-committee-level"${disabled}>${committeeLevels.map((label,i)=>opt(String(i),label,String(draft.level))).join('')}</select></label>`;
  if(!draft.level)return levelField;
  const availableMonths=state.committee.plan_months;
  const startOptions=availableMonths.map(month=>opt(month,shortMonth(month),draft.start_month)).join('');
  const endOptions=availableMonths.filter(month=>month>=draft.start_month).map(month=>opt(month,shortMonth(month),draft.end_month)).join('');
  return `${levelField}<label class="field inline-field">决策期次<input type="month" id="grid-committee-period" value="${esc(draft.period)}" max="${esc(state.committee.current_period)}"${disabled}></label><label class="field inline-field">决策范围<select id="grid-committee-scope"${disabled}>${[['','选择范围'],['all','全部'],['brands','按品牌'],['models','按车型']].map(([value,label])=>opt(value,label,draft.scope)).join('')}</select></label>
    <div class="toolbar" role="group" aria-label="计划月份范围"><label class="field inline-field">计划起月<select id="grid-committee-start" required${disabled}><option value="">选择月份</option>${startOptions}</select></label>
    <label class="field inline-field">计划止月<select id="grid-committee-end" required${gridLocked()||!draft.start_month?' disabled':''}><option value="">选择月份</option>${endOptions}</select></label></div>`;
}
function committeePayload(){
  const draft=gridDraft.committee;
  if(!draft?.level)return null;
  if(!/^\d{4}-(0[1-9]|1[0-2])$/.test(draft.period)||draft.period>state.committee.current_period){
    $('#grid-committee-period')?.focus();
    throw new Error('请选择有效的决策期次');
  }
  const {level,period,scope,start_month,end_month,base_revision}=draft;
  if(!['all','brands','models'].includes(scope)){
    $('#grid-committee-scope')?.focus();
    throw new Error('请选择决策范围');
  }
  const availableMonths=state.committee.plan_months;
  if(!availableMonths.includes(start_month)){
    $('#grid-committee-start')?.focus();
    throw new Error('请选择计委决策的计划起月');
  }
  if(!availableMonths.includes(end_month)||end_month<start_month){
    $('#grid-committee-end')?.focus();
    throw new Error('请选择有效的计划止月，止月不能早于起月');
  }
  const request={level,period,scope,start_month,end_month,base_revision};
  if(scope==='all')return request;
  const field=scope==='brands'?'brands':'model_keys';
  const choices=new Map(scope==='brands'
    ? committeeScopeOptions(scope).map(({value})=>[value,value])
    : committeeScopeModels().map(({value,key})=>[value,key]));
  if(!draft[field].length||draft[field].some(value=>!choices.has(value))){
    $('[data-committee-selection]')?.focus();
    throw new Error(scope==='brands'?'请选择本次确认的品牌':'请选择本次确认的车型及年款');
  }
  return {...request,[field]:[...new Set(draft[field].map(value=>choices.get(value)))]};
}
function gridToolbar(){
  if(!gridDraft)return '';
  const correction=`<label class="checkbox-label"><input type="checkbox" id="grid-historical-receipt-correction"${gridDraft.historicalReceiptCorrection?' checked':''}${gridLocked()?' disabled':''}>按实际入库量修正</label>`;
  return `<section class="grid-toolbar" aria-label="网页维护"><div class="toolbar"><label class="field inline-field">维护人<input id="grid-owner" maxlength="100" value="${esc(gridDraft.owner)}" ${gridLocked()?'disabled':''} ${access.gateway?'readonly aria-readonly="true"':''}></label><label class="field inline-field">调整原因<input id="grid-reason" maxlength="200" value="${esc(gridDraft.reason)}" ${gridLocked()?'disabled':''}></label>${correction}${committeeFields()}</div><div id="grid-committee-options">${committeeSelectionControls()}</div><div id="grid-error" class="inline-error" role="alert">${esc(gridDraft.error||'')}</div></section>`;
}
function gridNewRows(anchor,visible){
  if(!gridDraft)return '';
  const total='<td class="annual-total">保存后计算</td>';
  return [...gridDraft.rows.values()].filter(row=>row.isNew&&!row.virtual&&row.anchor===anchor).map(row=>{
    const cells=visible.map(month=>{
      const past=month<state.current_month;
      const content=past?'<span class="missing">—</span>':isPlanMonthOpen(month)?gridCell(row,month):'未开放';
      return `<td class="sop-cell number${past?' past-month':''}">${content}</td>${range==='rolling'&&month.endsWith('-12')?total:''}`;
    }).join('');
    return `<tr class="version-row metric-plan-row grid-new-row" data-new-row="${esc(row.id)}"><th class="sticky-name" scope="row">${gridLabel(row)}</th><td class="sticky-metric">SOP计划量</td>${cells}${range==='rolling'?'':total}</tr>`;
  }).join('');
}
function gridAddRow(b=brand,m='',anchor=''){if(!gridDraft||gridLocked()||gridDraft.groups.get(anchor)?.deleted)return;const group=gridDraft.groups.get(anchor);if(group){b=group.brand;m=group.model;}const id='new_'+gridUUID(),isModel=!anchor;gridDraft.rows.set(id,{id,isNew:true,isModel,brand:b||'',model:m||'',version:isModel?modelPlanLabel:'',anchor,parent_generation:group?.parent_generation,values:{},baselines:{},deleted:false});if(anchor)expanded.add(anchor);gridDraft.request=null;gridRender();$(`[data-grid-id="${id}"][data-grid-field="${isModel?'model':'version'}"]`)?.focus();}
function gridRemoveRow(id){if(!gridDraft||gridLocked())return;const row=gridRow(id);if(!row)return;if(row.isNew)gridDraft.rows.delete(id);else row.deleted=!row.deleted;gridDraft.request=null;gridRender();}
function gridQuantity(value){if(value===''||value==null)return null;if(!/^\d+$/.test(String(value).trim()))throw new Error('计划量只能填写非负整数，留空表示待维护');const number=Number(value);if(!Number.isSafeInteger(number)||number>1e12)throw new Error('计划量超出允许范围');return number;}
function gridPayload(){
  const changes=[],catalog_changes=[],entries=[];const fail=(row,field,message)=>{const element=$(`[data-grid-id="${row.id}"][${field.startsWith('20')?'data-grid-month':'data-grid-field'}="${field}"]`);element?.setAttribute('aria-invalid','true');element?.focus();throw new Error(message);};
  if(!gridDraft.owner.trim())throw new Error('请填写维护人');
  for(const group of gridDraft.groups.values()){
    if(group.deleted)continue;
    const suffix=(group.originalModel||group.id).match(/(?:^|\s)(20\d{2}款.*)$/)?.[1];
    if(!group.brand.trim()||!group.model.trim()||(suffix&&(!group.model.trim().endsWith(' '+suffix)||!group.model.trim().slice(0,-suffix.length).trim()))){
      const field=!group.brand.trim()?'brand':'model';$$('[data-grid-group]').find(el=>el.dataset.gridGroup===group.id&&el.dataset.gridGroupField===field)?.focus();
      throw new Error(suffix?`请填写品牌和车型名称，并保留年款“${suffix}”；其他年款请新增车型`:'请填写品牌和车型名称');
    }
  }
  for(const row of gridDraft.rows.values()){
    if(row.virtual&&!Object.values(row.values).some(value=>value!==''&&value!=null))continue;
    if(row.deleted){if(row.isNew)continue;const plan_revisions=Object.fromEntries(Object.entries(row.baselines).map(([m,p])=>[m,p.revision||0]));catalog_changes.push({id:row.id,revision:row.original.revision||0,active:false,plan_revisions});continue;}
    for(const field of (row.isModel?['brand','model']:['brand','model','version']))if(!row[field].trim())fail(row,field,'请完整填写品牌、车型年款；版本维护时还需填写版本');
    const plans=[];
    for(const [month,value] of Object.entries(row.values)){
      let sop;try{sop=gridQuantity(value);}catch(error){fail(row,month,error.message);}
      if(sop===(row.baselines[month]?.sop??null))continue;
      if(month<state.current_month){
        if(!gridDraft.historicalReceiptCorrection){
          $('#grid-historical-receipt-correction')?.focus();
          throw new Error(`${month} 的历史计划量已修改，请勾选“按实际入库量修正”后保存`);
        }
        if(sop===null)fail(row,month,'实际入库量必须填写非负整数');
      }
      if(row.isNew)plans.push({month,sop});
      else changes.push({product_id:row.id,month,sop,revision:row.baselines[month]?.revision||0});
    }
    if(row.isNew)entries.push({client_id:row.id,brand:row.brand.trim(),model:row.model.trim(),version:row.isModel?modelPlanLabel:row.version.trim(),...(row.isModel?{plan_level:'model'}:{}),...(row.parent_generation?{parent_generation:row.parent_generation}:{}),plans});
    else {const c={id:row.id,revision:row.original.revision||0};for(const field of ['brand','model','version']){const original=field==='model'?catalogModel(row.original):row.original[field];if(row[field].trim()!==original)c[field]=row[field].trim();}if(Object.keys(c).length>2)catalog_changes.push(c);}
  }
  const committee=committeePayload();
  return {request_id:gridUUID(),owner:gridDraft.owner.trim(),reason:gridDraft.reason.trim(),generation_mode:gridDraft.mode,
    generation_rule_signature:gridDraft.signature,changes,catalog_changes,entries,
    ...(gridDraft.historicalReceiptCorrection?{historical_receipt_correction:state.current_month}:{}),...(committee?{committee}:{})};
}
async function saveGrid(){
  if(!gridDraft||gridDraft.saving)return;let payload;try{payload=gridDraft.request||gridPayload();if(!payload.changes.length&&!payload.catalog_changes.length&&!payload.entries.length&&!payload.committee)return toast('没有需要保存的修改');}catch(error){gridDraft.error=error.message;$('#grid-error').textContent=error.message;return;}
  gridDraft.request=payload;gridDraft.saving=true;gridDraft.error='';gridRender();
  try{const response=await serviceFetch('/api/plans/batch-edit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});let result;try{result=await response.json();}catch{throw new Error('无法确认保存结果，请重试确认；草稿已保留');}
    if(!response.ok){const error=new Error(result.error||'保存失败');error.definite=response.status>=400&&response.status<500&&response.status!==408&&response.status!==429;throw error;}
    if(!result.ok)throw new Error('无法确认保存结果，请重试确认');
    for(const c of payload.catalog_changes.filter(c=>c.active===false)){const p=gridRow(c.id)?.original;hiddenRows[rowViewKey('version',c.id)]=p?`${p.model} · ${p.version}（已停用）`:c.id;}
    for(const group of gridDraft.groups.values())if(group.deleted)hiddenRows[rowViewKey('model',group.id)]=group.model+'（已停用）';
    const addedModels=payload.entries.filter(entry=>entry.plan_level==='model'&&!gridRow(entry.client_id)?.virtual);
    const hiddenSaved=persistHiddenRows();owner=payload.owner;gridDraft=null;
    if(addedModels.length||payload.catalog_changes.some(c=>'brand' in c||'model' in c)){brand='';model='';query='';}
    if(addedModels.some(entry=>entry.brand==='问界')&&!showWenjie){showWenjie=true;try{localStorage.setItem(wenjiePreferenceKey,'true');}catch{/* The saved model remains available even if browser preferences cannot persist. */}}
    try{localStorage.setItem('sop-owner',owner);}catch{/* Business save already succeeded; storage failure must not trigger a retry. */}
    await load();toast(`已保存计划${result.saved_count||0}项，新增${result.created_count||0}行，停用${result.deactivated_count||0}行${result.committee?' · '+committeeLevels[result.committee.level]+'已留存':''}${result.workbook_sync&&['error','conflict','pending'].includes(result.workbook_sync.status)?'；Excel等待同步':''}${hiddenSaved?'':'；浏览器未能记住隐藏行设置'}`,!hiddenSaved);
  }catch(error){if(!gridDraft){toast('已保存，页面刷新失败，请点击刷新数据',true);return;}gridDraft.saving=false;gridDraft.uncertain=!error.definite;gridDraft.error=error.message+(error.definite?'；修改仍保留，可取消后刷新核对。':'；保存结果尚未确认，请重试原请求，避免重复新增。');if(error.definite)gridDraft.request=null;gridRender();}
}
const committeeKey = (level,modelKey,month) => JSON.stringify([level,modelKey,month]);
const key = (id, month) => `${id}|${month}`;
function shift(month, n) { const [y,m] = month.split('-').map(Number); const d = new Date(Date.UTC(y,m-1+n,1)); return d.toISOString().slice(0,7); }
function shortMonth(m) { return `${m.slice(0,4)}.${m.slice(5)}`; }
function dateText(t) { return t ? String(t).replace('T',' ').replace(/\.\d+/,'').replace(/\+08:00|Z/g,'').slice(0,19) : '尚未维护'; }
function toast(msg, error = false) { const el = $('#toast'); el.textContent = msg; el.className = `toast visible ${error?'error':''}`; clearTimeout(toast.timer); toast.timer = setTimeout(()=>el.className='toast',5000); }
async function api(path, body) {
  const response = await serviceFetch(path, body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  let data;
  try { data = await response.json(); }
  catch (cause) {
    const error = new Error(`业务服务响应未能完整读取${response.status?`（${response.status}）`:''}，请确认连接和服务状态后重试。`);
    error.connection = true; error.status = response.status; error.cause = cause; throw error;
  }
  if (!response.ok) {
    const error = new Error(data.error || data.message || `请求失败（${response.status}）`);
    error.status = response.status; throw error;
  }
  return data;
}
function indexData() {
  planIndex = new Map(allPlans().map(p=>[key(p.product_id,p.month),p]));
  salesIndex = new Map(allSales().map(p=>[`${key(p.product_id,p.month)}|${p.metric}`,p.value]));
  shipmentIndex = new Map((state.shipments || []).map(p=>[key(p.product_id,p.month),p]));
  committeeIndex = new Map((state.committee?.amounts||[]).map(r=>[
    committeeKey(r.level,r.model_key||modelGroupKey(r),r.month),r.sop,
  ]));
}
async function load(render = true) {
  if(gridDraft)throw new Error('请先保存或取消表内修改，再刷新数据');
  const freshState=await api('/api/state');if(gridDraft)throw new Error('请先保存或取消表内修改，再刷新数据');state=freshState;optionRequestId++;optionMix=null;optionMixError='';generationViewError=state.generation_view?.error||'';generationErrorKind='rules';indexData();restoreInitialView();
  target ||= state.current_month; matchMonth ||= shift(state.current_month,-1); metric = 'locked_orders';
  const ob = $('#owner-btn'); if (ob) ob.textContent = owner || '设置维护人';
  if(render) await renderPage();
  applyAccessControls();
}
function generationMode() { return state?.generation_view?.mode||state?.settings?.generation_mode||'detail'; }
function generationModeLabel(mode=generationMode()) { return mode==='summary'?'汇总代际':'单代际'; }
function generationStatusChanged(view) {
  if(!view)return false;
  const previous=state?.generation_view||{};
  return ['mode','rule_signature','error'].some(field=>(view[field]||'')!==(previous[field]||''));
}
function showGenerationError(message,kind=generationErrorKind) {
  generationViewError=message;generationErrorKind=kind;
  const title=kind==='connection'?'业务服务连接中断':'代际范围暂不可用';
  const guidance=kind==='connection'?'请确认计划工作台服务仍在运行，连接恢复后点击“刷新数据”；页面也会自动重试。确认当前口径前暂停编辑。':'请修正规则后点击“刷新数据”；页面也会自动重试。';
  if(page!=='history')$('#app').innerHTML=`<section class="card empty-state" role="alert"><h1>${title}</h1><p>${esc(message)}</p><p>${guidance}</p></section>`;
}
async function reloadGenerationState() {
  for(let attempt=0;attempt<3;attempt++) {
    try { await load();return; }
    catch(error) {
      if(!error.connection||attempt===2)throw error;
      await new Promise(resolve=>setTimeout(resolve,400*(attempt+1)));
    }
  }
}
function generationWarnings() {
  const notes=(state.generation_view?.warnings||[]).map(item=>typeof item==='string'?item:item.message).filter(Boolean);
  return notes.length?`<div class="callout subtle" role="status"><p><strong>${generationModeLabel()}范围提示：</strong>${notes.map(esc).join('；')}</p></div>`:'';
}
async function changeGenerationMode(value,control) {
  requireWrite();
  if(generationSwitchPending)return;
  if(gridDraft){control.value=generationMode();throw new Error('请先保存或取消表内修改，再切换车型口径');}
  if(!['detail','summary'].includes(value))throw new Error('请选择单代际或汇总代际');
  if(value===generationMode())return;
  generationSwitchPending=true;control.disabled=true;
  const app=$('#app');app.inert=true;app.setAttribute('aria-busy','true');
  try {
    const result=await api('/api/settings',{generation_mode:value});
    if(result.ok===false)throw new Error(result.error||result.message||'代际范围切换失败');
    model='';query='';selected=null;matchProduct='';expanded.clear();
    await load();
    const sync=result.workbook_sync;
    toast(`已切换为${generationModeLabel()}${sync&&['error','pending','conflict'].includes(sync.status)?'；'+(sync.message||'Excel待同步'):''}`,!!sync&&['error','pending','conflict'].includes(sync.status));
  } catch(error) {
    // A lost response can follow a successful save. Re-read the authoritative
    // mode before showing another editable table or restoring the selector.
    try {
      await reloadGenerationState();
      if(error.connection&&generationMode()===value)toast(`已重新连接并确认当前口径为${generationModeLabel()}`);
      else toast(error.message,true);
    } catch(recoveryError) {
      showGenerationError('无法确认当前代际范围，请刷新重试。'+recoveryError.message,recoveryError.connection?'connection':'rules');
      toast(recoveryError.message,true);
    }
  } finally {
    generationSwitchPending=false;control.disabled=!access.canWrite;control.value=generationMode();
    app.inert=false;app.removeAttribute('aria-busy');
    $('#generation-filter')?.focus({preventScroll:true});
  }
}
function compareBrands(a,b) {
  const rank=b=>brands.includes(b)?brands.indexOf(b):brands.length;
  return rank(a)-rank(b)||(a<b?-1:a>b?1:0);
}
function sortedProducts(ps) { return [...ps].sort((a,b)=>compareBrands(a.brand,b.brand)); }
function visibleBrands() { return [...new Set([...brands,...state.products.map(p=>p.brand)])].filter(b=>page!=='planning'||showWenjie||b!=='问界').sort(compareBrands); }
function products({includeWenjie=page!=='planning'||showWenjie,applyFilters=true}={}) {
  const term=query.toLowerCase(),memberMatches=new Set((memberData().products||[]).filter(p=>`${p.brand} ${p.model} ${p.version}`.toLowerCase().includes(term)).map(p=>p.parent_generation));
  return sortedProducts(state.products.filter(p=>(includeWenjie||p.brand!=='问界')&&(!applyFilters||((!brand||p.brand===brand)&&(!model||p.model===model)&&(!query||`${p.brand} ${p.model} ${p.version}`.toLowerCase().includes(term)||memberMatches.has(p.summary_generation))))));
}

function setWenjieVisibility(visible) {
  showWenjie=visible;localStorage.setItem(wenjiePreferenceKey,String(visible));
  normalizePlanningFilters();
}
function normalizePlanningFilters() {
  if(!showWenjie&&brand==='问界'){brand='';model='';query='';}
  const ps=products({includeWenjie:showWenjie,applyFilters:false});
  if(model&&!ps.some(p=>p.model===model&&(!brand||p.brand===brand)))model='';
  if(selected&&!ps.some(p=>p.id===selected))selected=null;
}
function planMonths() {
  const known=[...new Set([state.current_month,state.plan_end,...allSales().map(s=>s.month),...allModelSales().map(s=>s.month),...allPlans().map(p=>p.month),...(state.history_months||[]),...(state.months||[])])].filter(Boolean).sort();
  if(!known.length)return [];
  const first=`${known[0].slice(0,4)}-01`,last=known.at(-1),result=[];
  for(let month=first;month<=last;month=shift(month,1))result.push(month);
  return result;
}
function isPlanMonthOpen(month) { return (!state.plan_end||month<=state.plan_end)&&planMonths().includes(month); }
function productApplies(p,month) { return (!p.start_month||month>=p.start_month)&&(!p.end_month||month<=p.end_month); }
function productPlanAvailable(p,month) {
  if(p.read_only)return false;
  const past=month<state.current_month;
  const row=past?planIndex.get(key(p.id,month)):null;
  return (p.active!==false||past)&&(productApplies(p,month)||row?.sop!=null||row?.forecast!=null);
}
function canEditProduct(p,month) {
  return month>=state.current_month&&isPlanMonthOpen(month)&&productPlanAvailable(p,month);
}
function yearMonths(year=planYear) { return Array.from({length:12},(_,i)=>`${year}-${String(i+1).padStart(2,'0')}`); }
function planYears() { return [...new Set([state.current_month,...planMonths(),...(state.model_sales||[]).map(s=>s.month)].filter(Boolean).map(m=>m.slice(0,4)))].sort(); }
function selectPlanYear(year) {
  planYear=year;
}
function metricLabel() { return (state.meta.metrics||[]).find(m=>m.id===metric)?.label || metric; }
function months() { return range==='rolling' ? Array.from({length:12},(_,i)=>shift(state.current_month,i-5)) : yearMonths(); }
function historyMonths() { if(historyRange==='all') return [...new Set(state.sales.map(s=>s.month))].filter(m=>m<state.current_month).sort(); return state.history_months || [-3,-2,-1].map(n=>shift(state.current_month,n)); }
function productGenerations(p) { return p.source_generations?.length?p.source_generations:p.generation?[p.generation]:[]; }
function lockToggleLabel() { return '显示锁单'; }
function wenjieToggleLabel() { return '显示问界'; }
function latestVersionLockMonth() {
  return (state.sales||[]).filter(r=>r.metric==='locked_orders'&&r.value!=null).map(r=>r.month).sort().at(-1)||'';
}
function sumSales(ps,month, data=salesIndex,allowModelTotals=ps.length>1) {
  let value=0,modelReference=false,referenceConflict=false,summaryKnown=0,summaryTotal=0;
  const parents=allowModelTotals?ps.filter(p=>isModelPlan(p)&&p.summary_generation):[];
  const parentKeys=new Set(parents.map(modelGroupKey));
  for(const parent of parents){summaryTotal++;const ref=allModelSales().find(r=>r.generation===parent.summary_generation&&r.month===month&&r.metric===metric);if(ref?.value!=null){value+=ref.value;summaryKnown++;modelReference=true;}}
  ps=ps.filter(p=>!isModelPlan(p)&&!parentKeys.has(modelGroupKey(p)));
  const groups=new Map(),modelScoped=new Set(),known=new Set(),references=[];
  ps.forEach(p=>productGenerations(p).forEach(g=>{if(!groups.has(g))groups.set(g,[]);groups.get(g).push(p);}));
  for(const [generation,group] of groups){
    const all=allProducts().filter(p=>!isModelPlan(p)&&productGenerations(p).includes(generation));
    const complete=allowModelTotals&&metric==='locked_orders'&&group.length===all.length;
    if(complete)group.forEach(p=>modelScoped.add(p.id));
    const ref=complete?allModelSales().find(r=>r.generation===generation&&r.month===month&&r.metric==='locked_orders'):null;
    if(ref?.value!=null)references.push({group,ref});
  }
  for(const entry of references){
    const overlaps=references.some(other=>other!==entry&&entry.group.some(p=>other.group.some(q=>q.id===p.id)));
    if(overlaps){referenceConflict=true;continue;}
    value+=entry.ref.value;entry.group.forEach(p=>known.add(p.id));modelReference=true;
  }
  ps.filter(p=>!modelScoped.has(p.id)).forEach(p=>{const v=data.get(`${key(p.id,month)}|${metric}`);if(v!=null){value+=v;known.add(p.id);}});
  return {value:known.size+summaryKnown?value:null,known:known.size+summaryKnown,total:ps.length+summaryTotal,...(modelReference?{model_reference:true}:{}),...(referenceConflict?{reference_conflict:true}:{})};
}
function sumPlan(ps,month,field,data=planIndex) {
  ps=effectivePlanProducts(ps,month,field,data);
  ps=ps.filter(p=>(productApplies(p,month)&&(p.active!==false||p.end_month||month<state.current_month))||data.get(key(p.id,month))?.[field]!=null);
  const vals=ps.map(p=>planValue(p,month,field,data)); const known=vals.filter(v=>v!=null);
  const partial=ps.some(p=>{const r=data.get(key(p.id,month));return p.summary_generation&&isModelPlan(p)?r?.sop==null&&!!(r?.auto_partial||r?.partial):r?.partial;});
  return {value:known.length?known.reduce((a,b)=>a+b,0):null, known:known.length, total:ps.length,...(partial?{partial:true}:{})};
}
function quantity(q, pending='待维护') {
  if(q.value==null)return q.reference_conflict?'<span class="missing" title="关联的新旧来源在同月都有车型锁单，请核对来源">来源冲突</span>':`<span class="missing">${pending}</span>`;
  const note=[q.model_reference?'源表交车锁单数量行，车型原值':'',q.reference_conflict?'同一版本的新旧来源同月冲突，冲突部分未汇总':'',q.partial||q.known<q.total?'仅已知项合计，仍有缺项':''].filter(Boolean).join('；');
  return note?`<span title="${note}" aria-label="${fmt(q.value)}，${note}">${fmt(q.value)}</span>`:fmt(q.value);
}
function opt(value,label,current) { return `<option value="${esc(value)}" ${value===current?'selected':''}>${esc(label)}</option>`; }
function pageHeading(name=page) {
  return `<h1 class="visually-hidden${name==='planning'?' plan-title':''}">${titles[name]}</h1>`;
}
function button(text,action,cls='secondary',extra='') { if ((writeActions.has(action)&&!access.canWrite)||(access.gateway&&action==='open-workbook')) extra+=' disabled title="'+esc(readOnlyMessage)+'"'; return `<button type="button" class="button ${cls}" data-action="${action}" ${extra}>${text}</button>`; }
function brandTabs() { return `<div class="brand-tabs" role="group" aria-label="品牌筛选">${['',...brands].map(b=>`<button class="brand-tab ${b===brand?'active':''}" data-brand="${b}" aria-pressed="${b===brand}">${b||'全部品牌'}<span>${state.products.filter(p=>!b||p.brand===b).length}</span></button>`).join('')}</div>`; }
function filterBar(extra='') {
  const ms=[...new Set(state.products.filter(p=>!brand||p.brand===brand).map(p=>p.model))];
  return `<div class="filterbar"><label class="field">车型<select id="model-filter">${opt('','全部车型',model)}${ms.map(m=>opt(m,m,model)).join('')}</select></label>${page==='matching'?'':`<label class="field search-field">搜索版本<input id="version-search" placeholder="输入车型或版本名称" value="${esc(query)}"></label>`}${extra}</div>`;
}
function selectedPanel(ps) {
  const picked=selected?ps.filter(p=>p.id===selected):ps;
  const dates=[...historyMonths(),state.current_month], aggregate=dates.map(m=>sumSales(picked,m)),totals=aggregate.map(q=>q.value);
  const max=Math.max(1,...totals.filter(v=>v!=null));
  return `<div class="two-col page-section"><section class="card"><div class="panel-head"><div><h2>历史锁单参考</h2><p>${selected?esc(picked[0]?.version||'已选版本'):'当前筛选范围'} · ${esc(metricLabel())}</p></div><span class="badge neutral">历史实际</span></div><div class="bar-chart" role="img" aria-label="最近三个月及本月订单参考">${dates.map((m,i)=>`<div class="bar-column"><span class="bar-value">${fmt(totals[i])}${aggregate[i].known<aggregate[i].total&&totals[i]!=null?'*':''}</span><div class="bar-track"><div class="bar ${i===dates.length-1?'current':''}" style="height:${totals[i]==null?0:Math.max(2,totals[i]/max*100)}%"></div></div><span>${shortMonth(m)}</span><small>${i===dates.length-1?'本月累计':'来源月值'}</small></div>`).join('')}</div></section><section class="card"><div class="panel-head"><div><h2>品牌维护进度</h2><p>${shortMonth(target)} · 仅统计SOP计划维护情况</p></div><span class="badge neutral">${ps.length} 个版本</span></div><div class="brand-progress">${brands.filter(b=>!brand||brand===b).map(b=>{
    const bp=ps.filter(p=>p.brand===b);const done=bp.filter(p=>{const v=planIndex.get(key(p.id,target));return v?.sop!=null;}).length;
    const latest=bp.map(p=>planIndex.get(key(p.id,target))).filter(Boolean).sort((a,b)=>b.updated_at.localeCompare(a.updated_at))[0];
    return `<div class="progress-row"><strong>${b}</strong><div><div class="progress-label"><span>${done}/${bp.length} 已维护</span><span>${latest?esc(latest.owner):'等待维护'}</span></div><div class="progress-track"><i style="width:${bp.length?done/bp.length*100:0}%"></i></div></div><span class="badge ${done===bp.length&&bp.length?'good':'warn'}">${done===bp.length&&bp.length?'已齐全':'待补齐'}</span></div>`;
  }).join('')}</div></section></div>`;
}
function annualQuantity(ps,visibleMonths,field,allowModelTotals=ps.length>1) {
  const quantities=visibleMonths.map(m=>field==='locks'?sumSales(ps,m,salesIndex,allowModelTotals):sumPlan(ps,m,'sop')).filter(q=>q.total>0);
  const known=quantities.filter(q=>q.value!=null),partial=known.some(q=>q.partial||q.known<q.total);
  return {value:known.length?known.reduce((sum,q)=>sum+q.value,0):null,knownMonths:known.length,totalMonths:quantities.length,partial,complete:quantities.length>0&&known.length===quantities.length&&!partial,model_reference:known.some(q=>q.model_reference)};
}
function annualCell(ps,visibleMonths,field,planYear='',allowModelTotals=ps.length>1) {
  const q=annualQuantity(ps,visibleMonths,field,allowModelTotals);
  const title=[planYear?`${planYear}年1—12月SOP计划，含视图外月份`:'',q.model_reference?'含车型汇总锁单':''].filter(Boolean).join('；');
  return `<td class="annual-total number${planYear?' calendar-plan':''}"${planYear?` data-plan-year="${planYear}"`:''}${title?` title="${title}"`:''}>${q.value==null?'':`<strong>${fmt(q.value)}</strong>`}</td>`;
}
function calendarPlanCell(ps,year,field) {
  if(field==='locks')return `<td class="annual-total number calendar-plan" data-plan-year="${year}" title="此列仅统计${year}年度SOP计划量"><span class="missing">—</span></td>`;
  return annualCell(ps,yearMonths(year),'sop',year);
}
function calendarPlanHeader(year) {
  return `<th scope="col" class="annual-total calendar-plan" data-plan-year="${year}" title="汇总${year}年1—12月全部SOP计划，包含当前视图未展示的月份">${year}年<span class="annual-caption">年度计划量</span></th>`;
}
function planMetricRow(ps,label,level,id,visibleMonths,field,{isOpen=false,showName=true,rowSpan=1,blockStart=false}={}) {
  const one=level==='version', p=one?ps[0]:null;
  const cls=(level==='total'?'total-row':level==='brand'?'group-row':one?'version-row':'model-row')+(ps.some(p=>p.parent_generation)?' generation-member-row':'');
  const isLock=field==='locks';
  const versionCount=ps.filter(p=>!isModelPlan(p)).length,childCount=new Set(childProducts(ps).map(modelGroupKey)).size,versionNote=hierarchicalPlanning()&&['brand','total'].includes(level)?`${new Set(ps.map(modelGroupKey)).size}车型`:hierarchicalPlanning()&&level==='model'&&ps.some(p=>p.summary_generation)?childCount?`${childCount}个单代际`:'汇总计划':versionCount?`${versionCount}版本`:'车型计划';
  let labelContent=one?`<button class="row-link" data-detail="${p.id}">${esc(label)}</button>${p.read_only?'<small>自动汇总 · 只读</small>':p.active===false?'<small>已停用</small>':''}${p.name_conflict?'<small class="catalog-alert">同名待区分</small>':''}`:level==='model'?`<button class="row-toggle" data-expand="${esc(id)}" aria-expanded="${isOpen}"><span>${isOpen?'−':'+'}</span>${esc(label)} <em>${versionNote}</em></button>`:`<strong>${esc(label)}</strong> <small>${versionNote}</small>`;
  const modelDraft=level==='model'&&gridDraft?gridRow(gridDraft.modelRows.get(id)):null;
  const draftRow=one&&gridDraft?gridRow(p.id):modelDraft;
  const canCorrectHistory=draftRow&&!isLock&&gridDraft.historicalReceiptCorrection;
  const contributors=canCorrectHistory&&draftRow.virtual?allProducts().filter(product=>modelGroupKey(product)===id):[];
  if(draftRow&&one&&!isLock)labelContent=gridLabel(draftRow);
  if(modelDraft&&showName)labelContent=gridModelLabel(id,isOpen);
  if(gridDraft&&level==='brand'&&showName)labelContent+=button('新增车型','grid-add','small grid-brand-add',`data-new-model-brand="${esc(id)}"${id?` aria-label="新增车型（${esc(id)}）"`:''}${gridLocked()?' disabled':''}`);
  if(!gridDraft&&level!=='total'&&showName)labelContent+=hiddenControl(level,id,one?`${p.model} · ${p.version}`:label);
  const cells=visibleMonths.map(m=>{
    const past=m<state.current_month?' past-month':'';
    let cell;
    if(isLock) {
      const actual=sumSales(ps,m,salesIndex,!one);
      const modelOnly=one&&state.meta?.version_lock_basis==='model_lock_row'&&allModelSales().some(r=>r.generation===p.generation&&r.month===m&&r.value!=null);
      cell=`<td class="history-cell number${past}" title="${m>state.current_month?'尚未发生':modelOnly?'源表仅提供车型锁单数，未分配至版本':actual.value==null?one?'源表暂无该月版本锁单数':'源表暂无该月车型锁单数':one?'源表“版本／数量”行，版本原值':actual.model_reference?'源表“交车锁单／数量”行，车型原值':'当月来源锁单数'}">${quantity(actual,'—')}</td>`;
    } else {
      const q=sumPlan(ps,m,'sop');
      const open=isPlanMonthOpen(m)&&(!one||productPlanAvailable(p,m));
      const closedLabel=one&&p.read_only?'—':one&&!productApplies(p,m)?'未适用':one&&p.active===false?'—':'未开放';
      const title=past?'已过去月份，计划量只读':open?'':one&&p.read_only?'自动汇总的版本行只读，请展开单代际维护成员计划':one&&p.active===false?'版本已停用，已有计划保留':one&&!productApplies(p,m)?'此月不在版本适用月份内':'此月份不在已开放计划范围内';
      cell=`<td class="sop-cell number${past}${open?'':' closed-month'}"${title?` title="${title}"`:''}>${one&&!past&&open&&access.canWrite?`<button class="edit-cell" data-edit="${p.id}" data-month="${m}" aria-label="编辑${esc(p.version)} ${m} SOP计划${q.value==null?'（待维护）':''}" title="${q.value==null?'待维护':'编辑计划量'}">${quantity(q,'—')}</button>`:quantity(q,open?'—':closedLabel)}</td>`;
    }
    if(draftRow&&!isLock){
      const editable=past
        ? canCorrectHistory&&isPlanMonthOpen(m)&&(draftRow.original
          ? productPlanAvailable(draftRow.original,m)
          : contributors.some(product=>productPlanAvailable(product,m)))
        : modelDraft?isPlanMonthOpen(m)&&(!draftRow.original||canEditProduct(draftRow.original,m)):canEditProduct(p,m);
      if(editable)cell=`<td class="sop-cell number${past}">${gridCell(draftRow,m)}</td>`;
    }
    return cell+(range==='rolling'&&m.endsWith('-12')?calendarPlanCell(ps,m.slice(0,4),field):'');
  }).join('');
  const nameCell=showName?`<th scope="row" class="sticky-name"${rowSpan>1?` rowspan="${rowSpan}"`:''}>${labelContent}</th>`:'';
  return `<tr class="${cls} ${isLock?'metric-lock-row':'metric-plan-row'}${!one&&showName?' summary-start':''}${blockStart?' metric-block-start':''}" data-plan-metric="${field}"${one?` data-version="${esc(p.id)}"`:''}>${nameCell}<td class="sticky-metric"><span class="metric-label ${isLock?'lock-label':'plan-label'}">${isLock?'锁单数':'SOP计划量'}</span></td>${cells}${range==='rolling'?'':annualCell(ps,visibleMonths,field,'',!one)}</tr>`;
}
function committeeRows(modelKey,visibleMonths,levels=visibleCommitteeLevels()) {
  return levels.map(level=>{
    const valueAt=month=>committeeIndex.get(committeeKey(level,modelKey,month));
    const annual=year=>{
      const values=yearMonths(year).map(valueAt).filter(value=>value!=null);
      return `<td class="annual-total number${range==='rolling'?' calendar-plan':''}" data-plan-year="${year}">${values.length?fmt(values.reduce((total,value)=>total+value,0)):''}</td>`;
    };
    const cells=visibleMonths.map(month=>{
      const value=valueAt(month),past=month<state.current_month?' past-month':'';
      return `<td class="number${past}">${value==null?'':fmt(value)}</td>${range==='rolling'&&month.endsWith('-12')?annual(month.slice(0,4)):''}`;
    }).join('');
    return `<tr class="committee-row" data-committee-level="${level}" data-committee-model="${esc(modelKey)}"><th scope="row" class="sticky-metric">上期${committeeLevels[level]}</th>${cells}${range==='rolling'?'':annual(planYear)}</tr>`;
  }).join('');
}
function planRow(ps,label,level,id,visibleMonths,isOpen=false) {
  const committeeLevelRows=level==='model'&&ps.length&&state.committee?visibleCommitteeLevels():[];
  const rowSpan=(showLocks?2:1)+committeeLevelRows.length;
  const locks=showLocks?planMetricRow(ps,label,level,id,visibleMonths,'locks',{isOpen,rowSpan}):'';
  return locks+planMetricRow(ps,label,level,id,visibleMonths,'sop',{isOpen,showName:!showLocks,rowSpan})+
    (committeeLevelRows.length?committeeRows(id,visibleMonths,committeeLevelRows):'');
}
function planVersionRows(ps,visibleMonths) {
  return (showLocks?['locks','sop']:['sop']).map(field=>ps.filter(p=>!isModelPlan(p)&&!productHidden(p)).map((p,i)=>
    planMetricRow([p],p.version,'version',p.id,visibleMonths,field,{blockStart:i===0})
  ).join('')).join('');
}
function workbookStrip() {
  const w=state.workbook_sync||{},bad=['conflict','error'].includes(w.status);
  if(!w.status||w.status==='absent')return '';
  const synced=w.status==='synced',label=bad?'Excel同步待处理':({synced:'Excel已同步',absent:'网页直接保存 · Excel可选',pending:'Excel待同步',syncing:'Excel同步中',idle:'Excel准备同步'})[w.status]||'Excel准备同步';
  return `<span class="sheet-sync ${synced?'synced':bad||w.status==='pending'?'warn':'neutral'}"${!synced&&w.message?` title="${esc(w.message)}"`:''} role="status"><i class="status-dot"></i>${label}</span>${bad?button('查看问题','workbook-status','small'):''}`;
}
function planToolIcon(name) {
  const paths={
    target:'<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
    shipment:'<path d="M3 5h11v12H3zm11 4h4l3 4v4h-7"/><circle cx="7" cy="18" r="2"/><circle cx="17" cy="18" r="2"/>',
    weeks:'<path d="M4 20V4m0 16h16M8 16v-3m4 3V9m4 7V6"/>',
    calendar:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4m8-4v4"/>',
    edit:'<path d="m16 3 5 5-12 12H4v-5L16 3Z"/><path d="m13 6 5 5"/>',import:'<path d="M12 3v12m-4-4 4 4 4-4M4 16v5h16v-5"/>',export:'<path d="M12 16V4m-4 4 4-4 4 4M4 16v5h16v-5"/>',search:'<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/>',expand:'<path d="M4 5h16M4 19h16m-4-9-4-4-4 4m0 4 4 4 4-4"/>',restore:'<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>'};
  return `<svg class="plan-tool-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name]}</svg>`;
}
function planChildrenRows(ps,visibleMonths){
  if(!hierarchicalPlanning()||!ps.some(p=>p.summary_generation))return planVersionRows(ps,visibleMonths);
  const children=childProducts(ps),groups=new Map();
  for(const p of children){const id=modelGroupKey(p);if(!groups.has(id))groups.set(id,[]);groups.get(id).push(p);}
  let rows='';
  for(const [id,items] of groups){
    if(rowHidden('model',id))continue;
    const opened=allExpanded||expanded.has(id)||(!gridDraft&&!!query);
    rows+=planRow(items,items[0].model,'model',id,visibleMonths,opened);
    if(opened)rows+=planVersionRows(items,visibleMonths);
    rows+=gridNewRows(id,visibleMonths);
  }
  return rows;
}
function planning() {
  if(generationViewError)return showGenerationError(generationViewError);
  normalizePlanningFilters();
  const years=planYears();selectPlanYear(years.includes(planYear)?planYear:state.current_month.slice(0,4));
  const ps=products(),visible=months(),editable=visible.filter(isPlanMonthOpen),rolling=range==='rolling';
  if(!editable.includes(target))target=editable.find(m=>m===state.current_month)||editable[0]||'';
  const models=[...new Set(products({applyFilters:false}).filter(p=>!brand||p.brand===brand).map(p=>p.model))];
  const versionLockLatest=showLocks?latestVersionLockMonth():'';
  const committeeToggles=state.committee?`<span class="committee-toggles" role="group" aria-label="上期计委"><span class="committee-toggle-label">上期计委</span>${[1,2,3].map(level=>button(['','一级','二级','三级'][level],'toggle-committee','small lock-toggle',`data-committee-level="${level}" aria-label="显示上期${committeeLevels[level]}" aria-pressed="${showCommitteeLevels[level-1]}"`)).join('')}</span>`:'';
  let rows='';
  for(const b of [...new Set([...brands,...ps.map(p=>p.brand)])]) {
    const bp=ps.filter(p=>p.brand===b);if(!bp.length||rowHidden('brand',b))continue;
    rows+=planRow(bp,b,'brand',b,visible);
    for(const m of [...new Set(bp.map(p=>p.model))]) {
      const mp=bp.filter(p=>p.model===m),id=`${b}|${m}`,opened=allExpanded||expanded.has(id)||(!gridDraft&&!!query);
      if(rowHidden('model',id))continue;
      rows+=planRow(mp,m,'model',id,visible,opened);
      if(opened)rows+=planChildrenRows(mp,visible);
      rows+=gridNewRows(id,visible);
    }
  }
  if(gridDraft&&!rows)rows+=planRow([],brand||'品牌','brand',brand,visible);
  rows+=gridNewRows('',visible);
  rows+=planRow(ps,brand?`${brand}合计`:showWenjie?'鸿蒙智行合计':'合计（不含问界）','total','',visible);
  const actions=`<div class="toolbar plan-actions">${gridDraft?button(gridDraft.saving?'正在保存…':gridDraft.uncertain?'重试确认保存':'保存修改','grid-save','primary small',gridDraft.saving?'disabled':'')+button('取消','grid-cancel','small',gridLocked()?'disabled':''):button(`${planToolIcon('edit')}网页维护`,'bulk-plan','primary small')}<div class="plan-file-actions">${button(`${planToolIcon('import')}导入Excel`,'import-plan','small')}${button(`${planToolIcon('export')}导出`,'export-plan','small')}</div></div>`;
  $('#app').innerHTML=`${pageHeading('planning')}
    <div class="plan-controls"><div class="annual-toolbar"><div class="annual-filters"><label class="field inline-field year-field">周期<select id="range-filter">${opt('rolling','M+6（滚动12个月）',range)}${opt('annual','全年',range)}</select></label>${rolling?'':`<label class="field inline-field year-field">年份<select id="year-filter">${years.map(y=>opt(y,`${y}年`,planYear)).join('')}</select></label>`}<label class="field inline-field">品牌<select id="brand-filter">${opt('','全部品牌',brand)}${visibleBrands().map(b=>opt(b,b,brand)).join('')}</select></label><label class="field inline-field">车型<select id="model-filter">${opt('','全部车型',model)}${models.map(m=>opt(m,m,model)).join('')}</select></label><label class="field inline-field annual-search"><span class="visually-hidden">搜索车型或版本</span>${planToolIcon('search')}<input id="version-search" placeholder="搜索车型 / 版本" value="${esc(query)}"></label></div><div class="plan-toolbar-actions"><div class="toolbar display-toggles">${committeeToggles}${button(wenjieToggleLabel(),'toggle-wenjie','small lock-toggle',`aria-pressed="${showWenjie}"`)}${button(lockToggleLabel(),'toggle-locks','small lock-toggle',`aria-pressed="${showLocks}"`)}${button(`${planToolIcon('expand')}${allExpanded?'收起明细':'展开明细'}`,'expand-all','small text-action')}</div>${actions}</div></div>
    <div class="sheet-tools">${viewControls()}<div class="sheet-meta"><span>销量截止 <strong>${esc(state.meta?.data_through_label||state.meta?.data_through||'待确认')}</strong></span><span>计划至 <strong>${shortMonth(state.plan_end)}</strong></span>${versionLockLatest?`<span>版本锁单最新来源月：<strong>${shortMonth(versionLockLatest)}</strong></span>`:''}${workbookStrip()}</div></div></div>
    ${gridToolbar()}${generationWarnings()}<section class="card plan-card annual-sheet${gridDraft?' grid-editing':''}${rolling?' rolling-sheet':''}" aria-label="${rolling?'M+6':planYear+'年'}SOP计划表"><div class="table-scroll" tabindex="0" aria-label="${showLocks?'锁单和':''}SOP计划表，可横向滚动"><table class="plan-table" style="--plan-month-count:${visible.length}"><thead><tr><th scope="col" class="sticky-name">品牌 / 车型 / 版本</th><th scope="col" class="sticky-metric">指标</th>${visible.map((m,i)=>`<th scope="col" class="${m===state.current_month?'selected-month':''}${m<state.current_month?' past-month':''}" title="${m}${m<state.current_month?' · 已过去月份':''}${isPlanMonthOpen(m)?'':' · SOP未开放'}">${rolling?shortMonth(m):`${Number(m.slice(5))}月`}${rolling?`<span class="month-tag">${i<5?`M${i-5}`:i===5?'本月':`M+${i-5}`}</span>`:m===state.current_month?'<span class="month-tag">本月</span>':''}</th>${rolling&&m.endsWith('-12')?calendarPlanHeader(m.slice(0,4)):''}`).join('')}${rolling?'':`<th scope="col" class="annual-total">${planYear}年合计</th>`}</tr></thead><tbody>${ps.length||gridDraft?rows:`<tr><td colspan="${visible.length+3}" class="empty-state">没有符合筛选条件的版本</td></tr>`}</tbody></table></div></section>`;  if(gridDraft){$$('.annual-toolbar input,.annual-toolbar select,.saved-view-strip input,.saved-view-strip select,.saved-view-strip button,[data-action=toggle-wenjie],[data-action=import-plan],[data-action=export-plan]').forEach(el=>el.disabled=true);if(gridLocked())$$('[data-expand],[data-action=expand-all],[data-action=toggle-locks]').forEach(el=>el.disabled=true);}
}
function openDialog(html) { $('#edit-dialog').classList.toggle('catalog-dialog',html.includes('id="catalog-manager"')); $('#edit-dialog').classList.toggle('export-dialog',html.includes('id="export-plan-form"')||html.includes('id="import-plan-form"')); $('#dialog-content').innerHTML=html; const title=$('h2',$('#dialog-content'));if(title)title.id='dialog-title';applyAccessControls();$('#edit-dialog').showModal(); }
function closeDialog() { $('#edit-dialog').close(); }
function productManager() {
  const catalog=allProducts().filter(p=>!p.read_only&&!p.summary_generation);
  openDialog(`<div id="catalog-manager"><h2>车型与版本</h2><div class="catalog-toolbar"><label class="field">查找<input id="catalog-search" placeholder="品牌、车型或版本"></label><div class="toolbar">${button('车型改名','rename-model')}${button('新增版本','new-product','primary')}</div></div><div class="table-scroll catalog-scroll"><table class="detail-table"><thead><tr><th>品牌 / 车型</th><th>版本</th><th>状态 / 来源</th><th>维护</th></tr></thead><tbody>${catalog.map(p=>`<tr data-catalog-search="${esc(`${p.brand} ${p.model} ${p.version}`.toLowerCase())}"><td>${esc(p.brand)}<strong>${esc(p.model)}</strong></td><td>${esc(p.version)}${p.name_conflict?'<small class="catalog-alert">同名记录，请核对来源并区分</small>':''}</td><td><span class="badge ${p.active===false?'neutral':'good'}">${p.active===false?'已停用':'使用中'}</span><small>${p.source_backed===false?'手工建立':p.source_present===false?'本次来源未包含':'销量来源'}</small>${p.name_conflict?`<small>${esc(p.source_generation||p.generation||'手工建立')}</small>`:''}</td><td><button type="button" class="button small" data-catalog-edit="${esc(p.id)}" aria-label="维护${esc(p.brand)} ${esc(p.model)} ${esc(p.version)}">维护</button><button type="button" class="button small" data-catalog-bind="${esc(p.id)}" aria-label="关联${esc(p.model)} ${esc(p.version)}的销量来源">关联来源</button></td></tr>`).join('')}</tbody></table></div><div class="dialog-actions">${button('关闭','close-dialog')}</div></div>`);
  $('#catalog-search').oninput=e=>{const term=e.target.value.trim().toLowerCase();$$('[data-catalog-search]').forEach(row=>row.hidden=!row.dataset.catalogSearch.includes(term));};
}
function productFormError(err) {
  const el=$('#catalog-error');el.textContent=err.message;el.focus();
}
function bindProductSource(id) {
  const p=allProducts().find(x=>x.id===id);if(!p)return;
  if(p.read_only)return toast('自动汇总的版本行只读，请展开单代际维护成员计划',true);
  const candidates=allProducts().filter(x=>!x.read_only&&!x.summary_generation&&x.id!==id&&x.source_backed&&x.source_present!==false&&!allPlans().some(r=>r.product_id===x.id)&&!(state.shipments||[]).some(r=>r.product_id===x.id));
  openDialog(`<form id="bind-source-form"><h2>关联销量来源</h2><p class="subtitle">${esc(p.brand)} · ${esc(p.model)} · ${esc(p.version)}</p><div id="catalog-error" class="inline-error" role="alert" tabindex="-1"></div><div class="form-grid"><label class="field full">对应的销量版本<select id="bind-source" required><option value="">请选择并核对来源</option>${candidates.map(x=>opt(x.id,`${x.brand} ${x.model} · ${x.version}（${x.source_generation||x.generation||'销量来源'}）`,'' )).join('')}</select></label><label class="field full">维护人<input id="bind-owner" value="${esc(owner)}" required maxlength="100"></label></div><p class="form-help">关联后合并为当前版本。</p>${candidates.length?'':'<p class="inline-error">当前没有可关联的来源版本。</p>'}<div class="dialog-actions">${button('返回目录','manage-products')}<button type="submit" class="button primary" ${candidates.length?'':'disabled'}>关联并更新模板</button></div></form>`);
  $('#bind-source-form').onsubmit=async e=>{e.preventDefault();const save=$('button[type="submit"]',e.target);save.disabled=true;try{
    await saveCatalog([{id:p.id,revision:p.revision||0,bind_source_id:$('#bind-source').value}],$('#bind-owner').value.trim());
  }catch(err){productFormError(err);save.disabled=false;}};
}
async function saveCatalog(changes,newOwner) {
  if(changes.some(change=>allProducts().find(p=>p.id===change.id)?.read_only))throw new Error('自动汇总的版本行只读，请展开单代际维护成员计划');
  changes=changes.map(change=>{const p=allProducts().find(p=>p.id===change.id);return p&&change.model===p.model?{...change,model:catalogModel(p)}:change;});
  const result=await api('/api/products',{changes,owner:newOwner});
  owner=newOwner;localStorage.setItem('sop-owner',owner);
  closeDialog();brand='';model='';query='';
  try {await load();productManager();}
  catch(err){toast('目录已保存，页面刷新暂未完成；请点击“刷新数据”，无需重复提交。',true);return;}
  const sync=result.workbook_sync;
  toast(sync&&sync.status!=='synced'?`目录已保存；${sync.message}`:'目录与计划表已更新',sync?.status==='error'||sync?.status==='conflict');
}
function catalogModel(p) { return p.catalog_model??p.model; }
function editProduct(id='') {
  const p=allProducts().find(x=>x.id===id)||{brand:brand||'',model:model||'',version:'',active:true,start_month:state.current_month};
  if(p.read_only)return toast('自动汇总的版本行只读，请展开单代际维护成员计划',true);
  const knownBrands=[...new Set([...brands,...state.products.map(x=>x.brand)])];
  openDialog(`<form id="catalog-form"><h2>${id?'维护版本':'新增版本'}</h2><div id="catalog-error" class="inline-error" role="alert" tabindex="-1"></div><div class="form-grid"><label class="field">品牌<input id="catalog-brand" list="catalog-brands" value="${esc(p.brand)}" required maxlength="100"><datalist id="catalog-brands">${knownBrands.map(b=>`<option value="${esc(b)}">`).join('')}</datalist></label><label class="field">车型<input id="catalog-model" list="catalog-models" value="${esc(id?catalogModel(p):p.model)}" required maxlength="100"><datalist id="catalog-models">${[...new Set(state.products.map(x=>id?catalogModel(x):x.model))].map(m=>`<option value="${esc(m)}">`).join('')}</datalist></label><label class="field full">版本名称<input id="catalog-version" value="${esc(p.version)}" required maxlength="200"></label><label class="field">开始计划月份<input id="catalog-start" type="month" value="${esc(p.start_month&&p.start_month!=='1900-01'?p.start_month:'')}"></label><label class="field">结束计划月份（可留空）<input id="catalog-end" type="month" value="${esc(p.end_month||'')}"></label><label class="field">状态<select id="catalog-active">${opt('active','使用中',p.active===false?'inactive':'active')}${opt('inactive','已停用',p.active===false?'inactive':'active')}</select></label><label class="field">维护人<input id="catalog-owner" value="${esc(owner)}" required maxlength="100"></label></div>${id?`<p class="form-help">来源：${esc(p.source_generation||p.generation||'手工建立')}</p>`:''}<div class="dialog-actions">${button('返回目录','manage-products')}<button type="submit" class="button primary">保存并更新模板</button></div></form>`);
  $('#catalog-active').onchange=()=>{if($('#catalog-active').value==='active'&&p.active===false)$('#catalog-end').value='';};
  $('#catalog-form').onsubmit=async e=>{e.preventDefault();const save=$('button[type="submit"]',e.target);save.disabled=true;try{
    const change={...(id?{id,revision:p.revision||0}:{}),brand:$('#catalog-brand').value.trim(),model:$('#catalog-model').value.trim(),version:$('#catalog-version').value.trim(),active:$('#catalog-active').value==='active',start_month:$('#catalog-start').value||null,end_month:$('#catalog-end').value||null};
    if(change.active&&p.active===false&&!$('#catalog-end').value)change.end_month=null;
    await saveCatalog([change],$('#catalog-owner').value.trim());
  }catch(err){productFormError(err);save.disabled=false;}};
}
function renameModel() {
  const groups=[...new Set(allProducts().filter(p=>!p.read_only&&!p.summary_generation).map(p=>`${p.brand}\t${p.model}`))];
  if(!groups.length)return editProduct();
  openDialog(`<form id="rename-model-form"><h2>车型及版本统一改名</h2><div id="catalog-error" class="inline-error" role="alert" tabindex="-1"></div><div class="form-grid"><label class="field full">当前车型<select id="rename-from">${groups.map(g=>opt(g,g.replace('\t',' · '),`${brand}\t${model}`)).join('')}</select></label><label class="field">新车型名称<input id="rename-to" required maxlength="100"></label><label class="field">维护人<input id="rename-owner" value="${esc(owner)}" required maxlength="100"></label></div><div class="dialog-actions">${button('返回目录','manage-products')}<button type="submit" class="button primary">保存并更新模板</button></div></form>`);
  const selectedProducts=()=>{const [b,m]=$('#rename-from').value.split('\t');return allProducts().filter(p=>!p.read_only&&!p.summary_generation&&p.brand===b&&p.model===m);};
  const refill=()=>{$('#rename-to').value=catalogModel(selectedProducts()[0]);};
  refill();$('#rename-from').onchange=refill;
  $('#rename-model-form').onsubmit=async e=>{e.preventDefault();const save=$('button[type="submit"]',e.target);save.disabled=true;try{
    const ps=selectedProducts(),newModel=$('#rename-to').value.trim(),unchanged=newModel===catalogModel(ps[0]);
    const changes=ps.map(p=>({id:p.id,revision:p.revision||0,brand:p.brand,model:unchanged?catalogModel(p):newModel,version:p.version,active:p.active!==false}));
    await saveCatalog(changes,$('#rename-owner').value.trim());
  }catch(err){productFormError(err);save.disabled=false;}};
}
function bulkPlan() {
  requireWrite();
  if(gridDraft)return;
  clearTimeout(searchTimer);
  const rows=new Map(),modelRows=new Map(),groups=new Map();
  const visible=products().filter(p=>!rowHidden('brand',p.brand)&&!rowHidden('model',modelGroupKey(p)));
  const chosen=[...visible,...childProducts(visible)].filter(p=>!p.read_only);
  const selectedKeys=new Set(chosen.map(modelGroupKey));
  const ps=allProducts().filter(p=>!p.read_only&&selectedKeys.has(modelGroupKey(p)));
  for(const p of ps){const id=modelGroupKey(p);if(!groups.has(id))groups.set(id,{id,brand:p.brand,model:p.model,originalModel:p.model,parent_generation:p.parent_generation,summary_generation:p.summary_generation,deleted:false});}
  for(const p of ps){const group=modelGroupKey(p),baselines=Object.fromEntries(allPlans().filter(r=>r.product_id===p.id).map(r=>[r.month,{...r}]));rows.set(p.id,{id:p.id,group,original:{...p},isModel:isModelPlan(p),brand:p.brand,model:catalogModel(p),version:p.version,parent_generation:p.parent_generation,baselines,values:{},deleted:false,isNew:false});if(isModelPlan(p))modelRows.set(group,p.id);}
  for(const p of ps){const group=modelGroupKey(p);if(modelRows.has(group))continue;const id='model_'+gridUUID();modelRows.set(group,id);rows.set(id,{id,group,isNew:true,isModel:true,virtual:true,brand:p.brand,model:p.model,parent_generation:p.parent_generation,version:modelPlanLabel,baselines:{},values:{},deleted:false});}
  const committee=Array.isArray(state.committee?.plan_months)?{
    level:0,period:state.committee.current_period,scope:'',start_month:'',end_month:'',
    brands:[],model_keys:[],base_revision:state.committee.base_revision,
  }:null;
  gridDraft={rows,modelRows,groups,owner,reason:'网页维护',mode:generationMode(),signature:state.generation_view?.rule_signature||'',saving:false,uncertain:false,request:null,error:'',historicalReceiptCorrection:false,committee};
  gridRender();$('#grid-owner')?.focus();
}

function editorOptions(id,ps=products()) {return ps.filter(p=>!p.read_only).map(p=>opt(p.id,`${p.brand} · ${p.model} · ${p.version}`,id)).join('');}
async function editInGrid(id,month=state.current_month){
  requireWrite();
  if(month<state.current_month)return toast('已过去月份的计划量只读',true);
  const product=allProducts().find(p=>p.id===id);
  if(!product||!canEditProduct(product,month))return toast('此行当前不可维护，请核对代际和月份',true);
  if($('#edit-dialog')?.open)closeDialog();
  page='planning';
  if(!products().some(p=>p.id===id)){brand=product.brand;model=product.model;query='';}
  if(!months().includes(month)){range='annual';planYear=month.slice(0,4);}
  expanded.add(modelGroupKey(product));
  await renderPage();bulkPlan();
  $(`[data-grid-id="${id}"][data-grid-month="${month}"]`)?.focus();
}
function editPlan(id,month=target) {
  if(month<state.current_month)return toast('已过去月份的计划量只读',true);
  if(!isPlanMonthOpen(month))return toast('此月份不在已开放计划范围内',true);
  const p=allProducts().find(p=>p.id===id); if(!p) return toast('请先选择一个版本',true);
  if(p.read_only)return toast('自动汇总的版本行只读，请展开单代际维护成员计划',true);
  if(!canEditProduct(p,month))return toast('此版本在该月不适用或已停用，已有计划仍保留',true);
  const record=planIndex.get(key(id,month))||{};
  openDialog(`<form id="plan-form"><h2>维护月度计划</h2><div class="form-grid"><label class="field full">版本<select id="edit-product">${editorOptions(id,allProducts().filter(x=>x.brand===p.brand&&canEditProduct(x,month)))}</select></label><label class="field full">目标月份<select id="edit-month">${planMonths().filter(m=>canEditProduct(p,m)).map(m=>opt(m,shortMonth(m),month)).join('')}</select></label><label class="field">预测销量（辆）<input id="edit-forecast" type="number" min="0" step="1" placeholder="待维护" value="${record.forecast??''}"></label><label class="field">SOP计划量（辆）<input id="edit-sop" type="number" min="0" step="1" placeholder="待维护" value="${record.sop??''}"></label><label class="field">维护人<input id="edit-owner" value="${esc(owner)}" required maxlength="60" placeholder="请输入姓名"></label><label class="field">调整原因<select id="edit-reason">${['首次维护','需求变化','供应变化','库存调整','产品变化','入库节奏变化','其他'].map(s=>opt(s,s,record.reason||'首次维护')).join('')}</select></label><label class="field full">调整说明<textarea id="edit-notes" rows="3" maxlength="1000" placeholder="简要记录判断依据">${esc(record.notes||'')}</textarea></label></div><div class="inline-error" id="form-error" role="alert"></div><div class="dialog-actions">${button('取消','close-dialog') }<button type="submit" class="button primary">保存计划</button></div></form>`);
  const reloadFields=()=>{
    const r=planIndex.get(key($('#edit-product').value,$('#edit-month').value))||{};
    $('#edit-forecast').value=r.forecast??'';$('#edit-sop').value=r.sop??'';$('#edit-notes').value=r.notes||'';$('#edit-reason').value=r.reason||'首次维护';
  };
  $('#edit-product').onchange=()=>{
    const chosen=allProducts().find(x=>x.id===$('#edit-product').value),current=$('#edit-month').value;
    $('#edit-month').innerHTML=planMonths().filter(m=>canEditProduct(chosen,m)).map(m=>opt(m,shortMonth(m),current)).join('');
    reloadFields();
  };
  $('#edit-month').onchange=()=>{
    const id=$('#edit-product').value,m=$('#edit-month').value;
    $('#edit-product').innerHTML=editorOptions(id,allProducts().filter(x=>x.brand===p.brand&&canEditProduct(x,m)));
    reloadFields();
  };
  $('#plan-form').onsubmit=async e=>{
    e.preventDefault();const save=$('button[type="submit"]',e.target);save.disabled=true;
    try {const pid=$('#edit-product').value,m=$('#edit-month').value,r=planIndex.get(key(pid,m));const newOwner=$('#edit-owner').value.trim();
      const parse=id=>$(id).value===''?null:Number($(id).value);
      await api('/api/plans',{changes:[{product_id:pid,month:m,forecast:parse('#edit-forecast'),sop:parse('#edit-sop'),revision:r?.revision||0}],owner:newOwner,reason:$('#edit-reason').value,notes:$('#edit-notes').value});
      owner=newOwner;localStorage.setItem('sop-owner',owner);closeDialog();await load();toast('计划已保存，修改记录已留存');
    } catch(err) {$('#form-error').textContent=err.message;save.disabled=false;}
  };
}
async function showDetail(id) {
  const p=allProducts().find(p=>p.id===id); selected=id;
  if(p?.read_only)return openDialog(`<h2>${esc(p.model)} · ${esc(p.version)}</h2><span class="badge neutral">汇总 · 只读</span><div class="dialog-actions">${button('关闭','close-dialog')}</div>`);
  const history=await api(`/api/history?product_id=${encodeURIComponent(id)}&month=${target}`); history.items=history.items.filter(h=>h.entity==='plan');
  const relevant=allProducts().filter(x=>modelGroupKey(x)===modelGroupKey(p)),lm=shift(state.current_month,-1);
  const versionSalesTotal=sumSales(relevant,lm,salesIndex,false),planTotal=sumPlan(relevant,target,'sop');
  openDialog(`<h2>${esc(p.model)} · ${esc(p.version)}</h2><p class="subtitle">${esc(p.brand)} · 关注月份 ${shortMonth(target)}</p><h3>版本结构对照</h3><div class="table-scroll"><table class="detail-table"><thead><tr><th>版本</th><th>${lm}版本锁单占比</th><th>${target}计划占比</th></tr></thead><tbody>${relevant.map(v=>{const a=sumSales([v],lm).value,b=sumPlan([v],target,'sop').value;return `<tr><td>${esc(v.version)}</td><td>${a!=null&&versionSalesTotal.value>0&&versionSalesTotal.known===versionSalesTotal.total?pct(a/versionSalesTotal.value*100):'—'}</td><td>${b!=null&&planTotal.value>0&&planTotal.known===planTotal.total?pct(b/planTotal.value*100):'待维护齐全'}</td></tr>`}).join('')}</tbody></table></div><h3>同月修订记录</h3>${history.items.length?`<div class="activity">${history.items.map(h=>`<article><strong>${esc(h.owner||h.after?.owner||'维护人')}</strong><span>${dateText(h.recorded_at||h.updated_at||h.created_at||h.at)}</span><p>${esc(h.reason||h.after?.reason||'计划更新')} · ${esc(h.notes||h.after?.notes||'')}</p><p class="muted">预测 ${fmt(h.forecast_before??h.before?.forecast)} → ${fmt(h.forecast_after??h.after?.forecast??h.forecast)} ｜ SOP ${fmt(h.sop_before??h.before?.sop)} → ${fmt(h.sop_after??h.after?.sop??h.sop)}</p></article>`).join('')}</div>`:'<div class="empty-state compact">这个版本的目标月份尚无修订记录。</div>'}<div class="dialog-actions">${button('关闭','close-dialog')}${button('维护此版本','detail-edit','primary',`data-product="${id}"`)}</div>`);
}
function exportCSV(name,header,rows) {
  const quote=v=>{let s=String(v??''); if(/^[=+@\-]/.test(s)&&!/^\-?\d+(\.\d+)?$/.test(s))s="'"+s;return '"'+s.replace(/"/g,'""')+'"';};
  const blob=new Blob(['\uFEFF'+[header,...rows].map(r=>r.map(quote).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),5000);
}
function exportableMonths() { return [...new Set([...planMonths(),...months()])].sort(); }
function maintenanceExportOptions() {
  return {includeLocks:false,includeVersions:true,includeWenjie:true,selectedMonths:planMonths(),allProducts:true};
}
function buildPlanExport({includeLocks=false,includeVersions=true,selectedMonths=[]}={},ps=products()) {
  if(generationViewError)throw new Error(generationViewError);
  const hierarchy=hierarchicalPlanning();
  ps=sortedProducts(ps);
  if(hierarchy&&includeVersions){const roots=ps.filter(p=>!p.read_only);ps=[...roots,...childProducts(roots)];}
  const allowed=new Set(exportableMonths());
  if(!Array.isArray(selectedMonths)||!selectedMonths.length)throw new Error('请至少选择一个导出月份');
  if(selectedMonths.some(m=>typeof m!=='string'||!/^\d{4}-(0[1-9]|1[0-2])$/.test(m)||!allowed.has(m)))throw new Error('导出月份不在可选范围内');
  if(!ps.length)throw new Error('当前筛选范围没有可导出的车型或版本');
  const chosen=[...new Set(selectedMonths)].sort(),rows=[],annualPlans=[],rowProductIds=[],rowParentGenerations=[],groups=new Map();
  const annualYears=chosen.filter(m=>m.endsWith('-12')).map(m=>m.slice(0,4));
  ps.forEach(p=>{const id=hierarchy?modelGroupKey(p):JSON.stringify([p.brand,p.model]);if(!groups.has(id))groups.set(id,[]);groups.get(id).push(p);});
  const fields=includeLocks?['locks','sop']:['sop'];
  let blocks=[...groups.values()];
  if(hierarchy){const roots=blocks.filter(group=>!group[0].parent_generation);blocks=roots.flatMap(group=>[group,...blocks.filter(child=>child[0].parent_generation&&group.some(p=>p.summary_generation===child[0].parent_generation))]);}
  for(const group of blocks) {
    const p=group[0],fullCount=allProducts().filter(x=>modelGroupKey(x)===modelGroupKey(p)).length;
    const append=(items,field,detail=false)=>{
      const notes=[],quantities=chosen.map(m=>field==='locks'?sumSales(items,m,salesIndex,!detail):detail&&items[0].summary_generation&&isModelPlan(items[0])?{value:planIndex.get(key(items[0].id,m))?.sop??null,known:planIndex.get(key(items[0].id,m))?.sop==null?0:1,total:1}:sumPlan(items,m,'sop'));
      if(detail&&allProducts().filter(x=>modelGroupKey(x)===modelGroupKey(p)&&x.version===items[0].version).length>1) {
        const version=items[0],source=version.source_generation||productGenerations(version).join('、')||'手工建立';
        notes.push(`同名版本来源：${source}；版本编码：${version.id}`);
      }
      if(!detail&&group.length<fullCount)notes.push(`仅汇总筛选的 ${group.length}/${fullCount} 个版本`);
      if(!detail&&includeVersions)notes.push('车型汇总与版本明细范围相同，请勿重复相加');
      if(field==='locks') {
        notes.push('锁单为来源月值，完整性以源表为准');
        if(quantities.some(q=>q.model_reference))notes.push('车型锁单取源表“交车锁单／数量”原值，不按版本重算');
        if(detail&&state.meta?.version_lock_basis==='source_version_quantity')notes.push('版本锁单取源表“版本／数量”原值，与车型锁单分别记录');
        if(detail&&state.meta?.version_lock_basis==='model_lock_row')notes.push('源表仅有车型锁单时，版本锁单留空');
      }
      const describe=(test,label)=>{const ms=chosen.filter((m,i)=>test(quantities[i]));if(ms.length)notes.push(`${ms.join('、')}：${label}`);};
      describe(q=>q.total===0,'未适用');
      describe(q=>q.total>0&&q.value==null,field==='locks'?'锁单缺失，留空':'计划待维护，留空');
      describe(q=>q.value!=null&&(q.partial||q.known<q.total),'仅已知项合计，仍有缺项');
      describe(q=>q.reference_conflict,'新旧来源同月冲突，冲突部分未汇总');
      const annual={};
      for(const year of annualYears) {
        const total=field==='sop'?annualQuantity(items,yearMonths(year),'sop'):null;
        annual[year]=total?.value??null;
        if(total?.value!=null&&!total.complete)notes.push(`${year}年度计划仅含已知值，${total.knownMonths}/${total.totalMonths}个月有数据${total.partial?'，版本仍有缺项':''}`);
      }
      rows.push([detail?'版本明细':'车型汇总',p.brand,p.model,...(includeVersions?[detail?items[0].version:'']:[]),field==='locks'?'锁单数':'SOP计划量',...quantities.map(q=>q.value),notes.join('；')]);
      annualPlans.push(annual);
      rowProductIds.push(detail?items[0].id:null);
      rowParentGenerations.push(p.parent_generation||null);
    };
    fields.forEach(field=>append(group,field));
    if(includeVersions)fields.forEach(field=>group.filter(product=>field!=='locks'||!isModelPlan(product)).forEach(product=>append([product],field,true)));
  }
  return {headers:['层级','品牌','车型',...(includeVersions?['版本']:[]),'指标',...chosen,'数据说明'],rows,selectedMonths:chosen,annualPlans,rowProductIds,selectedProductIds:ps.map(p=>p.id),...(hierarchy?{rowParentGenerations}:{})};
}
function exportProducts(options) { return products({includeWenjie:options.includeWenjie??showWenjie,applyFilters:!options.allProducts}); }
function exportScope(options) {
  const scope=options.allProducts?'全部品牌 · 全部车型':[brand||'全部品牌',model||'全部车型',query?`搜索“${query}”`:''].filter(Boolean).join(' · ');
  return scope+((options.includeWenjie??showWenjie)?'':'（不含问界）')+(hierarchicalPlanning()?'':' · '+generationModeLabel());
}
async function downloadPlanExport(options) {
  const result=buildPlanExport(options,exportProducts(options)),chosen=result.selectedMonths,includeVersions=result.headers.includes('版本');
  const scope=exportScope(options);
  const response=await serviceFetch('/api/plans/export-xlsx',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    schema_version:1,current_month:state.current_month,months:chosen,include_locks:!!options.includeLocks,include_versions:includeVersions,
    ...(state.generation_view?{generation_mode:generationMode(),generation_rule_signature:state.generation_view.rule_signature}:{}),
    scope,headers:result.headers,rows:result.rows,annual_plans:result.annualPlans,row_product_ids:result.rowProductIds,selected_product_ids:result.selectedProductIds,...(result.rowParentGenerations?{row_parent_generations:result.rowParentGenerations}:{})
  })});
  if(!response.ok){let message='Excel导出未完成，请重试';try{message=(await response.json()).error||message;}catch{}throw new Error(message);}
  const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');
  link.href=url;link.download=`SOP计划_${chosen[0]}_${chosen.at(-1)}_${chosen.length}个月_${includeVersions?'版本明细':'车型汇总'}.xlsx`;
  document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);
  toast(`已导出所选 ${chosen.length} 个月的Excel${options.includeLocks?'（含锁单）':''}`);
  return result;
}
function exportPlans() {
  const defaults=maintenanceExportOptions(),maintenance=new Set(defaults.selectedMonths),available=exportableMonths(),current=new Set(months());
  const years=[...new Set(available.map(m=>m.slice(0,4)))],hasFilter=!!(brand||model||query);
  openDialog(`<form id="export-plan-form"><h2>导出SOP计划</h2><p id="export-scope" class="subtitle"></p><div id="export-error" class="inline-error" role="alert"></div><fieldset class="export-content-options"><legend>导出内容</legend><label class="export-check"><input type="checkbox" id="export-wenjie" checked><span>包含问界</span></label><label class="export-check"><input type="checkbox" id="export-locks"><span>增加锁单行</span></label><label class="export-check"><input type="checkbox" id="export-versions" checked><span>${hierarchicalPlanning()?'包含单代际及版本明细':generationMode()==='summary'?'包含版本明细（只读）':'包含版本明细'}</span></label>${hasFilter?'<label class="export-check"><input type="checkbox" id="export-filtered"><span>仅导出当前筛选</span></label>':''}</fieldset><div class="export-month-heading"><h3>导出月份</h3><div class="toolbar"><button type="button" class="button ghost small" data-export-preset="maintenance">恢复维护表格式</button><button type="button" class="button ghost small" data-export-months="current">当前视图</button><button type="button" class="button ghost small" data-export-months="all">全部月份</button><button type="button" class="button ghost small" data-export-months="none">清空</button></div></div><div class="export-month-list">${years.map(y=>`<fieldset class="export-month-year"><legend>${y}年</legend><div class="export-month-grid">${available.filter(m=>m.startsWith(y+'-')).map(m=>`<label class="export-check"><input type="checkbox" name="export-month" value="${m}" aria-label="${y}年${Number(m.slice(5))}月" ${maintenance.has(m)?'checked':''}><span>${Number(m.slice(5))}月</span></label>`).join('')}</div></fieldset>`).join('')}</div><p id="export-selection" class="export-selection" role="status"></p><div class="dialog-actions">${button('取消','close-dialog')}<button type="submit" class="button primary">导出维护模板</button></div></form>`);
  const form=$('#export-plan-form'),monthInputs=$$('input[name="export-month"]',form),save=$('button[type="submit"]',form);
  const versionOption=$('#export-versions',form),lockOption=$('#export-locks',form),wenjieOption=$('#export-wenjie',form),filterOption=$('#export-filtered',form);
  const reset=()=>{
    versionOption.checked=defaults.includeVersions;lockOption.checked=defaults.includeLocks;wenjieOption.checked=defaults.includeWenjie;
    if(filterOption)filterOption.checked=false;
    monthInputs.forEach(input=>input.checked=maintenance.has(input.value));
  };
  const update=(clearError=true)=>{
    const selectedMonths=monthInputs.filter(el=>el.checked).map(el=>el.value),count=selectedMonths.length,versions=versionOption.checked;
    const options={allProducts:!filterOption?.checked,includeWenjie:wenjieOption.checked},ps=exportProducts(options),scope=exportScope(options);
    const template=versions&&!lockOption.checked&&wenjieOption.checked&&!filterOption?.checked&&count===maintenance.size&&selectedMonths.every(m=>maintenance.has(m));
    $('#export-scope',form).textContent=`${scope} · ${new Set(ps.map(p=>JSON.stringify([p.brand,p.model]))).size} 个车型 / ${ps.length} 个版本`;
    $('#export-selection',form).textContent=count?`已选 ${count} 个月`:'请至少选择一个月份';
    save.textContent=template?'导出维护模板':versions?'导出版本计划':'导出车型计划';
    save.disabled=!count||!ps.length;
    if(clearError)$('#export-error',form).textContent=ps.length?'':'当前筛选范围没有可导出的车型或版本';
  };
  $('[data-export-preset="maintenance"]',form).onclick=()=>{reset();update();};
  $$('[data-export-months]',form).forEach(el=>el.onclick=()=>{monthInputs.forEach(input=>input.checked=el.dataset.exportMonths==='all'||(el.dataset.exportMonths==='current'&&current.has(input.value)));update();});
  [...monthInputs,versionOption,lockOption,wenjieOption,...(filterOption?[filterOption]:[])].forEach(el=>el.onchange=()=>update());
  form.onsubmit=async e=>{
    e.preventDefault();if(form.getAttribute('aria-busy')==='true')return;
    const options={includeLocks:lockOption.checked,includeVersions:versionOption.checked,includeWenjie:wenjieOption.checked,selectedMonths:monthInputs.filter(el=>el.checked).map(el=>el.value),allProducts:!filterOption?.checked};
    const controls=$$('input,button',form),previous=controls.map(el=>el.disabled);
    form.setAttribute('aria-busy','true');controls.forEach(el=>el.disabled=true);save.textContent='正在生成Excel…';$('#export-error',form).textContent='';
    try{await downloadPlanExport(options);if($('#export-plan-form')===form)closeDialog();}
    catch(err){if($('#export-plan-form')===form)$('#export-error',form).textContent=err.message;else toast(err.message,true);}
    finally{form.removeAttribute('aria-busy');controls.forEach((el,i)=>el.disabled=previous[i]);if($('#export-plan-form')===form)update(false);}
  };
  reset();
  wenjieOption.checked=showWenjie;
  update();
}
const MAX_PLAN_IMPORT_BYTES=12*1024*1024;
function validatePlanImportFile(file) {
  if(!file)throw new Error('请先选择修改后的Excel文件');
  if(!/\.xlsx$/i.test(file.name))throw new Error('请选择 .xlsx 格式的SOP计划表');
  if(!file.size)throw new Error('文件为空，请重新保存Excel后选择');
  if(file.size>MAX_PLAN_IMPORT_BYTES)throw new Error('文件不能超过12MB，请缩小导出月份或版本范围');
  return file;
}
async function readPlanImportFile(file) {
  validatePlanImportFile(file);
  const content_base64=await new Promise((resolve,reject)=>{
    const reader=new FileReader();
    reader.onload=()=>{const encoded=String(reader.result||'').split(',')[1];encoded?resolve(encoded):reject(new Error('无法读取文件内容，请重新选择'));};
    reader.onerror=()=>reject(new Error('读取文件失败，请确认Excel已保存后重试'));
    reader.onabort=()=>reject(new Error('文件读取已取消，请重新选择'));
    reader.readAsDataURL(file);
  });
  return {filename:file.name,content_base64};
}
async function submitPlanImport(payload,choice) {
  if(choice!==undefined&&!['db','workbook'].includes(choice))throw new Error('请选择冲突项的处理方式');
  const body={filename:payload.filename,content_base64:payload.content_base64,...(choice?{choice}:{})};
  const response=await serviceFetch('/api/plans/import-xlsx',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  let data;
  try{data=await response.json();}catch{throw new Error('未能确认导入结果，请刷新看板检查后重试');}
  if(!response.ok){const fallback=response.status===409?'表格与网页存在同时修改的计划，请核对后选择保留值':`导入未完成（${response.status}）`;const error=new Error(data.error||data.message||fallback);error.status=response.status;error.data=data;throw error;}
  return data;
}
function planConflictTableHtml(conflicts,maintenance=false) {
  const unknownBaseline=!maintenance&&conflicts.some(c=>c.baseline_unknown);
  return `<div class="table-scroll import-conflict-table"><table class="detail-table"><thead><tr><th>车型 / 版本 / 月份</th><th>${maintenance?'共同基准':unknownBaseline?'原表基准':'导出时'}</th><th>${maintenance?'统一维护表值':'Excel修改值'}</th><th>网页当前值</th></tr></thead><tbody>${conflicts.map(c=>{const p=allProducts().find(p=>p.id===c.product_id)||c;return `<tr><td>${esc(p.brand)} ${esc(p.model)}<small>${esc(p.version||c.product_id)} · ${esc(conflictField(c))}${c.reason==='plans_changed'?'（导出后计划已更新）':''}</small></td><td>${!maintenance&&c.baseline_unknown?'未提供':conflictValue(c)(c.baseline)}</td><td>${conflictValue(c)(c.workbook)}</td><td>${conflictValue(c)(c.database)}</td></tr>`;}).join('')}</tbody></table></div>`;
}
function planImportConflictsHtml(conflicts) {
  return `${planConflictTableHtml(conflicts)}<p class="form-help">${conflicts.some(c=>c.baseline_unknown)?'表格计划与网页当前值不一致，请选择保留网页值或采用表格值。':'两边都修改了以上单元格。选择仅作用于这些冲突项，其余修改正常合并。'}</p><div class="dialog-actions import-conflict-actions"><button type="button" class="button secondary" data-import-choice="db">冲突项保留网页值</button><button type="button" class="button primary" data-import-choice="workbook">冲突项采用Excel值</button></div>`;
}
function importPlans() {
  openDialog(`<form id="import-plan-form"><h2>导入SOP计划</h2><div id="import-error" class="inline-error" role="alert" tabindex="-1"></div><label class="field import-file-field">Excel文件（.xlsx，最大 12 MB）<input id="import-plan-file" type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" required></label><p id="import-progress" class="import-progress" role="status" aria-live="polite"></p><div id="import-conflicts"></div><div class="dialog-actions">${button('取消','close-dialog')}<button id="import-save" type="submit" class="button primary" disabled>导入计划</button></div></form>`);
  const form=$('#import-plan-form'),input=$('#import-plan-file'),save=$('#import-save'),error=$('#import-error'),progress=$('#import-progress'),conflicts=$('#import-conflicts'),dialog=$('#edit-dialog'),close=$('[data-close]',dialog);
  let prepared=null,pending=false,valid=false,hasConflicts=false,maintenanceBlocked=false;
  const cancel=e=>{if(pending)e.preventDefault();};
  dialog.addEventListener('cancel',cancel);
  dialog.addEventListener('close',()=>dialog.removeEventListener('cancel',cancel),{once:true});
  const showError=message=>{error.textContent=message;error.focus();};
  const setBusy=busy=>{pending=busy;form.setAttribute('aria-busy',String(busy));$$('input,button',form).forEach(el=>el.disabled=busy);close.disabled=busy;save.disabled=busy||!valid;save.hidden=hasConflicts||maintenanceBlocked;save.textContent=busy?'正在导入…':'导入计划';progress.textContent=busy?'正在检查表格并合并计划，请稍候…':'';};
  input.onchange=()=>{
    prepared=null;valid=false;hasConflicts=false;maintenanceBlocked=false;conflicts.innerHTML='';error.textContent='';
    try{validatePlanImportFile(input.files[0]);valid=true;}catch(err){if(input.files.length)showError(err.message);}
    setBusy(false);
  };
  const showMaintenance=w=>{
    maintenanceBlocked=true;hasConflicts=false;
    const items=w.conflicts||[];
    conflicts.innerHTML=`<p class="form-help">维护表同步未完成，已保留选中的导入文件。</p>${items.length?planConflictTableHtml(items,true):''}<div class="dialog-actions import-conflict-actions">${items.length?'<button type="button" class="button secondary" data-maintenance-choice="db">维护表冲突保留网页值</button><button type="button" class="button primary" data-maintenance-choice="workbook">维护表冲突采用维护表值</button>':'<button type="button" class="button primary" data-maintenance-choice="retry">重新同步维护表</button>'}</div>`;
    $$('[data-maintenance-choice]',conflicts).forEach(el=>el.onclick=async()=>{
      if(pending)return;
      setBusy(true);progress.textContent='正在同步统一维护表…';error.textContent='';
      try{
        const result=await api('/api/plan-workbook/sync',el.dataset.maintenanceChoice==='retry'?{}:{choice:el.dataset.maintenanceChoice});
        if(result.ok===false||['conflict','error'].includes(result.status)){showMaintenance(result);showError(result.message||'维护表尚未完成同步，请处理后继续');return;}
        maintenanceBlocked=false;conflicts.innerHTML='<p class="form-help">维护表已同步，已保留选中的文件。</p>';
        try{await load(false);}catch{showError('维护表已同步，页面数据刷新失败。可点击“导入计划”重新检查并合并。');}
      }catch(err){showError(err.message);}finally{setBusy(false);}
    });
  };
  const submit=async choice=>{
    if(pending)return;
    error.textContent='';setBusy(true);
    try{
      prepared||=await readPlanImportFile(input.files[0]);
      const result=await submitPlanImport(prepared,choice);
      closeDialog();
      const message=result.imported||result.created||result.catalog_updated?`已导入 ${result.imported||0} 项计划${result.created?`，新增 ${result.created} 行`:''}${result.catalog_updated?`，更新 ${result.catalog_updated} 个版本`:''}${result.deactivated?`（其中停用 ${result.deactivated} 个）`:''}`:'计划已核对，没有需要导入的新修改';
      try{await load();toast(result.warning?`${message}；${result.warning}`:message,!!result.warning);}catch{toast(`${message}；页面刷新失败，请点击刷新查看${result.warning?`。${result.warning}`:''}`,true);}
    }catch(err){
      if($('#import-plan-form')!==form){toast(err.message,true);return;}
      if(err.status===400){
        prepared=null;input.value='';valid=false;hasConflicts=false;maintenanceBlocked=false;conflicts.innerHTML='';
        showError(`${err.message}\n请保存Excel后重新选择文件导入。`);return;
      }
      if(err.status===409&&err.data?.source==='maintenance'){
        showMaintenance(err.data);
      }else if(err.status===409&&Array.isArray(err.data?.conflicts)&&err.data.conflicts.length){
        hasConflicts=true;conflicts.innerHTML=planImportConflictsHtml(err.data.conflicts);
        $$('[data-import-choice]',conflicts).forEach(el=>el.onclick=()=>submit(el.dataset.importChoice));
      }
      showError(err.message);
    }finally{setBusy(false);}
  };
  form.onsubmit=e=>{e.preventDefault();return submit();};
}
function snapshotBackupLabel(backup) {
  return ({ready:'Excel已备份',error:'Excel待重试',pending:'Excel生成中'})[backup?.status]||'Excel待生成';
}
function committeeModelName(modelKey){
  if(modelKey.startsWith('[')){
    try{
      const [type,parent,brand,model]=JSON.parse(modelKey);
      if(type==='member')return `${brand} · ${model} · 单代际（${parent}）`;
    }catch{/* The stored model key is still safe to display as text. */}
  }
  return modelKey.replace('|',' · ');
}
function committeeScopeName(decision){
  if(decision.scope==='all')return '全部';
  if(decision.scope==='brands')return (decision.brands||[]).join('、');
  return (decision.model_keys||[]).map(committeeModelName).join('、');
}
function committeeHistoryMonths({start_month,end_month}){
  if(!start_month||!end_month)return '';
  return ` · 计划月份 ${start_month===end_month?start_month:`${start_month}—${end_month}`}`;
}
function committeeHistory(items){
  if(!state.committee)return '';
  const records=items.map(item=>`<button class="snapshot-item${item.id===currentCommitteeDecision?.decision.id?' active':''}" type="button" data-committee-decision="${esc(item.id)}"><div><strong>${esc(item.period)} · ${committeeLevels[item.level]}</strong><span class="badge neutral">${item.model_count} 个车型</span></div><p>${esc(committeeScopeName(item)+committeeHistoryMonths(item))}</p><small>${esc(dateText(item.recorded_at))} · ${esc(item.owner)}</small></button>`).join('');
  return `<section class="card page-section"><div class="panel-head"><h2>计委记录</h2></div>${records?`<div class="snapshot-list">${records}</div>`:'<div class="empty-state">暂无计委记录</div>'}</section>`;
}
function renderCommitteeDecision(detail){
  const {decision,amounts,products=[],plans=[]}=detail;
  const byId=new Map(products.map(product=>[product.id,product]));
  const versions=plans.filter(row=>{const product=byId.get(row.product_id);return product&&!product.read_only&&!isModelPlan(product);});
  const quantity=value=>value==null?'':fmt(value);
  const modelName=row=>row.model_key?committeeModelName(row.model_key):`${row.brand} · ${row.model}`;
  const table=`<div class="table-scroll" tabindex="0" aria-label="计委车型总量"><table class="detail-table"><thead><tr><th>品牌 / 车型</th><th>目标月</th><th>计委量</th></tr></thead><tbody>${amounts.map(row=>`<tr><td>${esc(modelName(row))}</td><td>${esc(row.month)}</td><td>${quantity(row.sop)}</td></tr>`).join('')}</tbody></table></div>`;
  const details=versions.length?`<details class="committee-version-detail"><summary>版本明细</summary><div class="table-scroll" tabindex="0" aria-label="计委版本明细"><table class="detail-table"><thead><tr><th>品牌 / 车型</th><th>版本</th><th>目标月</th><th>计委量</th></tr></thead><tbody>${versions.map(row=>{
    const product=byId.get(row.product_id);
    return `<tr><td>${esc(committeeModelName(modelGroupKey(product)))}</td><td>${esc(product.version)}</td><td>${esc(row.month)}</td><td>${quantity(row.sop)}</td></tr>`;
  }).join('')}</tbody></table></div></details>`:'';
  $('#snapshot-detail').innerHTML=`<section class="card page-section committee-decision-detail"><div class="panel-head"><div><h2>${esc(decision.period)} · ${committeeLevels[decision.level]}</h2><p>${esc(committeeScopeName(decision)+committeeHistoryMonths(decision))} · ${esc(dateText(decision.recorded_at))} · ${esc(decision.owner)}</p></div></div>${table}${details}</section>`;
}
async function historyPage() {
  const [archive,backups,committees]=await Promise.all([
    api('/api/snapshots'),api('/api/backups/status'),state.committee?api('/api/committee-decisions'):{items:[]},
  ]);
  snapshotList=archive.items;
  if(currentSnapshot) currentSnapshot=await api(`/api/snapshots/${encodeURIComponent(currentSnapshot.snapshot.id)}`);
  $('#app').innerHTML=pageHeading('history')+
    `<div class="sync-strip ${backups.failed?'warn':''}"><div><strong>Excel备份 · 已生成 ${backups.ready}/${backups.total} 份</strong>${backups.failed?'<p>备份生成失败，等待重试</p>':backups.pending?'<p>备份生成中</p>':''}<small>${esc(backups.directory)}</small>${backups.last_error?`<p>${esc(backups.last_error)}</p>`:''}</div><div class="toolbar">${button('刷新备份状态','refresh-backups','small')}</div></div>
    <div class="two-col page-section"><section class="card"><div class="panel-head"><div><h2>快照档案</h2><p>${snapshotList.length} 份快照</p></div>${button('保存会议快照','create-snapshot','primary small')}</div>${snapshotList.length?`<div class="snapshot-list">${snapshotList.map(s=>`<button class="snapshot-item ${s.id===currentSnapshot?.snapshot?.id?'active':''}" data-snapshot="${esc(s.id)}"><div><strong>${esc(s.label||s.business_date)}</strong><span class="badge ${s.kind==='manual'?'good':'neutral'}">${s.kind==='sunday'?'周日考核':s.kind==='daily'?'每日快照':'会议快照'}</span></div><p>截点 ${dateText(s.cutoff_at)}</p><small>实际保存 ${dateText(s.created_at)} · ${s.plan_count??0} 条计划 · ${snapshotBackupLabel(s.excel_backup)}</small></button>`).join('')}</div>`:'<div class="empty-state">暂无快照</div>'}</section>
    <section class="card"><div class="panel-head"><div><h2>两次快照比较</h2></div></div><div class="card-body"><div class="form-grid"><label class="field">比较基准<select id="snapshot-left"><option value="">选择较早快照</option>${snapshotList.map(s=>opt(String(s.id),`${s.label||s.business_date} · ${dateText(s.cutoff_at)}`,'' )).join('')}</select></label><label class="field">当前快照<select id="snapshot-right"><option value="">选择较新快照</option>${snapshotList.map(s=>opt(String(s.id),`${s.label||s.business_date} · ${dateText(s.cutoff_at)}`,'' )).join('')}</select></label></div><div class="dialog-actions">${button('查看变化','compare-snapshots','primary')}</div><div id="comparison-result" class="empty-state compact">暂无比较结果</div></div></section></div>${committeeHistory(committees.items)}<section id="snapshot-detail"></section>`;
  if(currentSnapshot)renderSnapshot(currentSnapshot);
  else if(currentCommitteeDecision)renderCommitteeDecision(currentCommitteeDecision);
}
function renderSnapshot(d) {
  const s=d.snapshot; const prods=snapshotProducts(d),list=snapshotRows(d);
  const lookup=new Map(prods.map(p=>[p.id,p]));
  $('#snapshot-detail').innerHTML=`<div class="card page-section"><div class="panel-head"><div><h2>${esc(s.label||s.business_date)} · 快照明细</h2><p>截点 ${dateText(s.cutoff_at)} · 实际生成 ${dateText(s.created_at)} · 历史只读</p></div>${button('下载Excel备份','export-snapshot')}</div><div class="table-scroll"><table class="detail-table"><thead><tr><th>品牌 / 车型</th><th>版本</th><th>目标月</th><th>预测销量</th><th>SOP计划量</th><th>维护人</th><th>调整说明</th></tr></thead><tbody>${list.length?list.map(v=>{const p=lookup.get(v.product_id)||{};return `<tr><td>${esc(p.brand)} / ${esc(p.model)}</td><td>${esc(p.version||v.product_id)}</td><td>${v.month}</td><td>${v.forecast==null?'待维护':fmt(v.forecast)}</td><td>${v.sop==null?'待维护':fmt(v.sop)}</td><td>${esc(v.owner)}</td><td>${esc(v.notes)}</td></tr>`}).join(''):'<tr><td colspan="7" class="empty-state">此截点尚无已维护计划，所有版本均为待维护。</td></tr>'}</tbody></table></div><div class="table-footer">已冻结销量参考数据：${esc(d.meta?.data_through||s.meta?.data_through||'见快照导出')} · 不随之后刷新改变</div></div>`;
}
function snapshotProducts(d) {
  const prods=[...(d.products||[])],ids=new Set(prods.map(p=>p.id));
  for(const plan of d.plans||[])if(!ids.has(plan.product_id)){
    prods.push({id:plan.product_id,brand:'未归类',model:'历史版本',version:plan.product_id});ids.add(plan.product_id);
  }
  return prods;
}
function snapshotRows(d) {
  const saved=new Map((d.plans||[]).map(r=>[key(r.product_id,r.month),r]));
  const visible=[...new Set([...(d.meta?.months||[]),...(d.plans||[]).map(r=>r.month)])].sort();
  return snapshotProducts(d).flatMap(p=>visible.map(month=>saved.get(key(p.id,month))||{product_id:p.id,month,forecast:null,sop:null,owner:'',notes:''}));
}
async function workbookStatusDialog() {
  const w=await api('/api/plan-workbook/status');
  if(w.status==='absent'){
    openDialog(`<h2>尚未建立维护表</h2><p class="form-help">预定位置：${esc(w.path)}</p><div class="dialog-actions">${button('关闭','close-dialog')}${button('创建维护Excel','open-workbook')}</div>`);
    return;
  }
  openDialog(`<h2>计划表同步</h2><p class="subtitle">${esc(w.message||'')}</p><p class="form-help">维护表：${esc(w.path)}</p>${w.conflicts?.length?`<div class="table-scroll"><table class="detail-table"><thead><tr><th>版本 / 月份</th><th>共同基准</th><th>表格数值</th><th>网页当前值</th></tr></thead><tbody>${w.conflicts.map(c=>{const p=allProducts().find(p=>p.id===c.product_id);return `<tr><td>${esc(p?.model||'')} ${esc(p?.version||c.product_id)}<small>${esc(conflictField(c))}${c.reason==='plans_changed'?'（导出后计划已更新）':''}</small></td><td>${conflictValue(c)(c.baseline)}</td><td>${conflictValue(c)(c.workbook)}</td><td>${conflictValue(c)(c.database)}</td></tr>`}).join('')}</tbody></table></div><p class="form-help">以下选择仅处理冲突项；没有冲突的修改仍按同一份计划合并。</p><div class="dialog-actions">${button('暂不处理','close-dialog')}${button('冲突项保留网页值','resolve-db')}${button('冲突项采用表格值','resolve-workbook','primary')}</div>`:`<div class="dialog-actions">${button('关闭','close-dialog')}${button('重新同步','sync-workbook','primary')}</div>`}`);
}
async function compareSnapshots() {
  const left=$('#snapshot-left').value,right=$('#snapshot-right').value;
  if(!left||!right||left===right)return toast('请选择两份不同的快照',true);
  comparison=await api(`/api/compare?left=${encodeURIComponent(left)}&right=${encodeURIComponent(right)}`);
  const changed=comparison.items.filter(v=>v.forecast_before!==v.forecast_after||v.sop_before!==v.sop_after);
  $('#comparison-result').className='';$('#comparison-result').innerHTML=changed.length?`<p class="form-help">${changed.length} 项数量发生变化</p><div class="table-scroll"><table class="detail-table"><thead><tr><th>版本 / 月份</th><th>预测 前→后</th><th>SOP 前→后</th></tr></thead><tbody>${changed.map(v=>{const p=state.products.find(x=>x.id===v.product_id);return `<tr><td>${esc(p?.model||'')} ${esc(p?.version||v.product_id)}<small>${v.month}</small></td><td>${fmt(v.forecast_before)} → ${fmt(v.forecast_after)}</td><td>${fmt(v.sop_before)} → ${fmt(v.sop_after)}</td></tr>`}).join('')}</tbody></table></div>`:'<div class="empty-state compact">两份快照的计划数量没有变化。</div>';
}
async function matchingPage() {
  const request=++activeRequest;
  const params=new URLSearchParams({month:matchMonth,brand,model,product_id:matchProduct});
  const d=await api('/api/matching?'+params);
  if(request!==activeRequest||page!=='matching')return;
  const ps=products();
  $('#app').innerHTML=pageHeading('matching')+
    brandTabs()+filterBar(`<label class="field">考核月份<input id="match-month" type="month" value="${matchMonth}"></label><label class="field">考核版本<select id="match-product">${opt('','当前范围合计',matchProduct)}${ps.map(p=>opt(p.id,`${p.model} · ${p.version}`,matchProduct)).join('')}</select></label><div class="toolbar">${button('维护发货实绩','edit-shipments','primary')}</div>`)+
    `<div class="stats"><div class="stat-card"><div class="stat-label">综合匹配率 P${planToolIcon('target')}</div><div class="stat-number">${pct(d.score)}</div><div class="stat-foot">${esc(statusText(d.status)||'待数据齐全')}</div></div><div class="stat-card"><div class="stat-label">考核月实际发货量 Y${planToolIcon('shipment')}</div><div class="stat-number">${fmt(d.actual)}<span class="unit">辆</span></div><div class="stat-foot">${esc(statusText(d.actual_status)||'待维护')} · 来源SP / IoC</div></div><div class="stat-card"><div class="stat-label">计划周次完整度${planToolIcon('weeks')}</div><div class="stat-number">${d.complete_weeks??0}<span class="unit">/ 14</span></div></div><div class="stat-card"><div class="stat-label">提前0周 · 业务日期${planToolIcon('calendar')}</div><div class="stat-number date-number">${esc(d.zero_week||'—')}</div><div class="stat-foot">周日24:00 · 北京时间</div></div></div>`+
    `<section class="card"><div class="panel-head"><div><h2>14周计划与发货对照</h2><p>${shortMonth(matchMonth)} · 全月计划</p></div><div class="legend"><span><i class="legend-square sop"></i>历史SOP计划</span><span><i class="legend-line"></i>实际发货</span></div></div><div class="match-chart" tabindex="0" role="region" aria-label="14周计划与发货对照，可横向滚动">${matchingChart(d)}</div></section>`+
    `<section class="card page-section"><div class="panel-head"><div><h2>匹配率计算明细</h2><p>Pi = min(Y, Mi) ÷ max(Y, Mi) × 100%</p></div>${button('导出考核明细','export-match')}</div><div class="table-scroll"><table class="detail-table"><thead><tr><th>提前周次</th><th>规定取数日（24点）</th><th>采用快照</th><th>SOP计划 Mi</th><th>实际发货 Y</th><th>单周匹配率</th><th>权重</th><th>加权贡献</th><th>状态</th></tr></thead><tbody>${d.rows.map(r=>`<tr><td><span class="week-label">W${r.week}</span></td><td>${r.business_date}</td><td>${r.snapshot_id?`<button class="text-button" data-match-snapshot="${esc(r.snapshot_id)}">查看快照</button>`:'—'}</td><td>${fmt(r.plan)}</td><td>${fmt(r.actual)}</td><td>${pct(r.match)}</td><td>${fmt(r.weight*100)}%</td><td>${r.contribution==null?'—':Number(r.contribution).toFixed(2)+' pp'}</td><td><span class="badge ${r.match!=null?'good':'warn'}">${esc(statusText(r.status))}</span></td></tr>`).join('')}</tbody></table></div><div class="table-footer">W13–W9：每周2%　·　W8–W5：每周7.5%　·　W4–W1：每周10%　·　W0：20%</div></section>`;
  window.currentMatching=d;
}
function matchingChart(d) {
  const known=d.rows.filter(r=>r.plan!=null);
  if(!known.length&&d.actual==null)return '<div class="empty-state compact"><h3>等待周度计划与发货实绩</h3><div class="week-track">'+d.rows.map(r=>`<span>W${r.week}</span>`).join('')+'</div></div>';
  const width=1000,height=180,max=Math.max(1,d.actual||0,...known.map(r=>r.plan));
  const x=i=>45+i*70,y=v=>height-30-v/max*(height-55);
  return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="14周SOP计划与实际发货对照"><line x1="30" x2="980" y1="150" y2="150" class="chart-baseline"/>${d.actual!=null?`<line x1="30" x2="980" y1="${y(d.actual)}" y2="${y(d.actual)}" class="chart-actual" stroke-width="2" stroke-dasharray="6 5"/><text x="970" y="${Math.max(12,y(d.actual)-8)}" text-anchor="end" class="chart-actual-label" font-size="12">发货 ${fmt(d.actual)}</text>`:''}${d.rows.map((r,i)=>`${r.plan!=null?`<rect x="${x(i)}" y="${y(r.plan)}" width="30" height="${150-y(r.plan)}" class="chart-plan" rx="5"><title>W${r.week}：${r.plan}辆</title></rect>`:''}<text x="${x(i)+15}" y="173" text-anchor="middle" class="chart-week" font-size="12">W${r.week}</text>`).join('')}</svg>`;
}
function editShipments(id=matchProduct||products().find(p=>!p.read_only)?.id) {
  const p=allProducts().find(p=>p.id===id);if(!p)return toast('当前筛选没有可维护版本，请切换至单代际',true);
  if(p.read_only)return toast('自动汇总的版本行只读，请展开单代际维护成员计划',true);
  const r=shipmentIndex.get(key(id,matchMonth))||{};
  openDialog(`<form id="shipment-form"><h2>维护实际发货量</h2><div class="form-grid"><label class="field full">版本<select id="ship-product">${editorOptions(id,products())}</select></label><label class="field">考核月份<input id="ship-month" type="month" value="${matchMonth}" required></label><label class="field">实际发货量（辆）<input id="ship-quantity" type="number" min="0" step="1" value="${r.quantity??''}" required></label><label class="field">来源<select id="ship-source">${['SP','IoC'].map(s=>opt(s,s,r.source||'SP')).join('')}</select></label><label class="field">结算状态<select id="ship-status">${[['pending','待结算'],['settled','已结算'],['corrected','已更正']].map(([v,l])=>opt(v,l,r.status||'pending')).join('')}</select></label><label class="field">维护人<input id="ship-owner" value="${esc(owner)}" required></label><label class="field">实绩批次<input id="ship-batch" value="${esc(r.batch||'')}" placeholder="如 SP-202609" required></label></div><div id="form-error" class="inline-error" role="alert"></div><div class="dialog-actions">${button('取消','close-dialog')}<button type="submit" class="button primary">保存实绩</button></div></form>`);
  const refill=()=>{const r=shipmentIndex.get(key($('#ship-product').value,$('#ship-month').value))||{};$('#ship-quantity').value=r.quantity??'';$('#ship-source').value=r.source||'SP';$('#ship-status').value=r.status||'pending';$('#ship-batch').value=r.batch||''};
  $('#ship-product').onchange=refill;$('#ship-month').onchange=refill;
  $('#shipment-form').onsubmit=async e=>{e.preventDefault();const save=$('button[type="submit"]',e.target);save.disabled=true;try {const pid=$('#ship-product').value,m=$('#ship-month').value;
    await api('/api/shipments',{changes:[{product_id:pid,month:m,quantity:Number($('#ship-quantity').value),source:$('#ship-source').value,status:$('#ship-status').value,batch:$('#ship-batch').value,revision:shipmentIndex.get(key(pid,m))?.revision||0}],owner:$('#ship-owner').value.trim()});owner=$('#ship-owner').value.trim();localStorage.setItem('sop-owner',owner);closeDialog();await load();toast('发货实绩已保存');
  }catch(err){$('#form-error').textContent=err.message;save.disabled=false;}};
}
function dataPage() {
  const meta=state.meta,sources=state.sources?.length?state.sources:(meta.source_files||[]),q=(meta.quality||[]).filter(item=>item?.level==='warning'||item?.level==='error');
  const workbookActions=button(state.workbook_sync?.status==='absent'?'创建维护Excel':'打开维护Excel','open-workbook','small')
    +(state.workbook_sync?.status==='absent'?'':button('同步Excel','sync-workbook','small'));
  $('#app').innerHTML=pageHeading('data')+
    `<div class="two-col"><section class="card"><div class="panel-head"><h2>销量数据接入</h2><div class="toolbar"><span class="badge good">已接入</span>${button('来源与停用管理','manage-products','small')}${button('刷新销量文件','refresh-sales','primary small')}</div></div><div class="card-body"><dl class="info-list"><dt>业务数据截至</dt><dd>${esc(meta.data_through||'待确认')}</dd>${state.source_refresh?.status==='error'?`<dt>读取异常</dt><dd>${esc(state.source_refresh.message)}</dd>`:''}<dt>最近读取</dt><dd>${dateText(meta.generated_at)}</dd><dt>版本数量</dt><dd>${state.products.length} 个</dd><dt>参考口径</dt><dd>${meta.metrics.map(m=>esc(m.label)).join('、')}</dd></dl><h3>来源文件</h3><ul class="source-list">${sources.map(s=>`<li>${esc(typeof s==='string'?s:s.file||s.name||s.path||JSON.stringify(s))}${s.sheet?`<small>${esc(s.sheet)}</small>`:''}</li>`).join('')}</ul></div></section><section class="card"><div class="panel-head"><h2>计划与快照规则</h2><div class="toolbar">${workbookActions}</div></div><div class="card-body"><dl class="info-list"><dt>维护颗粒度</dt><dd>品牌 → 车型＋年款 × 月份；版本可选</dd><dt>本月计划口径</dt><dd>全月总量（含已发生部分）</dd><dt>年度扩展</dt><dd>每年9月开放到次年12月</dd><dt>当前计划截止</dt><dd>${shortMonth(state.plan_end)}</dd><dt>每日快照截点</dt><dd>24:00（北京时间）</dd><dt>提前0周</dt><dd>月初周内属于当月的天数超过4天，取上周日24:00。其余取当周日24:00。</dd></dl><label class="checkbox-field"><input id="bridge-toggle" type="checkbox" ${state.settings.bridge_m6!==false?'checked':''}>7、8月补足M+6（7月到次年1月，8月到次年2月）</label>${button('保存规则','save-settings')}</div></section></div>`+
    (q.length?`<section class="card page-section"><div class="panel-head"><h2>数据检查</h2></div><div class="card-body">${q.map(item=>`<div class="quality-row"><span class="badge warn">${item.level==='error'?'异常':'待核对'}</span><span>${esc(item.message||item.name||'')}</span></div>`).join('')}</div></section>`:'');
}
function optionMonths() {
  return optionRange==='rolling'
    ? Array.from({length:12},(_,i)=>shift(state.current_month,i-5))
    : yearMonths(optionYear);
}
function optionProductGroups() {
  const groups=new Map();
  for(const product of state.products||[]) {
    const id=modelGroupKey(product);
    if(!groups.has(id))groups.set(id,{id,brand:product.brand,model:product.model,products:[]});
    groups.get(id).products.push(product);
  }
  return [...groups.values()].sort((a,b)=>compareBrands(a.brand,b.brand)||a.model.localeCompare(b.model,'zh-CN'));
}
function optionMemberGroups(parent) {
  const generation=optionGeneration(parent),groups=new Map();
  for(const product of memberData().products||[]) {
    if(product.parent_generation!==generation)continue;
    const id=modelGroupKey(product);
    if(!groups.has(id))groups.set(id,{id,brand:product.brand,model:product.model,products:[],parent});
    groups.get(id).products.push(product);
  }
  return [...groups.values()].sort((a,b)=>a.model.localeCompare(b.model,'zh-CN'));
}
function optionPlanQuantity(group,month) {
  if(group.parent&&group.parent.products.some(p=>isModelPlan(p)&&planIndex.get(key(p.id,month))?.sop!=null))return null;
  return sumPlan(group.products,month,'sop').value;
}
function optionGeneration(group) {
  const summaries=[...new Set(group.products.map(p=>p.summary_generation).filter(Boolean))];
  if(summaries.length)return summaries.length===1?summaries[0]:'';
  const generations=[...new Set(group.products.flatMap(productGenerations).filter(Boolean))];
  if(generations.length===1)return generations[0];
  const inferred=`${group.brand} ${group.model}`;
  return generations.length?'' : (optionMix?.rows||[]).some(row=>row.generation===inferred)?inferred:'';
}
function optionSourceGroups(generation) {
  const groups=new Map();
  for(const row of optionMix?.rows||[]) {
    if(row.generation!==generation||row.is_vehicle_option!==true)continue;
    if(!groups.has(row.dimension))groups.set(row.dimension,[]);
    groups.get(row.dimension).push(row);
  }
  return groups;
}
function optionNameKey(value) { return String(value||'').replace(/^\s*\d{4}款\s*/i,'').replace(/[\s·・]/g,'').toLowerCase(); }
function optionShareDisplay(item) { return item.share_display??(item.share==null?'':`${(item.share*100).toFixed(2)}%`); }
function optionAllocation(group,dimension,items,month) {
  const plan=optionPlanQuantity(group,month);
  if(plan==null)return {values:items.map(()=>null)};
  if(!Number.isSafeInteger(plan)||plan<0)return {error:'SOP计划量不是非负整数'};
  const keys=items.map(item=>optionNameKey(item.option));
  if(new Set(keys).size!==keys.length)return {error:'原表选配项名称重复'};
  const modelTotal=group.products.some(p=>isModelPlan(p)&&planValue(p,month,'sop')!=null);
  const versionPlans=dimension==='版本'?group.products.filter(p=>!isModelPlan(p)&&!p.read_only&&planValue(p,month,'sop')!=null):[];
  if(versionPlans.length&&!modelTotal) {
    const byName=new Map();
    for(const p of versionPlans) {
      const name=optionNameKey(p.configuration||p.version);
      if(keys.includes(name))byName.set(name,(byName.get(name)||0)+planValue(p,month,'sop'));
    }
    return {values:keys.map(name=>byName.has(name)?byName.get(name):null),manual:true};
  }
  if(items.some(item=>typeof item.share!=='number'||!Number.isFinite(item.share)||item.share<0||item.share>1))return {error:'原表比例存在空白或无效值'};
  const shareTotal=items.reduce((sum,item)=>sum+item.share,0);
  if(Math.abs(shareTotal-1)>0.005)return {error:`原表比例合计 ${Number((shareTotal*100).toFixed(2))}%，未拆分`};
  // The displayed source shares stay unchanged; only their tiny rounding drift
  // is reconciled while allocating an integer plan across exclusive options.
  const exact=items.map(item=>plan*item.share/shareTotal),values=exact.map(value=>Math.floor(value+1e-9));
  const remainder=plan-values.reduce((sum,value)=>sum+value,0);
  if(remainder<0||remainder>items.length)return {error:'原表比例与计划总量无法按整数守恒'};
  const order=exact.map((value,index)=>({index,fraction:value-values[index]}))
    .sort((a,b)=>b.fraction-a.fraction||a.index-b.index);
  for(let i=0;i<remainder;i++)values[order[i].index]++;
  return {values,modelTotal};
}
function optionAnnual(group,dimension,items,year,index) {
  const results=yearMonths(year).map(month=>optionAllocation(group,dimension,items,month));
  if(results.some(result=>result.error))return '';
  const values=results.map(result=>result.values[index]).filter(value=>value!=null);
  return values.length?fmt(values.reduce((sum,value)=>sum+value,0)):'';
}
function optionManualVersionRows(group,generation,items,visible,rolling) {
  const sourceKeys=new Set(items.map(item=>optionNameKey(item.option))),manual=new Map();
  for(const p of group.products) {
    if(isModelPlan(p)||p.read_only)continue;
    const id=optionNameKey(p.configuration||p.version);
    if(!manual.has(id))manual.set(id,{name:p.version,products:[]});
    manual.get(id).products.push(p);
  }
  const valueAt=(entry,month,unlisted)=>{
    const override=group.products.some(p=>isModelPlan(p)&&planValue(p,month,'sop')!=null);
    if(!unlisted&&!override)return null;
    const values=entry.products.map(p=>planValue(p,month,'sop')).filter(value=>value!=null);
    return values.length?values.reduce((sum,value)=>sum+value,0):null;
  };
  const annual=(entry,year,unlisted)=>{const values=yearMonths(year).map(month=>valueAt(entry,month,unlisted)).filter(v=>v!=null);return values.length?fmt(values.reduce((sum,v)=>sum+v,0)):'';};
  let html='';
  for(const [id,entry] of manual) {
    const unlisted=!sourceKeys.has(id);
    if(!visible.some(month=>valueAt(entry,month,unlisted)!=null)&&!(rolling?visible.filter(month=>month.endsWith('-12')).some(month=>annual(entry,month.slice(0,4),unlisted)):annual(entry,optionYear,unlisted)))continue;
    const cells=visible.map(month=>`<td class="number${month<state.current_month?' past-month':''}" title="人工版本计划${unlisted?'，原表无此选项':'，车型总量优先'}">${valueAt(entry,month,unlisted)==null?'':fmt(valueAt(entry,month,unlisted))}</td>${rolling&&month.endsWith('-12')?`<td class="number annual-total">${annual(entry,month.slice(0,4),unlisted)}</td>`:''}`).join('');
    html+=`<tr class="option-item-row option-manual-row" data-option-generation="${esc(generation)}" data-option-dimension="版本" data-option-name="${esc(entry.name)}" data-option-manual="true"><th scope="row" class="option-name">${esc(entry.name)}<small>人工计划${unlisted?' · 原表无比例':' · 车型量优先'}</small></th><td class="option-share"></td>${cells}${rolling?'':`<td class="number annual-total">${annual(entry,optionYear,unlisted)}</td>`}</tr>`;
  }
  return html;
}
function optionPageRender() {
  if(optionMixError){$('#app').innerHTML=pageHeading('option-split')+`<section class="card empty-state" role="alert"><strong>${esc(optionMixError)}</strong>${button('重新读取','retry-option-mix','small')}</section>`;return;}
  const all=optionProductGroups();
  const dimensions=[...new Set((optionMix?.rows||[]).filter(row=>row.is_vehicle_option===true).map(row=>row.dimension))];
  if(optionDimension&&!dimensions.includes(optionDimension))optionDimension='';
  const years=planYears();optionYear=years.includes(optionYear)?optionYear:state.current_month.slice(0,4);
  const visible=optionMonths(),rolling=optionRange==='rolling';
  const selected=all.filter(group=>(!optionBrand||group.brand===optionBrand)&&(!optionModel||group.id===optionModel)&&(!!optionModel||visible.some(month=>optionPlanQuantity(group,month)!=null)));
  const yearCell=year=>`<th scope="col" class="annual-total calendar-plan">${year}年年度计划量</th>`;
  const headers=visible.map(month=>`<th scope="col" class="${month<state.current_month?'past-month':''}">${shortMonth(month)}</th>${rolling&&month.endsWith('-12')?yearCell(month.slice(0,4)):''}`).join('')+(rolling?'':`<th scope="col" class="annual-total">${optionYear}年合计</th>`);
  const yearPlan=(group,year)=>{const values=yearMonths(year).map(month=>optionPlanQuantity(group,month)).filter(v=>v!=null);return values.length?fmt(values.reduce((sum,v)=>sum+v,0)):'';};
  const planCells=group=>visible.map(month=>`<td class="number${month<state.current_month?' past-month':''}">${fmt(optionPlanQuantity(group,month))}</td>${rolling&&month.endsWith('-12')?`<td class="number annual-total">${yearPlan(group,month.slice(0,4))}</td>`:''}`).join('')+(rolling?'':`<td class="number annual-total">${yearPlan(group,optionYear)}</td>`);
  const unavailable=new Map((optionMix?.missing_summaries||[]).map(entry=>[entry.generation,entry.error]));
  const fullColumns=visible.length+2+(rolling?visible.filter(month=>month.endsWith('-12')).length:1);
  const renderGroup=(group,member=false)=>{
    const generation=optionGeneration(group),dimensionGroups=optionSourceGroups(generation),name=member?generation:`${group.brand} · ${group.model}`;
    const children=member?[]:optionMemberGroups(group);
    const source=dimensionGroups.values().next().value?.[0];
    const sourceMeta=source?.latest_date?`<small class="option-sample">${esc(source.latest_date)} · ${Number(source.covered_days)||0}/28天</small>`:'';
    const expand=children.length?`<button type="button" class="option-expand" data-option-expand="${esc(group.id)}" aria-label="${optionExpanded.has(group.id)?'收起':'展开'}${esc(name)}的单代际" aria-expanded="${optionExpanded.has(group.id)}">${optionExpanded.has(group.id)?'−':'+'}</button>`:'';
    let html=`<tr class="option-model-row${member?' option-member-row':''}" data-option-generation="${esc(generation)}"><th scope="row" class="option-name">${expand}${esc(name)}${sourceMeta}</th><td class="option-share">SOP计划量</td>${planCells(group)}</tr>`;
    if(!generation||!dimensionGroups.size) {
      const reason=unavailable.has(generation)?`${generation}：${unavailable.get(generation)}`:`原表缺少“${generation||name}”的直接近28天选配比例`;
      const manual=(!optionDimension||optionDimension==='版本')?optionManualVersionRows(group,generation,[],visible,rolling):'';
      return html+`<tr class="option-source-error" data-option-generation="${esc(generation)}"><td colspan="${fullColumns}">${esc(reason)}</td></tr>`+(manual?`<tr class="option-dimension-row" data-option-dimension="版本"><th scope="row" class="option-name">版本</th><td class="option-share"></td><td colspan="${fullColumns-2}"></td></tr>${manual}`:'');
    }
    if(optionDimension&&!dimensionGroups.has(optionDimension))html+=`<tr class="option-source-error" data-option-generation="${esc(generation)}"><td colspan="${fullColumns}">${esc(generation)} 原表缺少“${esc(optionDimension)}”比例</td></tr>`;
    for(const [dimension,items] of dimensionGroups) {
      if(optionDimension&&optionDimension!==dimension)continue;
      const monthResults=visible.map(month=>optionAllocation(group,dimension,items,month));
      const issue=monthResults.find(result=>result.error)?.error;
      const manual=dimension==='版本'&&monthResults.some(result=>result.manual);
      const overridden=dimension==='版本'&&visible.some(month=>group.products.some(p=>!isModelPlan(p)&&!p.read_only&&planValue(p,month,'sop')!=null)&&group.products.some(p=>isModelPlan(p)&&planValue(p,month,'sop')!=null));
      html+=`<tr class="option-dimension-row${member?' option-member-row':''}" data-option-generation="${esc(generation)}" data-option-dimension="${esc(dimension)}"><th scope="row" class="option-name">${esc(dimension)}${manual?'<small>已维护版本计划</small>':overridden?'<small>车型量优先</small>':''}</th><td class="option-share"></td><td colspan="${fullColumns-2}">${issue?`<span class="option-source-error">${esc(issue)}</span>`:''}</td></tr>`;
      if(!issue)items.forEach((item,index)=>{
        const itemCells=visible.map((month,mIndex)=>`<td class="number${month<state.current_month?' past-month':''}"${monthResults[mIndex].manual?' title="已维护版本计划"':''}>${monthResults[mIndex].values[index]==null?'':fmt(monthResults[mIndex].values[index])}</td>${rolling&&month.endsWith('-12')?`<td class="number annual-total">${optionAnnual(group,dimension,items,month.slice(0,4),index)}</td>`:''}`).join('');
        const annual=rolling?'':`<td class="number annual-total">${optionAnnual(group,dimension,items,optionYear,index)}</td>`;
        const sourceCell=`${item.source_sheet||`${item.source_generation||generation}by天`}!${item.source_cell||''}`;
        const share=optionShareDisplay(item);
        html+=`<tr class="option-item-row${member?' option-member-row':''}" data-option-generation="${esc(generation)}" data-option-dimension="${esc(dimension)}" data-option-name="${esc(item.option)}"><th scope="row" class="option-name">${esc(item.option)}</th><td class="option-share" title="${esc(sourceCell)}">${esc(share)}</td>${itemCells}${annual}</tr>`;
      });
      if(dimension==='版本')html+=optionManualVersionRows(group,generation,items,visible,rolling);
    }
    if(!dimensionGroups.has('版本')&&(!optionDimension||optionDimension==='版本')) {
      const manual=optionManualVersionRows(group,generation,[],visible,rolling);
      if(manual)html+=`<tr class="option-dimension-row" data-option-generation="${esc(generation)}" data-option-dimension="版本"><th scope="row" class="option-name">版本</th><td class="option-share"></td><td colspan="${fullColumns-2}"><span class="option-source-error">原表缺少版本比例</span></td></tr>${manual}`;
    }
    return html;
  };
  const rows=selected.map(group=>renderGroup(group)+(optionExpanded.has(group.id)?optionMemberGroups(group).map(child=>renderGroup(child,true)).join(''):'')).join('');
  const status=optionMix?.latest_date?`选配截至 <strong>${esc(optionMix.latest_date)}</strong> · 近28天覆盖 <strong>${Number(optionMix.covered_days)||0}/28天</strong>`:'选配数据待更新';
  const freshness=optionMix?.status==='stale'||optionMix?.error?`<span class="option-source-error" title="${esc(optionMix.error||'选配来源待刷新')}">选配来源待刷新</span>`:'';
  const unclassified=optionMix?.unclassified_dimensions?.length??[...new Set((optionMix?.rows||[]).filter(row=>row.is_vehicle_option==null).map(row=>row.dimension))].length;
  $('#app').innerHTML=pageHeading('option-split')+`<div class="plan-controls option-controls"><div class="annual-toolbar"><div class="annual-filters"><label class="field inline-field">周期<select id="option-range">${opt('rolling','M+6（滚动12个月）',optionRange)}${opt('annual','全年',optionRange)}</select></label>${rolling?'':`<label class="field inline-field">年份<select id="option-year">${years.map(year=>opt(year,`${year}年`,optionYear)).join('')}</select></label>`}<label class="field inline-field">品牌<select id="option-brand">${opt('','全部品牌',optionBrand)}${[...new Set(all.map(group=>group.brand))].map(name=>opt(name,name,optionBrand)).join('')}</select></label><label class="field inline-field">车型<select id="option-model">${opt('','全部车型',optionModel)}${all.filter(group=>!optionBrand||group.brand===optionBrand).map(group=>opt(group.id,optionBrand?group.model:`${group.brand} · ${group.model}`,optionModel)).join('')}</select></label><label class="field inline-field">维度<select id="option-dimension">${opt('','全部维度',optionDimension)}${dimensions.map(name=>opt(name,name,optionDimension)).join('')}</select></label></div></div><div class="sheet-meta">${status}${freshness}${unclassified?`<span>待分类维度 <strong>${unclassified}</strong></span>`:''}</div></div><section class="card option-sheet" aria-label="选配拆分计划表"><div class="table-scroll" tabindex="0"><table class="option-table"><thead><tr><th scope="col" class="option-name">车型 / 选配项</th><th scope="col" class="option-share">原表近28天占比</th>${headers}</tr></thead><tbody>${rows||`<tr><td class="empty-state" colspan="${fullColumns}">${selected.length?'没有可拆分的选配维度':'当前周期没有计划量'}</td></tr>`}</tbody></table></div></section>`;
}
async function optionPage() {
  if(!optionMix&&!optionMixError) {
    $('#app').innerHTML=pageHeading('option-split')+'<div class="loading-state" role="status"><span class="loading-spinner" aria-hidden="true"></span><strong>正在读取选配比例</strong></div>';
    const request=++optionRequestId;
    try { const fresh=await api('/api/option-mix');if(request!==optionRequestId)return;optionMix=fresh; }
    catch(error) { if(request!==optionRequestId)return;optionMixError=error.message; }
  }
  if(page==='option-split')optionPageRender();
}
let reconcilePage;
async function renderReconcilePage() {
  if (!reconcilePage) {
    const {createReconcilePage} = await import('./reconcile.mjs?v=4');
    reconcilePage ||= createReconcilePage({
      host: $('#app'), api, serviceFetch, openDialog, closeDialog, toast, esc, fmt, dateText,
      isActive: () => page === 'reconcile', getOwner: () => access.user || owner, canWrite: () => access.canWrite,
      isOwnerLocked: hasTrustedOperator,
      rememberOwner: value => {
        if (hasTrustedOperator() || !value) return;
        owner = value;
        $('#owner-btn').textContent = owner;
        try { localStorage.setItem('sop-owner', owner); } catch { /* Keep the current operator in memory. */ }
      },
    });
  }
  if (page === 'reconcile') await reconcilePage.load();
}

async function renderPage() {
  $('#page-title').textContent=titles[page];
  $$('[data-page]').forEach(b=>{b.classList.toggle('active',b.dataset.page===page);b.setAttribute('aria-current',b.dataset.page===page?'page':'false');});
  if(page==='reconcile')return renderReconcilePage();
  if(generationViewError&&page!=='history')return showGenerationError(generationViewError);
  if(page==='planning')planning();else if(page==='option-split')await optionPage();else if(page==='history')await historyPage();else if(page==='matching')await matchingPage();else dataPage();
}
async function handleAction(action,el) {
  if(writeActions.has(action))requireWrite();
  if(access.gateway&&action==='open-workbook')throw new Error('请通过下载或导出使用Excel');
  if(action==='grid-save')return saveGrid();
  if(action==='grid-cancel'){if(gridLocked())return;gridDraft=null;planning();return;}
  if(action==='grid-add')return gridAddRow(el?.dataset?.newModelBrand??brand);
  if(action==='view-save')return saveTableView();
  if(action==='view-apply')return applyTableView();
  if(action==='view-delete'){tableViews.views=tableViews.views.filter(v=>v.id!==selectedView);if(tableViews.active===selectedView)tableViews.active='';selectedView='';viewNameDraft=null;persistTableViews();planning();return;}
  if(action==='rows-restore'){hiddenRows={};refreshHiddenRows();return;}
  if(action==='retry-option-mix'){optionRequestId++;optionMix=null;optionMixError='';return optionPage();}

  if(action==='close-dialog')return closeDialog();
  if(action==='manage-products')return productManager();
  if(action==='new-product')return editProduct();
  if(action==='rename-model')return renameModel();
  if(action==='open-workbook'){
    const creating=state.workbook_sync?.status==='absent';
    await api('/api/plan-workbook/open',{});
    if($('#edit-dialog').open)closeDialog();
    await load();
    return toast(creating?'已创建并打开维护表；修改后请保存':'已请求打开统一计划表；修改后请保存');
  }
  if(action==='workbook-status')return workbookStatusDialog();
  if(['sync-workbook','resolve-db','resolve-workbook'].includes(action)){
    el.disabled=true;
    try { const result=await api('/api/plan-workbook/sync',action.startsWith('resolve-')?{choice:action==='resolve-db'?'db':'workbook'}:{});
      if($('#edit-dialog').open)closeDialog();await load();
      if(result.status==='conflict')return workbookStatusDialog();
      toast(result.message||'计划表已同步',result.ok===false||result.status==='error');
    } finally {el.disabled=false;}return;
  }
  if(action==='edit-first')return bulkPlan();
  if(action==='bulk-plan'){if($('#edit-dialog').open)closeDialog();return bulkPlan();}
  if(action==='expand-all'){allExpanded=!allExpanded;expanded.clear();return gridDraft?gridRender():planning();}
  if(action==='toggle-committee'){
    const level=Number(el.dataset.committeeLevel);
    if(![1,2,3].includes(level))return;
    showCommitteeLevels[level-1]=!showCommitteeLevels[level-1];
    try{localStorage.setItem(committeeLevelsPreferenceKey,JSON.stringify(showCommitteeLevels));}catch{/* The current view is still updated. */}
    gridRender();
    $(`[data-action="toggle-committee"][data-committee-level="${level}"]`)?.focus({preventScroll:true});
    return;
  }
  if(action==='toggle-locks'){
    const scroll=$('.plan-card .table-scroll'),left=scroll?.scrollLeft||0;
    showLocks=!showLocks;localStorage.setItem(lockPreferenceKey,String(showLocks));planning();
    $('.plan-card .table-scroll').scrollLeft=left;
    $('[data-action="toggle-locks"]').focus({preventScroll:true});return;
  }
  if(action==='export-plan')return exportPlans();
  if(action==='toggle-wenjie'){
    const left=$('.plan-card .table-scroll')?.scrollLeft||0;
    setWenjieVisibility(!showWenjie);planning();
    $('.plan-card .table-scroll').scrollLeft=left;
    $('[data-action="toggle-wenjie"]').focus({preventScroll:true});return;
  }
  if(action==='import-plan')return importPlans();
  if(action==='detail-edit'){closeDialog();return editInGrid(el.dataset.product);}
  if(action==='edit-shipments')return editShipments();
  if(action==='save-settings'){await api('/api/settings',{bridge_m6:$('#bridge-toggle').checked});await load();return toast('计划范围规则已保存');}
  if(action==='refresh-sales') {el.disabled=true;const text=el.textContent;el.textContent='正在读取…';try{await api('/api/refresh',{});await load();toast('销量已刷新，人工计划已保留');}finally{el.disabled=false;el.textContent=text;}return;}
  if(action==='refresh-backups')return historyPage();
  if(action==='create-snapshot') {
    openDialog(`<form id="snapshot-form"><h2>保存会议快照</h2><label class="field">快照名称<input id="snapshot-label" value="${state.current_month} SOP会议" required maxlength="100"></label><div id="form-error" class="inline-error"></div><div class="dialog-actions">${button('取消','close-dialog')}<button class="button primary" type="submit">保存快照</button></div></form>`);
    $('#snapshot-form').onsubmit=async e=>{e.preventDefault();const save=$('button[type="submit"]',e.target);save.disabled=true;try{const saved=await api('/api/snapshots',{label:$('#snapshot-label').value});currentSnapshot=saved;currentCommitteeDecision=null;closeDialog();await historyPage();toast(saved.excel_backup?.status==='ready'?'会议快照与Excel备份已保存':'快照已保存，Excel备份正在生成或等待重试',saved.excel_backup?.status==='error');}catch(err){$('#form-error').textContent=err.message;save.disabled=false;}};return;
  }
  if(action==='compare-snapshots')return compareSnapshots();
  if(action==='export-snapshot') {
    const s=currentSnapshot.snapshot,originalText=el.textContent;el.disabled=true;el.textContent='正在准备Excel…';
    try {
      const response=await serviceFetch(`/api/snapshots/${encodeURIComponent(s.id)}/excel`);
      if(!response.ok){const failure=await response.json();throw new Error(failure.error||'Excel备份尚未生成，请稍后重试');}
      const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');
      link.href=url;link.download=`SOP快照_${s.business_date}_${s.id}.xlsx`;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);
      await historyPage();toast('Excel备份已准备好');
    }finally{el.disabled=false;el.textContent=originalText;}
    return;
  }
  if(action==='export-match') {const d=window.currentMatching;return exportCSV(`匹配率_${matchMonth}.csv`,['提前周次','业务日期','SOP计划','实际发货','匹配率%','权重','贡献百分点','状态'],d.rows.map(r=>[r.week,r.business_date,r.plan,r.actual,r.match,r.weight,r.contribution,statusText(r.status)]));}
}
async function pollWorkbookStatus() {
  if(!state||document.hidden||$('#edit-dialog').open||generationSwitchPending||gridDraft)return;
  try {
    const sync=await api('/api/plan-workbook/status');if(gridDraft)return;const previous=state.workbook_sync||{},view=sync.generation_view;
    if(view?.error) {
      state.generation_view=view;
      showGenerationError(view.error,'rules');
      return;
    }
    if(generationViewError||generationStatusChanged(view)||sync.last_sync!==previous.last_sync||sync.status!==previous.status||sync.message!==previous.message||JSON.stringify(sync.source_refresh||{})!==JSON.stringify(state.source_refresh||{})) {
      const scrolls=$$('.table-scroll').map(el=>({left:el.scrollLeft,top:el.scrollTop}));
      await load();$$('.table-scroll').forEach((el,i)=>{if(scrolls[i]){el.scrollLeft=scrolls[i].left;el.scrollTop=scrolls[i].top;}});
    }
  } catch { /* Manual refresh reports connection errors; the next poll retries. */ }
}
document.addEventListener('click',async e=>{
  const el=e.target.closest('button');if(!el)return;
  try {
    if(el.dataset.gridRemove){gridRemoveRow(el.dataset.gridRemove);return;}
    if(el.dataset.gridRemoveModel){gridRemoveModel(el.dataset.gridRemoveModel);return;}
    if('gridAddBrand' in el.dataset){gridAddRow(el.dataset.gridAddBrand,el.dataset.gridAddModel,el.dataset.gridAnchor);return;}
    if(el.dataset.optionExpand){const scroll=$('.option-sheet .table-scroll'),left=scroll?.scrollLeft||0,top=scroll?.scrollTop||0,id=el.dataset.optionExpand;if(optionExpanded.has(id))optionExpanded.delete(id);else optionExpanded.add(id);optionPageRender();const next=$('.option-sheet .table-scroll');if(next){next.scrollLeft=left;next.scrollTop=top;}$(`[data-option-expand="${CSS.escape(id)}"]`)?.focus({preventScroll:true});return;}
    if(el.dataset.expand){if(!gridLocked())toggleModel(el.dataset.expand);return;}
    if(gridDraft&&!['grid-save','grid-cancel','grid-add','expand-all','toggle-locks','toggle-committee'].includes(el.dataset.action)){toast('请先保存或取消表内修改，再进行其他操作',true);return;}
    if(gridLocked()&&['expand-all','toggle-locks','toggle-committee'].includes(el.dataset.action))return;
    if(el.dataset.hideLevel){hideRow(el.dataset.hideLevel,el.dataset.hideId,el.dataset.hideLabel);return;}
    if(el.dataset.restoreRow){delete hiddenRows[el.dataset.restoreRow];refreshHiddenRows();return;}
    if(el.dataset.catalogBind){requireWrite();bindProductSource(el.dataset.catalogBind);return;}
    if(el.dataset.catalogEdit){requireWrite();editProduct(el.dataset.catalogEdit);return;}
    if(el.dataset.page){page=el.dataset.page;if(page==='matching')query='';if(page==='option-split'){optionRequestId++;optionMix=null;optionMixError='';}await renderPage();return;}
    if('brand' in el.dataset){brand=el.dataset.brand;model='';matchProduct='';query='';selected=null;await renderPage();return;}
    if(el.dataset.range){range=el.dataset.range;planning();return;}
    if(el.dataset.edit){requireWrite();await editInGrid(el.dataset.edit,el.dataset.month);return;}
    if(el.dataset.detail){await showDetail(el.dataset.detail);return;}
    if(el.dataset.snapshot||el.dataset.committeeDecision){
      const request=++activeRequest,decision=el.dataset.committeeDecision;
      const detail=await api(`/api/${decision?'committee-decisions':'snapshots'}/${encodeURIComponent(decision||el.dataset.snapshot)}`);
      if(page!=='history'||request!==activeRequest)return;
      currentSnapshot=decision?null:detail;
      currentCommitteeDecision=decision?detail:null;
      $$('[data-snapshot],[data-committee-decision]').forEach(button=>button.classList.toggle('active',button===el));
      if(decision)renderCommitteeDecision(detail);else renderSnapshot(detail);
      return;
    }
    if(el.dataset.matchSnapshot){currentSnapshot=await api(`/api/snapshots/${encodeURIComponent(el.dataset.matchSnapshot)}`);currentCommitteeDecision=null;page='history';await renderPage();return;}
    if(el.hasAttribute('data-close'))return closeDialog();
    if(el.dataset.action)await handleAction(el.dataset.action,el);
  }catch(err){toast(err.message,true);}
});
document.addEventListener('change',async e=>{
  try {
    const control=e.target,decision=gridDraft?.committee;
    if(gridDraft&&control.id==='grid-historical-receipt-correction'){
      if(gridLocked())return;
      gridDraft.historicalReceiptCorrection=control.checked;
      gridDraft.request=null;
      gridDraft.error='';
      gridRender();
      $('#grid-historical-receipt-correction')?.focus({preventScroll:true});
      return;
    }
    if(decision&&!gridLocked()&&['grid-committee-level','grid-committee-period','grid-committee-scope','grid-committee-start','grid-committee-end'].includes(control.id)){
      if(control.id==='grid-committee-level'){
        decision.level=Number(control.value);
        decision.start_month='';
        decision.end_month='';
      }
      if(control.id==='grid-committee-period')decision.period=control.value;
      if(control.id==='grid-committee-scope')decision.scope=control.value;
      if(control.id==='grid-committee-start'){
        decision.start_month=control.value;
        if(!decision.start_month||decision.end_month<decision.start_month)decision.end_month='';
      }
      if(control.id==='grid-committee-end')decision.end_month=control.value;
      gridDraft.request=null;
      if(!['grid-committee-period','grid-committee-end'].includes(control.id)){
        gridRender();
        $(`#${control.id}`)?.focus({preventScroll:true});
      }
      return;
    }
    if(decision&&!gridLocked()&&control.hasAttribute?.('data-committee-selection')){
      const field=decision.scope==='brands'?'brands':'model_keys';
      decision[field]=$$('[data-committee-selection]:checked').map(input=>input.value);
      $('#grid-committee-count').textContent=String(decision[field].length);
      gridDraft.request=null;
      return;
    }
    if(control.dataset.gridId||control.dataset.gridGroup){
      const options=$('#grid-committee-options');
      if(decision?.level&&options&&(control.dataset.gridGroup||['brand','model'].includes(control.dataset.gridField))){
        options.innerHTML=committeeSelectionControls();
      }
      return;
    }
    if(['grid-owner','grid-reason'].includes(control.id))return;
    if(gridDraft){toast('请先保存或取消表内修改',true);gridRender();return;}
    if(e.target.id==='saved-table-view'){const prior=selectedView;try{selectedView=e.target.value;viewNameDraft=null;if(selectedView)applyTableView();else planning();}catch(error){selectedView=prior;e.target.value=prior;throw error;}return;}
    if(e.target.id==='restore-hidden-row'){if(e.target.value){delete hiddenRows[e.target.value];refreshHiddenRows();}return;}
    if(e.target.id==='generation-filter'){await changeGenerationMode(e.target.value,e.target);return;}
    if(e.target.id==='option-range'){optionRange=e.target.value==='annual'?'annual':'rolling';optionPageRender();return;}
    if(e.target.id==='option-year'){optionYear=e.target.value;optionPageRender();return;}
    if(e.target.id==='option-brand'){optionBrand=e.target.value;optionModel='';optionPageRender();return;}
    if(e.target.id==='option-model'){optionModel=e.target.value;optionPageRender();return;}
    if(e.target.id==='option-dimension'){optionDimension=e.target.value;optionPageRender();return;}
    if(e.target.id==='range-filter'){range=e.target.value==='annual'?'annual':'rolling';planning();}
    if(e.target.id==='year-filter'){selectPlanYear(e.target.value);planning();}
    if(e.target.id==='brand-filter'){brand=e.target.value;model='';matchProduct='';query='';selected=null;planning();}
    if(e.target.id==='model-filter'){model=e.target.value;selected=null;matchProduct='';await renderPage();}
    if(e.target.id==='history-range'){historyRange=e.target.value;planning();}
    if(e.target.id==='metric-filter'){metric=e.target.value;planning();}
    if(e.target.id==='match-month'){if(!e.target.value)return;matchMonth=e.target.value;await matchingPage();}
    if(e.target.id==='match-product'){matchProduct=e.target.value;await matchingPage();}
  }catch(err){toast(err.message,true);}
});
if(typeof MutationObserver!=='undefined'){const accessObserver=new MutationObserver(()=>applyAccessControls());for(const selector of ['#app','#dialog-content']){const region=$(selector);if(region)accessObserver.observe(region,{childList:true,subtree:true});}}
let searchTimer;
document.addEventListener('input',e=>{
  if(gridDraft){if(gridLocked())return;if(e.target.dataset.gridGroup){gridChangeModel(e.target.dataset.gridGroup,e.target.dataset.gridGroupField,e.target.value);return;}const row=gridRow(e.target.dataset.gridId);if(row){if(e.target.dataset.gridMonth)row.values[e.target.dataset.gridMonth]=e.target.value;else if(e.target.dataset.gridField)row[e.target.dataset.gridField]=e.target.value;e.target.removeAttribute('aria-invalid');e.target.classList.add('changed');gridDraft.request=null;return;}if(e.target.id==='grid-owner'){if(!hasTrustedOperator())gridDraft.owner=e.target.value;return;}if(e.target.id==='grid-reason'){gridDraft.reason=e.target.value;return;}return;}
  if(e.target.id==='table-view-name'){viewNameDraft=e.target.value;return;}
  if(e.target.id==='version-search'){clearTimeout(searchTimer);query=e.target.value;searchTimer=setTimeout(async()=>{const pos=e.target.selectionStart;await renderPage();const inp=$('#version-search');if(inp){inp.focus();inp.setSelectionRange(pos,pos)}},300);}});
$('#owner-btn')?.addEventListener('click',()=>{
  if(hasTrustedOperator())return;
  if(gridDraft)return toast('请在表内维护条填写维护人',true);
  openDialog(`<form id="owner-form"><h2>设置维护人</h2><label class="field">姓名<input id="owner-name" value="${esc(owner)}" required maxlength="60"></label><div class="dialog-actions">${button('取消','close-dialog')}<button class="button primary" type="submit">保存</button></div></form>`);
  $('#owner-form').onsubmit=e=>{e.preventDefault();owner=$('#owner-name').value.trim();localStorage.setItem('sop-owner',owner);$('#owner-btn').textContent=owner;closeDialog();};
});
$('#refresh-btn')?.addEventListener('click',async()=>{try{await load();toast('已载入最新计划');}catch(err){toast(err.message,true);}});
window.addEventListener('beforeunload',e=>{if(gridDraft){e.preventDefault();e.returnValue='';}});
window.addEventListener('unhandledrejection',e=>toast(e.reason?.message||'加载失败，请重试',true));
initializeAccess().then(()=>load()).catch(err=>{$('#app').innerHTML=`<div class="empty-state"><h1>暂时无法连接数据服务</h1><p>${esc(err.message)}</p><p>${access.gateway?'请联系应用负责人检查内网业务服务，稍后刷新重试。':'请使用项目中的启动脚本打开看板。'}</p></div>`;});

// Rule changes also refresh an absent maintenance workbook's current view.
setInterval(pollWorkbookStatus,8000);
