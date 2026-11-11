"""Real temporary SQLite stores and Excel files; no live business data."""
import unittest
import base64
import http.client
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

from server import make_handler
from sop.roundtrip import PlanRoundtrip
from sop.store import SOPStore, ValidationError
from sop.workbook import WorkbookSync
from tests import test_roundtrip as fixture


class MultiuserImportTest(unittest.TestCase):
    def setUp(self):
        self.f = fixture.RoundtripTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.f.save('a', '2026-10', 10)
        self.f.export()
        self.f.change('a', '2026-10', 20)
        self.f.change('b', '2026-10', 30)
        self.f.save('a', '2026-10', 15)

    def submit(self, choice=None, token=None, actor='DOMAIN\\alice', content=None):
        return self.f.service.import_content(content or self.f.path.read_bytes(), choice,
            actor=actor, confirmation_token=token)

    def test_third_user_edit_requires_new_confirmation_and_keeps_atomicity(self):
        first = self.submit()
        self.f.save('a', '2026-10', 17)
        second = self.submit('workbook', first['confirmation_token'])
        self.assertFalse(second['ok'])
        self.assertEqual(second['conflicts'][0]['database'], 17)
        self.assertNotEqual(first['confirmation_token'], second['confirmation_token'])
        self.assertEqual(self.f.value(), 17)
        self.assertIsNone(self.f.value('b'))
        final = self.submit('workbook', second['confirmation_token'])
        self.assertEqual(final['imported'], 2)
        self.assertEqual((self.f.value(), self.f.value('b')), (20, 30))
        self.assertEqual(self.f.store.db.execute("SELECT owner FROM plans WHERE product_id='a'").fetchone()[0], 'DOMAIN\\alice')
        # A lost HTTP response can be retried even after another person's save.
        self.f.save('a', '2026-10', 19)
        self.f.service = PlanRoundtrip(self.f.store)
        self.assertEqual(self.submit('workbook', second['confirmation_token']), final)
        self.assertEqual(self.f.value(), 19)

    def test_disappearing_conflict_and_aba_do_not_authorize_old_choice(self):
        first = self.submit()
        self.f.save('a', '2026-10', 10)
        with self.assertRaisesRegex(ValidationError, '确认已失效'):
            self.submit('workbook', first['confirmation_token'])
        self.assertEqual(self.f.value(), 10)
        self.assertIsNone(self.f.value('b'))
        self.f.save('a', '2026-10', 15)
        self.assertFalse(self.submit('workbook', first['confirmation_token'])['ok'])

    def test_confirmation_is_bound_to_file_actor_and_individual_preview(self):
        first = self.submit()
        other = self.submit(actor='DOMAIN\\bob')
        self.assertFalse(self.submit('workbook', other['confirmation_token'])['ok'])
        self.f.change('b', '2026-10', 31)
        self.assertFalse(self.submit('workbook', first['confirmation_token'])['ok'])
        self.assertEqual(self.f.value(), 15)
        self.assertIsNone(self.f.value('b'))
        # A new preview in another tab cannot silently renew an older token.
        self.f.change('b', '2026-10', 30)
        self.f.save('a', '2026-10', 16)
        self.submit()
        self.assertFalse(self.submit('workbook', first['confirmation_token'])['ok'])

    def test_database_choice_also_rechecks_then_preserves_latest_values(self):
        first = self.submit()
        self.f.save('a', '2026-10', 18)
        second = self.submit('db', first['confirmation_token'])
        self.assertFalse(second['ok'])
        self.assertIsNone(self.f.value('b'))
        self.assertEqual(self.submit('db', second['confirmation_token'])['imported'], 1)
        self.assertEqual((self.f.value(), self.f.value('b')), (18, 30))

    def test_other_database_connection_write_before_commit_is_detected(self):
        first = self.submit()
        peer = SOPStore(self.f.root / 'test.sqlite3', self.f.root / 'sales.json', self.f.store.clock)
        self.addCleanup(peer.close)
        real_import = self.f.store.import_workbook_plans

        def race(body, **kwargs):
            row = peer.db.execute("SELECT revision FROM plans WHERE product_id='a'").fetchone()
            peer.save_plans({'owner': 'third', 'changes': [{'product_id': 'a', 'month': '2026-10',
                'sop': 99, 'forecast': None, 'revision': row[0]}]})
            return real_import(body, **kwargs)

        with patch.object(self.f.store, 'import_workbook_plans', side_effect=race):
            with self.assertRaisesRegex(ValidationError, '已有变化'):
                self.submit('workbook', first['confirmation_token'])
        self.assertEqual(self.f.value(), 99)
        self.assertIsNone(self.f.value('b'))

    def test_frontend_only_ignores_server_file_and_does_not_create_one(self):
        path = self.f.root / 'not-required' / 'maintenance.xlsx'
        sync = WorkbookSync(self.f.store, path, frontend_only=True)
        self.f.service = PlanRoundtrip(self.f.store, sync)
        self.assertEqual(sync.sync()['status'], 'frontend')
        self.assertFalse(path.exists())
        first = self.submit()
        result = self.submit('workbook', first['confirmation_token'])
        self.assertTrue(result['ok'])
        self.assertFalse(path.exists())
        self.assertFalse(path.parent.exists())
        before = sync.status()['data_revision']
        path.parent.mkdir()
        path.write_bytes(b'An obsolete or invalid server-side workbook')
        self.assertEqual(sync.sync(force_export=True, choice='workbook')['status'], 'frontend')
        self.assertEqual(self.f.value(), 20)
        self.f.save('a', '2026-10', 77)
        self.assertNotEqual(sync.status()['data_revision'], before)
        self.assertEqual(path.read_bytes(), b'An obsolete or invalid server-side workbook')

    def test_real_http_conflict_flow_and_frontend_only_endpoints(self):
        path = self.f.root / 'maintenance.xlsx'
        sync = WorkbookSync(self.f.store, path, frontend_only=True)
        sync.sync()
        service = PlanRoundtrip(self.f.store, sync)
        server = ThreadingHTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler)
        server.RequestHandlerClass = make_handler(self.f.store, self.f.root, server.server_port,
            workbook_sync=sync, plan_roundtrip=service,
            source_refresh=SimpleNamespace(status=lambda: {}), console_user='DOMAIN\\alice')
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.02), daemon=True)
        thread.start()

        def request(path, body=None):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
            try:
                connection.request('GET' if body is None else 'POST', path,
                    None if body is None else json.dumps(body), {'Content-Type': 'application/json'})
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        upload = {'filename': 'plan.xlsx', 'content_base64': base64.b64encode(self.f.path.read_bytes()).decode()}
        try:
            code, first = request('/api/plans/import-xlsx', upload)
            self.assertEqual(code, 409)
            self.f.save('a', '2026-10', 18)
            code, second = request('/api/plans/import-xlsx', {**upload, 'choice': 'workbook',
                'confirmation_token': first['confirmation_token']})
            self.assertEqual(code, 409)
            self.assertEqual(second['conflicts'][0]['database'], 18)
            code, result = request('/api/plans/import-xlsx', {**upload, 'choice': 'workbook',
                'confirmation_token': second['confirmation_token']})
            self.assertEqual(code, 200, result)
            self.assertEqual(result['imported'], 2)
            self.assertFalse(path.exists())
            code, state = request('/api/state')
            self.assertEqual(code, 200)
            before = state['workbook_sync']['data_revision']
            self.f.save('a', '2026-10', 55)
            code, status = request('/api/plan-workbook/status')
            self.assertEqual(code, 200)
            self.assertNotEqual(before, status['data_revision'])
            for endpoint in ('sync', 'open', 'export'):
                self.assertEqual(request('/api/plan-workbook/' + endpoint, {})[0], 400)
            self.assertEqual(request('/api/plan-workbook/download')[0], 400)
            self.assertFalse(path.exists())
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()


if __name__ == '__main__':
    unittest.main()
