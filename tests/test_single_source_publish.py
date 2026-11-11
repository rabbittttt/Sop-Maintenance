"""Lock quantities and option shares become visible from one reviewed source version."""
import http.client
import json
import tempfile
import threading
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import openpyxl

from server import make_handler
from sop import data
from sop.option_mix import SOURCE_NAME
from sop.refresh import SalesRefresh
from sop.store import SOPStore, TZ
from tests import test_option_mix as option_fixture


class SingleSourcePublishTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.input_dir = self.root / 'input_file'
        self.input_dir.mkdir()
        self.source = self.input_dir / SOURCE_NAME
        self.rules = self.root / 'shared_config.py'
        self.rules.write_text('MERGE_GROUPS = {}\n', encoding='utf-8')
        rules_patch = patch.dict('os.environ', {'SOP_SHARED_CONFIG': str(self.rules)})
        rules_patch.start()
        self.addCleanup(rules_patch.stop)
        self.save_source(10, .2)
        self.sales = self.root / 'sales.json'
        data.refresh(self.input_dir, self.sales, as_of='2026-11-11')
        self.store = SOPStore(self.root / 'plan.sqlite3', self.sales,
                              lambda: datetime(2026, 11, 11, 12, tzinfo=TZ))
        self.addCleanup(self.store.close)
        self.now = 0
        self.watcher = SalesRefresh(self.store, root=self.root,
            reader=lambda output_path: data.refresh(self.input_dir, output_path, as_of='2026-11-11'),
            clock=lambda: self.now)

    def save_source(self, quantity, share):
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = '智界 R7 2026款by月'
        sheet.append(['指标', '统计类型', '分类', '26-10'])
        sheet.append(['交车锁单', '数量', '数量', quantity])
        sheet.append(['版本', '数量', 'Max', quantity])
        option_fixture.OptionMixTest.add_sheet(book, '智界 R7 2026款', share)
        book.save(self.source)
        book.close()

    def test_only_lock_workbook_is_required(self):
        published = json.loads(self.sales.read_text(encoding='utf-8'))
        self.assertEqual([entry['name'] for entry in published['meta']['source_files']], [SOURCE_NAME])
        self.assertEqual(published['model_sales'][0]['value'], 10)
        self.assertEqual(published['sales'][0]['value'], 10)
        self.assertIn('option_mix_sheets', published)
        self.assertNotIn('option_mix_sheets', self.store.state())
        self.assertEqual(len(self.watcher.fingerprint()), 2)  # one Excel and optional source settings
        self.assertFalse((self.input_dir / '鸿蒙智行大定选配比例分析.xlsx').exists())
        self.assertFalse((self.input_dir / '鸿蒙智行销量数据汇总.xlsx').exists())

    def test_first_start_without_sales_cache_waits_for_stable_source(self):
        fresh_sales = self.root / 'new-sales.json'
        fresh_store = SOPStore(self.root / 'new-plan.sqlite3', fresh_sales,
                               lambda: datetime(2026, 11, 11, 12, tzinfo=TZ))
        self.addCleanup(fresh_store.close)
        watcher = SalesRefresh(fresh_store, root=self.root,
            reader=lambda output_path: data.refresh(self.input_dir, output_path, as_of='2026-11-11'),
            clock=lambda: self.now)
        self.assertEqual(watcher.poll()['status'], 'waiting')
        self.assertFalse(fresh_sales.exists())
        self.assertEqual(fresh_store.state()['sales'], [])
        self.now = 59
        self.assertIsNone(watcher.poll())
        self.assertFalse(fresh_sales.exists())
        self.now = 60
        self.assertEqual(watcher.poll()['status'], 'synced')
        self.assertTrue(fresh_sales.exists())
        self.assertEqual(fresh_store.state()['model_sales'][0]['value'], 10)

    def test_http_option_and_lock_values_change_together_after_stability(self):
        self.watcher.refresh()
        self.watcher.poll()  # no change; the next routine scan is in five minutes
        server = ThreadingHTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler)
        server.RequestHandlerClass = make_handler(self.store, self.root, server.server_port,
                                                   source_refresh=self.watcher)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.02), daemon=True)
        thread.start()

        def get(path):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
            try:
                connection.request('GET', path)
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        def visible():
            status, option = get('/api/option-mix')
            self.assertEqual(status, 200, option)
            share = next(row['share'] for row in option['rows'] if row['dimension'] == '动力'
                         and row['option'] == '增程')
            status, plan = get('/api/state')
            self.assertEqual(status, 200, plan)
            return share, plan['model_sales'][0]['value']

        try:
            self.assertEqual(visible(), (.2, 10))
            self.save_source(30, .7)
            self.now = 300
            self.assertEqual(self.watcher.poll()['status'], 'waiting')
            self.assertEqual(visible(), (.2, 10))
            self.now = 359
            self.assertIsNone(self.watcher.poll())
            self.assertEqual(visible(), (.2, 10))
            self.now = 360
            self.assertEqual(self.watcher.poll()['status'], 'synced')
            self.assertEqual(visible(), (.7, 30))
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()


if __name__ == '__main__':
    unittest.main()
