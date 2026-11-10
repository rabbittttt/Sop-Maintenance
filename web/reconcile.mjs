const ROOT = '/api/reconcile';
const PAGE_SIZE = 100;
const statusNames = {pending:'待确认', confirmed:'已确认', excluded:'已排除'};
const sourceNames = {excel:'Excel', text:'消息', image:'截图', shared:'共享 Excel'};
const grainNames = {month:'月', week:'周', day:'日'};
const targetLevels = {summary:'汇总车型', model:'单代际', generation:'单代际', version:'版本'};
const stockFields = ['opening_stock', 'inbound_actual', 'outbound_actual', 'adjustments', 'closing_stock'];
const preferredMetrics = ['sop_plan', 'locked_orders', 'supply_plan', 'inbound_actual', 'sales_actual', 'sales_plan', 'transit', 'inbound_forecast', 'opening_stock', 'outbound_actual', 'adjustments', 'closing_stock'];
const importEntries = [['shared', '共享 Excel'], ['excel', '本地 Excel'], ['text', '粘贴消息'], ['image', '导入截图']];
const resultViews = {
  all: {label:'全部结果'},
  locks: {label:'计划与锁单', metrics:['sop_plan', 'locked_orders']},
  supply: {label:'计划与供应', metrics:['sop_plan', 'supply_plan']},
  stock: {label:'库存平衡', metrics:stockFields},
  issues: {label:'异常核对'},
};
const path = id => ROOT + '/' + encodeURIComponent(id);

