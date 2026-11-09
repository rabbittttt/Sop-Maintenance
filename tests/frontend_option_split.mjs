import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source=readFileSync(new URL('../web/app.js',import.meta.url),'utf8');
const app={innerHTML:''};
const context=vm.createContext({
  document:{querySelector:selector=>selector==='#app'?app:null,querySelectorAll:()=>[]},
  localStorage:{getItem:()=>null,setItem(){}},
});
vm.runInContext(source.split("document.addEventListener('click'")[0],context);
const run=code=>vm.runInContext(code,context);
run(`state={current_month:'2026-10',plan_end:'2027-12',months:['2026-10','2026-12','2027-01'],meta:{},
  products:[
    {id:'model',brand:'智界',model:'R7 2026款',generation:'智界 R7 2026款',version:modelPlanLabel,plan_level:'model',active:true},
    {id:'manual-version',brand:'智界',model:'R7 2026款',generation:'智界 R7 2026款',version:'新版本',active:true},
    {id:'summary',brand:'享界',model:'X4 2026款',summary_generation:'享界 X4 2026款汇总',version:modelPlanLabel,plan_level:'model',active:true},
    {id:'empty',brand:'尊界',model:'历史车型 2025款',generation:'尊界 历史车型 2025款',version:modelPlanLabel,plan_level:'model',active:true},
  ],
  plans:[{product_id:'model',month:'2026-10',sop:100},{product_id:'model',month:'2026-12',sop:7},
    {product_id:'manual-version',month:'2026-10',sop:20},{product_id:'summary',month:'2026-10',sop:80}],
  sales:[],model_sales:[],generation_members:{products:[
    {id:'member',brand:'享界',model:'S9 2026款',generation:'享界 S9 2026款',parent_generation:'享界 X4 2026款汇总',version:modelPlanLabel,plan_level:'model',active:true}
  ],plans:[],sales:[],model_sales:[],groups:[]}};
  page='option-split';indexData();
  optionMix={status:'ready',source_file:'鸿蒙智行锁单选配比例分析.xlsx',latest_date:'2026-08-18',covered_days:14,
    rows:[
      {generation:'智界 R7 2026款',dimension:'外观',option:'红',share:.6,share_display:'60.00%',sample_count:1,latest_date:'2026-08-18',covered_days:14,is_vehicle_option:true,source_cell:'R20'},
      {generation:'智界 R7 2026款',dimension:'外观',option:'蓝',share:.4,share_display:'40.00%',sample_count:999,latest_date:'2026-08-18',covered_days:14,is_vehicle_option:true,source_cell:'R21'},
      {generation:'智界 R7 2026款',dimension:'版本',option:'Max',share:.7,share_display:'70.00%',latest_date:'2026-08-18',covered_days:14,is_vehicle_option:true,source_cell:'R9'},
      {generation:'智界 R7 2026款',dimension:'版本',option:'Pro',share:.3,share_display:'30.00%',latest_date:'2026-08-18',covered_days:14,is_vehicle_option:true,source_cell:'R10'},
      {generation:'智界 R7 2026款',dimension:'地理区域',option:'华北',share:1,share_display:'100.00%',is_vehicle_option:false},
      {generation:'享界 S9 2026款',dimension:'外观',option:'银',share:1,share_display:'100.00%',is_vehicle_option:true}
    ],missing_summaries:[{generation:'享界 X4 2026款汇总',error:'享界 X4 2026款汇总缺少直接来源'}]};
  optionPageRender()`);

assert.match(app.innerHTML,/data-page|原表近28天占比/);
assert.match(app.innerHTML,/60\.00%/);
assert.match(app.innerHTML,/近28天覆盖 <strong>14\/28天<\/strong>/);
assert.doesNotMatch(app.innerHTML,/data-option-dimension="地理区域"|data-option-generation="尊界 历史车型 2025款"/);
const row=(generation,dimension,name)=>app.innerHTML.match(new RegExp(`<tr class="option-item-row[^>]*data-option-generation="${generation}"[^>]*data-option-dimension="${dimension}"[^>]*data-option-name="${name}"[^]*?<\\/tr>`))?.[0];
assert.match(row('智界 R7 2026款','外观','红'),/60\.00%[^]*?<td class="number">60<\/td>/,'Use the source displayed ratio, not the inconsistent sample counts.');
assert.match(app.innerHTML,/2026年年度计划量/, 'The source row remains in the calendar table.');
assert.match(app.innerHTML,/享界 X4 2026款汇总缺少直接来源/);
assert.doesNotMatch(app.innerHTML,/data-option-generation="享界 X4 2026款汇总"[^]*?data-option-name="银"/,'Member ratios must not stand in for a missing summary source.');
assert.match(app.innerHTML,/data-option-manual="true"[^]*?新版本/,'A manual version absent from the source remains visible.');
assert.match(app.innerHTML,/原表无比例/);
assert.equal(run("optionAllocation(optionProductGroups()[0],'外观',optionSourceGroups('智界 R7 2026款').get('外观'),'2026-12').values[0]"),4);
run("optionRange='annual';optionYear='2026';optionPageRender()");
assert.match(row('智界 R7 2026款','外观','红'),/annual-total">64<\/td>/,'Annual option quantities sum all 12 months.');
run("optionExpanded.add(optionProductGroups()[1].id);optionRange='rolling';optionPageRender()");
assert.match(app.innerHTML,/data-option-generation="享界 S9 2026款"/,'Single generations can be inspected without filling the summary.');
run("optionMix.status='stale';optionMix.error='源文件暂时不可读';optionPageRender()");
assert.match(app.innerHTML,/选配来源待刷新/);
const largeGroup=run('optionProductGroups()[0]');
context.largeGroup=largeGroup;
run("planIndex.set('model|2026-12',{sop:100000})");
const nearHundred=[{option:'甲',share:.4999},{option:'乙',share:.5}];
context.nearHundred=nearHundred;
assert.equal(run("optionAllocation(largeGroup,'外观',nearHundred,'2026-12').values.reduce((a,b)=>a+b,0)"),100000,
  'Source display rounding may total 99.99%; integer apportionment must still conserve a large plan.');
assert.match(run("optionAllocation(largeGroup,'外观',[{option:'甲',share:.6},{option:'乙',share:.2}],'2026-12').error"),/比例合计/,
  'A material source gap must not be normalized away.');
