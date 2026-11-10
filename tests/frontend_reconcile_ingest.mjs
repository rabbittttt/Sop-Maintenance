import assert from 'node:assert/strict';
import { extractRows, extractText, suggestMapping } from '../web/reconcile_ingest/index.mjs';

const context = {metric:'supply_plan', period:'2026-11', granularity:'month', scope:'total'};
for (const text of ['车型：智界 R7 2027款，2,600辆', '车型：智界 R7 2027款，供应计划2,600辆']) {
  const records = extractText(text, context);
  assert.equal(records.length, 1);
  assert.equal(records[0].model, '智界 R7 2027款');
  assert.equal(records[0].quantity, 2600);
  assert.equal(records[0].metric, 'supply_plan');
}
for (const text of ['车型：智界 R7 2027款，2,600辆和700辆', '车型：智界 R7 2027款 ≥2600辆']) {
  assert.ok(extractText(text, context).every(record => record.quantity == null));
}
const rows = [['车型', '版本', '月份', '供应计划'], ['R7 2027款', '合计', '2026-11', 2600]];
const mapping = suggestMapping(rows);
const totals = extractRows(rows, mapping, context);
assert.equal(totals.length, 1);
assert.equal(totals[0].variant, '');
assert.equal(totals[0].quantity, 2600);
assert.deepEqual(extractRows(rows, mapping, {...context, scope:'detail'}), []);
console.log('PASS: reconciliation text quantities and model-total Excel rows.');
