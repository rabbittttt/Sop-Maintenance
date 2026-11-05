"""Editable summary parents and their member generations share one Excel grid."""
import json
import os
import subprocess
import sys
import uuid
import unittest
from pathlib import Path
from unittest.mock import patch

import openpyxl

from sop.plan_scope import MODEL_PLAN_LABEL, plan_total
from sop.roundtrip import PlanRoundtrip
from sop.workbook import WorkbookSync, generation_model_groups
from sop.xlsx_plan import write_plan_workbook
from sop.xlsx_export import write_export_workbook
from sop.store import ValidationError
from sop.runtime import node_executable
from tests import test_grid_batch as grid
from tests.test_workbook_sync import edit_cell


class SummaryHierarchyExcelTest(unittest.TestCase):
    setUp = grid.GridBatchTest.setUp

    def products_and_plans(self):
        sales = self.store.read_sales()
        members = sales.get("generation_members", {})
        products = [p for p in sales["products"] if not p.get("read_only")] + members.get("products", [])
        plans = {(r["product_id"], r["month"]): r for r in [*self.store.view_plans(sales), *members.get("plans", [])]}
        return products, plans

    def parent(self):
        return next(p for p in self.store.read_sales()["products"] if "summary_member_product_ids" in p)

    def body(self, months=None):
        months = months or ["2026-09", "2026-10", "2026-12"]
        products, _ = self.products_and_plans()
        rows, ids, scopes = [], [], []
        for (brand, model, parent), members in generation_model_groups(products)[0]:
            rows.append(["车型汇总", brand, model, "", "SOP计划量", *[None for _ in months], ""])
            ids.append(None); scopes.append(parent)
            for product in members:
                rows.append(["版本明细", brand, model, product["version"], "SOP计划量", *[None for _ in months], ""])
                ids.append(product["id"]); scopes.append(parent)
        return {"schema_version": 1, "current_month": "2026-10", "months": months,
                "include_versions": True, "include_locks": False, "scope": "all",
                "headers": ["层级", "品牌", "车型", "版本", "指标", *months, "数据说明"],
                "rows": rows, "row_product_ids": ids, "row_parent_generations": scopes,
                "selected_product_ids": [p["id"] for p in products],
                "annual_plans": [{m[:4]: None for m in months if m.endswith("-12")} for _ in rows]}

    def export(self):
        service = PlanRoundtrip(self.store)
        clean, context = service.prepare(self.body())
        path = self.root / "hierarchy-export.xlsx"
        write_export_workbook({**clean, "roundtrip": context, "output_path": str(path)})
        service.register(path, context)
        return service, path, clean, context

    def input_address(self, path, pid):
        book = openpyxl.load_workbook(path)
        try:
            sheet = book["SOP计划"]
            row = next(r for r in sheet.iter_rows() if any(c.value == pid for c in r))
            return f"E{row[0].row}"  # September, October, December.
        finally:
            book.close()

    def test_parent_blank_is_raw_and_noop_roundtrip_does_not_create_override(self):
        parent = self.parent()
        service, path, clean, context = self.export()
        index = context["row_product_ids"].index(parent["id"])
        self.assertIsNone(clean["rows"][index][6])
        book = openpyxl.load_workbook(path, data_only=True)
        try:
            sheet = book["SOP计划"]
            self.assertIsNone(sheet[self.input_address(path, parent["id"])].value)
            self.assertEqual(next(r for r in sheet.iter_rows() if r[1].value == parent["model"] + " 合计")[4].value, 30)
            self.assertEqual(next(r for r in sheet.iter_rows() if r[1].value == "智界 合计")[4].value, 30)
            self.assertEqual(len([m for m in sheet.merged_cells.ranges if str(m).startswith("A5:A")]), 1)
            self.assertTrue(any(sheet.row_dimensions[r[0].row].outlineLevel == 1 and sheet.row_dimensions[r[0].row].hidden
                                for r in sheet.iter_rows() if any(c.value == "a" for c in r)))
        finally:
            book.close()
        self.assertTrue(service.import_content(path.read_bytes())["ok"])
        raw = self.store.db.execute("SELECT sop FROM plans WHERE product_id=?", (parent["id"],)).fetchall()
        self.assertTrue(not raw or all(row["sop"] is None for row in raw))

    def test_parent_zero_and_clear_preserve_members_and_totals(self):
        parent = self.parent()
        for value, expected in ((0, 0), (90, 90), (None, 30)):
            service, path, _, _ = self.export()
            edit_cell(path, self.input_address(path, parent["id"]), value)
            self.assertTrue(service.import_content(path.read_bytes())["ok"])
            sales = self.store.read_sales()
            plans = {(r["product_id"], r["month"]): r for r in self.store.view_plans(sales)}
            self.assertEqual(plan_total(sales["products"], "2026-10", plans), expected)
            self.assertEqual(self.store.db.execute("SELECT sop FROM plans WHERE product_id='a' AND month='2026-10'").fetchone()[0], 10)
            maintenance = self.root / "maintenance.xlsx"
            sync = WorkbookSync(self.store, maintenance, writer=write_plan_workbook)
            self.assertEqual(sync.sync()["status"], "synced")
            book = openpyxl.load_workbook(maintenance, data_only=True)
            try:
                total = next(row for row in book["SOP计划"].iter_rows() if row[1].value == "智界 合计")
                self.assertEqual(total[12].value, expected)
            finally:
                book.close()

    def test_identical_parent_and_member_model_names_keep_distinct_inputs(self):
        self.config.write_text("MERGE_GROUPS = {'智界 A 2026款汇总': ['智界 A 2026款', '智界 B 2026款']}\n", encoding="utf-8")
        parent = self.parent()
        service, path, _, context = self.export()
        self.assertIn("a", context["selected_product_ids"])
        self.assertIn(parent["id"], context["selected_product_ids"])
        edit_cell(path, self.input_address(path, parent["id"]), 77)
        self.assertTrue(service.import_content(path.read_bytes())["ok"])
        self.assertEqual(self.store.db.execute("SELECT sop FROM plans WHERE product_id='a' AND month='2026-10'").fetchone()[0], 10)
        self.assertEqual(self.store.db.execute("SELECT sop FROM plans WHERE product_id=? AND month='2026-10'", (parent["id"],)).fetchone()[0], 77)

    def test_existing_detail_maintenance_imports_pending_edits_before_upgrading(self):
        self.store.update_settings({"generation_mode": "detail"})
        path = self.root / "old-detail.xlsx"
        sync = WorkbookSync(self.store, path, writer=write_plan_workbook)
        self.assertEqual(sync.sync()["status"], "synced")
        context, _ = sync._context()
        address = next(c["cell"] for c in context["layout"]["cells"] if c["product_id"] == "a" and c["month"] == "2026-10")
        edit_cell(path, address, 17)
        self.store.update_settings({"generation_mode": "summary"})
        self.assertEqual(sync.sync()["status"], "synced")
        self.assertEqual(self.store.db.execute("SELECT sop FROM plans WHERE product_id='a' AND month='2026-10'").fetchone()[0], 17)
        sales = self.store.read_sales()
        plans = {(r["product_id"], r["month"]): r for r in self.store.view_plans(sales)}
        self.assertEqual(plan_total(sales["products"], "2026-10", plans), 37)
        self.assertIsNone(next(r for r in self.store.view_plans(sales) if r["product_id"] == self.parent()["id"])["sop"])

    def test_node_and_python_hierarchy_writers_keep_the_same_grid(self):
        root = Path(__file__).resolve().parents[1]
        node = node_executable()
        if not node or not (root / "scripts/node_modules/@oai/artifact-tool/package.json").is_file():
            self.skipTest("Optional Node spreadsheet package unavailable")
        sync = WorkbookSync(self.store, self.root / "not-created.xlsx", writer=write_plan_workbook)
        context, _ = sync._context()
        payload = {**context, "token": uuid.uuid4().hex, "generated_at": "2026-10-29T12:00:00+08:00"}
        reference = self.root / "python-maintenance.xlsx"
        actual = self.root / "node-maintenance.xlsx"
        write_plan_workbook({**payload, "output_path": str(reference)})
        environment = {**os.environ, "SOP_PYTHON_PATH": sys.executable, "PYTHONDONTWRITEBYTECODE": "1"}
        result = subprocess.run([node, str(root / "scripts/build_plan_workbook.mjs")],
                                input=json.dumps({**payload, "output_path": str(actual)}),
                                cwd=root, env=environment, capture_output=True, encoding="utf-8", timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        for cached in (False, True):
            left, right = openpyxl.load_workbook(reference, data_only=cached), openpyxl.load_workbook(actual, data_only=cached)
            try:
                self.assertEqual(list(left["SOP计划"].values), list(right["SOP计划"].values))
                self.assertEqual({str(r) for r in left["SOP计划"].merged_cells.ranges}, {str(r) for r in right["SOP计划"].merged_cells.ranges})
            finally:
                left.close(); right.close()
        service = PlanRoundtrip(self.store)
        clean, context = service.prepare(self.body())
        write_export_workbook({**clean, "roundtrip": context, "output_path": str(reference)})
        result = subprocess.run([node, str(root / "scripts/build_export_workbook.mjs")],
                                input=json.dumps({**clean, "roundtrip": context, "output_path": str(actual)}),
                                cwd=root, env=environment, capture_output=True, encoding="utf-8", timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        left, right = openpyxl.load_workbook(reference, data_only=True), openpyxl.load_workbook(actual, data_only=True)
        try:
            self.assertEqual(list(left["SOP计划"].values), list(right["SOP计划"].values))
        finally:
            left.close(); right.close()
        service.register(actual, context)
        self.assertTrue(service.import_content(actual.read_bytes())["ok"])

    def test_parent_identity_cannot_be_deleted_but_member_plan_is_editable(self):
        parent = self.parent()
        service, path, _, _ = self.export()
        book = openpyxl.load_workbook(path)
        try:
            sheet = book["SOP计划"]
            row = next(r for r in sheet.iter_rows() if any(c.value == parent["id"] for c in r))
            self.assertTrue(row[1].protection.locked)
            self.assertFalse(row[4].protection.locked)
            sheet.delete_rows(row[0].row)
            book.save(path)
        finally:
            book.close()
        with self.assertRaisesRegex(ValidationError, "不能删除"):
            service.import_content(path.read_bytes())


if __name__ == "__main__":
    unittest.main()
