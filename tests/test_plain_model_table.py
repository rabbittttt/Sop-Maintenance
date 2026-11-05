"""Unmarked model plan tables are parsed without identity guesses or writes."""
import io
import json
import os
import uuid
from datetime import datetime
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import openpyxl

from sop.model_table import parse_model_table
from sop.plan_scope import MODEL_PLAN_LABEL
from sop.store import SOPStore, TZ, ValidationError
from sop.workbook import WorkbookSync
from sop.xlsx_plan import write_plan_workbook
from sop.xlsx_export import write_export_workbook


class PlainModelTableTest(unittest.TestCase):
    def sheet(self, rows):
        book = openpyxl.Workbook()
        book.active.title = "SOP计划"
        for row in rows:
            book.active.append(row)
        self.addCleanup(book.close)
        return book.active

    def parse_both(self, sheet):
        content = io.BytesIO()
        sheet.parent.save(content)
        data = content.getvalue()
        result = None
        for read_only in (False, True):
            with self.subTest(read_only=read_only):
                book = openpyxl.load_workbook(io.BytesIO(data), read_only=read_only, data_only=False)
                try:
                    actual = parse_model_table(book[sheet.title])
                    if result is None:
                        result = actual
                    else:
                        self.assertEqual(actual, result)
                finally:
                    book.close()
        self.assertEqual(content.getvalue(), data)
        return result

    def assert_bad(self, sheet, message):
        content = io.BytesIO(); sheet.parent.save(content)
        for read_only in (False, True):
            with self.subTest(read_only=read_only):
                book = openpyxl.load_workbook(io.BytesIO(content.getvalue()), read_only=read_only, data_only=False)
                try:
                    with self.assertRaisesRegex(ValidationError, message):
                        parse_model_table(book[sheet.title])
                finally:
                    book.close()

    def test_standard_two_row_headers_cross_year_merges_totals_and_zero(self):
        sheet = self.sheet([
            ["SOP计划表"], ["单位：辆"],
            ["品牌", "车型", "2026年", None, None, "2027年"],
            [None, None, "1月", "12月", "年度合计", "1月", "3月", "期间合计", "product_id", "row_kind", "row_key"],
            ["智界", "Future 2027款", 0, 12, 999, 20, None, 999, "fake-id", "product", "untrusted-key"],
            [None, "Same 2025款 合计", 3, 4, 888, 5, 6, 888],
            [None, "Future 2027款 锁单 合计", "=SUM(C5:C6)", 900, None, 900, 900],
            [None, "智界 合计", 900, 900, 900, 900, 900],
            ["总计", None, 900, 900, 900, 900, 900]])
        for area in ("A3:A4", "B3:B4", "C3:E3", "F3:H3", "A5:A8"):
            sheet.merge_cells(area)
        result = self.parse_both(sheet)
        self.assertEqual(result, [
            {"brand": "智界", "model": "Future 2027款", "version": MODEL_PLAN_LABEL,
             "plans": [{"month": "2026-01", "sop": 0, "cell": "C5"},
                       {"month": "2026-12", "sop": 12, "cell": "D5"},
                       {"month": "2027-01", "sop": 20, "cell": "F5"}]},
            {"brand": "智界", "model": "Same 2025款", "version": MODEL_PLAN_LABEL,
             "plans": [{"month": "2026-01", "sop": 3, "cell": "C6"},
                       {"month": "2026-12", "sop": 4, "cell": "D6"},
                       {"month": "2027-01", "sop": 5, "cell": "F6"},
                       {"month": "2027-03", "sop": 6, "cell": "G6"}]}])
        self.assertTrue(all(set(entry) == {"brand", "model", "version", "plans"} for entry in result))

    def test_explicit_month_headers_support_early_brand_merges_and_blank_months(self):
        sheet = self.sheet([
            ["品牌", "车型", "2026-10", "2027-04", "年度合计"],
            ["享界", "G9 2027款", None, 0, 1000],
            [None, "S9 2026款", 5, None, 1000]])
        sheet.merge_cells("A2:A3")
        result = self.parse_both(sheet)
        self.assertEqual([entry["plans"] for entry in result], [
            [{"month": "2027-04", "sop": 0, "cell": "D2"}],
            [{"month": "2026-10", "sop": 5, "cell": "C3"}]])

    def test_visible_metrics_skip_locks_and_hidden_ids_are_ignored(self):
        sheet = self.sheet([
            ["品牌", "车型", "指标", "2026-10", "product_id", "row_kind", "row_key"],
            ["智界", "Future 2027款", "锁单数", 987, "wrong-lock-id", "product", "lock:wrong"],
            ["智界", "Future 2027款", "SOP计划量", 12, "wrong-plan-id", "product", "plan:wrong"]])
        result = self.parse_both(sheet)
        self.assertEqual(result[0]["plans"], [{"month": "2026-10", "sop": 12, "cell": "D3"}])
        self.assertNotIn("product_id", result[0])

    def test_same_model_different_years_stay_separate_and_disjoint_month_rows_combine(self):
        result = self.parse_both(self.sheet([
            ["品牌", "车型", "2026-10", "2026-11"],
            ["智界", "Future 2026款", 1, None],
            ["智界", "Future 2027款", 2, None],
            ["智界", "Future 2026款", None, 3]]))
        self.assertEqual([entry["model"] for entry in result], ["Future 2026款", "Future 2027款"])
        self.assertEqual([plan["sop"] for plan in result[0]["plans"]], [1, 3])

    def test_actual_old_model_export_without_metadata_can_be_parsed(self):
        with tempfile.TemporaryDirectory(prefix="sop-plain-export-") as folder:
            target = Path(folder) / "old-model.xlsx"
            write_export_workbook({"schema_version": 1, "output_path": str(target), "current_month": "2026-10",
                "months": ["2026-10", "2026-12"], "include_versions": False, "include_locks": False,
                "scope": "old format", "headers": ["层级", "品牌", "车型", "指标", "2026-10", "2026-12", "数据说明"],
                "rows": [["车型汇总", "尚界", "New 2027款", "SOP计划量", 5, 0, ""]], "annual_plans": [{"2026": 5}]})
            original = target.read_bytes()
            for read_only in (False, True):
                book = openpyxl.load_workbook(target, read_only=read_only, data_only=False)
                try:
                    self.assertNotIn("_sop_import", book.sheetnames)
                    result = parse_model_table(book["SOP计划"])
                    self.assertEqual((result[0]["brand"], result[0]["model"]), ("尚界", "New 2027款"))
                    self.assertEqual([p["sop"] for p in result[0]["plans"]], [5, 0])
                finally:
                    book.close()
            self.assertEqual(target.read_bytes(), original)

    def test_optional_blank_version_column_still_accepts_model_rows(self):
        result = self.parse_both(self.sheet([
            ["品牌", "车型", "版本", "2026-10", "2026-11"],
            ["智界", "Future 2027款", None, 0, 2],
            ["智界", "Other 2027款 合计", None, 3, None]]))
        self.assertEqual([entry["model"] for entry in result], ["Future 2027款", "Other 2027款"])
        self.assertEqual([plan["sop"] for plan in result[0]["plans"]], [0, 2])

    def test_model_labels_are_recognized_and_actual_version_or_lock_rows_ignored(self):
        sheet = self.sheet([
            ["品牌", "车型", "版本", "2026-10", "2026-11"],
            ["智界", "Future 2027款", "Max", 88, 99],
            [None, None, MODEL_PLAN_LABEL, 0, None],
            [None, "Future 2027款 合计", None, "=SUM(D2:D3)", 12],
            [None, "Future 2027款", "Max（锁单数）", 900, 900],
            [None, "智界 合计", None, 900, 900]])
        sheet.merge_cells("A2:A6"); sheet.merge_cells("B2:B3")
        result = self.parse_both(sheet)
        self.assertEqual(result, [{"brand": "智界", "model": "Future 2027款", "version": MODEL_PLAN_LABEL,
            "plans": [{"month": "2026-10", "sop": 0, "cell": "D3"},
                      {"month": "2026-11", "sop": 12, "cell": "E4"}]}])
        sheet.cell(4, 4).value = 11
        self.assert_bad(sheet, "重复填写")

    def test_explicit_version_level_requires_model_label_to_be_a_model_input(self):
        result = self.parse_both(self.sheet([
            ["层级", "品牌", "车型", "版本", "2026-10"],
            ["版本明细", "智界", "Future 2027款", "Max", 90],
            ["版本明细", "智界", "Future 2027款", MODEL_PLAN_LABEL, 12],
            ["版本明细", "智界", "Other 2027款", None, 999],
            ["车型汇总", "智界", "Future 2027款 合计", None, "=SUM(E2:E3)"]]))
        self.assertEqual(result[0]["plans"], [{"month": "2026-10", "sop": 12, "cell": "E3"}])
        self.assertEqual(len(result), 1)

    def test_real_maintenance_model_inputs_or_handwritten_subtotals_preserve_versions(self):
        for add_model, model_value, handwritten in ((True, 80, False), (True, None, True),
                                                   (True, None, False), (False, None, False)):
            with self.subTest(add_model=add_model, model_value=model_value, handwritten=handwritten), tempfile.TemporaryDirectory(prefix="sop-plain-maintenance-") as folder:
                root = Path(folder)
                rules = root / "rules.py"; rules.write_text("MERGE_GROUPS = {}", encoding="utf-8")
                source = root / "sales.json"
                source.write_text(json.dumps({"meta": {}, "products": [
                    {"id": pid, "brand": "智界", "model": "Future", "version": version, "generation": "智界 Future 2027款"}
                    for pid, version in (("max", "Max"), ("pro", "Pro"))], "sales": []}), encoding="utf-8")
                with patch.dict(os.environ, {"SOP_SHARED_CONFIG": str(rules), "SOP_EXCEL_ENGINE": "python"}):
                    store = SOPStore(root / "test.sqlite3", source, lambda: datetime(2026, 10, 29, 12, tzinfo=TZ))
                    try:
                        store.save_plans({"owner": "test", "changes": [
                            {"product_id": pid, "month": "2026-10", "sop": amount, "forecast": None, "revision": 0}
                            for pid, amount in (("max", 10), ("pro", 20))]})
                        if add_model:
                            view = store.generation_view()
                            store.save_grid_batch({"request_id": str(uuid.uuid4()), "owner": "test",
                                "generation_mode": view["mode"], "generation_rule_signature": view["rule_signature"],
                                "changes": [], "catalog_changes": [], "entries": [{"client_id": "model", "brand": "智界",
                                    "model": "Future 2027款", "plan_level": "model",
                                    "plans": [] if model_value is None else [{"month": "2026-10", "sop": model_value}]}]})
                        target = root / "maintenance.xlsx"
                        sync = WorkbookSync(store, target, writer=write_plan_workbook)
                        self.assertEqual(sync.sync()["status"], "synced")
                        before = [tuple(row) for row in store.db.execute("SELECT * FROM plans ORDER BY product_id,month")]
                        if handwritten:
                            book = openpyxl.load_workbook(target)
                            try:
                                sheet = book["SOP计划"]
                                subtotal = next(row[0].row for row in sheet.iter_rows() if row[1].value == "Future 2027款 合计")
                                sheet.cell(subtotal, 13).value = 123  # October; other months retain formulas.
                                book.save(target)
                            finally:
                                book.close()
                        for read_only in (False, True):
                            book = openpyxl.load_workbook(target, read_only=read_only, data_only=False)
                            try:
                                self.assertNotIn("_sop_import", book.sheetnames)
                                if model_value is None and not handwritten:
                                    with self.assertRaisesRegex(ValidationError, "普通表只导入车型总量.*带标识原表可维护版本"):
                                        parse_model_table(book["SOP计划"])
                                else:
                                    entries = parse_model_table(book["SOP计划"])
                                    self.assertEqual(len(entries), 1)
                                    self.assertEqual((entries[0]["brand"], entries[0]["model"], entries[0]["version"]), ("智界", "Future 2027款", MODEL_PLAN_LABEL))
                                    self.assertEqual([(p["month"], p["sop"]) for p in entries[0]["plans"]], [("2026-10", 123 if handwritten else 80)])
                            finally:
                                book.close()
                        self.assertEqual([tuple(row) for row in store.db.execute("SELECT * FROM plans ORDER BY product_id,month")], before)
                        self.assertEqual([row[0] for row in store.db.execute("SELECT sop FROM plans WHERE product_id IN ('max','pro') ORDER BY product_id")], [10, 20])
                    finally:
                        store.close()

    def test_plain_blank_brand_is_not_inherited(self):
        self.assert_bad(self.sheet([["品牌", "车型", "2026-10"],
            ["智界", "Future 2026款", 1], [None, "Future 2027款", 2]]), "普通空白")

    def test_duplicate_model_month_and_duplicate_month_columns_are_rejected(self):
        self.assert_bad(self.sheet([["品牌", "车型", "2026-10"],
            ["智界", "Future 2027款", 1], ["智界", "Future 2027款", 1]]), "重复填写")
        self.assert_bad(self.sheet([["品牌", "车型", "2026-10", "2026-10"],
            ["智界", "Future 2027款", 1, 2]]), "月份表头重复")

    def test_versions_or_version_level_cannot_be_interpreted_as_model_totals(self):
        self.assert_bad(self.sheet([["品牌", "车型", "版本", "2026-10"],
            ["智界", "Future 2027款", "Max", 1]]), "普通表只导入车型总量")
        self.assert_bad(self.sheet([["层级", "品牌", "车型", "2026-10"],
            ["版本明细", "智界", "Future 2027款", 1]]), "普通表只导入车型总量")

    def test_missing_year_and_ambiguous_or_invalid_month_headers_are_rejected(self):
        self.assert_bad(self.sheet([["品牌", "车型", "2026-10"], ["智界", "Future", 1]]), "缺少年款")
        self.assert_bad(self.sheet([["品牌", "车型", "1月"], ["智界", "Future 2027款", 1]]), "缺少年份")
        self.assert_bad(self.sheet([["品牌", "车型", "2026-13"], ["智界", "Future 2027款", 1]]), "1至12")
        self.assert_bad(self.sheet([["品牌", "车型", "26年"], [None, None, "1月"], ["智界", "Future 2027款", 1]]), "缺少年份")
        self.assert_bad(self.sheet([["品牌", "车型", "2026年", None], [None, None, "12月", "1月"],
            ["智界", "Future 2027款", 1, 2]]), "跨年年份不明确")

    def test_formula_labels_and_quantities_are_rejected(self):
        for brand, model, amount, message in [
            ('="智界"', "Future 2027款", 1, "品牌不能填写公式"),
            ("智界", '="Future 2027款"', 1, "车型不能填写公式"),
            ("智界", "Future 2027款", "=1+1", "数量不能填写公式")]:
            with self.subTest(message=message):
                self.assert_bad(self.sheet([["品牌", "车型", "2026-10"], [brand, model, amount]]), message)
        sheet = self.sheet([["品牌", "车型", "2026-10"], ['="智界"', "Future 2027款", 1], [None, "Other 2027款", 2]])
        sheet.merge_cells("A2:A3")
        self.assert_bad(sheet, "品牌不能填写公式")

    def test_invalid_quantities_are_not_coerced(self):
        for amount in (True, -1, 1.5, "100", "2027-01", "1月"):
            with self.subTest(amount=amount):
                self.assert_bad(self.sheet([["品牌", "车型", "2026-10"], ["智界", "Future 2027款", amount]]), "非负整数")

    def test_empty_table_or_all_blank_months_has_clear_error(self):
        self.assert_bad(self.sheet([]), "未找到车型计划主表")
        self.assert_bad(self.sheet([["品牌", "车型", "2026-10"], ["智界", "Future 2027款", None]]), "没有可导入")

    def test_parser_bounds_rows_columns_months_and_data_items(self):
        sheet = self.sheet([["品牌", "车型", "2026-10"], ["智界", "Future 2027款", 1]])
        sheet.cell(15001, 1).value = "too far"
        with self.assertRaisesRegex(ValidationError, "范围过大"): parse_model_table(sheet)
        sheet = self.sheet([["品牌", "车型", "2026-10"], ["智界", "Future 2027款", 1]])
        sheet.cell(1, 201).value = "too wide"
        with self.assertRaisesRegex(ValidationError, "范围过大"): parse_model_table(sheet)
        sheet = self.sheet([["品牌", "车型", "2026-10", "2026-11"], ["智界", "Future 2027款", 1, 2]])
        with patch("sop.model_table.MAX_MONTHS", 1), self.assertRaisesRegex(ValidationError, "120个月"):
            parse_model_table(sheet)
        with patch("sop.model_table.MAX_ITEMS", 1), self.assertRaisesRegex(ValidationError, "20000项"):
            parse_model_table(sheet)


if __name__ == "__main__":
    unittest.main()
