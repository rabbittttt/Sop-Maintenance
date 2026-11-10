/** Conservative, browser-local evidence extraction. Never confirms a candidate. */
const LABELS = Object.freeze({
  locked_orders: '交车锁单', sop_plan: 'SOP计划量', sales_actual: '实际销量', sales_plan: '销量预测/计划', supply_plan: '供应计划',
  transit: '在途量', inbound_forecast: '预计入库', inbound_actual: '实际入库', outbound_actual: '实际出库',
  opening_stock: '期初库存', closing_stock: '期末库存', adjustments: '库存调整',
});
export function metricLabel(key) { return LABELS[key] || '待选择口径'; }
export function columnLabel(index) {
  if (!Number.isInteger(index) || index < 0) return '';
  let value = index + 1, label = '';
  while (value) { value--; label = String.fromCharCode(65 + value % 26) + label; value = Math.floor(value / 26); }
  return label;
}
const clean = value => value == null ? '' : String(value).trim();
const compact = value => clean(value).replace(/[\s()（）:：_]/g, '').toLowerCase();
const blank = value => value == null || clean(value) === '';
const uid = () => globalThis.crypto?.randomUUID?.() || `record-${Date.now()}-${Math.random().toString(36).slice(2)}`;
const unique = values => [...new Set(values.filter(value => value !== '' && value != null && value !== false))];
const fieldMatchers = {
  model: /^(车型|车系|产品|产品线|model|vehiclemodel|sku)$/i,
  variant: /^(配置|版本|款型|配置版本|动力|variant|trim)$/i,
  location: /^(仓库|仓库名称|库位|地点|入库地点|交付中心|location|warehouse)$/i,
  period: /^(日期|时间|周期|月份|月度|周次|周|周起始日|统计日期|期间|period|date|week|month)$/i,
  quantity: /^(数量|台数|辆数|量|数量辆|数量台|quantity|qty|value)$/i,
  batch: /^(批次|批次号|批次编号|vin|车架号|车辆识别码|batch)$/i,
  metric: /^(指标|口径|指标名称|业务口径|数据类型|metric)$/i,
};
const metricRules = [
  ['sop_plan', /S\s*&?\s*OP\s*(?:计划(?:量)?|数量|量)/i],
  ['locked_orders', /交车锁单(?:量|数)?|锁单(?:量|数)?/],
  ['sales_plan', /销量预测|销售预测|预测销量|销量计划|销售计划|计划销量|预计销量|预计销售|计划销售/],
  ['supply_plan', /供应计划|供货计划|计划供应|计划供货|预计供应|预计供货|供应承诺|可供应|排产计划|计划排产|排产|供应量|供货量|供应|供货/],
  ['transit', /在途(?:数量|车辆|量)?/],
  ['opening_stock', /期初库存|周初库存|月初库存|初始库存|期初/],
  ['closing_stock', /期末库存|周末库存|月末库存|期末结存|期末/],
  ['adjustments', /库存调整|调整量|调整数量|盘盈|盘亏/],
  ['inbound_forecast', /预计入库|计划入库|预测入库|预估入库|入库预测|入库计划|入库预计|入库预估/],
  ['inbound_actual', /实际入库|入库实绩|已入库|完成入库|入库实际|入库量|入库/],
  ['outbound_actual', /实际出库|出库实绩|已出库|完成出库|出库实际|出库量|出库/],
  ['sales_actual', /实际销量|实际销售|销量实绩|销售实绩|已销售|已售|销量|销售量/],
];
function metricFrom(value) {
  const text = clean(value);
  if (Object.hasOwn(LABELS, text)) return text;
  if (text === LABELS.sales_plan) return 'sales_plan';
  for (const [key, pattern] of metricRules) if (pattern.test(text)) {
    if (['inbound_actual', 'outbound_actual', 'sales_actual'].includes(key) && /预计|计划|预测|拟|承诺|目标|待|将/.test(text)) return key === 'inbound_actual' && /预计|计划|预测|预估/.test(text) ? 'inbound_forecast' : '';
    return key;
  }
  return '';
}
function dateParts(year, month, day) {
  const date = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)));
  if (date.getUTCFullYear() !== Number(year) || date.getUTCMonth() + 1 !== Number(month) || date.getUTCDate() !== Number(day)) return null;
  return date;
}
const iso = date => date.toISOString().slice(0, 10);
function monday(date) { const result = new Date(date); result.setUTCDate(result.getUTCDate() - (result.getUTCDay() + 6) % 7); return result; }
function contextDate(context) {
  const match = clean(context.asOf).match(/^(\d{4})-(\d{2})-(\d{2})/);
  return match ? dateParts(match[1], match[2], match[3]) : null;
}
function periodFrom(value, context = {}) {
  const result = { period: '', granularity: context.granularity || 'week', warnings: [] };
  if (value instanceof Date && !Number.isNaN(+value)) value = iso(value);
  const text = clean(value);
  if (!text) return result;
  let match;
  // ISO weeks have an explicit, well-defined Monday. Bare W43 needs the supplied year.
  match = text.match(/(?:(\d{4})\s*[-年/]?\s*)?[Ww](\d{1,2})(?!\d)/);
  if (match) {
    const asOf = contextDate(context), year = match[1] ? +match[1] : asOf?.getUTCFullYear();
    if (!year || +match[2] < 1 || +match[2] > 53) return { ...result, warnings: ['周编号缺少年份或超出范围；请补全周期'] };
    const start = monday(new Date(Date.UTC(year, 0, 4))); start.setUTCDate(start.getUTCDate() + (+match[2] - 1) * 7);
    const thursday = new Date(start); thursday.setUTCDate(thursday.getUTCDate() + 3);
    if (thursday.getUTCFullYear() !== year) return { ...result, warnings: ['该年份不存在此 ISO 周；请核对'] };
    return { period: iso(start), granularity: 'week', warnings: match[1] ? [] : ['周编号年份来自资料截止日期，请确认'] };
  }
  if (/(?:第?\d+周|周一|周二|周三|周四|周五|周六|周日|周末|月底|月初|旬)/.test(text) && !/本周|下周|上周/.test(text)) {
    return { ...result, warnings: ['周次/旬/月底表述不能唯一确定周期，请手动补全'] };
  }
  match = text.match(/(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})(?:日|号)?(?!\d)/);
  if (match) {
    const date = dateParts(match[1], match[2], match[3]);
    if (!date) return { ...result, warnings: ['日期无效，请核对'] };
    const grain = context.granularity || (/周|week/i.test(text) ? 'week' : 'day');
    if (/\d\s*(?:~|～|至|—)\s*\d/.test(text)) result.warnings.push('检测到日期范围，请核对所选周期覆盖范围');
    if (grain === 'month') return { period: iso(date).slice(0, 7), granularity: grain, warnings: ['完整日期按所选月粒度归属，请确认'] };
    if (grain === 'week') result.warnings.push('明确日日期按所选周粒度归属到周一，请确认此数量确为整周口径（即使原日期恰为周一）');
    return { period: iso(grain === 'week' ? monday(date) : date), granularity: grain, warnings: result.warnings };
  }
  match = text.match(/(\d{4})\s*[-/.年]\s*(\d{1,2})(?:月)?(?!\d)/);
  if (match) {
    if (+match[2] < 1 || +match[2] > 12) return { ...result, warnings: ['月份无效，请核对'] };
    return { period: `${match[1]}-${String(+match[2]).padStart(2, '0')}`, granularity: 'month', warnings: [] };
  }
  const asOf = contextDate(context);
  match = text.match(/(\d{1,2})\s*(?:月|\/)\s*(\d{1,2})(?:日|号)?(?!\d)/);
  if (match) {
    if (!asOf) return { ...result, warnings: ['日期缺少年份；请设置资料截止日期或补全日期'] };
    const date = dateParts(asOf.getUTCFullYear(), match[1], match[2]);
    if (!date) return { ...result, warnings: ['日期无效，请核对'] };
    const grain = context.granularity || 'day';
    return { period: grain === 'month' ? iso(date).slice(0, 7) : iso(grain === 'week' ? monday(date) : date), granularity: grain, warnings: ['日期年份来自资料截止日期，请确认（跨年时尤其注意）', ...(grain === 'week' ? ['明确日日期按所选周粒度归属到周一，请确认此数量确为整周口径'] : [])] };
  }
  match = text.match(/(\d{1,2})月/);
  if (match) {
    if (!asOf || +match[1] < 1 || +match[1] > 12) return { ...result, warnings: ['月份缺少年份或无效，请补全'] };
    return { period: `${asOf.getUTCFullYear()}-${String(+match[1]).padStart(2, '0')}`, granularity: 'month', warnings: ['月份年份来自资料截止日期，请确认'] };
  }
  match = text.match(/本周|下周|上周|本月|下月|上月|今天|今日|明天|明日|昨天|昨日/);
  if (match) {
    if (!asOf) return { ...result, warnings: ['相对日期缺少资料截止日期，未按今天猜测'] };
    const token = match[0], date = new Date(asOf);
    if (token.endsWith('周')) { const start = monday(date); start.setUTCDate(start.getUTCDate() + (token === '下周' ? 7 : token === '上周' ? -7 : 0)); return { period: iso(start), granularity: 'week', warnings: ['相对周期按资料截止日期解析，请确认'] }; }
    if (token.endsWith('月')) { date.setUTCDate(1); date.setUTCMonth(date.getUTCMonth() + (token === '下月' ? 1 : token === '上月' ? -1 : 0)); return { period: iso(date).slice(0, 7), granularity: 'month', warnings: ['相对周期按资料截止日期解析，请确认'] }; }
    date.setUTCDate(date.getUTCDate() + (/明/.test(token) ? 1 : /昨/.test(token) ? -1 : 0));
    return { period: iso(date), granularity: 'day', warnings: ['相对日期按资料截止日期解析，请确认'] };
  }
  return result;
}
function looksLikePeriod(value) {
  const text = clean(value);
  return /^\s*(?:(?:\d{4}[-/.年])?\d{1,2}(?:[-/.月]\d{1,2}(?:日|号)?)?|(?:\d{4}[-年/]?)?[Ww]\d{1,2}|\d{1,2}月|本周|下周|本月|下月)(?:月|周|\s*\(.*\))?\s*$/.test(text) && /[-/.年月周w]/i.test(text);
}
function parseQuantity(value, metric) {
  const text = clean(value);
  if (!text || /^(?:-|—|–|n\/?a|null|未提供|待定|未知|待确认)$/i.test(text)) return { quantity: null, warnings: ['数量缺失，未按零处理'] };
  if (typeof value === 'boolean' || /约|大概|左右|以上|以下|至少|至多|不少于|不超过|预计区间|待确认|\d\s*[-~～至到—/]\s*\d/.test(text)) return { quantity: null, warnings: ['数量为范围或模糊描述，请手动确认'] };
  const match = text.replace(/，/g, ',').match(/^([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(万)?\s*(辆|台|台车|单)?$/);
  if (!match) return { quantity: null, warnings: ['数量格式无法可靠识别，请手动确认'] };
  if (match[3] === '单' && metric !== 'locked_orders') return { quantity: null, warnings: ['原数量以订单为单位，请确认指标口径'] };
  const quantity = Number(match[1].replaceAll(',', '')) * (match[2] ? 10000 : 1);
  if (!Number.isSafeInteger(quantity) || (metric !== 'adjustments' && quantity < 0)) return { quantity: null, warnings: ['数量必须为安全范围内整数；只有库存调整允许负数'] };
  return { quantity, warnings: match[2] ? ['数量已按明确“万”单位换算为辆，请确认'] : [] };
}
function candidate(context, fields = {}) {
  const record = {
    id: uid(), sourceId: context.sourceId || '', sourceRef: '', model: clean(context.model), variant: '',
    location: clean(context.location), period: '', granularity: context.granularity || 'week', metric: context.metric || '',
    quantity: null, batch: '', scope: context.scope === 'total' ? 'total' : 'detail', status: 'pending', warnings: [], originalText: '', ...fields,
  };
  record.warnings = unique([
    ...record.warnings, !record.model && '车型缺失，请补全', !record.period && '周期缺失，请补全',
    !Object.hasOwn(LABELS, record.metric) && '业务口径未明确，请选择',
    record.quantity == null && '数量尚未明确',
  ]);
  return record;
}

export async function readWorkbook(file) {
  const XLSX = globalThis.XLSX;
  if (!XLSX?.read || !XLSX?.utils) throw new Error('Excel 解析器未加载，请确认本地 vendor/xlsx.full.min.js 文件完整。');
  if (!file?.arrayBuffer) throw new Error('请选择有效的 Excel 或 CSV 文件。');
  if (file.size > 25 * 1024 * 1024) throw new Error('文件超过 25 MB，请先按工作表或车型拆分后导入。');
  let workbook;
  try { workbook = XLSX.read(await file.arrayBuffer(), { type: 'array', cellDates: false, cellNF: true, cellFormula: true, dense: false }); }
  catch { throw new Error('无法读取此工作簿。请检查文件是否损坏或加密，另存为 .xlsx 后重试。'); }
  return workbook.SheetNames.map(name => {
    const sheet = workbook.Sheets[name], range = sheet['!ref'] ? XLSX.utils.decode_range(sheet['!ref']) : null;
    if (!range) return { name, rows: [], merges: [] };
    if ((range.e.r + 1) * (range.e.c + 1) > 500000 || range.e.r > 50000 || range.e.c > 1000) throw new Error(`工作表“${name}”范围过大，请删除多余空白行列或拆分后导入。`);
    const rows = [], formulaCells = [];
    for (let r = 0; r <= range.e.r; r++) {
      const row = [];
      for (let c = 0; c <= range.e.c; c++) {
        const address = XLSX.utils.encode_cell({ r, c }), cell = sheet[address];
        let value = cell?.v ?? null;
        if (cell?.f) formulaCells.push(address);
        if (cell?.t === 'e') value = cell.w || '#ERROR';
        if (typeof value === 'number' && cell.z && XLSX.SSF?.is_date?.(cell.z)) {
          const parts = XLSX.SSF.parse_date_code(value, { date1904: !!workbook.Workbook?.WBProps?.date1904 });
          if (parts) value = `${String(parts.y).padStart(4, '0')}-${String(parts.m).padStart(2, '0')}-${String(parts.d).padStart(2, '0')}`;
        }
        row.push(value);
      }
      rows.push(row);
    }
    const merges = (sheet['!merges'] || []).map(merge => ({ s: { ...merge.s }, e: { ...merge.e } }));
    Object.defineProperty(rows, 'merges', { value: merges, enumerable: false });
    Object.defineProperty(rows, 'formulaCells', { value: formulaCells, enumerable: false });
    return { name, rows, merges, formulaCells };
  });
}

export function suggestMapping(rows) {
  if (!Array.isArray(rows) || !rows.length) return { headerRow: 0, layout: 'long', columns: {}, warnings: ['工作表为空'] };
  let best = null;
  for (let r = 0; r < Math.min(rows.length, 40); r++) {
    const row = Array.isArray(rows[r]) ? rows[r] : [], columns = {}, periodColumns = [], metricColumns = [];
    row.forEach((value, c) => {
      const header = compact(value);
      for (const [field, matcher] of Object.entries(fieldMatchers)) if (matcher.test(header) && columns[field] == null) columns[field] = c;
      if (looksLikePeriod(value)) periodColumns.push(c);
      const metric = metricFrom(value); if (metric && !Object.values(columns).includes(c)) metricColumns.push({ c, metric });
    });
    const score = Object.keys(columns).length * 4 + periodColumns.length * 3 + metricColumns.length * 2 + (columns.model != null ? 4 : 0) + (columns.quantity != null ? 3 : 0);
    if (!best || score > best.score) best = { headerRow: r, columns, periodColumns, metricColumns, score };
  }
  const { headerRow, columns, periodColumns, metricColumns, score } = best;
  const warnings = [], layout = periodColumns.length ? 'wide' : 'long';
  let suggestedMetric = '';
  if (layout === 'long' && columns.quantity == null && metricColumns.length) { columns.quantity = metricColumns[0].c; suggestedMetric = metricColumns[0].metric; }
  if (!suggestedMetric) {
    const title = rows.slice(0, headerRow + 1).flat().map(clean).filter(Boolean).join(' ');
    const metrics = unique(metricRules.filter(([, pattern]) => pattern.test(title)).map(([metric]) => metric));
    if (metrics.length === 1) suggestedMetric = metricFrom(title);
  }
  if (score < 8) warnings.push('未可靠识别标题行，请手动选择标题行与列映射');
  if (columns.model == null) warnings.push('未识别车型列，请手动指定或填写默认车型');
  if (layout === 'long' && columns.quantity == null) warnings.push('未识别数量列，请手动指定');
  if (layout === 'long' && metricColumns.length > 1) warnings.push('检测到多个指标数量列；当前只选择第一列，请分次选择其他口径导入');
  if (layout === 'wide') warnings.push('横表将逐个日期列生成候选；请核对年、粒度与默认业务口径');
  return { headerRow, layout, columns, periodColumns: layout === 'wide' ? periodColumns : [], suggestedMetric, warnings };
}

function isSubtotal(row, columns) {
  const keys = ['model', 'variant', 'location', 'batch'].map(key => columns[key]).filter(Number.isInteger);
  const values = (keys.length ? keys.map(i => row[i]) : row.slice(0, 3)).map(clean);
  return values.some(value => /^(?:(?:.*[\s:：])?)(?:总计|合计|小计|汇总|subtotal|grand\s*total|total)$/i.test(value) || /(?:小计|合计|总计)$/.test(value));
}
export function extractRows(rows, mapping = {}, context = {}) {
  const results = [], columns = mapping.columns || {}, headerRow = Number.isInteger(mapping.headerRow) ? mapping.headerRow : 0;
  if (!Array.isArray(rows)) return results;
  const headers = rows[headerRow] || [], merges = mapping.merges || context.merges || rows.merges || [];
  const formulaCells = new Set(mapping.formulaCells || context.formulaCells || rows.formulaCells || []);
  const selectedPeriods = unique((mapping.periodColumns || []).map(Number).filter(index => Number.isInteger(index) && index >= 0));
  for (let r = headerRow + 1; r < rows.length; r++) {
    const row = rows[r] || [];
    if (!row.some(value => !blank(value)) || isSubtotal(row, columns)) continue;
    const rowWarnings = [], refs = [];
    function field(key) {
      const c = columns[key];
      if (!Number.isInteger(c) || c < 0) return '';
      const value = row[c]; refs.push(`${columnLabel(c)}${r + 1}`);
      if (!blank(value) || !['model', 'variant', 'location'].includes(key)) return clean(value);
      const merge = merges.find(item => item?.s?.c === c && item?.e?.c === c && item.s.r > headerRow && item.s.r < r && item.e.r >= r);
      if (merge && !blank(rows[merge.s.r]?.[c])) {
        rowWarnings.push(`${key === 'model' ? '车型' : key === 'variant' ? '配置' : '地点'}来自明确合并单元格 ${columnLabel(c)}${merge.s.r + 1}:${columnLabel(c)}${merge.e.r + 1}，请确认`);
        refs.push(`${columnLabel(c)}${merge.s.r + 1}`);
        return clean(rows[merge.s.r][c]);
      }
      if (key === 'model') rowWarnings.push('空白车型未自动沿用上一行；请核对合并范围或手动填写');
      return '';
    }
    const model = field('model') || clean(context.model), variant = field('variant'), location = field('location') || clean(context.location), batch = field('batch');
    const metricValue = field('metric');
    const metric = metricValue ? metricFrom(metricValue) : context.metric || mapping.suggestedMetric || '';
    const periodValue = field('period') || context.period || '';
    if (metricValue && !metric) rowWarnings.push(`原指标“${metricValue}”未可靠匹配，请手动选择`);
    const numberColumns = mapping.layout === 'wide' ? selectedPeriods : [columns.quantity];
    if (!numberColumns.length) numberColumns.push(undefined);
    const originalText = row.map((value, i) => `${columnLabel(i)}${r + 1}${blank(headers[i]) ? '' : ` [${clean(headers[i])}]`}: ${clean(value) || '（空白）'}`).join(' | ');
    for (const c of numberColumns) {
      if (results.length >= 20000) throw new Error('本次映射将产生超过 20,000 条候选，请按月份或车型拆分工作表后导入；本次未返回部分结果。');
      const warnings = [...rowWarnings];
      const period = periodFrom(mapping.layout === 'wide' ? headers[c] : periodValue, context);
      const quantity = parseQuantity(Number.isInteger(c) ? row[c] : null, metric);
      const quantityAddress = Number.isInteger(c) ? `${columnLabel(c)}${r + 1}` : '';
      if (formulaCells.has(quantityAddress)) warnings.push('数量来自工作簿保存的公式缓存；未重新计算，请核对原表');
      results.push(candidate(context, {
        sourceRef: `${context.sheetName || '工作表'}!${unique([...refs, quantityAddress, mapping.layout === 'wide' && Number.isInteger(c) ? `${columnLabel(c)}${headerRow + 1}` : '']).join(', ') || `第${r + 1}行`}`,
        model, variant, location, batch, metric, period: period.period, granularity: period.granularity,
        quantity: quantity.quantity, warnings: [...warnings, ...period.warnings, ...quantity.warnings], originalText,
      }));
    }
  }
  return results;
}

function namedModels(text) {
  const explicit = [...text.matchAll(/车型\s*[:：]\s*([^\s，,；;。]+)/g)].map(match => match[1]);
  const codes = [...text.matchAll(/(?:问界|智界|享界|尊界|尚界|极氪|理想|小米)?\s*[A-Z]{1,3}\s?\d{1,4}[A-Z]?(?![A-Za-z0-9])(?:\s+\d{4}\s*款)?/g)]
    .filter(match => !/(?:批次(?:号|编号)?|VIN|车架号|车辆识别码|订单号|单号)\s*[:：]?\s*$/i.test(text.slice(0, match.index)))
    .map(match => match[0].trim().replace(/\s+/g, ' ')).filter(model => !/^W\d{1,2}$/i.test(model));
  return unique([...explicit, ...codes]);
}
function allMetricHits(text) {
  const hits = [];
  for (const [metric, pattern] of metricRules) for (const match of text.matchAll(new RegExp(pattern.source, pattern.flags + 'g'))) hits.push({ metric, index: match.index, text: match[0] });
  hits.sort((a, b) => a.index - b.index || b.text.length - a.text.length);
  return hits.filter((hit, index, all) => !all.slice(0, index).some(previous => hit.index >= previous.index && hit.index < previous.index + previous.text.length));
}
export function extractText(text, context = {}) {
  const records = [];
  clean(text).split(/\r?\n/).forEach((line, lineIndex) => {
    if (!line.trim()) return;
    const lineModels = namedModels(line), linePeriod = periodFrom(line, context);
    const dateTokens = line.match(/\d{4}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}(?:日|号)?)?|\d{1,2}月\d{1,2}日|本周|下周|上周|本月|下月|上月|今天|明天|昨天/g) || [];
    const linePeriodSafe = unique(dateTokens).length <= 1;
    const segments = line.split(/[；;。]|(?<!\d)[，,]|[，,](?!\d{3}(?:\D|$))/).map(clean).filter(Boolean);
    for (let segmentIndex = 0; segmentIndex < segments.length; segmentIndex++) {
      const segment = segments[segmentIndex], localModels = namedModels(segment);
      const model = localModels.length === 1 ? localModels[0] : localModels.length ? '' : lineModels.length === 1 ? lineModels[0] : clean(context.model);
      const localPeriod = periodFrom(segment, context);
      const resolvedPeriod = localPeriod.period || localPeriod.warnings.length ? localPeriod : linePeriodSafe && linePeriod.period ? linePeriod : periodFrom(context.period || '', context);
      const hits = allMetricHits(segment);
      const baseWarnings = [...resolvedPeriod.warnings];
      if (lineModels.length > 1 && !localModels.length) baseWarnings.push('原行包含多个车型，此片段车型未自动继承');
      if (localModels.length > 1) baseWarnings.push('同一片段包含多个车型，数量归属需手动确认');
      if (!linePeriodSafe && !localPeriod.period) baseWarnings.push('原行包含多个日期，此片段周期未自动继承');
      if (hits.length === 0) {
        records.push(candidate(context, {
          sourceRef: `第 ${lineIndex + 1} 行 · 片段 ${segmentIndex + 1}`, model,
          period: resolvedPeriod.period, granularity: resolvedPeriod.granularity, metric: '',
          warnings: [...baseWarnings, '未识别出明确的数量与业务口径，原文已保留，请人工补全'], originalText: line,
        }));
        continue;
      }
      hits.forEach((hit, hitIndex) => {
        const end = hits[hitIndex + 1]?.index ?? segment.length;
        const fragment = segment.slice(hit.index, end), prefix = segment.slice(hitIndex ? hits[hitIndex - 1].index + hits[hitIndex - 1].text.length : 0, hit.index);
        let metric = hit.metric;
        const warnings = [...baseWarnings];
        const prefixPeriod = periodFrom(prefix, context);
        const hitPeriod = prefixPeriod.period ? prefixPeriod : linePeriodSafe ? resolvedPeriod : { period: '', granularity: context.granularity || 'week', warnings: ['同一行包含多个日期且当前数值周期不明确，请手动归属'] };
        warnings.push(...hitPeriod.warnings);
        const planned = /预计|计划|预测|预估|拟|承诺|目标|待|将/.test(prefix) || /^(?:入库|出库|销量|销售量)\s*(?:预计|计划|预测|目标)/.test(fragment);
        if (['inbound_actual', 'outbound_actual', 'sales_actual'].includes(metric) && planned) {
          metric = metric === 'sales_actual' ? 'sales_plan' : metric === 'inbound_actual' && /预计|计划|预测|预估/.test(`${prefix}${fragment}`) ? 'inbound_forecast' : '';
          warnings.push('计划/预计入出库与实际发生分开记录；请人工确认口径');
        }
        if (['inbound_actual', 'outbound_actual', 'sales_actual'].includes(metric) && !/实际|实绩|已|完成/.test(hit.text)) warnings.push('原文未明确“实际”，请确认口径后再使用');
        const after = fragment.slice(hit.text.length).replace(/^\s*[:：为是=]?\s*/, '');
        const amount = after.match(/^((?:约|大概|至少|至多)?\s*[+-]?(?:\d{1,3}(?:[,，]\d{3})+|\d+)(?:\.\d+)?\s*(?:[-~～至到—/]\s*\d+(?:\.\d+)?)?\s*(?:万)?\s*(?:辆|台车|台|单)?\s*(?:左右|以上|以下|待确认)?)/);
        const quantity = parseQuantity(amount?.[1] || '', metric);
        const remainder = amount ? after.slice(amount[0].length).trim() : '';
        if (/^(?:多|余|来|到|至|~|～|—|±|\/|%|％|元|件|套|吨|万|千|百)/.test(remainder)) { quantity.quantity = null; warnings.push('数量后存在范围、模糊修饰或非车辆单位，请手动确认'); }
        if (/可能|暂估|估计|待确认|左右|大概|约/.test(fragment)) { quantity.quantity = null; warnings.push('原数量带不确定表述，未当作明确数值'); }
        if (/盘亏/.test(hit.text) && quantity.quantity != null && quantity.quantity > 0) { quantity.quantity = null; warnings.push('盘亏需确认负向调整数，未自动改变符号'); }
        if (localModels.length > 1) quantity.quantity = null;
        const variant = segment.match(/配置\s*[:：]\s*([^\s，,；;。]+)/)?.[1] || '';
        const location = segment.match(/(?:仓库|库位|地点)\s*[:：]\s*([^\s，,；;。]+)/)?.[1] || clean(context.location);
        const batch = segment.match(/(?:批次|批次号|VIN)\s*[:：]\s*([^\s，,；;。]+)/i)?.[1] || '';
        records.push(candidate(context, {
          sourceRef: `第 ${lineIndex + 1} 行 · 片段 ${segmentIndex + 1} · ${hit.text}`,
          model, variant, location, batch, period: hitPeriod.period, granularity: hitPeriod.granularity,
          metric, quantity: quantity.quantity, scope: /全系|全部车型|总计|合计/.test(segment) ? 'total' : context.scope === 'total' ? 'total' : 'detail',
          warnings: [...warnings, ...quantity.warnings], originalText: line,
        }));
      });
    }
  });
  return records;
}