export function createReconcilePage({
  host, api, serviceFetch, openDialog, closeDialog, toast, esc, fmt, dateText,
  isActive, getOwner, canWrite, rememberOwner, isOwnerLocked = () => false,
}) {
  let rounds = [], snapshots = [], currentMonth = '', metrics = {}, detail = null, selectedRound = '';
  let view = 'results', status = 'pending', resultFilter = 'all', sourceFilter = '', resultTarget = '';
  let request = 0, pageNumber = 0, selectedRecords = new Set();

  const option = (value, label, chosen) =>
    '<option value="' + esc(value) + '"' + (String(value) === String(chosen) ? ' selected' : '') + '>' + esc(label) + '</option>';
  const action = (key, label, {id='', primary=false, write=false}={}) =>
    '<button type="button" class="button small' + (primary ? ' primary' : '') +
    '" data-rec="' + esc(key) + '"' + (id ? ' data-id="' + esc(id) + '"' : '') +
    (write && !canWrite() ? ' disabled' : '') + '>' + esc(label) + '</button>';
  const statusBadge = value => '<span class="badge ' +
    ({pending:'warn', confirmed:'good', excluded:'neutral'}[value] || 'neutral') + '">' +
    esc(statusNames[value] || value) + '</span>';
  const cell = value => '<td>' + esc(value ?? '—') + '</td>';
  const numericCell = value => '<td class="rec-number">' + esc(fmt(value)) + '</td>';
  const date = value => value ? dateText(value) : '—';
  const roundPath = () => path(detail.id);
  const clearSelection = () => { pageNumber = 0; selectedRecords = new Set(); };
  function showSelection() {
    const count = host.querySelector('[data-rec-selected-count]');
    if (count) count.textContent = '已选 ' + selectedRecords.size;
  }

  async function download(url, fallbackName, control) {
    control.disabled = true;
    try {
      const response = await serviceFetch(url);
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.error || '下载失败，请重试');
      }
      const filename = response.headers.get('Content-Disposition')?.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
      const objectUrl = URL.createObjectURL(await response.blob());
      const anchor = document.createElement('a');
      anchor.href = objectUrl;
      anchor.download = filename ? decodeURIComponent(filename) : fallbackName;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(objectUrl), 10000);
    } finally {
      control.disabled = false;
    }
  }

  function table(headers, rows, emptyLabel) {
    const columns = headers.map(label => '<th scope="col">' + esc(label) + '</th>').join('');
    const body = rows.length ? rows.join('') : '<tr><td class="empty-state compact" colspan="' +
      headers.length + '">' + esc(emptyLabel) + '</td></tr>';
    return '<div class="table-scroll" tabindex="0"><table class="rec-table"><thead><tr>' +
      columns + '</tr></thead><tbody>' + body + '</tbody></table></div>';
  }

  function visiblePage(records) {
    const lastPage = Math.max(0, Math.ceil(records.length / PAGE_SIZE) - 1);
    pageNumber = Math.min(pageNumber, lastPage);
    return records.slice(pageNumber * PAGE_SIZE, (pageNumber + 1) * PAGE_SIZE);
  }

  function pager(count) {
    const pages = Math.max(1, Math.ceil(count / PAGE_SIZE));
    if (pages <= 1) return '';
    return '<div class="table-footer rec-pager"><span>' + (pageNumber + 1) + ' / ' + pages + '</span><div>' +
      (pageNumber > 0 ? action('previous', '上一页') : '') +
      (pageNumber + 1 < pages ? action('next', '下一页') : '') + '</div></div>';
  }

  function sourcesTable() {
    const counts = new Map();
    for (const record of detail.records) {
      const count = counts.get(record.source_id) || {total:0, pending:0};
      count.total++;
      if (record.status === 'pending') count.pending++;
      counts.set(record.source_id, count);
    }
    const byId = new Map(detail.sources.map(source => [source.id, source]));
    const rows = visiblePage(detail.sources).map(source => {
      const count = counts.get(source.id) || {total:0, pending:0};
      const previous = byId.get(source.previous_id);
      const name = '<td><strong>' + esc(source.name) + '</strong>' +
        (previous ? '<small>上版 ' + esc(previous.name) + ' · ' + esc(date(previous.created_at)) + '</small>' : '') + '</td>';
      return '<tr>' + name + cell(sourceNames[source.type] || source.type) +
        cell(source.as_of || '—') + cell(date(source.created_at)) + cell(source.owner || '—') +
        numericCell(count.total) + numericCell(count.pending) +
        '<td>' + action('source', '查看', {id:source.id}) + '</td></tr>';
    });
    return table(['资料', '类型', '资料日期', '导入时间', '操作人', '记录数', '待确认', '操作'], rows, '暂无资料') +
      pager(detail.sources.length);
  }

  function filteredRecords() {
    return detail.records.filter(record =>
      (!status || record.status === status) && (!sourceFilter || record.source_id === sourceFilter));
  }

  function recordsTable() {
    const byId = new Map(detail.sources.map(source => [source.id, source]));
    const byTarget = new Map(detail.targets.map(target => [target.id, target.label]));
    const records = filteredRecords();
    const rows = visiblePage(records).map(record => {
      const source = byId.get(record.source_id);
      const name = byTarget.get(record.target_id) || [record.model, record.variant].filter(Boolean).join(' / ');
      const check = '<td><input type="checkbox" data-rec-select="' + esc(record.id) +
        '" aria-label="' + esc('选择记录 ' + (name || record.source_ref || '')) + '"' +
        (selectedRecords.has(record.id) ? ' checked' : '') + (!canWrite() ? ' disabled' : '') + '></td>';
      const errors = record.confirmation_errors || [];
      return '<tr>' + check + cell(name || '未关联') + cell(detail.metrics[record.metric] || '—') +
        cell(record.period || '—') + cell(record.location || '—') + numericCell(record.quantity) +
        '<td>' + statusBadge(record.status) + (errors.length ? '<small class="rec-review-error" title="' +
          esc(errors.join('；')) + '">' + esc(errors[0]) + (errors.length > 1 ? ' 等 ' + errors.length + ' 项' : '') + '</small>' : '') + '</td>' +
        '<td>' + (source ? action('source', source.name, {id:source.id}) : '—') +
        '<small>' + esc(record.source_ref || '') + '</small></td>' +
        '<td>' + action('record', canWrite() ? '核对' : '查看', {id:record.id}) + '</td></tr>';
    });
    const filters = '<div class="rec-row rec-filter"><label class="field inline-field">状态<select data-rec-change="status">' +
      option('', '全部', status) + Object.entries(statusNames).map(([id, name]) => option(id, name, status)).join('') +
      '</select></label><label class="field inline-field">资料<select data-rec-change="source">' +
      option('', '全部', sourceFilter) + detail.sources.map(source => option(source.id, source.name, sourceFilter)).join('') +
      '</select></label><span class="rec-count">' + records.length + ' 条</span><div class="rec-actions">' +
      '<span class="rec-count" data-rec-selected-count aria-live="polite">已选 ' + selectedRecords.size + '</span>' +
      action('select-page', '选择本页', {write:true}) +
      action('confirm-selected', '确认所选', {write:true}) +
      action('exclude-selected', '排除所选', {write:true}) + '</div></div>';
    return filters + table(['选择', '车型', '指标', '期间', '库区', '数量', '状态', '资料 / 位置', '操作'],
      rows, '没有符合条件的记录') + pager(records.length);
  }

  function groupsTable() {
    const scope = resultViews[resultFilter].metrics;
    const hasMetric = (group, key) => group.record_ids?.[key]?.length ||
      group.values?.[key] != null || group.baseline_values?.[key] != null;
    const groups = (detail?.result?.groups || []).filter(group =>
      (!resultTarget || group.target_id === resultTarget) && (!scope || scope.some(key => hasMetric(group, key))));
    const byTarget = new Map((detail?.targets || []).map(target => [target.id, target.label]));
    const labels = detail?.metrics || metrics;
    const metricKeys = scope || [...new Set([...preferredMetrics, ...Object.keys(labels)])].filter(key => key in labels &&
      (['sop_plan', 'locked_orders', 'supply_plan'].includes(key) || groups.some(group => hasMetric(group, key))));
    const showGap = resultFilter === 'all' || resultFilter === 'supply';
    const showStock = resultFilter === 'stock' || (resultFilter === 'all' &&
      groups.some(group => stockFields.some(key => hasMetric(group, key))));
    function gapNote(group) {
      if (resultFilter !== 'supply' || group.supply_gap != null || !hasMetric(group, 'supply_plan')) return '';
      const fields = ['sop_plan', 'supply_plan'];
      if (fields.some(key => group.conflicts?.includes(key))) return '数值待核对';
      if (!hasMetric(group, 'sop_plan')) return '缺少同口径 SOP';
      if (fields.some(key => group.partial_baseline_metrics?.includes(key))) return '基准部分覆盖';
      return '合计范围不一致';
    }
    const rows = visiblePage(groups).map(group => '<tr>' +
      cell(byTarget.get(group.target_id) || group.model) +
      cell(group.period + (group.granularity === 'month' ? '' : ' · ' + (grainNames[group.granularity] || ''))) +
      cell(group.location || '—') +
      metricKeys.map(key => {
        const value = group.values?.[key], baseline = group.baseline_values?.[key];
        const partial = group.partial_baseline_metrics?.includes(key);
        const note = partial ? '基准部分覆盖' : value == null && baseline != null ? '基准 ' + fmt(baseline) : '';
        return '<td class="rec-number">' + esc(fmt(value)) + (note ? '<small>' + esc(note) + '</small>' : '') + '</td>';
      }).join('') +
      (showGap ? '<td class="rec-number' + (group.supply_gap > 0 ? ' rec-shortfall' : '') + '">' +
      esc(fmt(group.supply_gap)) + (gapNote(group) ? '<small>' + esc(gapNote(group)) + '</small>' : '') + '</td>' : '') +
      (showStock ? numericCell(group.balance?.expected_closing) + numericCell(group.balance?.difference) : '') + '</tr>');
    return table(['车型', '期间', '库区', ...metricKeys.map(key => labels[key]), ...(showGap ? ['供应缺口'] : []),
      ...(showStock ? ['计算期末库存', '库存差额'] : [])], rows, detail ? '暂无可对账数据' : '暂无对账批次') + pager(groups.length);
  }

  function issuesTable() {
    const byKey = new Map((detail?.result?.groups || []).map(group => [group.key, group]));
    const records = new Map([...(detail?.records || []), ...(detail?.baseline_records || [])].map(record => [record.id, record]));
    const issues = (detail?.result?.issues || []).filter(issue => !resultTarget ||
      byKey.get(issue.group_key)?.target_id === resultTarget ||
      (issue.record_ids || []).some(id => records.get(id)?.target_id === resultTarget));
    const rows = visiblePage(issues).map(issue => {
      const group = byKey.get(issue.group_key);
      const hasRecords = (issue.record_ids || []).some(id => records.has(id));
      return '<tr>' + cell(issue.title) + cell(group ? group.model + ' · ' + group.period : '—') +
        '<td class="rec-wrap">' + esc(issue.detail) + '</td><td>' +
        (hasRecords ? action('issue', '查看关联记录', {id:issue.id}) : '') + '</td></tr>';
    });
    return table(['异常', '车型 / 期间', '差异', '操作'], rows, detail ? '暂无异常' : '暂无对账批次') + pager(issues.length);
  }

  function resultsTable() {
    const targetIds = new Set((detail?.result?.groups || []).map(group => group.target_id));
    const targets = (detail?.targets || []).filter(target => targetIds.has(target.id));
    const toggles = '<div class="rec-row rec-filter"><div class="segmented rec-segmented" role="group" aria-label="对账项目">' +
      Object.entries(resultViews).map(([key, item]) => '<button type="button" data-rec="result-filter" data-id="' + key +
        '" aria-pressed="' + (resultFilter === key) + '">' +
        item.label + (key === 'issues' && detail ? ' ' + (detail.result?.issues?.length || 0) : '') + '</button>').join('') +
      '</div>' + (detail ? '<label class="field inline-field">车型<select data-rec-change="result-target">' +
      option('', '全部车型及版本', resultTarget) +
      targets.map(target => option(target.id, target.label, resultTarget)).join('') +
      '</select></label>' : '') + '</div>';
    return toggles + (resultFilter === 'issues' ? issuesTable() : groupsTable());
  }

  function render() {
    if (!isActive()) return;
    const picker = '<label class="field inline-field">对账批次<select data-rec-change="round"' +
      (rounds.length ? '' : ' disabled') + '>' + (rounds.length
        ? rounds.map(round => option(round.id, round.name, selectedRound)).join('')
        : option('', '暂无批次', '')) + '</select></label>';
    const primary = '<div class="rec-row rec-toolbar">' + picker +
      (detail ? '<span class="rec-period">' + esc(detail.start_month + ' 至 ' + detail.end_month) + '</span>' : '') +
      '<div class="rec-actions">' + action('new', '新建对账', {write:true}) +
      (detail ? action('baseline', '更新基准', {write:true}) : '') +
      '</div><div class="rec-imports" role="group" aria-label="导入资料">' +
      importEntries.map(([id, label]) => action('import', label, {id, primary:id === 'shared', write:true})).join('') +
      '</div></div>';
    const pending = detail?.records.filter(record => record.status === 'pending').length || 0;
    const activeView = detail ? view : 'results';
    const labels = {sources:'资料', records:'待确认' + (pending ? ' ' + pending : ''), results:'对账结果'};
    const tabs = '<div class="segmented" role="group" aria-label="对账视图">' + Object.entries(labels)
      .map(([key, label]) => '<button type="button" data-rec="view" data-id="' + key +
        '" aria-pressed="' + (activeView === key) + '"' + (!detail && key !== 'results' ? ' disabled' : '') +
        '>' + esc(label) + '</button>').join('') + '</div>';
    const snapshot = snapshots.find(item => String(item.id) === String(detail?.snapshot_id));
    const caption = detail ? '<span class="rec-count">' + esc(snapshot ? '快照 ' + snapshot.label : 'SOP 基准') +
      ' · ' + esc(date(detail.baseline_at)) + '</span>' : '';
    const exports = detail && activeView === 'results' ? '<div class="rec-actions">' + action('export', '导出 Excel') +
      action('summary', '导出摘要') + '</div>' : '';
    const content = activeView === 'sources' ? sourcesTable() : activeView === 'records' ? recordsTable() : resultsTable();
    host.innerHTML = primary + '<section class="card rec-card"><div class="rec-row rec-filter">' + tabs + caption +
      exports + '</div>' + content + '</section>';
  }

  async function load() {
    const ticket = ++request;
    if (isActive()) host.innerHTML = '<div class="loading-state" role="status"><span class="loading-spinner" aria-hidden="true"></span><strong>正在读取对账</strong></div>';
    try {
      const index = await api(ROOT);
      if (ticket !== request || !isActive()) return;
      rounds = index.rounds || [];
      snapshots = index.snapshots || [];
      currentMonth = index.current_month;
      metrics = index.metrics || {};
      selectedRound = rounds.find(round => String(round.id) === String(selectedRound))?.id ?? rounds[0]?.id ?? '';
      const next = selectedRound ? await api(path(selectedRound)) : null;
      if (ticket !== request || !isActive()) return;
      detail = next;
      render();
    } catch (error) {
      if (ticket !== request || !isActive()) return;
      host.innerHTML = '<section class="card empty-state" role="alert"><strong>' + esc(error.message) +
        '</strong> ' + action('retry', '重新读取') + '</section>';
    }
  }

  function ownerInput() {
    return '<label class="field">操作人<input id="rec-owner" name="owner" maxlength="100" required value="' +
      esc(getOwner()) + '"></label>';
  }

  function snapshotPicker(value) {
    return '<label class="field">SOP 基准<select name="snapshot_id">' + option('', '当前计划与锁单', value) +
      snapshots.map(item => option(item.id, (item.label || '历史快照') + ' · ' + date(item.cutoff), value)).join('') +
      '</select></label>';
  }

  function formDialog(title, fields, submitLabel, save, {editable=true, afterSave, onBack, confirmPending=false}={}) {
    openDialog('<h2>' + esc(title) + '</h2><form id="rec-form"><div class="rec-dialog-grid">' + fields +
      '</div><p class="field-error" role="alert"></p><div class="dialog-actions">' +
      '<button type="button" data-close class="button">' + (editable ? '取消' : '关闭') + '</button>' +
      (onBack ? '<button type="button" class="button" data-rec-back>返回关联记录</button>' : '') +
      (confirmPending ? '<button type="submit" class="button secondary">保存修改</button>' : '') +
      (editable ? '<button type="submit" class="button primary"' + (confirmPending ? ' data-confirm' : '') + '>' +
        esc(confirmPending ? '确认记录' : submitLabel) + '</button>' : '') +
      '</div></form>');
    const form = document.getElementById('rec-form');
    form.querySelector('[data-rec-back]')?.addEventListener('click', () => { closeDialog(); onBack(); });
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (!editable) return;
      const submits = [...form.querySelectorAll('button[type="submit"]')];
      if (submits.some(button => button.disabled)) return;
      const errorField = form.querySelector('.field-error');
      const values = Object.fromEntries(new FormData(form));
      if (event.submitter?.hasAttribute('data-confirm')) values.status = 'confirmed';
      submits.forEach(button => { button.disabled = true; });
      errorField.textContent = '';
      try {
        await save(values);
        rememberOwner(values.owner?.trim());
      } catch (error) {
        errorField.textContent = error.message;
        submits.forEach(button => { button.disabled = false; });
        return;
      }
      const dialog = form.closest('dialog');
      const closed = afterSave && dialog?.open
        ? new Promise(resolve => dialog.addEventListener('close', resolve, {once:true})) : null;
      closeDialog();
      try {
        if (closed) await closed;
        await afterSave?.();
      }
      catch (error) { toast(error.message, true); }
    });
  }

  function newRound(importKind) {
    const monthField = (name, label) => '<label class="field">' + label +
      '<input name="' + name + '" type="month" required value="' + esc(currentMonth) + '"></label>';
    formDialog('新建对账', '<label class="field">批次名称<input name="name" maxlength="100" required value="' +
      esc(currentMonth ? currentMonth + ' 对账' : '') + '"></label>' + snapshotPicker('') +
      monthField('start_month', '开始月份') + monthField('end_month', '结束月份') + ownerInput(), importKind ? '创建并继续' : '创建',
    async values => {
      const created = await api(ROOT, values);
      selectedRound = created.id;
      view = 'results';
      sourceFilter = '';
      resultTarget = '';
      clearSelection();
      await load();
      toast('对账批次已创建');
    }, {afterSave: importKind ? () => detail?.id === selectedRound && addSource(importKind) : undefined});
  }

  function changeBaseline() {
    formDialog('更新对账基准', snapshotPicker(detail.snapshot_id || '') + ownerInput(), '更新',
      async values => {
        detail = await api(roundPath() + '/baseline', {...values, revision:detail.revision});
        render();
        toast('对账基准已更新');
      });
  }

  function recordDialog(record, baseline = false, returnToIssue) {
    const editable = !baseline && canWrite();
    const disabled = editable ? '' : ' disabled';
    const textInput = (name, label, type='text') => '<label class="field">' + esc(label) +
      '<input name="' + name + '" type="' + type + '" value="' + esc(record[name]) + '"' + disabled + '></label>';
    const select = (name, label, entries) => '<label class="field"><span class="field-label">' + esc(label) + '</span><select name="' + name +
      '"' + disabled + '>' + entries.map(([id, value]) => option(id, value, record[name] || '')).join('') + '</select></label>';
    const targets = detail.targets.map(target => [target.id, target.label + (targetLevels[target.level] ?
      ' · ' + targetLevels[target.level] : '')]);
    const sourceName = baseline ? 'SOP 基准' : detail.sources.find(source => source.id === record.source_id)?.name;
    const original = '<div class="rec-full"><strong>来源</strong><div>' + esc(sourceName || '—') + ' · ' + esc(record.source_ref) + '</div>' +
      '<pre class="rec-evidence">' + esc(record.original_text || '—') + '</pre></div>';
    const warnings = (record.warnings || []).length ? '<div class="rec-full rec-warnings">' +
      record.warnings.map(warning => '<div>' + esc(warning) + '</div>').join('') + '</div>' : '';
    const errors = (record.confirmation_errors || []).map(error => '<div>' + esc(error) + '</div>').join('');
    const fields = original + (errors ? '<div class="rec-full rec-warnings">' + errors + '</div>' : '') + warnings +
      select('target_id', '车型', [['', '未关联' + (record.model ? ' · ' + record.model : '')], ...targets]) +
      select('metric', '指标', [['', '请选择'], ...Object.entries(detail.metrics)]) +
      textInput('period', '期间') + select('granularity', '粒度', Object.entries(grainNames)) +
      textInput('quantity', '数量', 'number') + textInput('location', '库区') + textInput('batch', '批次') +
      select('scope', '范围', [['total', '合计'], ['detail', '明细']]) +
      select('status', '状态', Object.entries(statusNames)) + (editable ? ownerInput() : '');
    formDialog('核对记录', fields, '保存', async values => {
      const {owner, ...changes} = values;
      changes.quantity = values.quantity === '' ? null : Number(values.quantity);
      await saveChanges([{id:record.id, ...changes}], owner);
    }, {editable, onBack:returnToIssue, afterSave:returnToIssue, confirmPending:editable && record.status === 'pending'});
  }

  async function saveChanges(changes, owner) {
    detail = await api(roundPath() + '/records', {revision:detail.revision, owner, changes});
    selectedRecords.clear();
    if (view === 'records' && status === 'pending' && changes.some(change => ['confirmed', 'excluded'].includes(change.status)) &&
        !detail.records.some(record => record.status === 'pending')) {
      view = 'results';
      resultFilter = 'all';
      pageNumber = 0;
    }
    render();
    toast('记录已更新');
  }

  function bulk(nextStatus) {
    if (!selectedRecords.size) return toast('请选择记录', true);
    const confirming = nextStatus === 'confirmed';
    const selected = detail.records.filter(record => selectedRecords.has(record.id));
    const eligible = confirming ? selected.filter(record => !record.confirmation_errors?.length) : selected;
    const skipped = selected.length - eligible.length;
    if (!eligible.length) return toast('所选记录均有待补齐字段', true);
    const changes = eligible.map(record => ({id:record.id, status:nextStatus}));
    const label = confirming ? '确认' : '排除';
    const note = skipped ? '<p class="rec-full rec-count">另有 ' + skipped + ' 条待补齐，仍保留在待确认</p>' : '';
    formDialog(label + ' ' + changes.length + ' 条记录', note + ownerInput(), label,
      values => saveChanges(changes, values.owner));
  }

  async function sourceDialog(id) {
    const selected = detail.id;
    const sourcePath = roundPath() + '/sources/' + encodeURIComponent(id);
    const source = await api(sourcePath);
    if (!isActive() || detail?.id !== selected) return;
    const raw = source.raw_text ? '<pre class="rec-evidence">' + esc(source.raw_text) + '</pre>' : '';
    openDialog('<h2>' + esc(source.name) + '</h2><div class="rec-count">' +
      esc(sourceNames[source.type] || source.type) + ' · ' + esc(source.as_of || date(source.created_at)) +
      '</div>' + raw + '<div class="dialog-actions">' +
      '<button type="button" class="button" data-rec-source-records>查看记录</button>' +
      (source.has_file ? '<button type="button" class="button" data-rec-source-download>下载原件</button>' : '') + '</div>');
    document.querySelector('[data-rec-source-records]').addEventListener('click', () => {
      sourceFilter = id;
      status = '';
      view = 'records';
      clearSelection();
      closeDialog();
      render();
    });
    document.querySelector('[data-rec-source-download]')?.addEventListener('click', async event => {
      try { await download(sourcePath + '/download', source.name, event.currentTarget); }
      catch (error) { toast(error.message, true); }
    });
  }

  function issueDialog(id) {
    const issue = detail.result.issues.find(item => item.id === id);
    if (!issue) return;
    const ids = new Set(issue.record_ids || []);
    const baselineIds = new Set((detail.baseline_records || []).map(record => record.id));
    const records = [...(detail.baseline_records || []), ...detail.records].filter(record => ids.has(record.id));
    const returnToIssue = () => {
      const remaining = detail.result.issues.find(item => item.type === issue.type && item.group_key === issue.group_key &&
        item.record_ids.length === ids.size && item.record_ids.every(recordId => ids.has(recordId)));
      if (remaining) issueDialog(remaining.id);
    };
    openDialog('<h2>' + esc(issue.title) + '</h2><p class="rec-wrap">' + esc(issue.detail) +
      '</p><ul class="rec-issue-records">' + records.map(record => '<li>' +
        esc([baselineIds.has(record.id) ? 'SOP 基准' : detail.sources.find(source => source.id === record.source_id)?.name,
          record.model, record.period, detail.metrics[record.metric], fmt(record.quantity), record.source_ref]
          .filter(Boolean).join(' · ')) + ' <button type="button" class="button small" data-rec-issue-record="' +
        esc(record.id) + '">查看</button></li>').join('') + '</ul>');
    for (const button of document.querySelectorAll('[data-rec-issue-record]')) {
      button.addEventListener('click', () => {
        const record = records.find(item => item.id === button.dataset.recIssueRecord);
        closeDialog();
        recordDialog(record, baselineIds.has(record.id), returnToIssue);
      });
    }
  }

  async function addSource(initialKind) {
    if (!canWrite()) return;
    if (!detail) return newRound(initialKind);
    const current = detail;
    const {openImportDialog} = await import('./reconcile_ingest/dialog.mjs?v=4');
    if (!isActive() || detail !== current) return;
    await openImportDialog({
      openDialog, closeDialog, api, serviceFetch, detail:current, owner:getOwner(), ownerReadOnly:isOwnerLocked(), esc, toast, initialKind,
      onImported(updated, operator) {
        detail = updated;
        rememberOwner(operator);
        if (!updated.added_records) {
          render();
          return;
        }
        view = 'records';
        status = 'pending';
        sourceFilter = '';
        clearSelection();
        render();
      },
    });
  }

  host.addEventListener('click', async event => {
    const button = event.target.closest('button[data-rec]');
    if (!button || !isActive()) return;
    const {rec, id} = button.dataset;
    try {
      if (rec === 'retry') await load();
      else if (rec === 'new') newRound();
      else if (rec === 'baseline') changeBaseline();
      else if (rec === 'import') await addSource(id);
      else if (rec === 'source') await sourceDialog(id);
      else if (rec === 'record') recordDialog(detail.records.find(record => record.id === id));
      else if (rec === 'issue') issueDialog(id);
      else if (rec === 'export') await download(roundPath() + '/export', 'SOP资料对账.xlsx', button);
      else if (rec === 'summary') await download(roundPath() + '/summary', 'SOP对账摘要.txt', button);
      else if (rec === 'confirm-selected') bulk('confirmed');
      else if (rec === 'exclude-selected') bulk('excluded');
      else if (rec === 'select-page') {
        for (const check of host.querySelectorAll('[data-rec-select]')) {
          check.checked = true;
          selectedRecords.add(check.dataset.recSelect);
        }
        showSelection();
      } else if (rec === 'view' || rec === 'result-filter') {
        if (rec === 'view') {
          if (view !== id && id === 'records') {
            status = 'pending';
            sourceFilter = '';
          }
          view = id;
        }
        else resultFilter = id;
        clearSelection();
        render();
        host.querySelector('[data-rec="' + rec + '"][data-id="' + id + '"]')?.focus();
      } else if (rec === 'next' || rec === 'previous') {
        pageNumber += rec === 'next' ? 1 : -1;
        render();
        (host.querySelector('[data-rec="' + rec + '"]') || host.querySelector('.rec-pager button'))?.focus();
      }
    } catch (error) {
      toast(error.message, true);
    }
  });

  host.addEventListener('change', async event => {
    if (!isActive()) return;
    const {recChange, recSelect} = event.target.dataset;
    if (recSelect) {
      if (event.target.checked) selectedRecords.add(recSelect);
      else selectedRecords.delete(recSelect);
      showSelection();
    } else if (recChange) {
      clearSelection();
      if (recChange === 'round') {
        selectedRound = event.target.value;
        sourceFilter = '';
        resultTarget = '';
        await load();
      } else {
        if (recChange === 'status') status = event.target.value;
        else if (recChange === 'result-target') resultTarget = event.target.value;
        else sourceFilter = event.target.value;
        render();
        host.querySelector('[data-rec-change="' + recChange + '"]')?.focus();
      }
    }
  });

  return {load};
}
