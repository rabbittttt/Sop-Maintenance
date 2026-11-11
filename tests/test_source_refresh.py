"""Five-minute source scan with one-minute stability and last-good retention."""
import json
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from sop.paths import sales_source_files
from sop.refresh import SalesRefresh
from sop.store import SOPStore, TZ, ValidationError


class SourceRefreshTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "input_file").mkdir()
        self.source = self.root / "input_file/鸿蒙智行锁单选配比例分析.xlsx"
        self.source.write_bytes(b"source-1")
        self.sales = self.root / "sales.json"
        self.sales.write_text(json.dumps({"meta": {}, "products": [], "sales": []}), encoding="utf-8")
        self.store = SOPStore(self.root / "test.sqlite3", self.sales, lambda: datetime(2026, 10, 24, 12, tzinfo=TZ))
        self.calls = []
        self.time = 0
        self.watcher = SalesRefresh(self.store, root=self.root, reader=self.read, clock=lambda: self.time)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def read(self, output_path):
        self.calls.append("read")
        data = {"meta": {"batch": len(self.calls)}, "products": [], "sales": []}
        output_path.write_text(json.dumps(data), encoding="utf-8")
        return data

    def tick(self, at, *, force=False):
        self.time = at
        return self.watcher.poll(force=force)

    def test_maintenance_workbook_does_not_trigger_sales_refresh(self):
        before = self.watcher.fingerprint()
        plan = self.root / "input_file/SOP计划维护.xlsx"
        plan.write_bytes(b"initial plan")
        self.assertEqual(self.watcher.fingerprint(), before)
        plan.write_bytes(b"revised plan")
        self.assertEqual(self.watcher.fingerprint(), before)
        self.assertEqual(sales_source_files(self.root / "input_file"), [self.source])
        (self.root / "input_file/unrelated.xlsx").write_bytes(b"other report")
        self.assertEqual(self.watcher.fingerprint(), before)
        self.source.write_bytes(b"new sales")
        self.assertNotEqual(self.watcher.fingerprint(), before)

    def test_five_minute_scan_and_one_minute_stability(self):
        self.assertEqual(self.tick(0)["status"], "waiting")
        self.assertEqual(self.calls, [])
        self.tick(59)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.tick(60)["status"], "synced")
        self.assertEqual(self.calls, ["read"])
        self.tick(359)
        self.assertEqual(self.calls, ["read"])
        self.source.write_bytes(b"changed source")
        self.tick(360)
        self.assertEqual(self.calls, ["read"])
        self.tick(419)
        self.assertEqual(self.calls, ["read"])
        self.assertEqual(self.tick(420)["status"], "synced")
        self.assertEqual(self.calls, ["read", "read"])
        self.assertEqual(self.watcher.status()["status"], "synced")

    def test_change_during_stability_restarts_the_minute(self):
        self.tick(0)
        self.source.write_bytes(b"intermediate")
        self.assertEqual(self.tick(60)["status"], "waiting")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.tick(119, force=True)["status"], "waiting")
        self.assertEqual(self.tick(120)["status"], "synced")
        self.assertEqual(self.calls, ["read"])

    def test_manual_check_respects_stability_and_does_not_claim_early_success(self):
        self.assertEqual(self.tick(0, force=True)["status"], "waiting")
        self.assertEqual(self.tick(59, force=True)["status"], "waiting")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.tick(60, force=True)["status"], "synced")
        self.assertEqual(self.tick(61, force=True)["status"], "unchanged")

    def test_reverted_change_cannot_reuse_an_old_stability_window(self):
        observed = ["original"]
        with patch.object(self.watcher, "fingerprint", side_effect=lambda: tuple(observed)):
            self.tick(0)
            self.tick(60)
            observed[0] = "temporary"
            self.assertEqual(self.tick(360)["status"], "waiting")
            observed[0] = "original"
            self.assertEqual(self.tick(420)["status"], "unchanged")
            observed[0] = "temporary"
            self.assertEqual(self.tick(720)["status"], "waiting")
            self.assertEqual(self.tick(779, force=True)["status"], "waiting")
            self.assertEqual(self.tick(780)["status"], "synced")
        self.assertEqual(self.calls, ["read", "read"])

    def test_invalid_refresh_retains_last_data_and_retries(self):
        previous = self.sales.read_bytes()
        self.tick(0)
        with patch.object(self.store, "validate_source_data", side_effect=ValidationError("conflict")):
            self.tick(60)
        self.assertEqual(self.sales.read_bytes(), previous)
        self.assertEqual(self.watcher.status()["status"], "error")
        self.assertEqual(list(self.root.glob(".sales-*.pending.json")), [])
        self.tick(120)
        self.assertEqual(self.watcher.status()["status"], "synced")

    def test_file_changed_during_read_is_not_published(self):
        previous = self.sales.read_bytes()
        def changing_reader(output_path):
            result = self.read(output_path)
            self.source.write_bytes(b"in progress data")
            return result
        self.watcher.reader = changing_reader
        with self.assertRaises(ValidationError):
            self.watcher.refresh()
        self.assertEqual(self.sales.read_bytes(), previous)

    def test_workbook_import_precedes_source_publish_and_conflicts_block_it(self):
        calls = self.calls
        class Sheet:
            lock = threading.RLock()
            _input_checked = True
            value = "synced"
            def sync(self):
                calls.append("sheet")
                return {"status": self.value, "message": "conflict"}
        sheet = Sheet()
        self.watcher.workbook_sync = sheet
        self.watcher.refresh()
        self.assertEqual(calls, ["sheet", "read", "sheet"])
        previous = self.sales.read_bytes()
        sheet.value = "conflict"
        with self.assertRaises(ValidationError):
            self.watcher.refresh()
        self.assertEqual(calls[-1], "sheet")
        self.assertEqual(self.sales.read_bytes(), previous)

    def test_failed_publish_restores_original_file(self):
        previous = self.sales.read_bytes()
        with patch.object(self.store, "record_sales", side_effect=RuntimeError("failure")):
            with self.assertRaises(RuntimeError):
                self.watcher.refresh()
        self.assertEqual(self.sales.read_bytes(), previous)
        self.assertEqual(list(self.root.glob(".sales-*.pending.json")), [])


if __name__ == "__main__":
    unittest.main()
