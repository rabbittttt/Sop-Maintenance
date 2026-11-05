"""Model-only exports are editable inputs with safe aggregate fallbacks."""
import json
import os
import tempfile
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import openpyxl

from sop.plan_scope import MODEL_PLAN_LABEL, is_model_plan, plan_total
from sop.roundtrip import PlanRoundtrip
from sop.store import SOPStore, TZ, ValidationError
from sop.workbook import generation_model_groups
from sop.xlsx_export import write_export_workbook
from tests.test_inline_workbook import change_rows
from tests.test_workbook_sync import edit_cell


class ModelExportRoundtripTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="sop-model-export-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.rules = self.root / "shared_config.py"
        self.rules.write_text("MERGE_GROUPS = {'智界 Together 2026款汇总': ['智界 A 2026款', '智界 B 2026款']}\n", encoding="utf-8")
        environment = patch.dict(os.environ, {"SOP_SHARED_CONFIG": str(self.rules), "SOP_EXCEL_ENGINE": "python"})
        environment.start(); self.addCleanup(environment.stop)
        self.source = self.root / "sales.json"
        self.payload = {"meta": {}, "products": [
            {"id": pid, "brand": brand, "model": model, "generation": f"{brand} {model} 2026款", "version": "Max"}
            for pid, brand, model in [("a", "智界", "A"), ("b", "智界", "B"), ("c", "智界", "C"), ("d", "享界", "S9")]],
            "sales": [], "model_sales": []}
        self.source.write_text(json.dumps(self.payload, ensure_ascii=False), encoding="utf-8")
        self.store = SOPStore(self.root / "test.sqlite3", self.source, lambda: datetime(2026, 10, 29, 12, tzinfo=TZ))
        self.addCleanup(self.store.close)
        for pid, value in [("a", 10), ("b", 20), ("c", 5), ("d", 40)]:
            self.save(pid, value)
        self.service = PlanRoundtrip(self.store)
        self.path = self.root / "model-export.xlsx"

    def save(self, pid, value, month="2026-10", forecast=None):
        row = self.store.db.execute("SELECT * FROM plans WHERE product_id=? AND month=?", (pid, month)).fetchone()
        self.store.save_plans({"owner": "web", "changes": [{"product_id": pid, "month": month, "sop": value,
            "forecast": forecast, "revision": row["revision"] if row else 0}]})

    def value(self, pid, month="2026-10"):
        row = self.store.db.execute("SELECT sop FROM plans WHERE product_id=? AND month=?", (pid, month)).fetchone()
        return row["sop"] if row else None

    def model_id(self, model):
        return next((p["id"] for p in self.store.read_sales(include_excluded=True)["products"]
                     if p["model"] == model and is_model_plan(p)), None)

    def export(self, *, locks=False, months=None, selected=None, legacy=False):
        months = months or ["2026-10", "2026-12"]
        products = self.store.read_sales()["products"]
        if selected is not None:
            products = [p for p in products if p["id"] in selected]
        rows, ids, parents = [], [], []
        for (brand, model, parent), _ in generation_model_groups(products)[0]:
            for metric in (["锁单数", "SOP计划量"] if locks else ["SOP计划量"]):
                rows.append(["车型汇总", brand, model, metric, *([7 if metric == "锁单数" else None] * len(months)), ""])
                ids.append(None); parents.append(parent)
        body = {"schema_version": 1, "current_month": "2026-10", "months": months,
                "include_versions": False, "include_locks": locks, "scope": "test",
                "headers": ["层级", "品牌", "车型", "指标", *months, "数据说明"], "rows": rows,
                "row_product_ids": ids, "row_parent_generations": parents,
                "selected_product_ids": [p["id"] for p in products],
                "annual_plans": [{m[:4]: None for m in months if m.endswith("-12")} for _ in rows]}
        clean, context = self.service.prepare(body)
        payload = {**clean, "roundtrip": context, "output_path": str(self.path)}
        if legacy:
            from sop.xlsx_export import _manifest
            def old_manifest(data, layout):
                result = _manifest(data, layout); result.pop("model_inputs", None)
                return result
            with patch("sop.xlsx_export._manifest", side_effect=old_manifest):
                write_export_workbook(payload)
        else:
            write_export_workbook(payload)
        self.service.register(self.path, context)
        book = openpyxl.load_workbook(self.path)
        try:
            self.manifest = json.loads("".join(row[1].value for row in book["_sop_import"].iter_rows(min_row=6)))
        finally:
            book.close()
        self.original = self.path.read_bytes()

    def address(self, model, month="2026-10"):
        key = next(key for key, item in self.manifest["model_inputs"].items() if item["model"] == model)
        row = next(row["row"] for row in self.manifest["rows"] if row["key"] == key)
        column = next(col["column"] for col in self.manifest["columns"] if col.get("month") == month)
        return f"{openpyxl.utils.get_column_letter(column)}{row}"

    def import_file(self, choice=None):
        return self.service.import_content(self.path.read_bytes(), choice)

    def create_model(self, model="C 2026款", value=80):
        view = self.store.generation_view()
        result = self.store.save_grid_batch({"request_id": str(uuid.uuid4()), "owner": "web",
            "generation_mode": view["mode"], "generation_rule_signature": view["rule_signature"],
            "changes": [], "catalog_changes": [], "entries": [{"client_id": "model", "brand": "智界",
                "model": model, "plan_level": "model", "plans": [{"month": "2026-10", "sop": value}]}]})
        return result["entry_ids"]["model"]

    def test_noop_does_not_create_overrides_or_undo_new_web_values_after_refresh(self):
        self.export()
        before = [tuple(row) for row in self.store.db.execute("SELECT * FROM plans ORDER BY product_id,month")]
        self.assertEqual(self.import_file()["imported"], 0)
        self.assertIsNone(self.model_id("C 2026款"))
        self.assertIsNone(self.value(self.model_id("Together 2026款")))
        self.assertEqual([tuple(row) for row in self.store.db.execute("SELECT * FROM plans ORDER BY product_id,month")], before)
        parent = self.model_id("Together 2026款")
        self.save(parent, 90); self.save("c", 8)
        self.store.record_sales()
        self.assertEqual(self.import_file()["imported"], 0)
        self.assertEqual((self.value(parent), self.value("c")), (90, 8))

    def test_raw_null_parent_can_save_zero_and_same_file_clear_is_never_silently_ignored(self):
        parent = self.model_id("Together 2026款")
        self.export()
        edit_cell(self.path, self.address("Together 2026款"), 0)
        self.assertEqual(self.import_file()["imported"], 1)
        self.assertEqual(self.value(parent), 0)
        edit_cell(self.path, self.address("Together 2026款"), None)
        result = self.import_file()
        self.assertFalse(result["ok"])
        self.assertEqual(result["conflicts"][0]["database"], 0)
        self.assertEqual(self.import_file("workbook")["imported"], 1)
        self.assertIsNone(self.value(parent))
        self.assertEqual((self.value("a"), self.value("b")), (10, 20))
        self.assertEqual(self.import_file()["imported"], 0)

    def test_model_without_anchor_is_created_once_and_same_file_clear_and_zero_work(self):
        self.export()
        edit_cell(self.path, self.address("C 2026款"), 123)
        result = self.import_file()
        self.assertEqual((result["imported"], result["created"]), (1, 1))
        pid = self.model_id("C 2026款")
        self.assertEqual(self.import_file()["created"], 0)
        edit_cell(self.path, self.address("C 2026款"), None)
        self.assertFalse(self.import_file()["ok"])
        self.assertEqual(self.import_file("workbook")["imported"], 1)
        self.assertIsNone(self.value(pid))
        edit_cell(self.path, self.address("C 2026款"), 0)
        self.assertEqual(self.import_file()["imported"], 1)
        self.assertEqual(self.model_id("C 2026款"), pid)
        self.assertEqual((self.value(pid), self.value("c")), (0, 5))
        self.store.record_sales()
        self.assertEqual(self.service.import_content(self.original)["imported"], 0)
        self.assertEqual(self.value(pid), 0)

    def test_web_conflicts_keep_model_forecast_and_unselected_brand(self):
        pid = self.create_model()
        self.save(pid, 80, forecast=77)
        self.export(selected={pid, "c"})
        edit_cell(self.path, self.address("C 2026款"), 100)
        self.save(pid, 90, forecast=88)
        conflict = self.import_file()
        self.assertFalse(conflict["ok"])
        self.assertEqual((conflict["conflicts"][0]["baseline"], conflict["conflicts"][0]["database"]), (80, 90))
        self.assertEqual(self.import_file("db")["imported"], 0)
        self.assertEqual(self.value(pid), 90)
        self.assertEqual(self.import_file("workbook")["imported"], 1)
        self.assertEqual(self.store.db.execute("SELECT forecast FROM plans WHERE product_id=?", (pid,)).fetchone()[0], 88)
        self.assertEqual(self.value("d"), 40)
        self.assertTrue(all(p["active"] for p in self.store.list_products()["items"]))

    def test_concurrent_new_model_override_conflicts_instead_of_creating_duplicate(self):
        self.export(selected={"c"})
        edit_cell(self.path, self.address("C 2026款"), 12)
        pid = self.create_model(value=9)
        result = self.import_file()
        self.assertFalse(result["ok"])
        self.assertEqual(result["conflicts"][0]["database"], 9)
        self.assertEqual(self.import_file("workbook")["created"], 0)
        self.assertEqual((self.model_id("C 2026款"), self.value(pid)), (pid, 12))

    def test_add_future_model_without_version_and_keep_other_model_years(self):
        self.export(months=["2026-10", "2027-03"])
        book = openpyxl.load_workbook(self.path)
        try:
            at = book["SOP计划"].max_row + 1
        finally:
            book.close()
        change_rows(self.path, at, insert={"A": "智界", "B": "Future 2027款", "D": 123})
        result = self.import_file()
        self.assertEqual((result["created"], result["imported"]), (1, 1))
        pid = self.model_id("Future 2027款")
        self.assertEqual(self.value(pid, "2027-03"), 123)
        self.assertEqual(self.import_file()["created"], 0)
        self.assertFalse(next(p for p in self.store.list_products()["items"] if p["id"] == pid)["source_backed"])
        self.assertEqual((self.value("a"), self.value("b"), self.value("c"), self.value("d")), (10, 20, 5, 40))

    def test_locked_reference_headers_metadata_names_formulas_and_deletions_are_rejected(self):
        for kind in ("lock", "header", "metadata", "name", "formula", "delete"):
            with self.subTest(kind=kind):
                self.export(locks=True)
                address = self.address("C 2026款")
                row = int("".join(c for c in address if c.isdigit()))
                if kind == "lock":
                    lock = next(r for r in self.manifest["rows"] if r["kind"] == "model_total" and r["metric"] == "锁单数")
                    edit_cell(self.path, f"C{lock['row']}", 8)
                elif kind == "header": edit_cell(self.path, "C4", "改月份")
                elif kind == "metadata": edit_cell(self.path, "B2", "changed", sheet=2)
                elif kind == "name": edit_cell(self.path, f"B{row}", "改车型 2027款")
                elif kind == "formula": edit_cell(self.path, address, "1+1", formula=True)
                else: change_rows(self.path, row, delete=True)
                with self.assertRaises(ValidationError): self.import_file()
                self.assertIsNone(self.model_id("C 2026款"))
                self.assertEqual(self.value("c"), 5)

    def test_unopened_month_and_new_model_without_year_are_rejected(self):
        self.export(months=["2026-10", "2028-01"])
        edit_cell(self.path, self.address("C 2026款", "2028-01"), 1)
        with self.assertRaisesRegex(ValidationError, "不可维护"): self.import_file()
        self.export()
        book = openpyxl.load_workbook(self.path)
        try: at = book["SOP计划"].max_row + 1
        finally: book.close()
        change_rows(self.path, at, insert={"A": "智界", "B": "Future", "C": 5})
        with self.assertRaisesRegex(ValidationError, "年款"): self.import_file()

    def test_old_model_only_files_still_request_new_trusted_export(self):
        self.export(legacy=True)
        with self.assertRaisesRegex(ValidationError, "重新导出"): self.import_file()


if __name__ == "__main__":
    unittest.main()
