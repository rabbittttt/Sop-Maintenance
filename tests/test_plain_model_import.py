"""Ordinary model-only worksheets import without hidden IDs or version plans."""
import base64
import http.client
import json
import threading
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import openpyxl

from server import make_handler
from sop.plan_scope import is_model_plan
from sop.store import TZ, ValidationError
from tests import test_model_export_roundtrip as support


class PlainModelImportTest(unittest.TestCase):
    def setUp(self):
        self.f = support.ModelExportRoundtripTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.path = self.f.root / 'ordinary.xlsx'

    def table(self, rows):
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = 'SOP计划'
        sheet['A1'] = 'SOP计划表'
        sheet.append([])
        for col, value in enumerate(['品牌', '车型', '2026年', None, None, '2027年'], 1):
            sheet.cell(3, col, value)
        sheet.merge_cells('C3:E3')
        for col, value in enumerate([None, None, '10月', '12月', '年度合计', '3月'], 1):
            sheet.cell(4, col, value)
        for number, row in enumerate(rows, 5):
            for column, value in enumerate(row, 1):
                sheet.cell(number, column, value)
        book.save(self.path)
        book.close()
        return self.path.read_bytes()

    def load(self, choice=None):
        return self.f.service.import_content(self.path.read_bytes(), choice)

    def test_totals_need_neither_version_nor_export_id_and_preserve_details_blanks_and_missing_rows(self):
        parent = self.f.model_id('Together 2026款')
        self.f.save(parent, 70, '2026-12')
        self.table([['智界', 'Together 2026款', 100, None, None, 0], ['智界', 'C 2026款', 50]])
        result = self.load()
        self.assertTrue(result['ok'])
        self.assertEqual(result['source'], 'model_table')
        self.assertEqual(result['created'], 1)
        self.assertEqual(self.f.value(parent), 100)
        self.assertEqual(self.f.value(parent, '2026-12'), 70)
        self.assertEqual(self.f.value(parent, '2027-03'), 0)
        self.assertEqual(self.f.value(self.f.model_id('C 2026款')), 50)
        self.assertEqual([self.f.value(p) for p in ['a', 'b', 'c', 'd']], [10, 20, 5, 40])
        self.assertTrue(all(p['active'] for p in self.f.store.list_products()['items']))
        again = self.load()
        self.assertEqual((again['created'], again['imported']), (0, 0))

    def test_historical_automatic_totals_are_references_and_only_current_plan_is_imported(self):
        parent = self.f.model_id('Together 2026款')
        self.f.save('c', 0)
        self.f.store.clock = lambda: datetime(2026, 12, 1, tzinfo=TZ)
        self.table([['智界', 'Together 2026款', 30, 17], ['智界', 'C 2026款', 0]])
        result = self.load()
        self.assertEqual((result['created'], result['imported']), (0, 1))
        self.assertIsNone(self.f.value(parent))
        self.assertIsNone(self.f.model_id('C 2026款'))
        self.assertEqual(self.f.value(parent, '2026-12'), 17)

        self.table([['智界', 'C 2026款', 1, 99]])
        with self.assertRaisesRegex(ValidationError, r'C5（2026-10）.*不可修改'):
            self.load('workbook')
        self.assertIsNone(self.f.model_id('C 2026款'))
        self.assertEqual(self.f.value('c'), 0)

    def test_future_models_without_sales_or_versions_can_be_added(self):
        self.table([['享界', 'Future 2027款', None, None, None, 123]])
        result = self.load()
        self.assertEqual((result['created'], result['imported']), (1, 1))
        model = next(p for p in self.f.store.list_products()['items'] if p['model'] == 'Future 2027款')
        self.assertTrue(is_model_plan(model))
        self.assertFalse(model['source_backed'])
        self.assertEqual(self.f.value(model['id'], '2027-03'), 123)
        self.assertEqual(self.f.store.state()['sales'], [])

    def test_overwrite_requires_a_preview_and_rechecks_changed_web_values(self):
        pid = self.f.create_model(value=80)
        self.table([['智界', 'C 2026款', 99]])
        first = self.load('workbook')
        self.assertFalse(first['ok'], 'A direct forced choice cannot bypass the first preview.')
        self.assertTrue(first['conflicts'][0]['baseline_unknown'])
        self.assertEqual(self.f.value(pid), 80)
        self.f.save(pid, 85, forecast=777)
        second = self.load('workbook')
        self.assertFalse(second['ok'])
        self.assertEqual(second['conflicts'][0]['database'], 85)
        self.assertEqual(self.f.value(pid), 85)
        self.assertTrue(self.load('workbook')['ok'])
        self.assertEqual(self.f.value(pid), 99)
        row = self.f.store.db.execute('SELECT forecast FROM plans WHERE product_id=? AND month=?', (pid, '2026-10')).fetchone()
        self.assertEqual(row[0], 777)

    def test_keep_web_choice_preserves_conflicts_but_imports_other_filled_models(self):
        pid = self.f.create_model(value=80)
        self.table([['智界', 'C 2026款', 99], ['享界', 'Future 2027款', None, None, None, 12]])
        self.assertFalse(self.load()['ok'])
        self.assertIsNone(self.f.model_id('Future 2027款'))
        result = self.load('db')
        self.assertTrue(result['ok'])
        self.assertEqual(self.f.value(pid), 80)
        self.assertEqual(self.f.value(self.f.model_id('Future 2027款'), '2027-03'), 12)

    def test_plain_table_maps_same_named_summary_before_its_child(self):
        self.f.rules.write_text("MERGE_GROUPS = {'智界 A 2026款汇总': ['智界 A 2026款', '智界 B 2026款']}\n", encoding='utf-8')
        root = next(p for p in self.f.store.read_sales()['products'] if p.get('summary_generation') == '智界 A 2026款汇总' and is_model_plan(p))
        self.table([['智界', 'A 2026款', 555]])
        self.assertTrue(self.load()['ok'])
        self.assertEqual(self.f.value(root['id']), 555)
        self.assertEqual(self.f.value('a'), 10)
        self.assertEqual(self.f.value('b'), 20)

    def test_removed_summary_does_not_capture_a_new_visible_model_plan(self):
        old_id = self.f.model_id('Together 2026款')
        self.f.save(old_id, 70)
        self.f.rules.write_text('MERGE_GROUPS = {}\n', encoding='utf-8')
        self.table([['智界', 'Together 2026款', 321]])
        result = self.load()
        self.assertTrue(result['ok'])
        self.assertEqual(result['created'], 1)
        visible = [p for p in self.f.store.state()['products'] if p['model'] == 'Together 2026款']
        self.assertEqual(len(visible), 1)
        self.assertNotEqual(visible[0]['id'], old_id)
        self.assertEqual(self.f.value(visible[0]['id']), 321)
        self.assertEqual(self.f.value(old_id), 70)

    def test_header_or_numeric_errors_are_atomic_and_corrupt_metadata_never_falls_back(self):
        parent = self.f.model_id('Together 2026款')
        self.table([['智界', 'Together 2026款', 999], ['享界', 'Future 2027款', None, None, None, '=1+1']])
        with self.assertRaises(ValidationError):
            self.load()
        self.assertIsNone(self.f.value(parent))
        self.table([['智界', 'Together 2026款', 999]])
        book = openpyxl.load_workbook(self.path)
        book.create_sheet('_sop_import')
        book.save(self.path); book.close()
        with self.assertRaisesRegex(ValidationError, '标识|格式'):
            self.load()
        self.assertIsNone(self.f.value(parent))

    def test_real_maintenance_template_can_fill_only_the_model_input_or_model_row(self):
        from sop.workbook import WorkbookSync
        from sop.xlsx_plan import write_plan_workbook
        parent = self.f.model_id('Together 2026款')
        sync = WorkbookSync(self.f.store, self.f.root / 'maintenance.xlsx', auto_create=False)
        for kind in ('product', 'model_total'):
            with self.subTest(row_kind=kind):
                self.f.save(parent, None)
                context, _ = sync._context()
                spec = context['layout']['sheets'][0]
                if kind == 'product':
                    row = next(r['row'] for r in spec['rows'] if r['kind'] == kind and r.get('product_id') == parent)
                else:
                    row = next(r['row'] for r in spec['rows'] if r['kind'] == kind and r.get('label_b') == 'Together 2026款 合计')
                column = next(c['column'] for c in spec['columns'] if c.get('month') == '2026-10')
                write_plan_workbook({**context, 'token': 'unmarked-test', 'generated_at': '2026-10-29T12:00:00+08:00', 'output_path': str(self.path)})
                book = openpyxl.load_workbook(self.path)
                self.assertNotIn('_sop_import', book.sheetnames)
                self.assertEqual(book['SOP计划']['C3'].value, '版本')
                book['SOP计划'].cell(row, column, 234)
                book.save(self.path); book.close()
                result = self.load()
                self.assertTrue(result['ok'])
                self.assertEqual(self.f.value(parent), 234)
                self.assertEqual([self.f.value(p) for p in ['a', 'b', 'c', 'd']], [10, 20, 5, 40])

    def test_real_unmarked_legacy_export_with_locks_and_hidden_columns_is_accepted(self):
        self.f.export(locks=True)
        book = openpyxl.load_workbook(self.f.path)
        book.remove(book['_sop_import'])
        book['SOP计划'][self.f.address('Together 2026款')] = 321
        book.save(self.path); book.close()
        self.assertTrue(self.load()['ok'])
        self.assertEqual(self.f.value(self.f.model_id('Together 2026款')), 321)
        self.assertEqual([self.f.value(p) for p in ['a', 'b']], [10, 20])

    def test_http_upload_accepts_a_model_table_without_version_or_hidden_identifiers(self):
        content = self.table([['智界', 'Together 2026款', 123]])
        service = ThreadingHTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler)
        service.RequestHandlerClass = make_handler(self.f.store, self.f.root, service.server_port, plan_roundtrip=self.f.service)
        thread = threading.Thread(target=lambda: service.serve_forever(poll_interval=.02), daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection('127.0.0.1', service.server_port, timeout=10)
            connection.request('POST', '/api/plans/import-xlsx', json.dumps({'filename': '车型计划.xlsx',
                'content_base64': base64.b64encode(content).decode()}), {'Content-Type': 'application/json'})
            response = connection.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(response.status, 200, payload)
            self.assertEqual(payload['source'], 'model_table')
            self.assertEqual(self.f.value(self.f.model_id('Together 2026款')), 123)
            connection.close()
        finally:
            service.shutdown(); thread.join(); service.server_close()


if __name__ == '__main__':
    unittest.main()