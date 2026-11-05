"""Future model plans survive web edits, Excel roundtrips and later sales arrival."""
import json
import os
import tempfile
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from sop.plan_scope import MODEL_PLAN_LABEL, is_model_plan, plan_total
from sop.roundtrip import PlanRoundtrip
from sop.store import SOPStore, TZ
from sop.xlsx_export import write_export_workbook


class FutureModelTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="sop-future-model-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        rules = self.root / "shared_config.py"
        rules.write_text("MERGE_GROUPS = {}\n", encoding="utf-8")
        environment = patch.dict(os.environ, {"SOP_SHARED_CONFIG": str(rules), "SOP_EXCEL_ENGINE": "python"})
        environment.start()
        self.addCleanup(environment.stop)
        self.source = self.root / "sales.json"
        self.source.write_text('{"meta": {}, "products": [], "sales": [], "model_sales": []}', encoding="utf-8")
        self.database = self.root / "sop.sqlite3"
        self.clock = lambda: datetime(2026, 11, 5, 14, tzinfo=TZ)
        self.store = SOPStore(self.database, self.source, self.clock)
        self.addCleanup(lambda: self.store.close())

    def test_model_without_sales_or_versions_survives_roundtrip_restart_and_source_arrival(self):
        view = self.store.generation_view()
        self.assertEqual(view["mode"], "summary")
        self.assertTrue(view["hierarchy"])
        created = self.store.save_grid_batch({
            "request_id": str(uuid.uuid4()), "owner": "网页维护人",
            "generation_mode": view["mode"], "generation_rule_signature": view["rule_signature"],
            "changes": [], "catalog_changes": [], "entries": [{
                "client_id": "future", "brand": "智界", "model": "Future 2027款", "plan_level": "model",
                "plans": [{"month": "2027-03", "sop": 123}]}]})
        pid = created["entry_ids"]["future"]
        state = self.store.state()
        self.assertEqual(len(state["products"]), 1)
        product = state["products"][0]
        self.assertEqual(product["id"], pid)
        self.assertTrue(is_model_plan(product))
        self.assertEqual(product["version"], MODEL_PLAN_LABEL)
        self.assertTrue(product["active"])
        self.assertFalse(product["source_backed"])
        self.assertFalse(product["source_present"])
        self.assertEqual(state["generation_members"]["products"], [])
        # Missing locks stay absent; a future plan does not manufacture zero sales.
        self.assertEqual(state["sales"], [])
        self.assertEqual(state["model_sales"], [])
        original_plan = dict(self.store.db.execute("SELECT * FROM plans WHERE product_id=?", (pid,)).fetchone())
        self.assertEqual(original_plan["sop"], 123)

        roundtrip = PlanRoundtrip(self.store)
        clean, context = roundtrip.prepare({
            "schema_version": 1, "current_month": "2026-11", "months": ["2027-03"],
            "include_locks": False, "include_versions": True, "scope": "未来车型测试",
            "generation_mode": view["mode"], "generation_rule_signature": view["rule_signature"],
            "headers": ["层级", "品牌", "车型", "版本", "指标", "2027-03", "数据说明"],
            "rows": [["车型汇总", "智界", "Future 2027款", "", "SOP计划量", 123, ""],
                     ["版本明细", "智界", "Future 2027款", MODEL_PLAN_LABEL, "SOP计划量", 123, ""]],
            "annual_plans": [{}, {}], "row_product_ids": [None, pid],
            "row_parent_generations": [None, None], "selected_product_ids": [pid]})
        exported = self.root / "future.xlsx"
        write_export_workbook({**clean, "roundtrip": context, "output_path": str(exported)})
        roundtrip.register(exported, context)
        self.assertTrue(roundtrip.import_content(exported.read_bytes())["ok"])
        self.assertEqual(dict(self.store.db.execute("SELECT * FROM plans WHERE product_id=?", (pid,)).fetchone()), original_plan)

        self.store.close()
        self.store = SOPStore(self.database, self.source, self.clock)
        self.store.record_sales()
        state = self.store.state()
        self.assertEqual([p["id"] for p in state["products"]], [pid])
        self.assertEqual(state["sales"], [])
        self.assertEqual(state["model_sales"], [])
        self.assertEqual(next(r["sop"] for r in state["plans"] if r["product_id"] == pid), 123)

        # Version rows and model lock totals are independent source facts.
        self.source.write_text(json.dumps({"meta": {}, "products": [{
            "id": "future-max", "brand": "智界", "model": "Future", "version": "Max",
            "generation": "智界 Future 2027款"}],
            "sales": [{"product_id": "future-max", "month": "2027-03", "metric": "locked_orders", "value": 7}],
            "model_sales": [{"brand": "智界", "model": "Future", "generation": "智界 Future 2027款",
                             "month": "2027-03", "metric": "locked_orders", "value": 10}]}), encoding="utf-8")
        self.store.record_sales()
        state = self.store.state()
        self.assertEqual({p["id"] for p in state["products"]}, {pid, "future-max"})
        self.assertEqual([p["id"] for p in state["products"] if is_model_plan(p)], [pid])
        self.assertEqual({p["model"] for p in state["products"]}, {"Future 2027款"})
        self.assertEqual([(r["product_id"], r["value"]) for r in state["sales"]], [("future-max", 7)])
        self.assertEqual([r["value"] for r in state["model_sales"]], [10])
        plans = {(r["product_id"], r["month"]): r for r in state["plans"]}
        self.assertEqual(plans[pid, "2027-03"]["sop"], 123)
        self.assertEqual(plan_total(state["products"], "2027-03", plans), 123)
        self.assertEqual(dict(self.store.db.execute("SELECT * FROM plans WHERE product_id=?", (pid,)).fetchone()), original_plan)


if __name__ == "__main__":
    unittest.main()
