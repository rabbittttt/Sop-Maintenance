import * as extraction from './extract.mjs';

export const MAX_FILE_BYTES = 15 * 1024 * 1024;
export const MAX_POST_BYTES = 26 * 1024 * 1024;
export const MAX_RECORDS = 10000;
export const MAX_RAW_TEXT = 200000;

export const { columnLabel, metricLabel, suggestMapping } = extraction;
let workbookParser;

function loadWorkbookParser() {
  if (globalThis.XLSX?.read && globalThis.XLSX?.utils) return Promise.resolve();
  workbookParser ??= new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = new URL('./vendor/xlsx.full.min.js', import.meta.url).href;
    script.addEventListener('load', () => {
      if (globalThis.XLSX?.read && globalThis.XLSX?.utils) resolve();
      else reject(new Error('Excel 解析器未就绪。'));
    }, { once: true });
    script.addEventListener('error', () => {
      script.remove();
      reject(new Error('Excel 解析器加载失败。'));
    }, { once: true });
    document.head.append(script);
  }).catch(error => {
    workbookParser = null;
    throw error;
  });
  return workbookParser;
}

export function checkFileSize(file) {
  if (!file?.size) throw new Error('请选择非空文件。');
  if (file.size > MAX_FILE_BYTES) throw new Error('单文件不能超过 15 MB。');
}

export async function readWorkbook(file) {
  checkFileSize(file);
  if (!/\.(xlsx|xls|csv)$/i.test(file.name || '')) throw new Error('请选择 .xlsx、.xls 或 .csv 文件。');
  await loadWorkbookParser();
  return extraction.readWorkbook(file);
}

function clip(value, max) {
  return value.length <= max ? value : Array.from(value).slice(0, max - 1).join('') + '…';
}

function mapRecord(record) {
  const warnings = [...record.warnings];
  const boundedField = (value, limit, label) => {
    if (value.length <= limit) return value;
    warnings.push(label + '过长，请核对原资料。');
    return '';
  };
  let quantity = record.quantity;
  if (quantity !== null && (!Number.isSafeInteger(quantity) || Math.abs(quantity) > 10 ** 12)) {
    quantity = null;
    warnings.push('数量超出范围，请核对原资料。');
  }
  if (record.originalText.length > 10000) warnings.push('记录原文较长，完整内容可查看来源。');
  return {
    source_ref: clip(record.sourceRef, 500),
    original_text: clip(record.originalText, 10000),
    target_id: '',
    model: boundedField(record.model, 200, '车型'),
    variant: boundedField(record.variant, 200, '版本'),
    period: boundedField(record.period, 40, '期间'),
    granularity: record.granularity,
    metric: boundedField(record.metric, 60, '指标'),
    quantity,
    location: boundedField(record.location, 200, '库区'),
    batch: boundedField(record.batch, 200, '批次'),
    scope: record.scope,
    status: 'pending',
    warnings: warnings.slice(0, 30).map(warning => clip(warning, 500)),
  };
}

export function extractRows(rows, mapping, context) {
  return extraction.extractRows(rows, mapping, context).map(mapRecord);
}

export function extractText(text, context) {
  if (text.length > MAX_RAW_TEXT) throw new Error('原文不能超过 20 万字符。');
  return extraction.extractText(text, context).map(mapRecord);
}

export async function recognizeImage(file, options) {
  const { recognizeImage: recognize } = await import('./ocr.mjs');
  return recognize(file, options);
}

export async function fileBase64(file) {
  checkFileSize(file);
  const bytes = new Uint8Array(await file.arrayBuffer());
  const chunks = [];
  for (let offset = 0; offset < bytes.length; offset += 32768) {
    chunks.push(String.fromCharCode(...bytes.subarray(offset, offset + 32768)));
  }
  return btoa(chunks.join(''));
}

const text = value => typeof value === 'string' ? value : '';
const allowedTypes = new Set(['excel', 'text', 'image']);
const grains = new Set(['day', 'week', 'month']);

function migratedRecord(record, index) {
  const warnings = Array.isArray(record.warnings) ? record.warnings.filter(item => typeof item === 'string') : [];
  if (record.status === 'confirmed') warnings.push('旧版已确认，迁移后需复核车型关联');
  if (record.status === 'excluded') warnings.push('旧版已排除');
  return mapRecord({
    ...record,
    sourceRef: text(record.sourceRef) || '旧版备份记录 ' + (index + 1),
    originalText: text(record.originalText),
    model: text(record.model),
    variant: text(record.variant),
    period: text(record.period),
    granularity: grains.has(record.granularity) ? record.granularity : 'month',
    metric: text(record.metric),
    quantity: Number.isSafeInteger(record.quantity) ? record.quantity : null,
    location: text(record.location),
    batch: text(record.batch),
    scope: record.scope === 'total' ? 'total' : 'detail',
    warnings,
  });
}

export async function readLegacyBackup(file) {
  checkFileSize(file);
  let backup;
  try { backup = JSON.parse(await file.text()); }
  catch { throw new Error('无法读取旧版 JSON 备份。'); }
  if (backup?.schemaVersion !== 1 || !Array.isArray(backup.sources) || !Array.isArray(backup.records)) {
    throw new Error('旧版备份格式不匹配。');
  }
  if (backup.demo || backup.sources.some(source => source?.synthetic)) throw new Error('旧版演示数据不支持迁移。');
  if (backup.sources.length > 100 || backup.records.length > MAX_RECORDS) throw new Error('备份最多可包含 100 份资料和 10,000 条记录。');
  const imports = new Map();
  for (const source of backup.sources) {
    if (!source || !text(source.id) || imports.has(source.id) || !allowedTypes.has(source.type)) {
      throw new Error('备份包含无效或重复的资料。');
    }
    const imported = {
      name: text(source.name).trim() || '旧版资料',
      type: source.type,
      as_of: text(source.asOf),
      raw_text: text(source.rawText),
    };
    if (source.type === 'image' && source.imageData) {
      const image = /^data:image\/(png|jpeg|webp);base64,([A-Za-z0-9+/=\r\n]+)$/i.exec(text(source.imageData));
      if (!image) throw new Error('旧版备份包含不支持的截图格式。');
      const extension = image[1].toLowerCase();
      if (!/\.(png|jpe?g|webp)$/i.test(imported.name)) imported.name += '.' + extension;
      imported.content_base64 = image[2].replace(/\s/g, '');
    }
    imports.set(source.id, { source: imported, records: [] });
  }
  const seenRecords = new Set();
  for (const [index, record] of backup.records.entries()) {
    const entry = imports.get(record?.sourceId);
    if (!entry || !text(record.id) || seenRecords.has(record.id)) throw new Error('备份包含无效或重复的记录。');
    seenRecords.add(record.id);
    entry.records.push(migratedRecord(record, index));
  }
  if (!imports.size) throw new Error('旧版备份没有资料。');
  return [...imports.values()];
}
