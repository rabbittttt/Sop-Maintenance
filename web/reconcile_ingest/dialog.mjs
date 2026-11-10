import {
  MAX_POST_BYTES, MAX_RAW_TEXT, MAX_RECORDS, checkFileSize, columnLabel, extractRows, extractText,
  fileBase64, metricFromHeader, readLegacyBackup, readWorkbook, recognizeImage, suggestMapping,
} from './index.mjs?v=2';

const kinds = [['shared', '共享 Excel'], ['excel', '本地 Excel'], ['text', '消息'], ['image', '截图'], ['backup', '旧版备份']];
const scopes = [['total', '合计'], ['detail', '明细']];
const columns = [
  ['model', '车型列'], ['variant', '版本列'], ['period', '期间列'], ['quantity', '数量列'],
  ['metric', '指标列'], ['location', '库区列'], ['batch', '批次列'],
];
const profilesKey = 'sop-reconcile-import-profiles-v1';
const imageExtensions = { 'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp' };
const contextDefaults = { metric: '', scope: 'total' };

function profileKey(row) {
  if (!row?.length || row.length > 150) return '';
  const labels = row.map(value => String(value ?? '').trim());
  return labels.some(Boolean) && labels.join('').length < 2000 ? JSON.stringify(labels) : '';
}

function mappingProfiles() {
  try {
    const entries = JSON.parse(localStorage.getItem(profilesKey) || '[]');
    return new Map(Array.isArray(entries) ? entries : []);
  } catch {
    return new Map();
  }
}

function rememberMapping(sheet, mapping, metric) {
  const key = profileKey(sheet?.rows[mapping?.headerRow]);
  if (!key) return;
  try {
    const profiles = mappingProfiles();
    profiles.delete(key);
    profiles.set(key, { layout: mapping.layout, columns: mapping.columns, periodColumns: mapping.periodColumns, metric });
    localStorage.setItem(profilesKey, JSON.stringify([...profiles.entries()].slice(-20)));
  } catch {
    // Browser preferences do not affect imports stored on the service.
  }
}

function applyProfile(sheet, mapping, metrics) {
  const key = profileKey(sheet?.rows[mapping.headerRow]);
  const profile = key && mappingProfiles().get(key);
  const width = sheet.rows[mapping.headerRow]?.length ?? 0;
  const inBounds = column => Number.isInteger(column) && column >= 0 && column < width;
  if (!profile || !['long', 'wide'].includes(profile.layout) || !profile.columns ||
      !Object.values(profile.columns).every(inBounds) || !Array.isArray(profile.periodColumns) ||
      !profile.periodColumns.every(inBounds)) return mapping;
  const columnMetric = profile.layout === 'long' ? metricFromHeader(sheet.rows[mapping.headerRow][profile.columns.quantity]) : '';
  return {
    ...mapping, layout: profile.layout, columns: profile.columns, periodColumns: profile.periodColumns,
    suggestedMetric: columnMetric || (Object.hasOwn(metrics, profile.metric) ? profile.metric : ''),
  };
}

function verifyImage(file) {
  if (Object.hasOwn(imageExtensions, file.type)) return;
  if (!file.type && /\.(png|jpe?g|webp)$/i.test(file.name)) return;
  throw new Error('请选择 PNG、JPG 或 WebP 截图。');
}

