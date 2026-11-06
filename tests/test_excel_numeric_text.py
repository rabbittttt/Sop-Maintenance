"""Numbers stored as Excel text still roundtrip, while invalid inputs identify cells."""
import io
import json
import math
import unittest
from datetime import datetime

import openpyxl

from tests import test_download_contract as downloads
from tests.test_workbook_sync import edit_cell
from sop.store import ValidationError
from sop.table import _quantity


class ExcelNumericTextTest(unittest.TestCase):
    def setUp(self):
        self.download = downloads.DownloadContractTest(
            "test_all_content_options_download_registered_editable_originals_that_roundtrip")
        self.addCleanup(self.download.doCleanups)
        self.download.setUp()
        self.fixture = self.download.fixture
        self.store = self.fixture.store
        self.parent_id = self.download.parent["id"]
        self.path = self.fixture.root / "saved-original.xlsx"

    def exported(self, versions, locks):
        status, _, content = self.download.request(
            "/api/plans/export-xlsx", self.download.payload(versions, locks))
        self.assertEqual(status, 200, content[:500])
        return content

    def save_cell(self, original, value, pid=None):
        pid = pid or self.parent_id
        book = openpyxl.load_workbook(io.BytesIO(original), data_only=False)
        try:
            metadata = book["_sop_import"]
            manifest = json.loads("".join(row[1] for row in metadata.iter_rows(min_row=6, values_only=True)))
            if metadata.cell(3, 2).value:
                row = next(row for row in manifest["rows"] if row.get("product_id") == pid
                           and row["kind"] == "product" and row["metric"] == "SOP计划量")
            else:
                key = next(key for key, item in manifest["model_inputs"].items() if item["product_id"] == pid)
                row = next(row for row in manifest["rows"] if row["key"] == key)
            column = next(col["column"] for col in manifest["columns"] if col.get("month") == "2026-10")
            cell = book["SOP计划"].cell(row["row"], column)
            cell.value = value
            address = cell.coordinate
            book.save(self.path)
        finally:
            book.close()
        return address

    def upload(self):
        status, _, content = self.download.upload(self.path)
        return status, json.loads(content)

    def test_text_model_quantities_save_and_upload_in_all_export_combinations(self):
        version_plans = self.download.source_plans()
        for versions, locks in ((False, False), (False, True), (True, False), (True, True)):
            with self.subTest(versions=versions, locks=locks):
                self.fixture.save(self.parent_id, None)
                content = self.exported(versions, locks)
                address = self.save_cell(content, "123")
                saved = openpyxl.load_workbook(self.path, read_only=True, data_only=False)
                try:
                    self.assertEqual((saved["SOP计划"][address].value, saved["SOP计划"][address].data_type), ("123", "s"))
                    self.assertIn("_sop_import", saved.sheetnames)
                finally:
                    saved.close()
                status, result = self.upload()
                self.assertEqual(status, 200, result)
                self.assertTrue(result["ok"])
                self.assertEqual((result["imported"], result["created"]), (1, 0))
                self.assertEqual(self.fixture.value(self.parent_id), 123)
                self.assertEqual(self.download.source_plans(), version_plans)
                self.assertEqual((self.fixture.value("a"), self.fixture.value("b")), (10, 20))

    def test_numeric_excel_storage_noise_imports_as_integer_in_all_export_combinations(self):
        version_plans = self.download.source_plans()
        raw = 2999.9999999999995
        for versions, locks in ((False, False), (False, True), (True, False), (True, True)):
            with self.subTest(versions=versions, locks=locks):
                self.fixture.save(self.parent_id, None)
                content = self.exported(versions, locks)
                address = self.save_cell(content, 3000)
                # Preserve the user's exact raw XML value: saving this Python
                # float through openpyxl would round it back to 3000 first.
                edit_cell(self.path, address, raw)
                saved = openpyxl.load_workbook(self.path, read_only=True, data_only=False)
                try:
                    cell = saved["SOP计划"][address]
                    self.assertEqual((cell.value, cell.data_type, cell.number_format), (raw, "n", "0"))
                finally:
                    saved.close()
                status, result = self.upload()
                self.assertEqual(status, 200, result)
                self.assertTrue(result["ok"])
                self.assertEqual((result["imported"], result["created"]), (1, 0))
                self.assertEqual(self.fixture.value(self.parent_id), 3000)
                self.assertIs(type(self.fixture.value(self.parent_id)), int)
                self.assertEqual(self.download.source_plans(), version_plans)
                self.assertEqual((self.fixture.value("a"), self.fixture.value("b")), (10, 20))

    def test_version_plan_text_zero_with_spaces_is_a_real_zero(self):
        content = self.exported(versions=True, locks=True)
        self.save_cell(content, " 0 ", pid="a")
        status, result = self.upload()
        self.assertEqual(status, 200, result)
        self.assertTrue(result["ok"])
        self.assertEqual(result["imported"], 1)
        self.assertEqual((self.fixture.value("a"), self.fixture.value("b")), (0, 20))
        self.assertIsNone(self.fixture.value(self.parent_id))

    def test_invalid_inputs_still_fail_with_exact_sheet_cell_and_preserve_plans(self):
        invalid = ("=1+1", datetime(2026, 11, 1), -0.5, "not a number", "1e3", "-1", "12.5")
        before = [tuple(row) for row in self.store.db.execute("SELECT * FROM plans ORDER BY product_id,month")]
        for versions in (False, True):
            content = self.exported(versions=versions, locks=True)
            for value in invalid:
                with self.subTest(versions=versions, value=value):
                    address = self.save_cell(content, value)
                    status, result = self.upload()
                    self.assertEqual(status, 400, result)
                    self.assertIn("SOP计划!" + address, result["error"])
                    self.assertEqual([tuple(row) for row in self.store.db.execute("SELECT * FROM plans ORDER BY product_id,month")], before)


class ExcelQuantityPrecisionTest(unittest.TestCase):
    def test_only_adjacent_machine_precision_noise_becomes_an_integer(self):
        for integer in (1, 3000, 4096):
            for direction in (-math.inf, math.inf):
                value = math.nextafter(float(integer), direction)
                with self.subTest(integer=integer, value=value):
                    self.assertEqual(_quantity(value), integer)
                    self.assertIs(type(_quantity(value)), int)

    def test_blanks_zero_and_integer_limits_keep_their_existing_meaning(self):
        for value, expected in ((None, None), ("", None), (0, 0), (0.0, 0),
                                (-0.0, 0), ("000", 0), ("3000", 3000), (10**12, 10**12)):
            with self.subTest(value=value):
                self.assertEqual(_quantity(value), expected)

    def test_real_fractions_and_invalid_values_are_not_rounded(self):
        invalid = [2999.99999, 3000.0000001, 3000.01, 1.5, 0.1,
                   math.nextafter(0.0, math.inf), 1e-100, -1e-100,
                   math.nextafter(10**12, -math.inf), math.nextafter(10**12, math.inf),
                   math.inf, -math.inf, math.nan, True, "3000.0", "=3000", "1e3"]
        for integer in (1, 3000, 4096):
            for direction in (-math.inf, math.inf):
                invalid.append(math.nextafter(math.nextafter(float(integer), direction), direction))
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    _quantity(value)


if __name__ == "__main__":
    unittest.main()
