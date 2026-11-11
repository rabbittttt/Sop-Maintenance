"""A successful Excel import materializes the configured maintenance workbook."""
import io
import os
import unittest
from unittest.mock import patch

import openpyxl

from sop.paths import PLAN_WORKBOOK_NAME, plan_workbook_path
from sop.roundtrip import PlanRoundtrip
from sop.store import ValidationError
from sop.workbook import WorkbookSync
from sop.xlsx_plan import write_plan_workbook
from tests import test_model_export_roundtrip as support
from tests.test_workbook_sync import edit_cell


class ImportMaintenanceFileTest(unittest.TestCase):
    def setUp(self):
        self.f = support.ModelExportRoundtripTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.project = self.f.root / "SOP维护"
        self.project.mkdir()
        self.shared = self.f.root / "共享数据"
        self.shared.mkdir()
        environment = patch.dict(os.environ, {"SHARED_DATA_ROOT": "../共享数据", "SOP_PLAN_DIR": ""})
        environment.start()
        self.addCleanup(environment.stop)
        self.path = plan_workbook_path(self.project)
        self.sync = WorkbookSync(self.f.store, self.path, writer=write_plan_workbook, auto_create=False)
        self.f.service = PlanRoundtrip(self.f.store, self.sync)
        self.assertEqual(self.path, (self.shared / "SOP计划" / PLAN_WORKBOOK_NAME).resolve())
        self.assertEqual(list(self.path.parent.iterdir()), [])

    def plain(self, *rows):
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = "SOP计划"
        sheet.append(["品牌", "车型", "2026-10", "2027-03"])
        for row in rows:
            sheet.append(row)
        content = io.BytesIO()
        book.save(content)
        book.close()
        return content.getvalue()

    def assert_no_maintenance(self):
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.path.parent.iterdir()), [])
        self.assertFalse((self.project / "input_file").exists())

    def assert_maintenance(self, expected):
        self.assertTrue(self.path.is_file())
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])
        self.assertFalse((self.project / "input_file").exists())
        token, context, _, _, values = self.sync._parse(self.path.read_bytes())
        self.assertTrue(token)
        self.assertEqual(context["schema_version"], 5)
        mappings = {(item["product_id"], item["month"]): item for item in context["layout"]["cells"]}
        book = openpyxl.load_workbook(self.path, read_only=True, data_only=False)
        try:
            self.assertIn("_sync", book.sheetnames)
            self.assertIn("_meta", book.sheetnames)
            self.assertNotEqual(book["_meta"].sheet_state, "visible")
            for (pid, month), expected_value in expected.items():
                self.assertEqual(values[pid + "\t" + month], expected_value)
                mapping = mappings[pid, month]
                actual = book[mapping["sheet"]][mapping["cell"]].value
                self.assertEqual(actual, expected_value)
                self.assertIs(type(actual), int)
                self.assertEqual(self.f.value(pid, month), expected_value)
        finally:
            book.close()

    def test_start_web_save_and_download_leave_the_shared_plan_directory_empty(self):
        self.assertEqual(self.sync.sync()["status"], "absent")
        revision = self.f.store.db.execute(
            "SELECT revision FROM plans WHERE product_id='c' AND month='2026-10'").fetchone()[0]
        result = self.sync.save_web_plans({"owner": "网页", "changes": [{
            "product_id": "c", "month": "2026-10", "sop": 6, "forecast": None, "revision": revision}]})
        self.assertEqual(result["workbook_sync"]["status"], "absent")
        self.f.export()
        self.assert_no_maintenance()

    def test_plain_import_creates_the_standard_file_and_later_import_updates_it(self):
        parent = self.f.model_id("Together 2026款")
        first = self.plain(["智界", "Together 2026款", 321, 0])
        result = self.f.service.import_content(first)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["workbook_sync"]["status"], "synced")
        self.assert_maintenance({(parent, "2026-10"): 321, (parent, "2027-03"): 0})
        before = self.path.read_bytes()
        second = self.plain(["智界", "Together 2026款", 322, 0])
        conflict = self.f.service.import_content(second)
        self.assertEqual(conflict["status"], "conflict")
        self.assertEqual(self.path.read_bytes(), before)
        updated = self.f.service.import_content(second, choice="workbook", confirmation_token=conflict['confirmation_token'])
        self.assertTrue(updated["ok"], updated)
        self.assert_maintenance({(parent, "2026-10"): 322, (parent, "2027-03"): 0})
        self.assertEqual((self.f.value("a"), self.f.value("b")), (10, 20))

    def test_tracked_import_creates_then_updates_the_same_maintenance_path(self):
        parent = self.f.model_id("Together 2026款")
        for amount in (123, 456):
            with self.subTest(amount=amount):
                self.f.export()
                if amount == 123:
                    self.assert_no_maintenance()
                edit_cell(self.f.path, self.f.address("Together 2026款"), amount)
                result = self.f.import_file()
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["workbook_sync"]["status"], "synced")
                self.assert_maintenance({(parent, "2026-10"): amount})

    def assert_noop_reimport_restores_missing_file(self, content):
        parent = self.f.model_id("Together 2026款")
        previous_service = PlanRoundtrip(self.f.store)
        first = previous_service.import_content(content)
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["imported"], 1)
        self.assert_no_maintenance()
        revision = self.f.store.db.execute(
            "SELECT revision FROM plans WHERE product_id=? AND month='2026-10'", (parent,)).fetchone()[0]
        repeated = self.f.service.import_content(content)
        self.assertTrue(repeated["ok"], repeated)
        self.assertEqual((repeated["imported"], repeated["created"]), (0, 0))
        self.assert_maintenance({(parent, "2026-10"): 321})
        self.assertEqual(self.f.store.db.execute(
            "SELECT revision FROM plans WHERE product_id=? AND month='2026-10'", (parent,)).fetchone()[0], revision)

    def test_reimporting_an_already_accepted_plain_file_creates_without_new_changes(self):
        self.assert_noop_reimport_restores_missing_file(self.plain(["智界", "Together 2026款", 321]))

    def test_reimporting_an_already_accepted_tracked_file_creates_without_new_changes(self):
        self.f.export()
        edit_cell(self.f.path, self.f.address("Together 2026款"), 321)
        self.assert_noop_reimport_restores_missing_file(self.f.path.read_bytes())

    def test_invalid_plain_input_and_unresolved_conflict_do_not_create_a_file(self):
        parent = self.f.model_id("Together 2026款")
        invalid = self.plain(["智界", "Together 2026款", 321], ["享界", "Future 2027款", "=1+1"])
        with self.assertRaises(ValidationError):
            self.f.service.import_content(invalid)
        self.assertIsNone(self.f.value(parent))
        self.assert_no_maintenance()
        self.f.save(parent, 80)
        result = self.f.service.import_content(self.plain(["智界", "Together 2026款", 99]))
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(self.f.value(parent), 80)
        self.assert_no_maintenance()

    def test_unresolved_tracked_conflict_does_not_create_a_file(self):
        parent = self.f.model_id("Together 2026款")
        self.f.export()
        edit_cell(self.f.path, self.f.address("Together 2026款"), 99)
        self.f.save(parent, 85)
        result = self.f.import_file()
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(self.f.value(parent), 85)
        self.assert_no_maintenance()

    def test_failed_writeback_keeps_committed_success_and_downloads_available(self):
        parent = self.f.model_id("Together 2026款")
        with patch.object(self.sync, "writer", side_effect=PermissionError("destination locked")):
            result = self.f.service.import_content(self.plain(["智界", "Together 2026款", 321]))
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["imported"], 1)
            self.assertEqual(result["workbook_sync"]["status"], "pending")
            self.assertIn("已导入", result["warning"])
            self.assertEqual(self.f.value(parent), 321)
            self.assert_no_maintenance()
            self.f.export()
            self.assertTrue(self.f.path.is_file())
            self.assert_no_maintenance()
        self.assertEqual(self.sync.sync(force_export=True)["status"], "synced")
        self.assert_maintenance({(parent, "2026-10"): 321})


if __name__ == "__main__":
    unittest.main()