export function openImportDialog({ openDialog, closeDialog, api, serviceFetch, metrics, getRound, owner, ownerReadOnly = false,
  onImported, esc, toast, initialKind = 'excel' }) {
  const sharedPath = '/api/reconcile/shared';
  const draft = {
    kind: kinds.some(([kind]) => kind === initialKind) ? initialKind : 'excel',
    owner: owner || '', context: { ...contextDefaults }, text: '', shared: null,
    shared_id: '', file: null, sheets: [], sheetIndex: 0, mapping: null, mappingOpen: false,
    imports: null, busy: false, saving: false, error: '', progress: '', controller: null,
  };
  openDialog('<h2>添加资料</h2><div data-reconcile-import></div>');
  const dialog = document.querySelector('#edit-dialog');
  const host = dialog.querySelector('[data-reconcile-import]');
  const closeButton = dialog.querySelector('[data-close]');
  const active = () => host.isConnected && dialog.open;
  const query = selector => host.querySelector(selector);
  const readValue = name => query('[name="' + name + '"]')?.value;
  const sheet = () => draft.sheets[draft.sheetIndex];
  const isWorkbook = () => draft.kind === 'excel' || draft.kind === 'shared';
  const option = (value, label, selected) =>
    `<option value="${esc(value)}" ${String(value) === String(selected) ? 'selected' : ''}>${esc(label)}</option>`;
  const select = (name, label, options, selected, emptyLabel) =>
    `<label class="field"><span class="field-label">${label}</span><select name="${name}">${emptyLabel ? option('', emptyLabel, selected) : ''}${options.map(([key, title]) => option(key, title, selected)).join('')}</select></label>`;
  const input = (name, label, value, type = 'text', attributes = '') =>
    `<label class="field"><span class="field-label">${label}</span><input name="${name}" type="${type}" value="${esc(value)}" ${attributes}></label>`;

  function workbookFields() {
    const shared = draft.shared || [];
    const sourceField = draft.kind === 'shared'
      ? select('shared-id', '共享目录', shared.map(file => [file.id, file.name]), draft.shared_id,
        shared.length ? '选择文件' : draft.busy ? '读取共享目录…' : '暂无文件')
      : `<label class="field"><span class="field-label">本地 Excel / CSV</span><input name="file" type="file" accept=".xlsx,.xls,.csv"></label>`;
    const fileName = draft.file ? '<span class="muted full">' + esc(draft.file.name) + '</span>' : '';
    if (!draft.mapping) return sourceField + fileName;
    const selected = sheet(), mapping = draft.mapping;
    const header = selected.rows[mapping.headerRow] || [];
    const choices = header.map((value, index) => [index, columnLabel(index) + (value === null || value === '' ? '' : ' · ' + String(value).slice(0, 80))]);
    const columnSelectors = columns.filter(([key]) => mapping.layout === 'long' || !['period', 'quantity'].includes(key))
      .map(([key, label]) => select('col-' + key, label, choices, mapping.columns[key] ?? '', '未选'));
    const wide = mapping.layout === 'wide'
      ? `<label class="field full"><span class="field-label">期间列</span><select name="period-columns" multiple size="4">${choices.map(([key, label]) => `<option value="${key}" ${mapping.periodColumns.includes(key) ? 'selected' : ''}>${esc(label)}</option>`).join('')}</select></label>` : '';
    return sourceField + fileName + `<details class="full" data-import-mapping ${draft.mappingOpen ? 'open' : ''}>
      <summary>表格映射</summary><div class="form-grid">` +
      select('sheet', '工作表', draft.sheets.flatMap((item, index) => item.rows.length > 1 ? [[index, item.name]] : []), draft.sheetIndex) +
      input('header', '表头行', mapping.headerRow + 1, 'number', 'min="1" max="' + Math.max(1, selected.rows.length - 1) + '"') +
      select('layout', '表格布局', [['long', '单列数量'], ['wide', '按期间分列']], mapping.layout) +
      columnSelectors.join('') + wide +
      select('metric', '默认指标', Object.entries(metrics), draft.context.metric, '待确认') +
      select('scope', '汇总范围', scopes, draft.context.scope) + '</div></details>';
  }

  function textarea(label) {
    return `<label class="field full"><span class="field-label">${label}</span><textarea name="raw-text" rows="5" maxlength="${MAX_RAW_TEXT}">${esc(draft.text)}</textarea></label>`;
  }

  function sourceFields() {
    if (isWorkbook()) return workbookFields();
    if (draft.kind === 'text') return textarea('消息原文');
    if (draft.kind === 'backup') return '<label class="field full"><span class="field-label">旧版 JSON 备份</span><input name="file" type="file" accept=".json"></label>' +
      (draft.file ? '<span class="muted full">' + esc(draft.file.name) + '</span>' : '');
    return `<label class="field"><span class="field-label">截图文件</span><input name="file" type="file" accept="image/png,image/jpeg,image/webp"></label>
      <div class="field"><span class="field-label">${esc(draft.file?.name || '截图识别')}</span>
        <button type="button" class="button secondary" data-import-command="ocr" ${draft.file && !draft.busy ? '' : 'disabled'}>识别文字</button>
      </div>${draft.text ? textarea('识别文字') : ''}`;
  }

  function sourceReady() {
    return draft.kind === 'text' ? Boolean(draft.text.trim()) : Boolean(draft.file);
  }

  function render() {
    if (!active()) return;
    host.innerHTML = `<div role="group" aria-label="资料类型">${kinds.map(([key, label]) => `<button type="button"
      class="button small ${draft.kind === key ? 'primary' : 'secondary'}" data-import-kind="${key}" aria-pressed="${draft.kind === key}" ${draft.busy ? 'disabled' : ''}>${label}</button>`).join(' ')}</div>
      <hr class="divider"><form novalidate>
      <div class="form-grid"><fieldset class="form-grid full" ${draft.busy ? 'disabled' : ''} style="border:0;padding:0;margin:0;min-width:0">
      <legend class="visually-hidden">资料内容</legend>${sourceFields()}
      ${ownerReadOnly ? '' : input('reconcile-import-owner', '操作人', draft.owner, 'text', 'id="reconcile-import-owner" maxlength="100" required')}
      </fieldset></div>
      ${draft.error ? `<p class="field-error" role="alert">${esc(draft.error)}</p>` : ''}
      <p data-import-progress class="muted" role="status" ${draft.progress ? '' : 'hidden'}>${esc(draft.progress)}</p>
      <div class="dialog-actions">
      <button type="button" class="button secondary" data-import-command="close" ${draft.saving ? 'disabled' : ''}>取消</button>
      <button type="submit" class="button primary" ${!sourceReady() || draft.busy ? 'disabled' : ''}>${draft.kind === 'backup' ? '迁移资料' : '导入'}</button>
      </div></form>`;
    if (closeButton) closeButton.disabled = draft.saving;
  }

  function syncInputs() {
    const fields = ['metric', 'scope'];
    fields.forEach(field => { if (readValue(field) !== undefined) draft.context[field] = readValue(field).trim(); });
    draft.owner = readValue('reconcile-import-owner')?.trim() ?? draft.owner;
    draft.text = readValue('raw-text') ?? draft.text;
    if (!draft.mapping) return;
    draft.mappingOpen = query('[data-import-mapping]')?.open ?? draft.mappingOpen;
    for (const [key] of columns) {
      const value = readValue('col-' + key);
      if (value === undefined) continue;
      if (value === '') delete draft.mapping.columns[key];
      else draft.mapping.columns[key] = Number(value);
    }
    const periodColumns = query('[name="period-columns"]');
    if (periodColumns) draft.mapping.periodColumns = [...periodColumns.selectedOptions].map(item => Number(item.value));
  }

  function resetContent() {
    Object.assign(draft, { text: '', shared_id: '', file: null, sheets: [], sheetIndex: 0, mapping: null, mappingOpen: false, imports: null });
  }

  function chooseSheet(index, headerIndex) {
    draft.sheetIndex = index;
    const selected = sheet();
    const mapping = headerIndex === undefined ? suggestMapping(selected.rows)
      : { ...suggestMapping([selected.rows[headerIndex] || []]), headerRow: headerIndex };
    draft.mapping = applyProfile(selected, mapping, metrics);
    draft.context.metric = Object.hasOwn(metrics, draft.mapping.suggestedMetric) ? draft.mapping.suggestedMetric : '';
  }

  function syncQuantityMetric() {
    const {layout, headerRow, columns} = draft.mapping;
    const metric = layout === 'long' ? metricFromHeader(sheet().rows[headerRow][columns.quantity]) : '';
    draft.context.metric = Object.hasOwn(metrics, metric) ? metric : '';
    draft.mapping.suggestedMetric = draft.context.metric;
  }

  async function useFile(file, sharedId = '') {
    checkFileSize(file);
    if (draft.kind === 'backup') {
      draft.imports = await readLegacyBackup(file);
      draft.file = file;
      return;
    }
    if (draft.kind === 'image') verifyImage(file);
    const sheets = isWorkbook() ? await readWorkbook(file) : [];
    draft.file = file;
    draft.shared_id = sharedId;
    draft.sheets = sheets;
    const firstSheet = sheets.findIndex(item => item.rows.length > 1);
    if (firstSheet >= 0) chooseSheet(firstSheet);
    if (draft.kind === 'image') draft.text = '';
  }

  async function run(job, { saving = false, progress = '' } = {}) {
    draft.busy = true;
    draft.saving = saving;
    draft.error = '';
    draft.progress = progress;
    render();
    try {
      await job();
    } catch (error) {
      if (active()) draft.error = error.message || '处理资料失败。';
    } finally {
      draft.busy = false;
      draft.saving = false;
      draft.progress = '';
      if (closeButton) closeButton.disabled = false;
      render();
    }
  }

  function showKind() {
    if (draft.kind !== 'shared' || draft.shared !== null) {
      render();
      return;
    }
    void run(async () => {
      const data = await api(sharedPath);
      draft.shared = Array.isArray(data?.files) ? data.files : [];
    }, { progress: '读取共享目录…' });
  }

  async function recognizeScreenshot() {
    draft.controller = new AbortController();
    const result = await recognizeImage(draft.file, {
      signal: draft.controller.signal,
      onProgress: ({ status, progress }) => {
        const indicator = query('[data-import-progress]');
        if (indicator) indicator.textContent = status + ' ' + Math.round(progress * 100) + '%';
      },
    });
    draft.text = result.text;
  }

  function extractRecords() {
    if (isWorkbook()) {
      const selected = sheet(), mapping = draft.mapping;
      if (!mapping || (mapping.layout === 'wide' ? !mapping.periodColumns.length : !Number.isInteger(mapping.columns.quantity))) return [];
      return extractRows(selected.rows, mapping, { ...draft.context, sheetName: selected.name, merges: selected.merges, formulaCells: selected.formulaCells });
    }
    const records = draft.text.trim() ? extractText(draft.text, draft.context) : [];
    if (draft.kind === 'image') records.forEach(record => record.warnings.push('来源为截图，请核对原图'));
    return records;
  }

  async function saveImport() {
    if (!draft.owner) throw new Error('请填写操作人。');
    const isBackup = draft.kind === 'backup';
    const body = { owner: draft.owner };
    let ocrFailed = false;
    if (isBackup) {
      if (!draft.imports) throw new Error('请选择旧版备份。');
      body.imports = draft.imports;
    } else {
      if (!sourceReady()) return;
      if (draft.kind === 'image' && !draft.text.trim()) {
        try { await recognizeScreenshot(); }
        catch (error) {
          if (!active()) throw error;
          ocrFailed = true;
        }
      }
      const records = extractRecords();
      if (records.length > MAX_RECORDS) throw new Error('单次导入最多 10,000 条记录。');
      body.source = {
        name: draft.kind === 'text' ? '消息原文' : draft.file.name,
        type: isWorkbook() ? 'excel' : draft.kind,
      };
      if (!isWorkbook()) body.source.raw_text = draft.text.trim();
      if (draft.shared_id) body.source.shared_id = draft.shared_id;
      else if (draft.file) body.source.content_base64 = await fileBase64(draft.file);
      body.records = records;
    }
    if (new Blob([JSON.stringify(body)]).size > MAX_POST_BYTES) throw new Error('导入内容超过 26 MB，请分批导入。');
    const round = await getRound(isBackup ? body.imports.flatMap(entry => entry.records) : body.records, draft.owner);
    body.revision = round.revision;
    const roundPath = '/api/reconcile/' + encodeURIComponent(round.id);
    const updated = await api(roundPath + (isBackup ? '/restore' : '/import'), body);
    if (isWorkbook()) rememberMapping(sheet(), draft.mapping, draft.context.metric);
    closeDialog();
    await onImported(updated, draft.owner);
    if (!updated.added_sources && !updated.added_records) toast('没有新增资料或记录');
    else if (isBackup) toast('旧版资料已迁入');
    else if (updated.added_records) toast('资料已加入待确认');
    else toast(ocrFailed ? '截图已保存，未能识别文字' : '资料已保存，未识别到记录');
  }

  host.addEventListener('input', event => {
    if (event.target.name !== 'raw-text') return;
    draft.text = event.target.value;
    query('[type="submit"]').disabled = !sourceReady() || draft.busy;
  });
  host.addEventListener('change', event => {
    if (draft.busy) return;
    const element = event.target;
    syncInputs();
    if (element.name === 'file' && element.files?.[0]) {
      const file = element.files[0];
      resetContent();
      void run(() => useFile(file), { progress: '读取资料…' });
    } else if (element.name === 'shared-id') {
      const item = draft.shared?.find(file => file.id === element.value);
      resetContent();
      if (!item) {
        draft.error = '';
        render();
        return;
      }
      void run(async () => {
        const response = await serviceFetch(sharedPath + '/' + encodeURIComponent(item.id));
        if (!response.ok) throw new Error(response.status === 404 ? '共享文件已更新，请重新打开导入窗口。' : '读取共享文件失败。');
        const blob = await response.blob();
        await useFile(new File([blob], item.name, { type: blob.type }), item.id);
      }, { progress: '读取共享文件…' });
    } else if (element.name === 'sheet') {
      chooseSheet(Number(element.value));
      render();
    } else if (element.name === 'header') {
      const index = Number(element.value) - 1;
      if (Number.isInteger(index) && index >= 0 && index < sheet().rows.length - 1) chooseSheet(draft.sheetIndex, index);
      else draft.error = '表头行需位于数据行之前。';
      render();
    } else if (element.name === 'layout') {
      draft.mapping.layout = element.value;
      syncQuantityMetric();
      render();
    } else if (element.name === 'col-quantity') {
      syncQuantityMetric();
      render();
      query('[name="col-quantity"]')?.focus();
    } else if (element.name === 'metric' && draft.mapping) {
      draft.mapping.suggestedMetric = draft.context.metric;
    }
  });
  host.addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!button) return;
    if (button.dataset.importCommand === 'close') {
      closeDialog();
      return;
    }
    if (draft.busy) return;
    syncInputs();
    if (button.dataset.importKind && button.dataset.importKind !== draft.kind) {
      resetContent();
      draft.kind = button.dataset.importKind;
      draft.context = { ...contextDefaults };
      draft.error = '';
      showKind();
    } else if (button.dataset.importCommand === 'ocr' && draft.file) {
      void run(recognizeScreenshot, { progress: '识别截图…' });
    }
  });
  host.addEventListener('submit', event => {
    event.preventDefault();
    if (draft.busy || event.submitter?.disabled) return;
    syncInputs();
    void run(saveImport, { saving: true, progress: '保存资料…' });
  });
  host.addEventListener('paste', event => {
    if (draft.kind !== 'image' || draft.busy) return;
    const clipboardImage = Array.from(event.clipboardData?.files || []).find(file => Object.hasOwn(imageExtensions, file.type));
    if (!clipboardImage) return;
    event.preventDefault();
    syncInputs();
    resetContent();
    const name = '截图.' + imageExtensions[clipboardImage.type];
    void run(() => useFile(new File([clipboardImage], name, { type: clipboardImage.type })), { progress: '读取截图…' });
  });
  function preventCancel(event) {
    if (draft.saving) event.preventDefault();
  }
  dialog.addEventListener('cancel', preventCancel);
  dialog.addEventListener('close', () => {
    dialog.removeEventListener('cancel', preventCancel);
    draft.controller?.abort();
  }, { once: true });
  showKind();
}
