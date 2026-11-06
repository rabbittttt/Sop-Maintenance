"""Both workbook engines use plain integer formats and retain strict quantities."""
import json
import os
import subprocess
import unittest
from unittest.mock import patch

import openpyxl

from sop.runtime import node_environment
from sop.store import ValidationError
from sop.workbook import WorkbookSync, generation_model_groups
from sop.xlsx_export import write_export_workbook
from sop.xlsx_plan import write_plan_workbook
from tests import test_inline_renderer as node_runtime
from tests import test_model_export_roundtrip as model_fixtures

ROOT = node_runtime.ROOT
NODE_READY = (node_runtime.NODE_AVAILABLE
              and (ROOT / "scripts/node_modules/@oai/artifact-tool/package.json").is_file())


class IntegerWorkbookFormatTest(unittest.TestCase):
    def setUp(self):
        self.fixture = model_fixtures.ModelExportRoundtripTest(
            "test_noop_does_not_create_overrides_or_undo_new_web_values_after_refresh")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.store = self.fixture.store
        self.model_id = self.fixture.create_model(value=3000)
        self.months = [f"{year}-{month:02d}" for year in (2026, 2027) for month in range(1, 13)]

    def node_writer(self, script, payload):
        result = subprocess.run([str(node_runtime.NODE), str(ROOT / "scripts" / script)],
            input=json.dumps(payload, ensure_ascii=False), cwd=ROOT, env=node_environment(),
            text=True, encoding="utf-8", capture_output=True, timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.assertEqual(result.returncode, 0, result.stderr)

    def export(self, engine, versions):
        products = [p for p in self.store.state()["products"] if p["brand"] == "智界" and p["model"] == "C 2026款"]
        # This independent group has no hierarchy-parent metadata, so the Node
        # full export exercises its native renderer rather than a Python bridge.
        self.assertFalse(any("summary_member_product_ids" in p for p in products))
        rows, ids, parents = [], [], []
        for (brand, model, parent), group in generation_model_groups(products)[0]:
            rows.append(["车型汇总", brand, model, *([""] if versions else []), "SOP计划量", *([None] * 24), ""])
            ids.append(None); parents.append(parent)
            if versions:
                for product in group:
                    rows.append(["版本明细", brand, model, product["version"], "SOP计划量", *([None] * 24), ""])
                    ids.append(product["id"]); parents.append(parent)
        body = {"schema_version": 1, "current_month": "2026-10", "months": self.months,
                "include_versions": versions, "include_locks": False, "scope": "integer format",
                "headers": ["层级", "品牌", "车型", *(["版本"] if versions else []), "指标", *self.months, "数据说明"],
                "rows": rows, "annual_plans": [{"2026": None, "2027": None} for _ in rows],
                "row_product_ids": ids, "row_parent_generations": parents,
                "selected_product_ids": [p["id"] for p in products]}
        clean, context = self.fixture.service.prepare(body)
        path = self.fixture.root / f"{engine}-versions-{versions}.xlsx"
        payload = {**clean, "roundtrip": context, "output_path": str(path)}
        if engine == "python":
            write_export_workbook(payload)
        else:
            self.node_writer("build_export_workbook.mjs", payload)
        self.fixture.service.register(path, context)
        book = openpyxl.load_workbook(path)
        try:
            manifest = json.loads("".join(row[1] for row in book["_sop_import"].iter_rows(min_row=6, values_only=True)))
        finally:
            book.close()
        return path, manifest

    def check_region(self, path, spec, integer=3000):
        book = openpyxl.load_workbook(path, data_only=False)
        seen_integer = False
        try:
            sheet = book["SOP计划"]
            for row in spec["rows"]:
                for column in spec["columns"]:
                    if column["kind"] not in {"month", "year_total"}:
                        continue
                    cell = sheet.cell(row["row"], column["column"])
                    self.assertEqual(cell.number_format, "0", f"{path.name}!{cell.coordinate}: {cell.number_format!r}")
                    self.assertFalse(cell.is_date, f"{path.name}!{cell.coordinate}")
                    if cell.value == integer:
                        self.assertIs(type(cell.value), int)
                        self.assertEqual(cell.data_type, "n")
                        seen_integer = True
            self.assertTrue(seen_integer, f"No preserved integer {integer} was found in the real input grid")
        finally:
            book.close()

    def check_engine(self, engine):
        original = [tuple(row) for row in self.store.db.execute("SELECT * FROM plans ORDER BY product_id,month")]
        path = self.fixture.root / f"{engine}-maintenance.xlsx"
        writer = write_plan_workbook if engine == "python" else lambda payload: self.node_writer("build_plan_workbook.mjs", payload)
        sync = WorkbookSync(self.store, path, writer=writer)
        self.assertEqual(sync.sync()["status"], "synced")
        payload, _ = sync._context()
        self.check_region(path, payload["layout"]["sheets"][0])
        result = self.fixture.service.import_content(path.read_bytes())
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["imported"], result["created"]), (0, 0))
        for versions in (False, True):
            with self.subTest(engine=engine, versions=versions):
                path, manifest = self.export(engine, versions)
                self.check_region(path, manifest)
                result = self.fixture.service.import_content(path.read_bytes())
                self.assertTrue(result["ok"], result)
                self.assertEqual((result["imported"], result["created"]), (0, 0))
        self.assertEqual([tuple(row) for row in self.store.db.execute("SELECT * FROM plans ORDER BY product_id,month")], original)
        self.assertEqual(self.fixture.value(self.model_id), 3000)
        self.assertEqual(self.fixture.value("c"), 5)

    def test_python_maintenance_and_both_export_layouts_use_plain_integer_format(self):
        self.check_engine("python")

    @unittest.skipUnless(NODE_READY, "Bundled Node/artifact-tool link unavailable")
    def test_node_maintenance_and_both_export_layouts_use_plain_integer_format(self):
        self.check_engine("node")

    def test_presentation_upgrade_imports_pending_edits_before_rebuilding_formats(self):
        path = self.fixture.root / "old-presentation.xlsx"
        def old_writer(payload):
            write_plan_workbook(payload)
            book = openpyxl.load_workbook(payload["output_path"])
            try:
                for spec in payload["layout"]["sheets"]:
                    for row in spec["rows"]:
                        for column in spec["columns"]:
                            book[spec["name"]].cell(row["row"], column["column"]).number_format = "#,##0"
                book.save(payload["output_path"])
            finally:
                book.close()
        sync = WorkbookSync(self.store, path, writer=old_writer)
        with patch("sop.workbook.PRESENTATION_VERSION", 8):
            self.assertEqual(sync.sync()["status"], "synced")
            payload, _ = sync._context()
        spec = payload["layout"]["sheets"][0]
        row = next(row for row in spec["rows"] if row.get("product_id") == self.model_id)
        column = next(col["column"] for col in spec["columns"] if col.get("month") == "2026-10")
        book = openpyxl.load_workbook(path)
        try:
            cell = book["SOP计划"].cell(row["row"], column)
            self.assertEqual(cell.number_format, "#,##0")
            cell.value = 4321
            book.save(path)
        finally:
            book.close()
        sync.writer = write_plan_workbook
        self.assertEqual(sync.sync()["status"], "synced")
        self.assertEqual(self.fixture.value(self.model_id), 4321)
        self.assertEqual(self.fixture.value("c"), 5)
        updated, _ = sync._context()
        self.assertGreater(updated["presentation_version"], 8)
        self.check_region(path, updated["layout"]["sheets"][0], integer=4321)

    def test_fraction_hidden_by_integer_display_is_rejected_without_rounding(self):
        before = [tuple(row) for row in self.store.db.execute("SELECT * FROM plans ORDER BY product_id,month")]
        for versions in (False, True):
            for number_format in ("0", '"¥"#,##0.00'):
                with self.subTest(versions=versions, number_format=number_format):
                    path, manifest = self.export("python", versions)
                    if versions:
                        row = next(row for row in manifest["rows"] if row.get("product_id") == self.model_id
                                   and row["kind"] == "product" and row["metric"] == "SOP计划量")
                    else:
                        key = next(key for key, item in manifest["model_inputs"].items() if item["product_id"] == self.model_id)
                        row = next(row for row in manifest["rows"] if row["key"] == key)
                    column = next(col["column"] for col in manifest["columns"] if col.get("month") == "2026-10")
                    book = openpyxl.load_workbook(path)
                    try:
                        cell = book["SOP计划"].cell(row["row"], column)
                        cell.value = 2999.99999
                        cell.number_format = number_format
                        coordinate = cell.coordinate
                        book.save(path)
                    finally:
                        book.close()
                    with self.assertRaises(ValidationError) as caught:
                        self.fixture.service.import_content(path.read_bytes())
                    message = str(caught.exception)
                    self.assertIn("SOP计划!" + coordinate, message)
                    self.assertIn("2999.99999", message)
                    self.assertIn(number_format, message)
                    self.assertEqual([tuple(row) for row in self.store.db.execute("SELECT * FROM plans ORDER BY product_id,month")], before)
                    self.assertEqual(self.fixture.value(self.model_id), 3000)


if __name__ == "__main__":
    unittest.main()
